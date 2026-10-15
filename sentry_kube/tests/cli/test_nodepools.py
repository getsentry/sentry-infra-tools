import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from kubernetes.client.exceptions import ApiException

from libsentrykube.network import GkeClusterRef
from libsentrykube.nodepools import (
    ClusterNodePools,
    ComputeClass,
    NodePool,
    ResourceLimit,
)
from sentry_kube.cli.cluster import cluster
from sentry_kube.cli.nodepools import connect_kube, format_text

CONTEXT = "gke_test-project_region-a_cluster-a"
REF = GkeClusterRef(project="test-project", location="region-a", name="cluster-a")


def result(findings: list[str]) -> ClusterNodePools:
    return ClusterNodePools(
        cluster=REF,
        control_plane_version="1.37.0-gke.3",
        autoprovisioning=True,
        resource_limits=[ResourceLimit("cpu", "cores", 412, 2000)],
        pools=[
            NodePool(
                name="nap-n4-a",
                compute_class="general-purpose",
                sentry_name=None,
                machine_type="n4-standard-8",
                autoprovisioned=True,
                version="1.37.0-gke.3",
                nodes=12,
                autoscaling=True,
                min_nodes=0,
                max_nodes=3000,
                cpu_allocatable=100,
                cpu_requested=71,
                memory_allocatable_gib=400,
                memory_requested_gib=256,
            ),
            NodePool(
                name="legacy-default",
                compute_class=None,
                sentry_name="default",
                machine_type="n2-standard-16",
                autoprovisioned=False,
                version="1.35.6-gke.1",
                nodes=3,
                autoscaling=True,
                min_nodes=1,
                max_nodes=3,
            ),
        ],
        compute_classes=[
            ComputeClass("general-purpose", ["n4", "c4d"], True, pools=1, nodes=12),
            ComputeClass("unused-a", ["n2"], True, pools=0, nodes=0),
            ComputeClass("unused-b", [], False, pools=0, nodes=0),
        ],
        findings=findings,
        notes=["Empty node pools outside any compute class: pool-z"],
    )


@pytest.fixture
def mocks():
    with (
        patch("sentry_kube.cli.nodepools.connect_kube") as connect,
        patch("sentry_kube.cli.nodepools.fetch_cluster_nodepools") as fetch,
    ):
        yield SimpleNamespace(connect=connect, fetch=fetch)


def invoke(*args: str):
    return CliRunner().invoke(
        cluster, ["nodepools", *args], obj=SimpleNamespace(context_name=CONTEXT)
    )


def test_text(mocks) -> None:
    mocks.fetch.return_value = result([])
    run = invoke()
    assert run.exit_code == 0
    mocks.fetch.assert_called_once_with(
        REF, mocks.connect.return_value, warn_threshold=0.8
    )
    out = run.output
    assert "Control plane: 1.37.0-gke.3" in out
    assert "cpu     412 / 2000 cores (21%)" in out
    lines = out.splitlines()
    nap = next(line for line in lines if "nap-n4-a" in line)
    assert (
        "general-purpose" in nap and "0-3000" in nap and "71%" in nap and "64%" in nap
    )
    legacy = next(line for line in lines if "legacy-default" in line)
    assert "default" in legacy and "1-3 (max)" in legacy and legacy.split()[0] == "-"
    assert "  general-purpose: n4 → c4d" in out
    assert "auto-creates pools; 1 pool(s), 12 node(s)" in out
    assert "  No nodes: unused-a, unused-b" in out
    assert "unused-a:" not in out
    assert "ℹ Empty node pools outside any compute class: pool-z" in out
    assert "No findings" in out


def test_json_and_check(mocks) -> None:
    mocks.fetch.return_value = result(["pool at max"])
    run = invoke("-o", "json", "--check", "--warn-threshold", "90")
    assert run.exit_code == 1
    mocks.fetch.assert_called_once_with(
        REF, mocks.connect.return_value, warn_threshold=0.9
    )
    data = json.loads(run.output)
    assert data["pools"][1]["at_max"] is True
    assert data["pools"][0]["cpu_utilization"] == 0.71
    assert data["findings"] == ["pool at max"]


def test_kube_api_error(mocks) -> None:
    mocks.fetch.side_effect = ApiException(status=403, reason="Forbidden")
    run = invoke()
    assert run.exit_code != 0
    assert "Kubernetes API request failed (403): Forbidden" in run.output


def test_connect_kube(monkeypatch) -> None:
    monkeypatch.setenv("KUBERNETES_OFFLINE", "1")
    ctx = MagicMock()
    ctx.obj.context_name = CONTEXT
    with (
        patch(
            "sentry_kube.cli.nodepools.ensure_iap_tunnel",
            return_value="/tmp/kubeconfig",
        ) as tunnel,
        patch("sentry_kube.cli.nodepools.kube_set_context") as set_context,
        patch("sentry_kube.cli.nodepools.kube_get_client") as get_client,
    ):
        client = connect_kube(ctx)
    tunnel.assert_called_once_with(ctx)
    set_context.assert_called_once_with(CONTEXT, kubeconfig="/tmp/kubeconfig")
    assert client is get_client.return_value
    assert "KUBERNETES_OFFLINE" not in os.environ
    assert os.environ["KUBECONFIG"] == "/tmp/kubeconfig"


def test_unknown_autoscaling_bound() -> None:
    pool = NodePool(
        name="pool-a",
        compute_class=None,
        sentry_name=None,
        machine_type="n4-standard-8",
        autoprovisioned=False,
        version="1.37.0-gke.3",
        nodes=0,
        autoscaling=True,
        min_nodes=0,
        max_nodes=None,
    )
    out = format_text(
        ClusterNodePools(REF, "1.37.0-gke.3", autoprovisioning=False, pools=[pool])
    )
    assert " 0-? " in out
    assert "Node auto-provisioning: disabled" in out


def test_autoprovisioning_disabled_but_classes_create_pools() -> None:
    classes = [ComputeClass("cc", ["n4"], True, pools=0, nodes=0)]
    out = format_text(
        ClusterNodePools(
            REF, "1.37.0-gke.3", autoprovisioning=False, compute_classes=classes
        )
    )
    assert (
        "Node auto-provisioning: disabled cluster-wide "
        "(compute classes still auto-create node pools)"
    ) in out


def test_long_priority_lists_are_truncated() -> None:
    priorities = [f"family-{i}" for i in range(10)]
    classes = [ComputeClass("cc", priorities, True, pools=1, nodes=1)]
    out = format_text(
        ClusterNodePools(
            REF, "1.37.0-gke.3", autoprovisioning=True, compute_classes=classes
        )
    )
    assert (
        "  cc: family-0 → family-1 → family-2 → family-3 → family-4 → family-5" in out
    )
    assert "family-6" not in out
    assert "→ … (+4 more)" in out
