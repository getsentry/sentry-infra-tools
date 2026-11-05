from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from libsentrykube.gke_upgrade import Clients, Node
from sentry_kube.cli.cluster import (
    cluster,
    old_node_counts,
    plan_upgrade,
    version_key,
)

OBJ = SimpleNamespace(
    customer_name="region1", context_name="gke_project-a_region-a-b_cluster-a"
)


def gke_cluster(master: str = "1.2.3", nodes: str = "1.2.3") -> dict[str, Any]:
    return {
        "name": "cluster-a",
        "zone": "region-a-b",
        "location": "region-a-b",
        "releaseChannel": {"channel": "STABLE"},
        "currentMasterVersion": master,
        "currentNodeVersion": nodes,
        "nodePools": [
            {"name": "pool-a", "version": nodes},
            {"name": "pool-b", "version": "1.2.4"},
        ],
    }


def nodes_for(cluster_obj: dict[str, Any]) -> list[Node]:
    return [
        Node(f"{p['name']}-0", p["name"], f"v{p['version']}")
        for p in cluster_obj["nodePools"]
    ]


class FakeRunner:
    instances: list["FakeRunner"] = []

    def __init__(self, clients: Clients, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.calls: list[tuple[str, Any]] = []
        self.results: list[Any] = []
        FakeRunner.instances.append(self)

    def upgrade_control_plane(self) -> None:
        self.calls.append(("control_plane", None))

    def upgrade_pools(self, pools: list[str]) -> list[Any]:
        self.calls.append(("pools", pools))
        return self.results


@pytest.fixture
def mocks():
    FakeRunner.instances.clear()
    cluster_obj = gke_cluster()
    state = SimpleNamespace(cluster=cluster_obj, nodes=nodes_for(cluster_obj), ops=[])
    clients = Clients(
        get_cluster=lambda: state.cluster,
        list_ops=lambda: state.ops,
        list_nodes=lambda: state.nodes,
        gcloud=lambda argv: (0, ""),
        log=lambda msg: None,
    )
    with (
        patch("sentry_kube.cli.cluster.get_cluster", return_value=cluster_obj) as c,
        patch(
            "sentry_kube.cli.cluster.get_channel_versions",
            return_value=["1.2.3", "1.2.4"],
        ) as v,
        patch("sentry_kube.cli.cluster.list_cluster_versions") as listed,
        patch("sentry_kube.cli.cluster.make_clients", return_value=clients),
        patch("sentry_kube.cli.cluster.UpgradeRunner", FakeRunner),
    ):
        yield SimpleNamespace(
            state=state, get_cluster=c, channel_versions=v, listed=listed
        )


def invoke(*args: str, input: str | None = None):
    return CliRunner().invoke(cluster, list(args), obj=OBJ, input=input)


def runner_calls() -> list[tuple[str, Any]]:
    return [call for r in FakeRunner.instances for call in r.calls]


def test_versions(mocks) -> None:
    run = invoke("versions")
    assert run.exit_code == 0
    mocks.listed.assert_called_once_with("project-a", "cluster-a")


def test_versions_labels() -> None:
    channel = {"defaultVersion": "1.2.2", "validVersions": ["1.2.4", "1.2.3", "1.2.2"]}
    with (
        patch("sentry_kube.cli.cluster.get_cluster", return_value=gke_cluster()),
        patch(
            "sentry_kube.cli.cluster.get_release_channel", return_value=channel
        ) as get_channel,
    ):
        run = invoke("versions")
    assert run.exit_code == 0
    get_channel.assert_called_once_with("project-a", "region-a-b", "STABLE")
    lines = run.output.splitlines()
    targets = lines.index("Upgrade targets:")
    older = lines.index(
        "Older than the control plane (`cluster upgrade` refuses these):"
    )
    assert lines[targets + 1 : older] == [
        "  1.2.4",
        "  1.2.3 (current control plane)",
        "",
    ]
    # A channel default can lag behind the control plane on fast channels.
    assert lines[older + 1 :] == ["  1.2.2 (default)"]


def test_versions_dims_older_versions() -> None:
    channel = {"defaultVersion": "1.2.3", "validVersions": ["1.2.3", "1.2.2"]}
    with (
        patch("sentry_kube.cli.cluster.get_cluster", return_value=gke_cluster()),
        patch("sentry_kube.cli.cluster.get_release_channel", return_value=channel),
    ):
        run = CliRunner().invoke(cluster, ["versions"], obj=OBJ, color=True)
    assert run.exit_code == 0
    assert click.style("  1.2.2", dim=True) in run.output
    assert (
        click.style("  1.2.3 (default, current control plane)", dim=True)
        not in run.output
    )


def test_versions_without_older_versions() -> None:
    channel = {"defaultVersion": "1.2.3", "validVersions": ["1.2.4", "1.2.3"]}
    with (
        patch("sentry_kube.cli.cluster.get_cluster", return_value=gke_cluster()),
        patch("sentry_kube.cli.cluster.get_release_channel", return_value=channel),
    ):
        run = invoke("versions")
    assert "Older than the control plane" not in run.output
    assert run.output.splitlines()[-1] == "  1.2.3 (default, current control plane)"


def test_version_key() -> None:
    assert version_key("1.37.0-gke.3503000") == (1, 37, 0, 3503000)
    assert version_key("1.37.0-gke.3503000") > version_key("1.36.4-gke.2046002")
    assert version_key("1.35.6-gke.1250001") > version_key("1.35.6-gke.1250000")
    assert version_key("1.2.3") == (1, 2, 3, 0)


def mixed_cluster() -> dict[str, Any]:
    # Control plane ahead of most pools, with some pools already on it.
    return {
        **gke_cluster(master="1.37.0-gke.3", nodes="1.35.6-gke.1"),
        "nodePools": [
            {"name": "old", "version": "1.35.6-gke.1"},
            {"name": "current", "version": "1.37.0-gke.3"},
        ],
    }


def test_plan_upgrade() -> None:
    plan = plan_upgrade(mixed_cluster(), "1.37.1-gke.1")
    assert plan.describe() == [
        "control plane: 1.37.0-gke.3 -> 1.37.1-gke.1",
        "node pool old: 1.35.6-gke.1 -> 1.37.1-gke.1",
        "node pool current: 1.37.0-gke.3 -> 1.37.1-gke.1",
    ]

    # Catching node pools up to the control plane leaves it alone.
    plan = plan_upgrade(mixed_cluster(), "1.37.0-gke.3")
    assert plan.control_plane is None
    assert plan.node_pools == [("old", "1.35.6-gke.1")]

    assert plan_upgrade(gke_cluster(master="1.2.4", nodes="1.2.4"), "1.2.4").empty


def test_plan_upgrade_honours_pool_order() -> None:
    plan = plan_upgrade(mixed_cluster(), "1.37.1-gke.1", pools=["current", "old"])
    assert [name for name, _ in plan.node_pools] == ["current", "old"]
    with pytest.raises(click.BadParameter, match="no such node pool"):
        plan_upgrade(mixed_cluster(), "1.37.1-gke.1", pools=["nope"])


def test_plan_upgrade_includes_flipped_pool_with_old_nodes() -> None:
    # The pool version reads the target (a failed or cancelled operation
    # flipped it) but nodes are still behind.
    plan = plan_upgrade(mixed_cluster(), "1.37.0-gke.3", old_nodes={"current": 4})
    assert plan.node_pools == [("old", "1.35.6-gke.1"), ("current", "1.37.0-gke.3")]


def test_old_node_counts() -> None:
    nodes = [
        Node("a", "p", "v1.2.4"),
        Node("b", "p", "v1.2.3"),
        Node("c", "q", "v1.2.3"),
    ]
    assert old_node_counts(nodes, "1.2.4") == {"p": 1, "q": 1}


def test_plan_upgrade_never_downgrades_node_pools() -> None:
    cluster_obj = mixed_cluster()
    cluster_obj["nodePools"].append({"name": "newer", "version": "1.37.0-gke.9"})
    plan = plan_upgrade(cluster_obj, "1.37.0-gke.5")
    assert plan.node_pools == [("old", "1.35.6-gke.1"), ("current", "1.37.0-gke.3")]
    assert plan.newer_node_pools == [("newer", "1.37.0-gke.9")]
    assert (
        "node pool newer: 1.37.0-gke.9 (newer than 1.37.0-gke.5, unchanged)"
        in plan.describe()
    )


def test_upgrade_refuses_control_plane_downgrade(mocks) -> None:
    mocks.state.cluster = mixed_cluster()
    mocks.channel_versions.return_value = ["1.37.0-gke.3", "1.36.4-gke.2"]
    run = invoke("upgrade", "1.36.4-gke.2", "--yes")
    assert run.exit_code == 2
    assert "older than the control plane (1.37.0-gke.3)" in run.output
    assert "Upgrade plan" not in run.output
    assert runner_calls() == []


def test_upgrade_confirmed(mocks) -> None:
    run = invoke("upgrade", "1.2.4", input="y\n")
    assert run.exit_code == 0, run.output
    assert "control plane: 1.2.3 -> 1.2.4" in run.output
    assert "node pool pool-a: 1.2.3 -> 1.2.4" in run.output
    mocks.channel_versions.assert_called_once_with("project-a", "region-a-b", "STABLE")
    assert runner_calls() == [("control_plane", None), ("pools", ["pool-a"])]
    runner = FakeRunner.instances[0]
    assert runner.args == ("project-a", "region-a-b", "cluster-a", "1.2.4")
    assert runner.kwargs == {
        "interval": 60,
        "max_attempts": 3,
        "fixed_capacity": False,
        "dry_run": False,
    }


def test_upgrade_declined(mocks) -> None:
    run = invoke("upgrade", "1.2.4", input="n\n")
    assert run.exit_code == 1
    assert runner_calls() == []


def test_upgrade_yes_skips_prompt(mocks) -> None:
    run = invoke("upgrade", "1.2.4", "--yes")
    assert run.exit_code == 0
    assert "Proceed?" not in run.output
    assert runner_calls() == [("control_plane", None), ("pools", ["pool-a"])]


def test_upgrade_pool_options(mocks) -> None:
    mocks.state.cluster = gke_cluster(master="1.2.4", nodes="1.2.3")
    mocks.state.cluster["nodePools"].append({"name": "pool-c", "version": "1.2.3"})
    mocks.state.nodes = nodes_for(mocks.state.cluster)
    run = invoke(
        "upgrade",
        "1.2.4",
        "--pools-only",
        "--pool",
        "pool-c",
        "--pool",
        "pool-a",
        "--fixed-capacity",
        "--max-attempts",
        "5",
        "--interval",
        "30",
        "--yes",
    )
    assert run.exit_code == 0, run.output
    assert "max surge 0 / max unavailable 1" in run.output
    assert runner_calls() == [("pools", ["pool-c", "pool-a"])]
    assert FakeRunner.instances[0].kwargs["fixed_capacity"] is True
    assert FakeRunner.instances[0].kwargs["max_attempts"] == 5
    assert FakeRunner.instances[0].kwargs["interval"] == 30


def test_upgrade_pools_only_refuses_version_above_control_plane(mocks) -> None:
    run = invoke("upgrade", "1.2.4", "--pools-only", "--yes")
    assert run.exit_code == 2
    assert "node pools cannot run a newer version" in run.output
    assert runner_calls() == []


def test_upgrade_pools_only_may_trail_the_control_plane(mocks) -> None:
    # A 1.37 control plane with pools still on 1.35.6: 1.35.9 is a valid
    # pool target even though it is older than the control plane.
    mocks.state.cluster = gke_cluster(master="1.37.1-gke.1", nodes="1.35.6-gke.1")
    mocks.state.nodes = nodes_for(mocks.state.cluster)
    mocks.channel_versions.return_value = ["1.37.1-gke.1", "1.35.9-gke.1"]
    run = invoke("upgrade", "1.35.9-gke.1", "--pools-only", "--pool", "pool-a", "--yes")
    assert run.exit_code == 0, run.output
    assert "control plane" not in run.output.split("Upgrade plan")[1]
    assert runner_calls() == [("pools", ["pool-a"])]


def test_upgrade_control_plane_only(mocks) -> None:
    run = invoke("upgrade", "1.2.4", "--control-plane-only", "--yes")
    assert run.exit_code == 0, run.output
    assert runner_calls() == [("control_plane", None)]


def test_upgrade_flags_exclude_each_other(mocks) -> None:
    run = invoke("upgrade", "1.2.4", "--pools-only", "--control-plane-only")
    assert run.exit_code == 2
    assert "exclude each other" in run.output


def test_upgrade_dry_run_skips_prompt(mocks) -> None:
    run = invoke("upgrade", "1.2.4", "--dry-run")
    assert run.exit_code == 0, run.output
    assert "Proceed?" not in run.output
    assert FakeRunner.instances[0].kwargs["dry_run"] is True


def test_upgrade_reports_failed_pools(mocks) -> None:
    failed = SimpleNamespace(
        name="pool-a", outcome="failed", detail="2 nodes still behind after 3 attempts"
    )

    def upgrade_pools(self: FakeRunner, pools: list[str]) -> list[Any]:
        self.results = [failed]
        return self.results

    with patch.object(FakeRunner, "upgrade_pools", upgrade_pools):
        run = invoke("upgrade", "1.2.4", "--yes")
    assert run.exit_code == 1
    assert (
        "pools not fully upgraded: pool-a (2 nodes still behind after 3 attempts)"
        in run.output
    )


def test_upgrade_unavailable_version(mocks) -> None:
    run = invoke("upgrade", "9.9.9", "--yes")
    assert run.exit_code == 2
    assert "Available: 1.2.3, 1.2.4" in run.output
    assert runner_calls() == []


def test_upgrade_already_current(mocks) -> None:
    mocks.state.cluster = gke_cluster(master="1.2.4", nodes="1.2.4")
    mocks.state.nodes = nodes_for(mocks.state.cluster)
    run = invoke("upgrade", "1.2.4")
    assert run.exit_code == 0
    assert "already at 1.2.4" in run.output
    assert runner_calls() == []


def test_upgrade_pool_at_target_with_old_nodes_is_not_current(mocks) -> None:
    mocks.state.cluster = gke_cluster(master="1.2.4", nodes="1.2.4")
    mocks.state.nodes = [Node("pool-a-0", "pool-a", "v1.2.3")]
    run = invoke("upgrade", "1.2.4", "--yes")
    assert run.exit_code == 0, run.output
    assert "node pool pool-a: 1.2.4 -> 1.2.4" in run.output
    assert runner_calls() == [("pools", ["pool-a"])]


def test_upgrade_requires_version(mocks) -> None:
    run = invoke("upgrade")
    assert run.exit_code == 2
    assert "Missing argument 'VERSION'" in run.output
    mocks.get_cluster.assert_not_called()


def test_watch_once(mocks) -> None:
    mocks.state.cluster = gke_cluster(master="1.2.4", nodes="1.2.3")
    mocks.state.nodes = nodes_for(mocks.state.cluster)
    mocks.state.ops = [
        {
            "name": "op-1",
            "status": "RUNNING",
            "operationType": "UPGRADE_NODES",
            "targetLink": "x/clusters/cluster-a/nodePools/pool-a",
            "startTime": "2026-11-05T00:00:00Z",
        }
    ]
    run = invoke("watch", "--once")
    assert run.exit_code == 0, run.output
    assert "cluster-a: 1/2 pools on 1.2.4, 1 running, 0 waiting" in run.output
    assert "pool-a" in run.output


def test_no_subcommand_shows_help(mocks) -> None:
    run = invoke()
    assert run.exit_code == 0
    for name in ("network", "nodepools", "upgrade", "versions", "watch"):
        assert name in run.output
    assert "--list" not in run.output


def test_network_subcommand(mocks) -> None:
    with patch("sentry_kube.cli.network.fetch_cluster_network") as fetch:
        fetch.return_value.findings = []
        invoke("network", "-o", "json")
    fetch.assert_called_once()
    mocks.listed.assert_not_called()
