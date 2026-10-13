from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sentry_kube.cli.cluster import cluster, upgrade_plan

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


def test_upgrade_plan() -> None:
    assert upgrade_plan(gke_cluster(), "1.2.4") == [
        "control plane: 1.2.3 -> 1.2.4",
        "node pool pool-a: 1.2.3 -> 1.2.4 (async)",
    ]
    assert upgrade_plan(gke_cluster(master="1.2.4", nodes="1.2.4"), "1.2.4") == []


def test_upgrade_confirmed(mocks) -> None:
    run = invoke("upgrade", "--version", "1.2.4", input="y\n")
    assert run.exit_code == 0
    assert "control plane: 1.2.3 -> 1.2.4" in run.output
    assert "node pool pool-a: 1.2.3 -> 1.2.4 (async)" in run.output
    mocks.channel_versions.assert_called_once_with("project-a", "region-a-b", "STABLE")
    mocks.upgraded.assert_called_once_with("project-a", "cluster-a", "1.2.4")


def test_upgrade_declined(mocks) -> None:
    run = invoke("upgrade", "--version", "1.2.4", input="n\n")
    assert run.exit_code == 1
    mocks.upgraded.assert_not_called()


def test_upgrade_yes_skips_prompt(mocks) -> None:
    run = invoke("upgrade", "--version", "1.2.4", "--yes")
    assert run.exit_code == 0
    assert "Proceed?" not in run.output
    mocks.upgraded.assert_called_once()


def test_upgrade_unavailable_version(mocks) -> None:
    run = invoke("upgrade", "--version", "9.9.9", "--yes")
    assert run.exit_code == 2
    assert "Available: 1.2.3, 1.2.4" in run.output
    mocks.upgraded.assert_not_called()


def test_upgrade_already_current(mocks) -> None:
    mocks.get_cluster.return_value = gke_cluster(master="1.2.4", nodes="1.2.4")
    run = invoke("upgrade", "--version", "1.2.4")
    assert run.exit_code == 0
    assert "already at 1.2.4" in run.output
    mocks.upgraded.assert_not_called()


def test_upgrade_requires_version(mocks) -> None:
    run = invoke("upgrade")
    assert run.exit_code == 2
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
