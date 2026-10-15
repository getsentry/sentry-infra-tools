"""
Read-only view of a GKE cluster's networking: its IP ranges, how full they
are, and the access rules that have to track the node range (control plane
authorized networks and GKE's intra-node firewall rule).
"""

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from ipaddress import IPv4Address, IPv4Network, collapse_addresses, ip_address
from ipaddress import ip_network
from typing import Any, Iterable, Mapping, Optional, Sequence

from libsentrykube.gcloud import build_api

# GCP reserves the network, gateway, second-to-last and broadcast addresses.
GCP_RESERVED_IPS_PER_SUBNET = 4
GKE_LABEL_CLUSTER = "goog-k8s-cluster-name"
GKE_LABEL_LOCATION = "goog-k8s-cluster-location"
GKE_LABEL_NODE_POOL = "goog-k8s-node-pool-name"
DEFAULT_WARN_THRESHOLD = 0.8


@dataclass(frozen=True)
class GkeClusterRef:
    project: str
    location: str
    name: str

    @classmethod
    def from_context(cls, context: str) -> "GkeClusterRef":
        parts = context.split("_")
        if len(parts) != 4 or parts[0] != "gke":
            raise ValueError(
                f"Kube context {context!r} is not in gke_PROJECT_LOCATION_CLUSTER format"
            )
        _, project, location, name = parts
        return cls(project=project, location=location, name=name)

    @property
    def resource_name(self) -> str:
        return f"projects/{self.project}/locations/{self.location}/clusters/{self.name}"


@dataclass(frozen=True)
class RangeUsage:
    role: str
    name: str
    cidr: str
    capacity: int
    used: Optional[int] = None

    @property
    def utilization(self) -> Optional[float]:
        if self.used is None or self.capacity <= 0:
            return None
        return self.used / self.capacity


@dataclass(frozen=True)
class AuthorizedNetwork:
    cidr: str
    display_name: str = ""


@dataclass
class ClusterNetwork:
    cluster: GkeClusterRef
    network: str
    subnet: str
    subnet_region: str
    ranges: list[RangeUsage]
    authorized_networks_enabled: bool
    authorized_networks: list[AuthorizedNetwork] = field(default_factory=list)
    # The node range at cluster creation (from GKE's -vms rule); GKE authorizes
    # it for control plane access without an explicit authorized network.
    original_node_ranges: list[str] = field(default_factory=list)
    node_firewall_rules: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for rng, usage in zip(data["ranges"], self.ranges):
            rng["utilization"] = usage.utilization
        return data


def _v4(cidr: Optional[str]) -> Optional[IPv4Network]:
    if not cidr:
        return None
    try:
        network = ip_network(cidr, strict=False)
    except ValueError:
        return None
    return network if isinstance(network, IPv4Network) else None


def _resource_path(link: Optional[str]) -> str:
    """Normalize a full or partial resource URL to 'projects/...'."""
    if not link:
        return ""
    return "projects/" + link.split("projects/", 1)[-1]


def _basename(link: Optional[str]) -> str:
    return (link or "").rstrip("/").rsplit("/", 1)[-1]


def _ips_in(addresses: Iterable[Optional[str]], network: IPv4Network) -> set[str]:
    found = set()
    for address in addresses:
        try:
            ip = ip_address(address or "")
        except ValueError:
            continue
        if isinstance(ip, IPv4Address) and ip in network:
            found.add(str(ip))
    return found


def pool_pod_range(
    pool: Mapping[str, Any], default_pod_range: Optional[str]
) -> Optional[str]:
    """
    The secondary range a node pool's pods get IPs from. It's fixed when the
    pool is created; pools without one use the cluster's default pod range.
    """
    return (pool.get("networkConfig") or {}).get("podRange") or default_pod_range


def _node_counts(
    cluster: GkeClusterRef, instances: Iterable[Mapping[str, Any]]
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for instance in instances:
        labels = instance.get("labels") or {}
        if labels.get(GKE_LABEL_CLUSTER) != cluster.name:
            continue
        if labels.get(GKE_LABEL_LOCATION, cluster.location) != cluster.location:
            continue
        counts[labels.get(GKE_LABEL_NODE_POOL, "")] += 1
    return counts


def build_cluster_network(
    cluster: GkeClusterRef,
    gke_cluster: Mapping[str, Any],
    subnet: Mapping[str, Any],
    instances: Sequence[Mapping[str, Any]],
    forwarding_rules: Sequence[Mapping[str, Any]],
    addresses: Sequence[Mapping[str, Any]],
    firewalls: Sequence[Mapping[str, Any]],
    warn_threshold: float = DEFAULT_WARN_THRESHOLD,
) -> ClusterNetwork:
    """Combine already-fetched API resources into a ClusterNetwork."""
    subnet_path = _resource_path(subnet.get("selfLink"))
    node_range = _v4(subnet.get("ipCidrRange"))
    if node_range is None:
        raise ValueError(f"Subnet {subnet.get('name')} has no IPv4 primary range")
    secondary = {
        r["rangeName"]: r["ipCidrRange"] for r in subnet.get("secondaryIpRanges", [])
    }

    def in_subnet(resource: Mapping[str, Any]) -> bool:
        return _resource_path(resource.get("subnetwork")) == subnet_path

    # Node range: every IP held in the subnet by VMs, internal LBs or reservations.
    used_ips = _ips_in(
        (
            nic.get("networkIP")
            for instance in instances
            for nic in instance.get("networkInterfaces", [])
            if in_subnet(nic)
        ),
        node_range,
    )
    used_ips |= _ips_in(
        (rule.get("IPAddress") for rule in forwarding_rules if in_subnet(rule)),
        node_range,
    )
    used_ips |= _ips_in(
        (address.get("address") for address in addresses if in_subnet(address)),
        node_range,
    )
    ranges = [
        RangeUsage(
            role="nodes",
            name=str(subnet.get("name", "")),
            cidr=str(node_range),
            capacity=max(node_range.num_addresses - GCP_RESERVED_IPS_PER_SUBNET, 0),
            used=len(used_ips),
        )
    ]

    # Pod ranges: each node reserves a fixed-size block from its pool's pod range.
    ip_policy = gke_cluster.get("ipAllocationPolicy") or {}
    default_pod_range = ip_policy.get("clusterSecondaryRangeName")
    nodes_per_pool = _node_counts(cluster, instances)
    pod_ranges: dict[str, int] = {}
    if default_pod_range:
        pod_ranges[default_pod_range] = 0
    for name in (ip_policy.get("additionalPodRangesConfig") or {}).get(
        "podRangeNames", []
    ):
        pod_ranges.setdefault(name, 0)
    for pool in gke_cluster.get("nodePools", []):
        range_name = pool_pod_range(pool, default_pod_range)
        block_size = pool.get("podIpv4CidrSize")
        if not range_name or not block_size:
            continue
        pod_ranges[range_name] = pod_ranges.get(range_name, 0) + nodes_per_pool[
            pool["name"]
        ] * 2 ** (32 - int(block_size))
    for name, used in pod_ranges.items():
        rng = _v4(secondary.get(name))
        if rng is not None:
            ranges.append(RangeUsage("pods", name, str(rng), rng.num_addresses, used))

    services_name = ip_policy.get("servicesSecondaryRangeName") or ""
    services = _v4(secondary.get(services_name) or gke_cluster.get("servicesIpv4Cidr"))
    if services is not None:
        ranges.append(
            RangeUsage("services", services_name, str(services), services.num_addresses)
        )

    control_plane = _v4(
        (gke_cluster.get("privateClusterConfig") or {}).get("masterIpv4CidrBlock")
    )
    if control_plane is not None:
        ranges.append(
            RangeUsage(
                "control plane", "", str(control_plane), control_plane.num_addresses
            )
        )

    for name, cidr in secondary.items():
        rng = _v4(cidr)
        if rng is not None and name not in pod_ranges and name != services_name:
            ranges.append(
                RangeUsage("unused secondary", name, str(rng), rng.num_addresses)
            )

    findings = [
        f"{usage.role} range {usage.name or usage.cidr} ({usage.cidr}) is "
        f"{usage.utilization:.0%} used ({usage.used}/{usage.capacity})"
        for usage in ranges
        if usage.utilization is not None and usage.utilization >= warn_threshold
    ]

    # GKE creates gke-<cluster>-<hash>-vms for the node range at creation and
    # never widens it, so an expanded subnet needs a second rule for the same tags.
    network_path = _resource_path(subnet.get("network"))
    vms_rule = re.compile(rf"^gke-{re.escape(cluster.name)}-[0-9a-f]+-vms$")
    network_rules = [
        fw for fw in firewalls if _resource_path(fw.get("network")) == network_path
    ]
    vms_rules = [fw for fw in network_rules if vms_rule.match(fw.get("name", ""))]
    # That makes the -vms rule the only record of the subnet's original range,
    # which GKE also authorizes implicitly for control plane access. Only
    # addresses added by expanding the subnet need explicit authorized networks.
    original_node_ranges = sorted(
        {
            str(n)
            for fw in vms_rules
            for n in (_v4(c) for c in fw.get("sourceRanges", []))
            if n is not None and n.overlaps(node_range)
        }
    )

    # Authorized networks: nodes outside them can't register with the control plane.
    man_config = gke_cluster.get("masterAuthorizedNetworksConfig") or {}
    man_enabled = bool(man_config.get("enabled"))
    authorized = [
        AuthorizedNetwork(b.get("cidrBlock", ""), b.get("displayName", ""))
        for b in man_config.get("cidrBlocks", [])
    ]
    if man_enabled and not _covered(
        node_range, [*(a.cidr for a in authorized), *original_node_ranges]
    ):
        original = (
            f" beyond its original range {', '.join(original_node_ranges)}"
            if original_node_ranges
            else ""
        )
        findings.append(
            f"Control plane authorized networks do not cover the node range "
            f"{node_range}{original}; nodes with uncovered IPs cannot register "
            "with the control plane"
        )

    node_rules: list[Mapping[str, Any]] = []
    if not vms_rules:
        findings.append(
            f"No GKE intra-node firewall rule (gke-{cluster.name}-<hash>-vms) found"
        )
    else:
        # Rules that can stand in for the -vms rule: on the node tag, sourced
        # from (part of) the node range (not e.g. GKE's pod-range -all rule),
        # and allowing at least what -vms allows; a narrow rule on the node tag
        # doesn't restore node-to-node traffic.
        node_tags = {tag for fw in vms_rules for tag in fw.get("targetTags", [])}
        node_rules = [
            fw
            for fw in network_rules
            if not fw.get("disabled")
            and fw.get("direction", "INGRESS") == "INGRESS"
            and node_tags.intersection(fw.get("targetTags", []))
            and any(
                src.overlaps(node_range)
                for src in (_v4(c) for c in fw.get("sourceRanges", []))
                if src is not None
            )
            and any(_allows_all_of(fw, vms) for vms in vms_rules)
        ]
        sources = [src for fw in node_rules for src in fw.get("sourceRanges", [])]
        if not _covered(node_range, sources):
            findings.append(
                f"Intra-node firewall rules ({', '.join(fw['name'] for fw in node_rules)}) "
                f"do not cover the node range {node_range}; GKE does not widen "
                "its -vms rule when the subnet is expanded"
            )

    return ClusterNetwork(
        cluster=cluster,
        network=_basename(subnet.get("network")),
        subnet=str(subnet.get("name", "")),
        subnet_region=_basename(subnet.get("region")),
        ranges=ranges,
        authorized_networks_enabled=man_enabled,
        authorized_networks=authorized,
        original_node_ranges=original_node_ranges,
        node_firewall_rules=[fw["name"] for fw in node_rules],
        findings=findings,
    )


def _port_ranges(ports: Optional[Sequence[str]]) -> list[tuple[int, int]]:
    if not ports:
        return [(0, 65535)]
    ranges = []
    for port in ports:
        low, _, high = port.partition("-")
        ranges.append((int(low), int(high or low)))
    return ranges


def _allows_all_of(candidate: Mapping[str, Any], reference: Mapping[str, Any]) -> bool:
    """True if `candidate` allows every protocol and port that `reference` allows."""
    granted: dict[str, list[tuple[int, int]]] = {}
    for allowed in candidate.get("allowed", []):
        proto = str(allowed["IPProtocol"]).lower()
        granted.setdefault(proto, []).extend(_port_ranges(allowed.get("ports")))
    if not granted:
        return False
    if "all" in granted:
        return True
    return all(
        any(g_low <= low and high <= g_high for g_low, g_high in granted.get(proto, []))
        for allowed in reference.get("allowed", [])
        for proto in [str(allowed["IPProtocol"]).lower()]
        for low, high in _port_ranges(allowed.get("ports"))
    )


def _covered(network: IPv4Network, cidrs: Iterable[str]) -> bool:
    """True if the union of `cidrs` contains all of `network`."""
    parsed = [n for n in (_v4(c) for c in cidrs) if n is not None]
    return any(network.subnet_of(block) for block in collapse_addresses(parsed))


def _list_all(collection: Any, **kwargs: Any) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []
    request = collection.list(**kwargs)
    while request is not None:
        response = request.execute()
        items.extend(response.get("items", []))
        request = collection.list_next(request, response)
    return items


def fetch_cluster_network(
    cluster: GkeClusterRef, warn_threshold: float = DEFAULT_WARN_THRESHOLD
) -> ClusterNetwork:
    """Look up a cluster's networking through the GKE and Compute APIs (read-only)."""
    container = build_api("container", "v1")
    compute = build_api("compute", "v1")

    gke_cluster = (
        container.projects()
        .locations()
        .clusters()
        .get(name=cluster.resource_name)
        .execute()
    )
    # projects/HOST_PROJECT/regions/REGION/subnetworks/NAME; differs from the
    # cluster's project on Shared VPC.
    subnet_path = _resource_path(gke_cluster["networkConfig"]["subnetwork"])
    _, host_project, _, region, _, subnet_name = subnet_path.split("/")
    subnet = (
        compute.subnetworks()
        .get(project=host_project, region=region, subnetwork=subnet_name)
        .execute()
    )

    zones = compute.regions().get(project=cluster.project, region=region).execute()
    instances = [
        instance
        for zone in zones.get("zones", [])
        for instance in _list_all(
            compute.instances(),
            project=cluster.project,
            zone=_basename(zone),
            fields="items(name,labels,networkInterfaces(subnetwork,networkIP)),nextPageToken",
        )
    ]
    forwarding_rules = _list_all(
        compute.forwardingRules(), project=cluster.project, region=region
    )
    addresses = _list_all(compute.addresses(), project=cluster.project, region=region)
    firewalls = _list_all(compute.firewalls(), project=host_project)

    return build_cluster_network(
        cluster,
        gke_cluster,
        subnet,
        instances,
        forwarding_rules,
        addresses,
        firewalls,
        warn_threshold=warn_threshold,
    )
