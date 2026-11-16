from collections.abc import Sequence
from typing import Any

import pytest

from libsentrykube.gke_upgrade import (
    Clients,
    FileSurgeStore,
    Node,
    UpgradeRunner,
    is_transient,
    pool_nodes,
    running_pool_ops,
    surge_settings,
    watch_report,
    with_retries,
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
        self.pending_ticks = 0
        self.interrupt_at: float | None = None
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
            if self._in_flight(argv[4]):
                return 1, "ERROR: (gcloud) Cluster is running incompatible operation"
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
        if self._in_flight(pool):
            return 1, "ERROR: (gcloud) Cluster is running incompatible operation"
        self.pool_versions[pool] = TARGET
        outcome = (
            self.script.get(pool, ["ok"]).pop(0) if self.script.get(pool) else "ok"
        )
        self.ops.append(
            {
                "name": f"op-{pool}-{len(self.ops)}",
                "operationType": "UPGRADE_NODES",
                "status": "PENDING" if self.pending_ticks else "RUNNING",
                "targetLink": f"/clusters/cluster-a/nodePools/{pool}",
                "progress": {"metrics": [{"name": "NODES_TOTAL", "intValue": "2"}]},
                "_pending": self.pending_ticks,
                "_left": self.ticks,
                "_outcome": outcome,
            }
        )
        return 0, ""

    def _in_flight(self, pool: str) -> bool:
        return any(
            op["status"] in ("PENDING", "RUNNING")
            and op["targetLink"].endswith(f"/{pool}")
            for op in self.ops
        )

    def sleep(self, seconds: float) -> None:
        self.clock += seconds
        if self.interrupt_at is not None and self.clock >= self.interrupt_at:
            self.interrupt_at = None
            raise KeyboardInterrupt
        for op in self.ops:
            if op["status"] == "PENDING":
                op["_pending"] -= 1
                if op["_pending"] <= 0:
                    op["status"] = "RUNNING"
                continue
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


class ApiError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.resp = type("Resp", (), {"status": status})()


class KubeError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"({status})")
        self.status = status


def test_is_transient() -> None:
    assert is_transient(ApiError(503))
    assert is_transient(ApiError(429))
    assert is_transient(KubeError(500))
    assert is_transient(ConnectionResetError())
    assert is_transient(TimeoutError())
    assert not is_transient(ApiError(403))
    assert not is_transient(KubeError(404))
    assert not is_transient(KeyError("currentMasterVersion"))


def test_reads_are_retried_through_transient_errors() -> None:
    gke = FakeGke({"p": 1})
    failures = [ApiError(503), ApiError(503)]
    inner = gke.get_cluster

    def flaky() -> dict[str, Any]:
        if failures:
            raise failures.pop(0)
        return inner()

    base = gke.clients()
    base.get_cluster = flaky
    clients = with_retries(base)
    assert clients.get_cluster()["name"] == "cluster-a"
    assert gke.clock == 15  # 5s, then 10s
    retries = [line for line in gke.log if "retry" in line]
    assert len(retries) == 2
    assert "reading the cluster failed (ApiError: HTTP 503), retry 1/7 in 5s" in retries


def test_retries_give_up_and_skip_permanent_errors() -> None:
    gke = FakeGke({"p": 1})
    base = gke.clients()
    calls = []

    def down() -> list[dict[str, Any]]:
        calls.append(1)
        raise ApiError(503)

    def denied() -> list[Node]:
        calls.append(1)
        raise ApiError(403)

    base.list_ops = down
    base.list_nodes = denied
    clients = with_retries(base, attempts=3)
    with pytest.raises(ApiError):
        clients.list_ops()
    assert len(calls) == 3
    calls.clear()
    with pytest.raises(ApiError):
        clients.list_nodes()
    assert len(calls) == 1


def test_a_run_survives_a_flaky_api() -> None:
    gke = FakeGke({"p": 2}, master=OLD)
    base = gke.clients()
    inner = base.get_cluster
    count = {"n": 0}

    def flaky() -> Any:
        count["n"] += 1
        if count["n"] % 3 == 0:
            raise ApiError(503)
        return inner()

    base.get_cluster = flaky
    r = UpgradeRunner(
        with_retries(base), "project-a", "region-a", "cluster-a", TARGET, interval=10
    )
    r.upgrade_control_plane()
    r.upgrade_pools(["p"])
    assert gke.master == TARGET
    assert outcomes(r) == [("p", "done")]


def test_pending_operation_is_waited_on_not_restarted() -> None:
    gke = FakeGke({"p": 2})
    gke.pending_ticks = 3
    r = runner(gke)
    r.upgrade_pools(["p"])
    assert outcomes(r) == [("p", "done")]
    assert len([c for c in gke.commands if "--node-pool" in c]) == 1
    assert not any("another operation holds the pool" in line for line in gke.log)
    assert any("pending, op 0/2" in line for line in gke.log)


def test_pending_operations_count_as_in_flight() -> None:
    ops = [
        {
            "status": "PENDING",
            "operationType": "UPGRADE_NODES",
            "targetLink": "x/clusters/a/nodePools/p1",
        }
    ]
    assert list(running_pool_ops(ops, "a")) == ["p1"]


def test_pending_control_plane_operation_is_adopted() -> None:
    gke = FakeGke({}, master=OLD)
    gke.ops.append(
        {
            "name": "op-master-0",
            "operationType": "UPGRADE_MASTER",
            "status": "PENDING",
            "targetLink": "/clusters/cluster-a",
            "_pending": 1,
            "_left": 1,
        }
    )
    runner(gke).upgrade_control_plane()
    assert gke.commands == []
    assert gke.master == TARGET


def test_node_changes_are_logged() -> None:
    gke = FakeGke({"p": 2})
    r = runner(gke)
    before = {"p-0": f"v{OLD}", "p-1": f"v{OLD}"}
    after = r._log_node_changes(
        "p",
        before,
        [
            Node("p-0", "p", f"v{TARGET}"),
            Node("p-2", "p", f"v{TARGET}"),
            Node("other", "q", f"v{OLD}"),
        ],
    )
    assert after == {"p-0": f"v{TARGET}", "p-2": f"v{TARGET}"}
    assert gke.log == [
        f"pool p: node p-0 v{OLD} -> v{TARGET}",
        f"pool p: node p-2 joined on v{TARGET}",
        f"pool p: node p-1 removed (was v{OLD})",
    ]


def test_pool_run_logs_each_node() -> None:
    gke = FakeGke({"p": 2})
    runner(gke).upgrade_pools(["p"])
    flips = [line for line in gke.log if " -> v" in line and "node p-" in line]
    assert flips == [
        f"pool p: node p-0 v{OLD} -> v{TARGET}",
        f"pool p: node p-1 v{OLD} -> v{TARGET}",
    ]


def test_file_surge_store(tmp_path) -> None:
    store = FileSurgeStore(tmp_path / "nested" / "c.surge.json")
    assert store.get("p") is None and store.pools() == []
    store.put("p", {"maxSurge": 5, "maxUnavailable": 0, "extra": 9})
    store.put("q", {"maxSurge": 1, "maxUnavailable": 0})
    assert FileSurgeStore(store.path).get("p") == {"maxSurge": 5, "maxUnavailable": 0}
    assert store.pools() == ["p", "q"]
    store.drop("p")
    store.drop("q")
    assert not store.path.exists()


def test_fixed_capacity_records_and_clears_original_settings(tmp_path) -> None:
    gke = FakeGke({"p": 2})
    gke.settings["p"] = (5, 0)
    store = FileSurgeStore(tmp_path / "c.surge.json")
    seen = []
    inner = gke.sleep

    def sleep(seconds: float) -> None:
        seen.append(store.get("p"))
        inner(seconds)

    clients = gke.clients()
    clients.sleep = sleep
    r = UpgradeRunner(
        clients,
        "project-a",
        "region-a",
        "cluster-a",
        TARGET,
        interval=10,
        fixed_capacity=True,
        surge_store=store,
    )
    r.upgrade_pools(["p"])
    assert seen and all(s == {"maxSurge": 5, "maxUnavailable": 0} for s in seen)
    assert gke.settings["p"] == (5, 0)
    assert store.pools() == []


def test_interrupted_fixed_capacity_run_is_restored_by_the_next_one(tmp_path) -> None:
    gke = FakeGke({"p": 2})
    gke.settings["p"] = (5, 0)
    gke.ticks = 4
    gke.interrupt_at = 10
    store = FileSurgeStore(tmp_path / "c.surge.json")
    with pytest.raises(KeyboardInterrupt):
        runner(gke, fixed_capacity=True, surge_store=store).upgrade_pools(["p"])
    # The restore was refused while the operation runs; the record survives.
    assert gke.settings["p"] == (0, 1)
    assert store.get("p") == {"maxSurge": 5, "maxUnavailable": 0}
    assert any("could not restore surge settings" in line for line in gke.log)

    # Next run, without the flag: adopts the operation, then restores.
    r = runner(gke, surge_store=store)
    r.restore_surge_settings()
    assert gke.settings["p"] == (0, 1)
    r.upgrade_pools(["p"])
    assert outcomes(r) == [("p", "done")]
    assert gke.settings["p"] == (5, 0)
    assert store.pools() == []


def test_restore_pass_fixes_pool_that_finished_on_its_own(tmp_path) -> None:
    gke = FakeGke({"p": 1, "gone": 1})
    gke.settings["p"] = (0, 1)
    gke.pool_versions["p"] = TARGET
    gke.nodes["p"] = [Node("p-0", "p", f"v{TARGET}")]
    store = FileSurgeStore(tmp_path / "c.surge.json")
    store.put("p", {"maxSurge": 5, "maxUnavailable": 0})
    store.put("deleted", {"maxSurge": 1, "maxUnavailable": 0})
    r = runner(gke, surge_store=store)
    r.restore_surge_settings()
    assert gke.settings["p"] == (5, 0)
    assert store.pools() == []
    assert [c[2] for c in gke.commands] == ["node-pools"]


def test_restore_pass_is_silent_in_dry_run(tmp_path) -> None:
    gke = FakeGke({"p": 1})
    gke.settings["p"] = (0, 1)
    store = FileSurgeStore(tmp_path / "c.surge.json")
    store.put("p", {"maxSurge": 5, "maxUnavailable": 0})
    runner(gke, dry_run=True, surge_store=store).restore_surge_settings()
    assert gke.commands == [] and store.pools() == ["p"]
