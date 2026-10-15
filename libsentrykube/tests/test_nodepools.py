import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.exceptions import ApiException

from libsentrykube.network import GkeClusterRef
from libsentrykube.nodepools import (
    ClusterNodePools,
    build_cluster_nodepools,
    fetch_cluster_nodepools,
    pod_requests,
)

REF = GkeClusterRef(project="test-project", location="region-a", name="cluster-a")
CC = "cloud.google.com/compute-class"


def gke_pool(
    name: str,
    machine: str = "n4-standard-8",
    version: str = "1.37.0-gke.3",
    autoscaling: dict[str, Any] | None = None,
    labels: dict[str, str] | None = None,
    zones: int = 3,
) -> dict[str, Any]:
    return {
        "name": name,
        "version": version,
        "locations": [f"region-a-{z}" for z in "bcd"[:zones]],
        "config": {"machineType": machine, "labels": labels or {}},
        "autoscaling": autoscaling or {},
    }


def gke_cluster(pools: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    cluster: dict[str, Any] = {
        "currentMasterVersion": "1.37.0-gke.3",
        "nodePools": pools,
        "autoscaling": {
            "enableNodeAutoprovisioning": True,
            "resourceLimits": [
                {"resourceType": "cpu", "minimum": "0", "maximum": "40"},
                {"resourceType": "memory", "maximum": "1000"},
            ],
        },
    }
    cluster.update(overrides)
    return cluster


def node(name: str, pool: str, compute_class: str | None = None) -> dict[str, Any]:
    labels = {"cloud.google.com/gke-nodepool": pool}
    if compute_class:
        labels[CC] = compute_class
    return {
        "metadata": {"name": name, "labels": labels},
        "status": {
            "capacity": {"cpu": "8", "memory": "32Gi"},
            "allocatable": {"cpu": "7910m", "memory": "28Gi"},
        },
    }


def pod(
    node_name: str | None,
    cpu: str = "1",
    memory: str = "1Gi",
    phase: str = "Running",
) -> dict[str, Any]:
    return {
        "spec": {
            "nodeName": node_name,
            "containers": [{"resources": {"requests": {"cpu": cpu, "memory": memory}}}],
        },
        "status": {"phase": phase},
    }


GENERAL_PURPOSE = {
    "metadata": {"name": "general-purpose"},
    "spec": {
        "nodePoolAutoCreation": {"enabled": True},
        "priorities": [
            {"machineFamily": "n4", "minCores": 8},
            {"machineFamily": "c4d", "spot": True},
            {"machineType": "n2-standard-16"},
            {"nodepools": ["pool-x", "pool-y"]},
            {"podFamily": "general-purpose"},
        ],
    },
}
UNUSED_CLASS = {"metadata": {"name": "unused"}, "spec": {"priorities": []}}


def build(**kwargs: Any) -> ClusterNodePools:
    pools = [
        gke_pool(
            "nap-n4-a",
            autoscaling={
                "enabled": True,
                "autoprovisioned": True,
                # GKE omits a zero minNodeCount.
                "maxNodeCount": 1000,
            },
        ),
        gke_pool(
            "legacy-default",
            machine="n2-standard-16",
            version="1.35.6-gke.1",
            autoscaling={
                "enabled": True,
                "totalMinNodeCount": 1,
                "totalMaxNodeCount": 2,
            },
            labels={"nodepool.sentry.io/name": "default"},
        ),
        gke_pool("legacy-empty", autoscaling={"enabled": True, "maxNodeCount": 5}),
        gke_pool("static", version="1.36.4-gke.2"),
    ]
    args: dict[str, Any] = {
        "cluster": REF,
        "gke_cluster": gke_cluster(pools),
        "nodes": [
            node("n1", "nap-n4-a", "general-purpose"),
            node("n2", "nap-n4-a", "general-purpose"),
            node("l1", "legacy-default"),
            node("l2", "legacy-default"),
            node("s1", "static"),
        ],
        "pods": [
            pod("n1", cpu="2", memory="7Gi"),
            pod("n2", cpu="2"),
            pod("l1", cpu="7"),
            pod("l2", cpu="7"),
            pod("l2", cpu="100", phase="Succeeded"),  # finished: not counted
            pod(None, cpu="100"),  # pending, unscheduled
        ],
        "compute_classes": [GENERAL_PURPOSE, UNUSED_CLASS],
    }
    args.update(kwargs)
    return build_cluster_nodepools(**args)


def by_name(result: ClusterNodePools) -> dict[str, Any]:
    return {p.name: p for p in result.pools}


def test_pools_grouped_and_sized() -> None:
    result = build()
    # Compute-class pools first, then the rest by name.
    assert [p.name for p in result.pools] == [
        "nap-n4-a",
        "legacy-default",
        "legacy-empty",
        "static",
    ]
    pools = by_name(result)

    nap = pools["nap-n4-a"]
    assert nap.compute_class == "general-purpose"
    assert nap.autoprovisioned
    assert (nap.nodes, nap.min_nodes, nap.max_nodes) == (2, 0, 3000)  # per zone x 3
    assert nap.cpu_requested == 4
    assert nap.cpu_utilization == pytest.approx(4 / (2 * 7.91))
    assert nap.memory_requested_gib == pytest.approx(8)
    assert nap.memory_utilization == pytest.approx(8 / 56)

    legacy = pools["legacy-default"]
    assert legacy.compute_class is None
    assert legacy.sentry_name == "default"
    assert (legacy.nodes, legacy.min_nodes, legacy.max_nodes) == (2, 1, 2)
    assert legacy.at_max
    assert legacy.cpu_requested == 14

    static = pools["static"]
    assert not static.autoscaling
    assert (static.min_nodes, static.max_nodes) == (None, None)
    assert not static.at_max


def test_compute_class_from_pool_labels_without_nodes() -> None:
    pools = [gke_pool("nap-empty", labels={CC: "high-performance"})]
    result = build(gke_cluster=gke_cluster(pools), nodes=[], pods=[])
    assert result.pools[0].compute_class == "high-performance"


def test_pod_ranges() -> None:
    pools = [
        {
            **gke_pool("on-additional"),
            "networkConfig": {"podRange": "pods-b"},
            "podIpv4CidrSize": 26,
            "maxPodsConstraint": {"maxPodsPerNode": "32"},
        },
        {**gke_pool("on-default"), "networkConfig": {}, "podIpv4CidrSize": 24},
    ]
    cluster = gke_cluster(
        pools, ipAllocationPolicy={"clusterSecondaryRangeName": "pods-a"}
    )
    result = by_name(build(gke_cluster=cluster, nodes=[], pods=[]))
    additional, default = result["on-additional"], result["on-default"]
    assert (additional.pod_range, additional.pod_block_prefix) == ("pods-b", 26)
    assert additional.max_pods_per_node == 32
    # No podRange on the pool: it uses the cluster's default pod range.
    assert (default.pod_range, default.pod_block_prefix) == ("pods-a", 24)
    assert default.max_pods_per_node is None


def test_compute_classes() -> None:
    classes = {c.name: c for c in build().compute_classes}
    gp = classes["general-purpose"]
    assert gp.priorities == [
        "n4 (≥8 cores)",
        "c4d (spot)",
        "n2-standard-16",
        "pools pool-x,pool-y",
        "podFamily general-purpose",
    ]
    assert gp.auto_create
    assert (gp.pools, gp.nodes) == (1, 2)
    assert (classes["unused"].pools, classes["unused"].nodes) == (0, 0)


def test_resource_limits_count_all_nodes() -> None:
    limits = {r.resource: r for r in build().resource_limits}
    assert (limits["cpu"].used, limits["cpu"].maximum) == (40, 40)  # 5 nodes x 8
    assert limits["memory"].used == pytest.approx(160)  # 5 x 32 GiB
    assert limits["memory"].unit == "GiB"


def test_findings_and_notes() -> None:
    result = build()
    assert result.findings == [
        "Node auto-provisioning cpu limit is 100% used (40/40 cores); "
        "at the limit the cluster autoscaler can't add nodes",
        "Node pool legacy-default is at its autoscaling maximum "
        "(2/2 nodes, 88% of CPU requested); it can't scale up",
        "1 node pool(s) are 2 minor versions behind the control plane "
        "(1.37.0-gke.3), the most GKE supports; upgrade them before the control "
        "plane's next minor upgrade: legacy-default",
    ]
    assert result.notes == [
        "Empty node pools outside any compute class (0 nodes, min 0), "
        "possibly removable: legacy-empty",
    ]


def test_pools_held_idle_at_minimum() -> None:
    held = {"enabled": True, "totalMinNodeCount": 2, "totalMaxNodeCount": 10}
    pools = [
        gke_pool("idle-at-min", autoscaling=held),
        gke_pool("busy-at-min", autoscaling=held),
        gke_pool("idle-above-min", autoscaling={**held, "totalMinNodeCount": 1}),
        gke_pool("idle-min-zero", autoscaling={"enabled": True, "maxNodeCount": 5}),
        gke_pool("idle-static"),
    ]
    nodes = [
        node("a1", "idle-at-min"),
        node("a2", "idle-at-min"),
        node("b1", "busy-at-min"),
        node("b2", "busy-at-min"),
        node("c1", "idle-above-min"),
        node("c2", "idle-above-min"),
        node("d1", "idle-min-zero"),
        node("e1", "idle-static"),
    ]
    pods = [
        pod("a1", cpu="1", memory="1Gi"),
        # Memory-bound: low CPU but most memory requested.
        pod("b1", cpu="1", memory="20Gi"),
        pod("b2", cpu="1", memory="20Gi"),
    ]
    result = build(gke_cluster=gke_cluster(pools), nodes=nodes, pods=pods)
    assert result.notes == [
        "Node pools held at their autoscaling minimum with under 25% of CPU and "
        "memory requested; the minimum keeps these nodes running: "
        "idle-at-min (2 nodes, 6% CPU / 2% memory)",
    ]


def test_shared_priority_constraints() -> None:
    def cc(name: str, priorities: list[dict[str, Any]]) -> dict[str, Any]:
        return {"metadata": {"name": name}, "spec": {"priorities": priorities}}

    classes = {
        c.name: c
        for c in build(
            compute_classes=[
                cc(
                    "shared",
                    [
                        {"machineFamily": "n4", "minCores": 30},
                        {"machineFamily": "c4d", "minCores": 30},
                    ],
                ),
                cc(
                    "mixed",
                    [
                        {"machineFamily": "n4", "minCores": 30},
                        {"machineFamily": "c4d"},
                    ],
                ),
                cc("single", [{"machineFamily": "n4", "minCores": 30}]),
            ]
        ).compute_classes
    }
    assert classes["shared"].priorities == ["n4", "c4d"]
    assert classes["shared"].shared_constraints == "≥30 cores"
    assert classes["mixed"].priorities == ["n4 (≥30 cores)", "c4d"]
    assert classes["mixed"].shared_constraints is None
    assert classes["single"].priorities == ["n4 (≥30 cores)"]
    assert classes["single"].shared_constraints is None


@pytest.mark.parametrize(
    "compute_classes, reported",
    [
        # Leftover limits with nothing auto-creating pools don't constrain anything.
        ([UNUSED_CLASS], False),
        # Pools that compute classes auto-create are still subject to them.
        ([GENERAL_PURPOSE], True),
    ],
)
def test_resource_limits_without_autoprovisioning(
    compute_classes: list[dict[str, Any]], reported: bool
) -> None:
    # The fixture's 40-core limit is exceeded by 5 x 8-core nodes.
    gke = gke_cluster([gke_pool("pool-a")])
    gke["autoscaling"]["enableNodeAutoprovisioning"] = False
    result = build(
        gke_cluster=gke,
        nodes=[node(f"n{i}", "pool-a") for i in range(5)],
        compute_classes=compute_classes,
    )
    assert not result.autoprovisioning
    assert result.limits_apply is reported
    assert any("auto-provisioning" in f for f in result.findings) is reported


def test_version_skew_beyond_supported() -> None:
    pools = [
        gke_pool("ok", version="1.36.4-gke.2"),
        gke_pool("at-limit", version="1.35.6-gke.1"),
        gke_pool("beyond", version="1.34.12-gke.1"),
    ]
    result = build(gke_cluster=gke_cluster(pools, autoscaling={}), nodes=[], pods=[])
    assert result.findings == [
        "1 node pool(s) are 2 minor versions behind the control plane "
        "(1.37.0-gke.3), the most GKE supports; upgrade them before the control "
        "plane's next minor upgrade: at-limit",
        "1 node pool(s) are more than 2 minor versions behind the control plane "
        "(1.37.0-gke.3), beyond what GKE supports: beyond",
    ]


def test_no_findings_when_healthy() -> None:
    cluster = gke_cluster(
        [gke_pool("nap-n4-a", autoscaling={"enabled": True, "maxNodeCount": 10})],
        autoscaling={"enableNodeAutoprovisioning": False},
    )
    result = build(
        gke_cluster=cluster,
        nodes=[node("n1", "nap-n4-a", "general-purpose")],
        pods=[],
        compute_classes=[GENERAL_PURPOSE],
    )
    assert not result.autoprovisioning
    assert result.resource_limits == []
    assert result.findings == []
    assert result.notes == []


def test_pod_requests() -> None:
    spec = {
        "containers": [
            {"resources": {"requests": {"cpu": "250m", "memory": "256Mi"}}},
            {"resources": {"requests": {"cpu": "250m"}}},
            {"resources": {}},
        ],
        "initContainers": [
            # Regular init container: only its peak counts.
            {"resources": {"requests": {"cpu": "2", "memory": "128Mi"}}},
            # Sidecar: runs alongside the containers.
            {
                "restartPolicy": "Always",
                "resources": {"requests": {"cpu": "100m", "memory": "64Mi"}},
            },
        ],
        "overhead": {"cpu": "10m"},
    }
    cpu, memory = pod_requests({"spec": spec})
    assert cpu == Decimal("2.01")  # max(0.6 incl. sidecar, 2) + overhead
    assert memory == 320 * 2**20  # max(256Mi + 64Mi, 128Mi)


def test_to_dict() -> None:
    data = build().to_dict()
    pool = data["pools"][1]
    assert pool["name"] == "legacy-default"
    assert pool["at_max"] is True
    assert pool["cpu_utilization"] == pytest.approx(14 / 15.82)
    assert data["resource_limits"][0]["utilization"] == 1.0


def _response(items: list[dict[str, Any]]) -> MagicMock:
    return MagicMock(data=json.dumps({"items": items}).encode())


def test_fetch_cluster_nodepools() -> None:
    container = MagicMock()
    container.projects().locations().clusters().get().execute.return_value = (
        gke_cluster([gke_pool("nap-n4-a")])
    )
    core = MagicMock()
    core.list_node.return_value = _response([node("n1", "nap-n4-a", "general-purpose")])
    core.list_pod_for_all_namespaces.return_value = _response([pod("n1")])
    custom = MagicMock()
    custom.list_cluster_custom_object.return_value = {"items": [GENERAL_PURPOSE]}

    with (
        patch("libsentrykube.nodepools.build_api", return_value=container),
        patch("libsentrykube.nodepools.kubernetes.client.CoreV1Api", return_value=core),
        patch(
            "libsentrykube.nodepools.kubernetes.client.CustomObjectsApi",
            return_value=custom,
        ),
    ):
        result = fetch_cluster_nodepools(REF, MagicMock())

    container.projects().locations().clusters().get.assert_called_with(
        name="projects/test-project/locations/region-a/clusters/cluster-a"
    )
    assert core.list_pod_for_all_namespaces.call_args.kwargs["field_selector"] == (
        "status.phase!=Succeeded,status.phase!=Failed"
    )
    custom.list_cluster_custom_object.assert_called_once_with(
        "cloud.google.com", "v1", "computeclasses"
    )
    assert result.pools[0].nodes == 1
    assert result.pools[0].cpu_requested == 1
    assert [c.name for c in result.compute_classes] == ["general-purpose"]


def test_fetch_without_compute_class_crd() -> None:
    container = MagicMock()
    container.projects().locations().clusters().get().execute.return_value = (
        gke_cluster([])
    )
    core = MagicMock()
    core.list_node.return_value = _response([])
    core.list_pod_for_all_namespaces.return_value = _response([])
    custom = MagicMock()
    custom.list_cluster_custom_object.side_effect = ApiException(status=404)

    with (
        patch("libsentrykube.nodepools.build_api", return_value=container),
        patch("libsentrykube.nodepools.kubernetes.client.CoreV1Api", return_value=core),
        patch(
            "libsentrykube.nodepools.kubernetes.client.CustomObjectsApi",
            return_value=custom,
        ),
    ):
        result = fetch_cluster_nodepools(REF, MagicMock())
    assert result.compute_classes == []

    custom.list_cluster_custom_object.side_effect = ApiException(status=403)
    with (
        patch("libsentrykube.nodepools.build_api", return_value=container),
        patch("libsentrykube.nodepools.kubernetes.client.CoreV1Api", return_value=core),
        patch(
            "libsentrykube.nodepools.kubernetes.client.CustomObjectsApi",
            return_value=custom,
        ),
        pytest.raises(ApiException),
    ):
        fetch_cluster_nodepools(REF, MagicMock())
