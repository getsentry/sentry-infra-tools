import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from google.auth.exceptions import DefaultCredentialsError
from googleapiclient.errors import HttpError

from libsentrykube.network import (
    AuthorizedNetwork,
    ClusterNetwork,
    GkeClusterRef,
    RangeUsage,
)
from sentry_kube.cli.network import network

CONTEXT = "gke_test-project_region-a-b_cluster-a"
REF = GkeClusterRef(project="test-project", location="region-a-b", name="cluster-a")


def result(findings: list[str]) -> ClusterNetwork:
    return ClusterNetwork(
        cluster=REF,
        network="net-a",
        subnet="subnet-a",
        subnet_region="region-a",
        ranges=[
            RangeUsage("nodes", "subnet-a", "10.2.0.0/22", 1020, 510),
            RangeUsage("services", "services", "10.8.0.0/22", 1024),
        ],
        authorized_networks_enabled=True,
        authorized_networks=[AuthorizedNetwork("10.2.0.0/22", "nodes")],
        node_firewall_rules=["gke-cluster-a-1a2b3c4d-vms"],
        findings=findings,
    )


@pytest.fixture
def mock_fetch():
    with patch("sentry_kube.cli.network.fetch_cluster_network") as mock:
        yield mock


def invoke(*args: str, context: str = CONTEXT):
    return CliRunner().invoke(
        network, ["show", *args], obj=SimpleNamespace(context_name=context)
    )


def test_show_text(mock_fetch) -> None:
    mock_fetch.return_value = result([])
    output = invoke().output

    mock_fetch.assert_called_once_with(REF, warn_threshold=0.8)
    assert "Cluster: cluster-a (region-a-b, project test-project)" in output
    assert "Subnet:  subnet-a in network net-a (region-a)" in output
    assert "50.0%" in output
    assert "10.2.0.0/22  (nodes)" in output
    assert "Intra-node firewall rules: gke-cluster-a-1a2b3c4d-vms" in output
    assert "No findings" in output


def test_show_json(mock_fetch) -> None:
    mock_fetch.return_value = result(["something"])
    run = invoke("-o", "json", "--warn-threshold", "50")

    assert run.exit_code == 0
    mock_fetch.assert_called_once_with(REF, warn_threshold=0.5)
    data = json.loads(run.output)
    assert data["ranges"][0]["utilization"] == 0.5
    assert data["ranges"][1]["utilization"] is None
    assert data["findings"] == ["something"]


def test_check_exit_code(mock_fetch) -> None:
    mock_fetch.return_value = result([])
    assert invoke("--check").exit_code == 0

    mock_fetch.return_value = result(["nodes range is 95% used"])
    run = invoke("--check")
    assert run.exit_code == 1
    assert "nodes range is 95% used" in run.output


def test_non_gke_context(mock_fetch) -> None:
    run = invoke(context="colima-local-k8s")
    assert run.exit_code != 0
    assert "not in gke_PROJECT_LOCATION_CLUSTER format" in run.output
    mock_fetch.assert_not_called()


def test_api_error(mock_fetch) -> None:
    mock_fetch.side_effect = HttpError(MagicMock(status=403, reason="Forbidden"), b"")
    run = invoke()
    assert run.exit_code != 0
    assert "GCP API request failed (403)" in run.output


def test_auth_error(mock_fetch) -> None:
    mock_fetch.side_effect = DefaultCredentialsError("no credentials")
    run = invoke()
    assert run.exit_code != 0
    assert "gcloud auth application-default login" in run.output
