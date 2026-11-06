from collections.abc import Sequence
from typing import Any

from libsentrykube.gke_upgrade import (
    Clients,
    Node,
    UpgradeRunner,
    pool_nodes,
    running_pool_ops,
    surge_settings,
    watch_report,
)

TARGET = "1.35.9-gke.1505000"
OLD = "1.35.6-gke.1250001"


class FakeGke:
    """
    A cluster whose node pool upgrades are driven by the test: each started
    operation runs for `ticks` polls and then lands (or fails) the way the
    test scripted it.
    """

    def __init__(self, pools: dict[str, int], master: str = TARGET) -> None:
        self.master = master
        self.pool_versions = {name: OLD for name in pools}
        self.nodes = {
            name: [Node(f"{name}-{i}", name, f"v{OLD}") for i in range(count)]
            for name, count in pools.items()
        }
        self.settings: dict[str, tuple[int, int]] = {name: (1, 0) for name in pools}
        self.ops: list[dict[str, Any]] = []
        self.commands: list[list[str]] = []
        self.log: list[str] = []
        # pool -> list of outcomes for successive attempts: "ok", "fail", "partial"
        self.script: dict[str, list[str]] = {}
        self.ticks = 1
        self.clock = 0.0

    def clients(self) -> Clients:
        return Clients(
            get_cluster=self.get_cluster,
            list_ops=self.list_ops,
            list_nodes=lambda: [n for ns in self.nodes.values() for n in ns],
            gcloud=self.gcloud,
            log=self.log.append,
            sleep=self.sleep,
            now=lambda: self.clock,
        )

    def get_cluster(self) -> dict[str, Any]:
        return {
            "name": "cluster-a",
            "currentMasterVersion": self.master,
            "nodePools": [
                {
                    "name": name,
                    "version": version,
                    "upgradeSettings": {
                        "strategy": "SURGE",
                        "maxSurge": self.settings[name][0],
                        "maxUnavailable": self.settings[name][1],
                    },
                }
                for name, version in self.pool_versions.items()
            ],
        }

    def list_ops(self) -> list[dict[str, Any]]:
        return [dict(op) for op in self.ops]

    def gcloud(self, argv: Sequence[str]) -> tuple[int, str]:
        self.commands.append(list(argv))
        if argv[2] == "node-pools":
            self.settings[argv[4]] = (
                int(argv[argv.index("--max-surge-upgrade") + 1]),
                int(argv[argv.index("--max-unavailable-upgrade") + 1]),
            )
            return 0, ""
        if "--master" in argv:
            self.ops.append(
                {
                    "name": f"op-master-{len(self.ops)}",
                    "operationType": "UPGRADE_MASTER",
                    "status": "RUNNING",
                    "targetLink": "/clusters/cluster-a",
                    "_left": self.ticks,
                }
            )
            return 0, ""
        pool = argv[argv.index("--node-pool") + 1]
        if any(
            op["status"] == "RUNNING" and op["targetLink"].endswith(f"/{pool}")
            for op in self.ops
        ):
            return 1, "ERROR: (gcloud) Cluster is running incompatible operation"
        self.pool_versions[pool] = TARGET
        outcome = (
            self.script.get(pool, ["ok"]).pop(0) if self.script.get(pool) else "ok"
        )
        self.ops.append(
            {
                "name": f"op-{pool}-{len(self.ops)}",
                "operationType": "UPGRADE_NODES",
                "status": "RUNNING",
                "targetLink": f"/clusters/cluster-a/nodePools/{pool}",
                "progress": {"metrics": [{"name": "NODES_TOTAL", "intValue": "2"}]},
                "_left": self.ticks,
                "_outcome": outcome,
            }
        )
        return 0, ""

    def sleep(self, seconds: float) -> None:
        self.clock += seconds
        for op in self.ops:
            if op["status"] != "RUNNING":
                continue
            op["_left"] -= 1
            if op["_left"] > 0:
                continue
            op["status"] = "DONE"
            if op["operationType"] == "UPGRADE_MASTER":
                self.master = TARGET
                continue
            pool = op["targetLink"].rsplit("/", 1)[1]
            nodes = self.nodes[pool]
            if op["_outcome"] == "fail":
                op["statusMessage"] = "ZONE_RESOURCE_POOL_EXHAUSTED"
            elif op["_outcome"] == "partial":
                op["statusMessage"] = (
                    "instance is not a member of instanceGroupManagers"
                )
                self.nodes[pool] = [
                    Node(n.name, pool, f"v{TARGET}") for n in nodes[:1]
                ] + nodes[1:]
            else:
                self.nodes[pool] = [Node(n.name, pool, f"v{TARGET}") for n in nodes]


def runner(gke: FakeGke, **kwargs: Any) -> UpgradeRunner:
    return UpgradeRunner(
        gke.clients(),
        "project-a",
        "region-a",
        "cluster-a",
        TARGET,
        interval=10,
        **kwargs,
    )


def outcomes(r: UpgradeRunner) -> list[tuple[str, str]]:
    return [(res.name, res.outcome) for res in r.results]


def test_pool_nodes_counts_old_and_cordoned() -> None:
    nodes = [
        Node("a", "p", f"v{TARGET}"),
        Node("b", "p", f"v{OLD}", unschedulable=True),
        Node("c", "q", f"v{OLD}"),
    ]
    assert pool_nodes(nodes, "p", TARGET) == pool_nodes(nodes, "p", f"v{TARGET}")
    counts = pool_nodes(nodes, "p", TARGET)
    assert (counts.new, counts.old, counts.cordoned, counts.total) == (1, 1, 1, 2)


def test_running_pool_ops_filters_by_cluster_and_status() -> None:
    ops = [
        {
            "status": "RUNNING",
            "operationType": "UPGRADE_NODES",
            "targetLink": "x/clusters/a/nodePools/p1",
        },
        {
            "status": "DONE",
            "operationType": "UPGRADE_NODES",
            "targetLink": "x/clusters/a/nodePools/p2",
        },
        {
            "status": "RUNNING",
            "operationType": "UPGRADE_NODES",
            "targetLink": "x/clusters/b/nodePools/p3",
        },
        {
            "status": "RUNNING",
            "operationType": "UPGRADE_MASTER",
            "targetLink": "x/clusters/a",
        },
    ]
    assert list(running_pool_ops(ops, "a")) == ["p1"]


def test_surge_settings_defaults() -> None:
    assert surge_settings({}) == {
        "strategy": "SURGE",
        "maxSurge": 0,
        "maxUnavailable": 0,
    }
    assert surge_settings({"upgradeSettings": {"maxSurge": 5}})["maxSurge"] == 5


def test_pools_upgrade_in_given_order_and_verify_kubelets() -> None:
    gke = FakeGke({"small": 1, "big": 2})
    r = runner(gke)
    r.upgrade_pools(["big", "small"])
    started = [
        c[c.index("--node-pool") + 1] for c in gke.commands if "--node-pool" in c
    ]
    assert started == ["big", "small"]
    assert outcomes(r) == [("big", "done"), ("small", "done")]
    assert all("--async" in c and "--quiet" in c for c in gke.commands)


def test_default_pool_list_includes_pools_at_target_with_old_nodes() -> None:
    gke = FakeGke({"flipped": 2, "fresh": 1, "behind": 1})
    gke.pool_versions["flipped"] = TARGET  # version flipped, nodes still old
    gke.pool_versions["fresh"] = TARGET
    gke.nodes["fresh"] = [Node("fresh-0", "fresh", f"v{TARGET}")]
    r = runner(gke)
    r.upgrade_pools([])
    assert outcomes(r) == [("flipped", "done"), ("behind", "done")]


def test_failed_attempt_is_retried_on_same_pool() -> None:
    gke = FakeGke({"p": 2})
    gke.script["p"] = ["fail", "partial", "ok"]
    r = runner(gke, max_attempts=3)
    r.upgrade_pools(["p"])
    assert outcomes(r) == [("p", "done")]
    assert r.results[0].attempts == 3
    assert any(
        "attempt 1 failed: ZONE_RESOURCE_POOL_EXHAUSTED" in line for line in gke.log
    )


def test_gives_up_after_max_attempts() -> None:
    gke = FakeGke({"p": 2})
    gke.script["p"] = ["fail", "fail"]
    r = runner(gke, max_attempts=2)
    r.upgrade_pools(["p"])
    assert outcomes(r) == [("p", "failed")]
    assert "2 nodes still behind after 2 attempts" in r.results[0].detail


def test_empty_pool_still_gets_its_version_upgraded() -> None:
    gke = FakeGke({"empty": 0})
    r = runner(gke)
    r.upgrade_pools(["empty"])
    assert outcomes(r) == [("empty", "done")]
    assert gke.pool_versions["empty"] == TARGET
    assert any("--node-pool" in c for c in gke.commands)


def test_empty_pool_at_target_is_skipped() -> None:
    gke = FakeGke({"empty": 0})
    gke.pool_versions["empty"] = TARGET
    r = runner(gke)
    r.upgrade_pools(["empty"])
    assert outcomes(r) == [("empty", "done")]
    assert gke.commands == []


def test_pool_deleted_mid_run_is_recorded_gone() -> None:
    gke = FakeGke({"nap": 1, "next": 1})
    gke.script["nap"] = ["fail"]
    real_sleep = gke.sleep

    def sleep(seconds: float) -> None:
        real_sleep(seconds)
        # The autoscaler removes the pool once its failed op has ended.
        if any(
            op["status"] == "DONE" and op["targetLink"].endswith("/nap")
            for op in gke.ops
        ):
            gke.pool_versions.pop("nap", None)
            gke.nodes.pop("nap", None)

    gke.sleep = sleep  # type: ignore[method-assign]
    r = runner(gke, fixed_capacity=True)
    r.upgrade_pools(["nap", "next"])
    assert outcomes(r) == [("nap", "gone"), ("next", "done")]
    # No restore of surge settings on a pool that is gone.
    assert [c for c in gke.commands if c[2] == "node-pools" and c[4] == "nap"][1:] == []


def test_control_plane_dry_run_does_not_wait_on_live_operation() -> None:
    gke = FakeGke({"p": 1}, master=OLD)
    gke.ticks = 100
    gke.gcloud(["gcloud", "container", "clusters", "upgrade", "cluster-a", "--master"])
    gke.commands.clear()
    runner(gke, dry_run=True).upgrade_control_plane()
    assert gke.commands == []
    assert gke.clock == 0


def test_vanished_pool_is_noted_not_fatal() -> None:
    gke = FakeGke({"p": 1})
    r = runner(gke)
    r.upgrade_pools(["gone-pool", "p"])
    assert outcomes(r) == [("gone-pool", "gone"), ("p", "done")]


def test_busy_pool_is_deferred_then_adopted() -> None:
    gke = FakeGke({"busy": 1, "free": 1})
    gke.ticks = 2
    gke.gcloud(
        [
            "gcloud",
            "container",
            "clusters",
            "upgrade",
            "cluster-a",
            "--node-pool",
            "busy",
        ]
    )
    gke.commands.clear()
    r = runner(gke)
    r.upgrade_pools(["busy", "free"])
    assert outcomes(r) == [("free", "done"), ("busy", "done")]
    # The existing operation was waited on, never restarted.
    assert [
        c[c.index("--node-pool") + 1] for c in gke.commands if "--node-pool" in c
    ] == ["free"]
    assert any("coming back to it" in line for line in gke.log)


def test_fixed_capacity_flips_and_restores_settings() -> None:
    gke = FakeGke({"p": 2})
    gke.settings["p"] = (5, 0)
    gke.script["p"] = ["fail", "ok"]
    r = runner(gke, fixed_capacity=True)
    r.upgrade_pools(["p"])
    updates = [c for c in gke.commands if c[2] == "node-pools"]
    assert len(updates) == 2
    assert updates[0][updates[0].index("--max-surge-upgrade") + 1 :][:3] == [
        "0",
        "--max-unavailable-upgrade",
        "1",
    ]
    assert gke.settings["p"] == (5, 0)
    # Flipped once, before the first attempt, restored once after the pool finished.
    order = [c[2] + ("/" + c[4] if c[2] == "node-pools" else "") for c in gke.commands]
    assert order == ["node-pools/p", "clusters", "clusters", "node-pools/p"]


def test_fixed_capacity_leaves_already_fixed_pool_alone() -> None:
    gke = FakeGke({"p": 1})
    gke.settings["p"] = (0, 1)
    runner(gke, fixed_capacity=True).upgrade_pools(["p"])
    assert not [c for c in gke.commands if c[2] == "node-pools"]


def test_control_plane_waits_for_operation() -> None:
    gke = FakeGke({"p": 1}, master=OLD)
    gke.ticks = 3
    runner(gke).upgrade_control_plane()
    assert gke.master == TARGET
    assert "--master" in gke.commands[0]
    assert gke.log[-1].startswith("control plane done in")


def test_control_plane_adopts_running_operation() -> None:
    gke = FakeGke({"p": 1}, master=OLD)
    gke.ticks = 2
    gke.gcloud(["gcloud", "container", "clusters", "upgrade", "cluster-a", "--master"])
    gke.commands.clear()
    runner(gke).upgrade_control_plane()
    assert gke.master == TARGET
    assert gke.commands == []
    assert any("already running, waiting" in line for line in gke.log)


def test_control_plane_tolerates_operation_lag() -> None:
    gke = FakeGke({"p": 1}, master=OLD)
    real_gcloud = gke.gcloud

    def slow_gcloud(argv: Sequence[str]) -> tuple[int, str]:
        code, err = real_gcloud(argv)
        gke.ops[-1]["status"] = "PENDING"  # not visible as RUNNING yet
        return code, err

    def sleep(seconds: float) -> None:
        if gke.ops[-1]["status"] == "PENDING":
            gke.ops[-1]["status"] = "RUNNING"
        else:
            FakeGke.sleep(gke, seconds)

    gke.gcloud = slow_gcloud  # type: ignore[method-assign]
    gke.sleep = sleep  # type: ignore[method-assign]
    runner(gke).upgrade_control_plane()
    assert gke.master == TARGET


def test_control_plane_already_there() -> None:
    gke = FakeGke({"p": 1})
    runner(gke).upgrade_control_plane()
    assert gke.commands == []


def test_dry_run_only_prints_commands() -> None:
    gke = FakeGke({"p": 1}, master=OLD)
    r = runner(gke, dry_run=True, fixed_capacity=True)
    r.upgrade_control_plane()
    r.upgrade_pools(["p"])
    assert gke.commands == []
    assert gke.master == OLD
    runs = [line for line in gke.log if line.startswith("run: ")]
    assert len(runs) == 4  # master, flip, pool, restore
    assert outcomes(r) == [("p", "skipped")]


def test_progress_line() -> None:
    gke = FakeGke({"p": 3})
    gke.nodes["p"][0] = Node("p-0", "p", f"v{TARGET}")
    gke.nodes["p"][1] = Node("p-1", "p", f"v{OLD}", unschedulable=True)
    op = {
        "progress": {
            "metrics": [
                {"name": "NODES_TOTAL", "intValue": "3"},
                {"name": "NODES_COMPLETE", "intValue": "9"},
                {"name": "NODE_PDB_DELAY_SECONDS", "intValue": "600"},
            ]
        }
    }
    gke.clock = 900
    assert (
        runner(gke).progress_line("p", op, 0)
        == "pool p: 15m, op 3/3, live 1 new / 2 old, 1 cordoned, pdb delay 10m"
    )


def test_watch_report() -> None:
    gke = FakeGke({"running": 2, "stale": 1, "waiting": 1, "done": 1})
    gke.pool_versions["stale"] = TARGET
    gke.pool_versions["done"] = TARGET
    gke.nodes["done"] = [Node("done-0", "done", f"v{TARGET}")]
    gke.nodes["running"][0] = Node("running-0", "running", f"v{TARGET}")
    ops = [
        {
            "name": "op-1",
            "status": "RUNNING",
            "operationType": "UPGRADE_NODES",
            "targetLink": "x/clusters/cluster-a/nodePools/running",
            "startTime": 0,
            "progress": {
                "metrics": [
                    {"name": "NODES_TOTAL", "intValue": "2"},
                    {"name": "NODES_COMPLETE", "intValue": "1"},
                ]
            },
        }
    ]
    report = watch_report(
        gke.get_cluster(),
        ops,
        gke.clients().list_nodes(),
        TARGET,
        1800,
        lambda op: op["startTime"],
    )
    lines = report.splitlines()
    assert lines[0] == f"cluster-a: 1/4 pools on {TARGET}, 1 running, 2 waiting"
    assert lines[3].split() == ["running", "30m", "1/2", "1/1", "0"]
    assert "pool reports target but still has old nodes: stale=1" in lines
    assert lines[-1] == "waiting (1): waiting [1n]"
