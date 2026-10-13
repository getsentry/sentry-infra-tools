from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from sentry_kube.cli.cluster import (
    cluster,
    plan_upgrade,
    upgrade_cluster_to_version,
    version_key,
)

OBJ = SimpleNamespace(
    customer_name="region1", context_name="gke_project-a_region-a-b_cluster-a"
)


def gke_cluster(master: str = "1.2.3", nodes: str = "1.2.3") -> dict[str, Any]:
    return {
        "name": "cluster-a",
        "zone": "region-a-b",
        "releaseChannel": {"channel": "STABLE"},
        "currentMasterVersion": master,
        "currentNodeVersion": nodes,
        "nodePools": [
            {"name": "pool-a", "version": nodes},
            {"name": "pool-b", "version": "1.2.4"},
        ],
    }


@pytest.fixture
def mocks():
    with (
        patch("sentry_kube.cli.cluster.Config"),
        patch("sentry_kube.cli.cluster.get_project", return_value="project-a") as p,
        patch("sentry_kube.cli.cluster.get_cluster", return_value=gke_cluster()) as c,
        patch(
            "sentry_kube.cli.cluster.get_channel_versions",
            return_value=["1.2.3", "1.2.4"],
        ) as v,
        patch("sentry_kube.cli.cluster.list_cluster_versions") as listed,
        patch("sentry_kube.cli.cluster.upgrade_cluster_to_version") as upgraded,
    ):
        yield SimpleNamespace(
            get_project=p,
            get_cluster=c,
            channel_versions=v,
            listed=listed,
            upgraded=upgraded,
        )


def invoke(*args: str, input: str | None = None):
    return CliRunner().invoke(cluster, list(args), obj=OBJ, input=input)


def test_versions(mocks) -> None:
    run = invoke("versions")
    assert run.exit_code == 0
    mocks.listed.assert_called_once_with("project-a", "cluster-a")
    mocks.get_project.assert_not_called()


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
        "node pool old: 1.35.6-gke.1 -> 1.37.1-gke.1 (async)",
        "node pool current: 1.37.0-gke.3 -> 1.37.1-gke.1 (async)",
    ]

    # Catching node pools up to the control plane leaves it alone.
    plan = plan_upgrade(mixed_cluster(), "1.37.0-gke.3")
    assert plan.control_plane is None
    assert plan.node_pools == [("old", "1.35.6-gke.1")]

    assert plan_upgrade(gke_cluster(master="1.2.4", nodes="1.2.4"), "1.2.4").empty


def test_plan_upgrade_never_downgrades_node_pools() -> None:
    cluster_obj = mixed_cluster()
    cluster_obj["currentMasterVersion"] = "1.37.0-gke.3"
    cluster_obj["nodePools"].append({"name": "newer", "version": "1.37.0-gke.9"})
    plan = plan_upgrade(cluster_obj, "1.37.0-gke.5")
    assert plan.node_pools == [("old", "1.35.6-gke.1"), ("current", "1.37.0-gke.3")]
    assert plan.newer_node_pools == [("newer", "1.37.0-gke.9")]
    assert (
        "node pool newer: 1.37.0-gke.9 (newer than 1.37.0-gke.5, unchanged)"
        in plan.describe()
    )


def test_upgrade_refuses_control_plane_downgrade(mocks) -> None:
    mocks.get_cluster.return_value = mixed_cluster()
    mocks.channel_versions.return_value = ["1.37.0-gke.3", "1.36.4-gke.2"]
    run = invoke("upgrade", "1.36.4-gke.2", "--yes")
    assert run.exit_code == 2
    assert "older than the control plane (1.37.0-gke.3)" in run.output
    assert "Upgrade plan" not in run.output
    mocks.upgraded.assert_not_called()


def test_upgrade_cluster_to_version_runs_the_plan() -> None:
    with (
        patch("sentry_kube.cli.cluster.get_cluster", return_value=mixed_cluster()),
        patch(
            "sentry_kube.cli.cluster.get_channel_versions",
            return_value=["1.37.0-gke.3"],
        ),
        patch("sentry_kube.cli.cluster.subprocess.check_output") as gcloud,
    ):
        upgrade_cluster_to_version("project-a", "cluster-a", "1.37.0-gke.3")
    # Control plane is already there; only the older pool is upgraded.
    assert len(gcloud.call_args_list) == 1
    argv = gcloud.call_args.args[0]
    assert argv[argv.index("--node-pool") + 1] == "old"
    assert "--master" not in argv


def test_deprecated_upgrade_flag_refuses_downgrade() -> None:
    with (
        patch("sentry_kube.cli.cluster.Config"),
        patch("sentry_kube.cli.cluster.get_project", return_value="project-a"),
        patch("sentry_kube.cli.cluster.get_cluster", return_value=mixed_cluster()),
        patch(
            "sentry_kube.cli.cluster.get_channel_versions",
            return_value=["1.36.4-gke.2"],
        ),
        patch("sentry_kube.cli.cluster.subprocess.check_output") as gcloud,
    ):
        run = invoke("--upgrade", "--cluster", "cluster-a", "--version", "1.36.4-gke.2")
    assert run.exit_code == 2
    gcloud.assert_not_called()


def test_upgrade_confirmed(mocks) -> None:
    run = invoke("upgrade", "1.2.4", input="y\n")
    assert run.exit_code == 0
    assert "control plane: 1.2.3 -> 1.2.4" in run.output
    assert "node pool pool-a: 1.2.3 -> 1.2.4 (async)" in run.output
    mocks.channel_versions.assert_called_once_with("project-a", "region-a-b", "STABLE")
    mocks.upgraded.assert_called_once_with("project-a", "cluster-a", "1.2.4")


def test_upgrade_declined(mocks) -> None:
    run = invoke("upgrade", "1.2.4", input="n\n")
    assert run.exit_code == 1
    mocks.upgraded.assert_not_called()


def test_upgrade_yes_skips_prompt(mocks) -> None:
    run = invoke("upgrade", "1.2.4", "--yes")
    assert run.exit_code == 0
    assert "Proceed?" not in run.output
    mocks.upgraded.assert_called_once()


def test_upgrade_unavailable_version(mocks) -> None:
    run = invoke("upgrade", "9.9.9", "--yes")
    assert run.exit_code == 2
    assert "Available: 1.2.3, 1.2.4" in run.output
    mocks.upgraded.assert_not_called()


def test_upgrade_already_current(mocks) -> None:
    mocks.get_cluster.return_value = gke_cluster(master="1.2.4", nodes="1.2.4")
    run = invoke("upgrade", "1.2.4")
    assert run.exit_code == 0
    assert "already at 1.2.4" in run.output
    mocks.upgraded.assert_not_called()


def test_upgrade_requires_version(mocks) -> None:
    run = invoke("upgrade")
    assert run.exit_code == 2
    assert "Missing argument 'VERSION'" in run.output
    mocks.get_cluster.assert_not_called()


def test_no_subcommand_shows_help(mocks) -> None:
    run = invoke()
    assert run.exit_code == 0
    for name in ("network", "upgrade", "versions"):
        assert name in run.output
    assert "--list" not in run.output
    mocks.get_project.assert_not_called()


def test_deprecated_list_flag(mocks) -> None:
    run = invoke("--list", "--cluster", "cluster-a")
    assert run.exit_code == 0
    assert "deprecated; use `sentry-kube cluster versions`" in run.stderr
    mocks.listed.assert_called_once_with("project-a", "cluster-a")


def test_deprecated_upgrade_flag(mocks) -> None:
    run = invoke("--upgrade", "--cluster", "cluster-a", "--version", "1.2.3")
    assert run.exit_code == 0
    assert "deprecated" in run.stderr
    mocks.upgraded.assert_called_once_with("project-a", "cluster-a", "1.2.3")


@pytest.mark.parametrize(
    "args, message",
    [
        (["--list"], "Please specify --cluster"),
        (["--upgrade", "--cluster", "cluster-a"], "Please specify --version"),
    ],
)
def test_deprecated_flags_validate(mocks, args: list[str], message: str) -> None:
    run = invoke(*args)
    assert run.exit_code == 2
    assert message in run.output
    mocks.listed.assert_not_called()
    mocks.upgraded.assert_not_called()


def test_network_subcommand_skips_legacy_path(mocks) -> None:
    with patch("sentry_kube.cli.network.fetch_cluster_network") as fetch:
        fetch.return_value.findings = []
        invoke("network", "-o", "json")
    fetch.assert_called_once()
    mocks.get_project.assert_not_called()
    mocks.listed.assert_not_called()
