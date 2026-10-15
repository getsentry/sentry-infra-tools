from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from libsentrykube.network import (
    ClusterNetwork,
    GkeClusterRef,
    RangeUsage,
    build_cluster_network,
    fetch_cluster_network,
)

API = "https://www.googleapis.com/compute/v1/"
NETWORK = f"{API}projects/test-project/global/networks/net-a"
SUBNET = f"{API}projects/test-project/regions/region-a/subnetworks/subnet-a"
OTHER_SUBNET = f"{API}projects/test-project/regions/region-a/subnetworks/subnet-b"
REF = GkeClusterRef(project="test-project", location="region-a-b", name="cluster-a")


def subnet(primary: str = "10.2.0.0/22") -> dict[str, Any]:
    return {
        "name": "subnet-a",
        "selfLink": SUBNET,
        "network": NETWORK,
        "region": f"{API}projects/test-project/regions/region-a",
        "ipCidrRange": primary,
        "secondaryIpRanges": [
            {"rangeName": "pods", "ipCidrRange": "10.4.0.0/20"},
            {"rangeName": "pods-extra", "ipCidrRange": "10.5.0.0/20"},
            {"rangeName": "services", "ipCidrRange": "10.8.0.0/22"},
            {"rangeName": "spare", "ipCidrRange": "10.9.0.0/24"},
        ],
    }


def gke_cluster(**overrides: Any) -> dict[str, Any]:
    cluster: dict[str, Any] = {
        "name": "cluster-a",
        "ipAllocationPolicy": {
            "clusterSecondaryRangeName": "pods",
            "servicesSecondaryRangeName": "services",
        },
        "privateClusterConfig": {"masterIpv4CidrBlock": "172.16.0.16/28"},
        "masterAuthorizedNetworksConfig": {
            "enabled": True,
            "cidrBlocks": [{"cidrBlock": "10.2.0.0/22", "displayName": "nodes"}],
        },
        "nodePools": [
            {"name": "pool-a", "podIpv4CidrSize": 24},
            {
                "name": "pool-b",
                "podIpv4CidrSize": 26,
                "networkConfig": {"podRange": "pods-extra"},
            },
        ],
    }
    cluster.update(overrides)
    return cluster


def node(name: str, ip: str, pool: str) -> dict[str, Any]:
    return {
        "name": name,
        "labels": {
            "goog-k8s-cluster-name": "cluster-a",
            "goog-k8s-cluster-location": "region-a-b",
            "goog-k8s-node-pool-name": pool,
        },
        "networkInterfaces": [{"subnetwork": SUBNET, "networkIP": ip}],
    }


def vm(name: str, ip: str, subnetwork: str) -> dict[str, Any]:
    return {
        "name": name,
        "networkInterfaces": [{"subnetwork": subnetwork, "networkIP": ip}],
    }


def vms_rule(name: str, sources: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "network": NETWORK,
        "direction": "INGRESS",
        "sourceRanges": sources,
        "targetTags": ["gke-cluster-a-1a2b3c4d-node"],
        "allowed": [{"IPProtocol": "tcp"}],
    }


NODES = [
    node("n1", "10.2.0.2", "pool-a"),
    node("n2", "10.2.0.3", "pool-a"),
    node("n3", "10.2.0.4", "pool-b"),
]
FIREWALLS = [vms_rule("gke-cluster-a-1a2b3c4d-vms", ["10.2.0.0/22"])]


def build(**kwargs: Any) -> ClusterNetwork:
    args: dict[str, Any] = {
        "cluster": REF,
        "gke_cluster": gke_cluster(),
        "subnet": subnet(),
        "instances": NODES,
        "forwarding_rules": [],
        "addresses": [],
        "firewalls": FIREWALLS,
    }
    args.update(kwargs)
    return build_cluster_network(**args)


def by_name(result: ClusterNetwork) -> dict[tuple[str, str], RangeUsage]:
    return {(r.role, r.name): r for r in result.ranges}


def test_from_context() -> None:
    ref = GkeClusterRef.from_context("gke_test-project_region-a-b_cluster-a")
    assert ref == REF
    assert ref.resource_name == (
        "projects/test-project/locations/region-a-b/clusters/cluster-a"
    )


@pytest.mark.parametrize("context", ["colima-local-k8s", "gke_a_b", "eks_a_b_c"])
def test_from_context_rejects_non_gke(context: str) -> None:
    with pytest.raises(ValueError):
        GkeClusterRef.from_context(context)


def test_ranges_and_usage() -> None:
    result = build(
        instances=[
            *NODES,
            vm("elsewhere", "10.3.0.2", OTHER_SUBNET),
            # a non-GKE VM still uses a node subnet IP
            vm("vm", "10.2.0.9", SUBNET),
        ],
        forwarding_rules=[
            {"name": "ilb", "subnetwork": SUBNET, "IPAddress": "10.2.0.10"},
            {"name": "external", "IPAddress": "34.1.2.3"},
        ],
        addresses=[
            # in use by the ILB above: counted once
            {"name": "ilb-ip", "subnetwork": SUBNET, "address": "10.2.0.10"},
            {"name": "reserved", "subnetwork": SUBNET, "address": "10.2.0.11"},
        ],
    )
    ranges = by_name(result)

    nodes = ranges[("nodes", "subnet-a")]
    assert (nodes.cidr, nodes.used, nodes.capacity) == ("10.2.0.0/22", 6, 1020)

    # pool-a: 2 nodes x /24, pool-b: 1 node x /26 in its own range
    assert ranges[("pods", "pods")].used == 2 * 256
    assert ranges[("pods", "pods")].capacity == 4096
    assert ranges[("pods", "pods-extra")].used == 64

    assert ranges[("services", "services")].used is None
    assert ranges[("services", "services")].capacity == 1024
    assert ranges[("control plane", "")].cidr == "172.16.0.16/28"
    assert ranges[("unused secondary", "spare")].cidr == "10.9.0.0/24"

    assert result.network == "net-a"
    assert result.subnet_region == "region-a"
    assert result.node_firewall_rules == ["gke-cluster-a-1a2b3c4d-vms"]
    assert result.findings == []


def test_utilization_threshold() -> None:
    nodes = [node(f"n{i}", f"10.2.0.{i + 2}", "pool-a") for i in range(12)]
    result = build(subnet=subnet("10.2.0.0/28"), instances=nodes, warn_threshold=0.9)
    # /28 has 12 usable addresses, all taken; pods use 12 x /24 of /20 (75%)
    assert result.findings == [
        "nodes range subnet-a (10.2.0.0/28) is 100% used (12/12)",
    ]


def test_original_node_range_is_implicitly_authorized() -> None:
    # Never-expanded subnet: the -vms rule still matches the subnet, and GKE
    # authorizes it for the control plane without an explicit entry.
    cluster = gke_cluster(masterAuthorizedNetworksConfig={"enabled": True})
    result = build(gke_cluster=cluster)
    assert result.original_node_ranges == ["10.2.0.0/22"]
    assert result.findings == []


def test_expanded_range_must_be_authorized() -> None:
    # Expanded /22 -> /20 with the firewall gap closed but only the original
    # range authorized: nodes in the added range can't register.
    firewalls = [
        *FIREWALLS,
        vms_rule("gke-cluster-a-1a2b3c4d-vms-expanded", ["10.2.0.0/20"]),
    ]
    result = build(subnet=subnet("10.2.0.0/20"), firewalls=firewalls)
    assert result.original_node_ranges == ["10.2.0.0/22"]
    assert result.findings == [
        "Control plane authorized networks do not cover the node range "
        "10.2.0.0/20 beyond its original range 10.2.0.0/22; nodes with "
        "uncovered IPs cannot register with the control plane"
    ]

    # Authorizing the expanded range clears it.
    cluster = gke_cluster(
        masterAuthorizedNetworksConfig={
            "enabled": True,
            "cidrBlocks": [{"cidrBlock": "10.2.0.0/20"}],
        }
    )
    result = build(
        gke_cluster=cluster, subnet=subnet("10.2.0.0/20"), firewalls=firewalls
    )
    assert result.findings == []


def test_authorized_networks_without_vms_rule() -> None:
    # Without the -vms rule there is no record of the original range.
    cluster = gke_cluster(masterAuthorizedNetworksConfig={"enabled": True})
    result = build(gke_cluster=cluster, firewalls=[])
    assert result.original_node_ranges == []
    assert any(
        "do not cover the node range 10.2.0.0/22; nodes" in f for f in result.findings
    )


def test_authorized_networks_disabled() -> None:
    cluster = gke_cluster(
        masterAuthorizedNetworksConfig={"cidrBlocks": [{"cidrBlock": "192.168.0.0/24"}]}
    )
    result = build(gke_cluster=cluster)
    assert not result.authorized_networks_enabled
    assert result.findings == []


def test_expanded_subnet_needs_wider_firewall_rule() -> None:
    cluster = gke_cluster(
        masterAuthorizedNetworksConfig={
            "enabled": True,
            "cidrBlocks": [{"cidrBlock": "10.0.0.0/8"}],
        }
    )
    result = build(gke_cluster=cluster, subnet=subnet("10.2.0.0/20"))
    assert len(result.findings) == 1
    assert "do not cover the node range 10.2.0.0/20" in result.findings[0]

    # A second rule for the same node tag closes the gap.
    expanded = vms_rule("gke-cluster-a-1a2b3c4d-vms-expanded", ["10.2.0.0/20"])
    result = build(
        gke_cluster=cluster,
        subnet=subnet("10.2.0.0/20"),
        firewalls=[*FIREWALLS, expanded],
    )
    assert result.findings == []
    assert result.node_firewall_rules == [
        "gke-cluster-a-1a2b3c4d-vms",
        "gke-cluster-a-1a2b3c4d-vms-expanded",
    ]


def test_firewall_coverage_can_span_rules() -> None:
    cluster = gke_cluster(masterAuthorizedNetworksConfig={})
    halves = [
        vms_rule("gke-cluster-a-1a2b3c4d-vms", ["10.2.0.0/21"]),
        vms_rule("extra", ["10.2.8.0/21"]),
    ]
    result = build(gke_cluster=cluster, subnet=subnet("10.2.0.0/20"), firewalls=halves)
    assert result.findings == []


def test_narrow_rule_on_node_tag_does_not_count() -> None:
    cluster = gke_cluster(masterAuthorizedNetworksConfig={})
    https_only = vms_rule("allow-internal-https", ["10.0.0.0/8"])
    https_only["allowed"] = [{"IPProtocol": "tcp", "ports": ["443"]}]
    result = build(
        gke_cluster=cluster,
        subnet=subnet("10.2.0.0/20"),
        firewalls=[*FIREWALLS, https_only],
    )
    assert result.node_firewall_rules == ["gke-cluster-a-1a2b3c4d-vms"]
    assert len(result.findings) == 1
    assert "do not cover the node range 10.2.0.0/20" in result.findings[0]

    # A rule allowing everything does stand in for the -vms rule.
    https_only["allowed"] = [{"IPProtocol": "all"}]
    result = build(
        gke_cluster=cluster,
        subnet=subnet("10.2.0.0/20"),
        firewalls=[*FIREWALLS, https_only],
    )
    assert result.findings == []


def test_node_firewall_rules_exclude_other_sources() -> None:
    # GKE's -all rule allows everything to the node tag, but from the pod range.
    pods_all = vms_rule("gke-cluster-a-1a2b3c4d-all", ["10.4.0.0/20"])
    pods_all["allowed"] = [{"IPProtocol": "all"}]
    internal = vms_rule("allow-internal-to-cluster-a", ["10.0.0.0/8"])
    internal["allowed"] = [{"IPProtocol": "all"}]
    result = build(firewalls=[*FIREWALLS, pods_all, internal])
    assert result.node_firewall_rules == [
        "gke-cluster-a-1a2b3c4d-vms",
        "allow-internal-to-cluster-a",
    ]
    assert result.findings == []


def test_missing_vms_rule() -> None:
    other_network = vms_rule("gke-cluster-a-1a2b3c4d-vms", ["10.2.0.0/22"])
    other_network["network"] = f"{API}projects/test-project/global/networks/net-b"
    result = build(firewalls=[other_network])
    assert result.findings == [
        "No GKE intra-node firewall rule (gke-cluster-a-<hash>-vms) found"
    ]


def test_to_dict_includes_utilization() -> None:
    data = build().to_dict()
    assert data["cluster"]["name"] == "cluster-a"
    assert data["ranges"][0]["utilization"] == pytest.approx(3 / 1020)


def test_fetch_cluster_network_wires_api_calls() -> None:
    cluster = gke_cluster(
        networkConfig={
            "network": "projects/test-project/global/networks/net-a",
            "subnetwork": "projects/test-project/regions/region-a/subnetworks/subnet-a",
        }
    )
    container = MagicMock()
    container.projects().locations().clusters().get().execute.return_value = cluster
    compute = MagicMock()
    compute.subnetworks().get().execute.return_value = subnet()
    compute.regions().get().execute.return_value = {
        "zones": [f"{API}projects/test-project/zones/region-a-b"]
    }

    def paged(pages: list[list[dict[str, Any]]]) -> MagicMock:
        collection = MagicMock()
        requests = [MagicMock() for _ in pages]
        for request, items in zip(requests, pages):
            request.execute.return_value = {"items": items}
        collection.list.return_value = requests[0]
        collection.list_next.side_effect = [*requests[1:], None]
        return collection

    compute.instances.return_value = paged([NODES[:2], NODES[2:]])
    compute.forwardingRules.return_value = paged([[]])
    compute.addresses.return_value = paged([[]])
    compute.firewalls.return_value = paged([FIREWALLS])

    with patch("libsentrykube.network.build_api") as build_api:
        build_api.side_effect = lambda api, version: {
            "container": container,
            "compute": compute,
        }[api]
        result = fetch_cluster_network(REF)

    container.projects().locations().clusters().get.assert_called_with(
        name="projects/test-project/locations/region-a-b/clusters/cluster-a"
    )
    compute.subnetworks().get.assert_called_with(
        project="test-project", region="region-a", subnetwork="subnet-a"
    )
    assert compute.instances().list.call_args.kwargs["zone"] == "region-a-b"
    assert by_name(result)[("nodes", "subnet-a")].used == 3
    assert result.findings == []
