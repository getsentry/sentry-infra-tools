from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sentry_kube.cli.cluster import cluster

OBJ = SimpleNamespace(customer_name="region1", context_name="gke_p_l_cluster-a")


@pytest.fixture
def mocks():
    with (
        patch("sentry_kube.cli.cluster.Config"),
        patch("sentry_kube.cli.cluster.get_project", return_value="project-a") as p,
        patch("sentry_kube.cli.cluster.list_cluster_versions") as listed,
        patch("sentry_kube.cli.cluster.upgrade_cluster_to_version") as upgraded,
    ):
        yield SimpleNamespace(get_project=p, listed=listed, upgraded=upgraded)


def test_list_flag_still_works(mocks) -> None:
    run = CliRunner().invoke(cluster, ["--list", "--cluster", "cluster-a"], obj=OBJ)
    assert run.exit_code == 0
    mocks.listed.assert_called_once_with("project-a", "cluster-a")


def test_upgrade_flag_still_works(mocks) -> None:
    run = CliRunner().invoke(
        cluster,
        ["--upgrade", "--cluster", "cluster-a", "--version", "1.2.3"],
        obj=OBJ,
    )
    assert run.exit_code == 0
    mocks.upgraded.assert_called_once_with("project-a", "cluster-a", "1.2.3")


def test_subcommand_skips_legacy_flags(mocks) -> None:
    with patch("sentry_kube.cli.network.fetch_cluster_network") as fetch:
        fetch.return_value.findings = []
        CliRunner().invoke(cluster, ["network", "-o", "json"], obj=OBJ)
    fetch.assert_called_once()
    mocks.get_project.assert_not_called()
    mocks.listed.assert_not_called()


def test_help_lists_network(mocks) -> None:
    run = CliRunner().invoke(cluster, ["--help"], obj=OBJ)
    assert run.exit_code == 0
    assert "network" in run.output
