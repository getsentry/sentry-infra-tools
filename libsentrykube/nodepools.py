"""
Read-only view of a GKE cluster's node pools and the ComputeClasses that drive
them: size and autoscaling bounds from GKE, live nodes and requested capacity
from Kubernetes, and node auto-provisioning limits.
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence, cast

import kubernetes.client
from kubernetes.client.exceptions import ApiException
from kubernetes.utils import parse_quantity

from libsentrykube.gcloud import build_api
from libsentrykube.network import GkeClusterRef, pool_pod_range

COMPUTE_CLASS_LABEL = "cloud.google.com/compute-class"
NODE_POOL_LABEL = "cloud.google.com/gke-nodepool"
SENTRY_NODE_POOL_LABEL = "nodepool.sentry.io/name"
DEFAULT_WARN_THRESHOLD = 0.8
# Node pools this many minor versions behind the control plane are reported.
VERSION_SKEW_MINORS = 2
GIB = Decimal(2**30)
_MINOR_VERSION = re.compile(r"^(\d+)\.(\d+)")


@dataclass(frozen=True)
class NodePool:
    name: str
    compute_class: Optional[str]
    sentry_name: Optional[str]
    machine_type: str
    autoprovisioned: bool
    version: str
    nodes: int
    autoscaling: bool
    min_nodes: Optional[int] = None
    max_nodes: Optional[int] = None
    cpu_allocatable: float = 0.0
    cpu_requested: float = 0.0
    memory_allocatable_gib: float = 0.0
    memory_requested_gib: float = 0.0
    # Secondary range the pool's pods get IPs from, and the block (prefix
    # length) each node reserves from it, sized by max pods per node.
    pod_range: Optional[str] = None
    pod_block_prefix: Optional[int] = None
    max_pods_per_node: Optional[int] = None

    @property
    def cpu_utilization(self) -> Optional[float]:
        return _ratio(self.cpu_requested, self.cpu_allocatable)

    @property
    def memory_utilization(self) -> Optional[float]:
        return _ratio(self.memory_requested_gib, self.memory_allocatable_gib)

    @property
    def at_max(self) -> bool:
        return (
            self.autoscaling
            and self.max_nodes is not None
            and self.nodes >= self.max_nodes
        )


@dataclass(frozen=True)
class ComputeClass:
    name: str
    priorities: list[str]
    auto_create: bool
    pools: int
    nodes: int


@dataclass(frozen=True)
class ResourceLimit:
    resource: str
    unit: str
    used: float
    maximum: float

    @property
    def utilization(self) -> Optional[float]:
        return _ratio(self.used, self.maximum)


@dataclass
class ClusterNodePools:
    cluster: GkeClusterRef
    control_plane_version: str
    autoprovisioning: bool
    resource_limits: list[ResourceLimit] = field(default_factory=list)
    pools: list[NodePool] = field(default_factory=list)
    compute_classes: list[ComputeClass] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for item, pool in zip(data["pools"], self.pools):
            item["cpu_utilization"] = pool.cpu_utilization
            item["memory_utilization"] = pool.memory_utilization
            item["at_max"] = pool.at_max
        for item, limit in zip(data["resource_limits"], self.resource_limits):
            item["utilization"] = limit.utilization
        return data


def _ratio(used: float, total: float) -> Optional[float]:
    return used / total if total > 0 else None


def _quantity(value: Any) -> Decimal:
    try:
        if value is None:
            return Decimal(0)
        return Decimal(parse_quantity(value))  # type: ignore[no-untyped-call]
    except ValueError:
        return Decimal(0)


def _minor(version: str) -> Optional[tuple[int, int]]:
    match = _MINOR_VERSION.match(version or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _container_requests(container: Mapping[str, Any]) -> tuple[Decimal, Decimal]:
    requests = (container.get("resources") or {}).get("requests") or {}
    return _quantity(requests.get("cpu")), _quantity(requests.get("memory"))


def pod_requests(pod: Mapping[str, Any]) -> tuple[Decimal, Decimal]:
    """
    A pod's effective (cpu cores, memory bytes) request, as the scheduler
    counts it: the larger of its containers' sum and its largest regular init
    container, plus sidecar init containers and pod overhead.
    """
    spec = pod.get("spec") or {}
    cpu = memory = Decimal(0)
    for container in spec.get("containers") or []:
        c, m = _container_requests(container)
        cpu, memory = cpu + c, memory + m
    init_cpu = init_memory = Decimal(0)
    for container in spec.get("initContainers") or []:
        c, m = _container_requests(container)
        if container.get("restartPolicy") == "Always":  # sidecar: runs alongside
            cpu, memory = cpu + c, memory + m
        else:
            init_cpu, init_memory = max(init_cpu, c), max(init_memory, m)
    overhead = spec.get("overhead") or {}
    return (
        max(cpu, init_cpu) + _quantity(overhead.get("cpu")),
        max(memory, init_memory) + _quantity(overhead.get("memory")),
    )


def _format_priority(priority: Mapping[str, Any]) -> str:
    if priority.get("machineType"):
        text = str(priority["machineType"])
    elif priority.get("machineFamily"):
        text = str(priority["machineFamily"])
    elif priority.get("nodepools"):
        text = "pools " + ",".join(priority["nodepools"])
    elif priority.get("podFamily"):
        text = f"podFamily {priority['podFamily']}"
    else:
        text = "/".join(sorted(priority)) or "any"
    extras = []
    if priority.get("spot"):
        extras.append("spot")
    if priority.get("minCores"):
        extras.append(f"≥{priority['minCores']} cores")
    if priority.get("minMemoryGb"):
        extras.append(f"≥{priority['minMemoryGb']} GB")
    return f"{text} ({', '.join(extras)})" if extras else text


def _total_bound(autoscaling: Mapping[str, Any], key: str, zones: int) -> Optional[int]:
    total = autoscaling.get(f"total{key[0].upper()}{key[1:]}")
    if total is not None:
        return int(total)
    per_zone = autoscaling.get(key)
    return int(per_zone) * zones if per_zone is not None else None


def _names(names: Sequence[str], limit: int = 5) -> str:
    shown = ", ".join(names[:limit])
    return f"{shown} and {len(names) - limit} more" if len(names) > limit else shown


def build_cluster_nodepools(
    cluster: GkeClusterRef,
    gke_cluster: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
    pods: Sequence[Mapping[str, Any]],
    compute_classes: Sequence[Mapping[str, Any]],
    warn_threshold: float = DEFAULT_WARN_THRESHOLD,
) -> ClusterNodePools:
    """Combine already-fetched GKE and Kubernetes resources into a ClusterNodePools."""
    # Live nodes, by pool, and the compute class GKE labelled them with.
    node_pool: dict[str, str] = {}
    pool_nodes: Counter[str] = Counter()
    pool_classes: dict[str, Counter[str]] = defaultdict(Counter)
    allocatable: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 2)
    capacity_cpu = capacity_memory = Decimal(0)
    for node in nodes:
        meta, status = node.get("metadata") or {}, node.get("status") or {}
        labels = meta.get("labels") or {}
        pool = labels.get(NODE_POOL_LABEL)
        capacity = status.get("capacity") or {}
        capacity_cpu += _quantity(capacity.get("cpu"))
        capacity_memory += _quantity(capacity.get("memory"))
        if not pool:
            continue
        node_pool[meta.get("name", "")] = pool
        pool_nodes[pool] += 1
        if labels.get(COMPUTE_CLASS_LABEL):
            pool_classes[pool][labels[COMPUTE_CLASS_LABEL]] += 1
        alloc = status.get("allocatable") or {}
        allocatable[pool][0] += _quantity(alloc.get("cpu"))
        allocatable[pool][1] += _quantity(alloc.get("memory"))

    requested: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 2)
    for pod in pods:
        if (pod.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
            continue
        pool = node_pool.get((pod.get("spec") or {}).get("nodeName", ""))
        if pool is None:
            continue
        cpu, memory = pod_requests(pod)
        requested[pool][0] += cpu
        requested[pool][1] += memory

    default_pod_range = (gke_cluster.get("ipAllocationPolicy") or {}).get(
        "clusterSecondaryRangeName"
    )
    pools = []
    for gke_pool in gke_cluster.get("nodePools", []):
        name = gke_pool["name"]
        config = gke_pool.get("config") or {}
        config_labels = config.get("labels") or {}
        autoscaling = gke_pool.get("autoscaling") or {}
        zones = len(gke_pool.get("locations") or []) or 1
        enabled = bool(autoscaling.get("enabled"))
        block_prefix = gke_pool.get("podIpv4CidrSize")
        max_pods = (gke_pool.get("maxPodsConstraint") or {}).get("maxPodsPerNode")
        compute_class = (
            pool_classes[name].most_common(1)[0][0]
            if pool_classes[name]
            else config_labels.get(COMPUTE_CLASS_LABEL)
        )
        pools.append(
            NodePool(
                name=name,
                compute_class=compute_class,
                sentry_name=config_labels.get(SENTRY_NODE_POOL_LABEL),
                machine_type=str(config.get("machineType", "")),
                autoprovisioned=bool(autoscaling.get("autoprovisioned")),
                version=str(gke_pool.get("version", "")),
                nodes=pool_nodes[name],
                autoscaling=enabled,
                # GKE omits a zero minimum from the API response.
                min_nodes=(_total_bound(autoscaling, "minNodeCount", zones) or 0)
                if enabled
                else None,
                max_nodes=_total_bound(autoscaling, "maxNodeCount", zones)
                if enabled
                else None,
                cpu_allocatable=float(allocatable[name][0]),
                cpu_requested=float(requested[name][0]),
                memory_allocatable_gib=float(allocatable[name][1] / GIB),
                memory_requested_gib=float(requested[name][1] / GIB),
                pod_range=pool_pod_range(gke_pool, default_pod_range),
                pod_block_prefix=int(block_prefix) if block_prefix else None,
                max_pods_per_node=int(max_pods) if max_pods else None,
            )
        )
    pools.sort(key=lambda p: (p.compute_class is None, p.compute_class or "", p.name))

    classes = []
    for cc in compute_classes:
        name = (cc.get("metadata") or {}).get("name", "")
        spec = cc.get("spec") or {}
        members = [p for p in pools if p.compute_class == name]
        classes.append(
            ComputeClass(
                name=name,
                priorities=[_format_priority(p) for p in spec.get("priorities", [])],
                auto_create=bool(
                    (spec.get("nodePoolAutoCreation") or {}).get("enabled")
                ),
                pools=len(members),
                nodes=sum(p.nodes for p in members),
            )
        )
    classes.sort(key=lambda c: c.name)

    # Node auto-provisioning limits count every node in the cluster.
    cluster_autoscaling = gke_cluster.get("autoscaling") or {}
    autoprovisioning = bool(cluster_autoscaling.get("enableNodeAutoprovisioning"))
    limits = []
    for limit in cluster_autoscaling.get("resourceLimits") or []:
        maximum = limit.get("maximum")
        if maximum is None:
            continue
        if limit.get("resourceType") == "cpu":
            limits.append(
                ResourceLimit("cpu", "cores", float(capacity_cpu), float(maximum))
            )
        elif limit.get("resourceType") == "memory":
            limits.append(
                ResourceLimit(
                    "memory", "GiB", float(capacity_memory / GIB), float(maximum)
                )
            )

    findings = []
    for res in limits:
        if res.utilization is not None and res.utilization >= warn_threshold:
            findings.append(
                f"Node auto-provisioning {res.resource} limit is "
                f"{res.utilization:.0%} used ({res.used:.0f}/{res.maximum:.0f} "
                f"{res.unit}); at the limit the cluster autoscaler can't add nodes"
            )
    for pool in pools:
        if pool.at_max:
            busy = (
                f", {pool.cpu_utilization:.0%} of CPU requested"
                if pool.cpu_utilization is not None
                else ""
            )
            findings.append(
                f"Node pool {pool.name} is at its autoscaling maximum "
                f"({pool.nodes}/{pool.max_nodes} nodes{busy}); it can't scale up"
            )
    control_plane = str(gke_cluster.get("currentMasterVersion", ""))
    cp_minor = _minor(control_plane)
    if cp_minor is not None:
        # GKE supports node pools up to VERSION_SKEW_MINORS minor versions
        # behind the control plane.
        at_limit, beyond = [], []
        for pool in pools:
            minor = _minor(pool.version)
            if minor is None or minor[0] != cp_minor[0]:
                continue
            skew = cp_minor[1] - minor[1]
            if skew == VERSION_SKEW_MINORS:
                at_limit.append(pool.name)
            elif skew > VERSION_SKEW_MINORS:
                beyond.append(pool.name)
        if at_limit:
            findings.append(
                f"{len(at_limit)} node pool(s) are {VERSION_SKEW_MINORS} minor versions "
                f"behind the control plane ({control_plane}), the most GKE supports; "
                "upgrade them before the control plane's next minor upgrade: "
                f"{_names(at_limit)}"
            )
        if beyond:
            findings.append(
                f"{len(beyond)} node pool(s) are more than {VERSION_SKEW_MINORS} minor "
                f"versions behind the control plane ({control_plane}), beyond what GKE "
                f"supports: {_names(beyond)}"
            )

    notes = []
    empty_legacy = [
        p.name
        for p in pools
        if p.compute_class is None
        and not p.autoprovisioned
        and p.nodes == 0
        and (p.min_nodes or 0) == 0
    ]
    if empty_legacy:
        notes.append(
            "Empty node pools outside any compute class (0 nodes, min 0), "
            f"possibly removable: {_names(empty_legacy)}"
        )

    return ClusterNodePools(
        cluster=cluster,
        control_plane_version=control_plane,
        autoprovisioning=autoprovisioning,
        resource_limits=limits,
        pools=pools,
        compute_classes=classes,
        findings=findings,
        notes=notes,
    )


def _list_json(call: Any, **kwargs: Any) -> list[Mapping[str, Any]]:
    """Call a kubernetes list API, returning raw dicts (no model deserialization)."""
    response = call(_preload_content=False, **kwargs)
    items: list[Mapping[str, Any]] = json.loads(response.data).get("items", [])
    return items


def fetch_cluster_nodepools(
    cluster: GkeClusterRef,
    kube_client: kubernetes.client.ApiClient,
    warn_threshold: float = DEFAULT_WARN_THRESHOLD,
) -> ClusterNodePools:
    """Look up node pools via GKE and nodes, pods and ComputeClasses via Kubernetes."""
    gke_cluster = (
        build_api("container", "v1")
        .projects()
        .locations()
        .clusters()
        .get(name=cluster.resource_name)
        .execute()
    )
    core = kubernetes.client.CoreV1Api(kube_client)
    nodes = _list_json(core.list_node)
    pods = _list_json(
        core.list_pod_for_all_namespaces,
        field_selector="status.phase!=Succeeded,status.phase!=Failed",
    )
    try:
        # A dict for custom objects; the client types it as object.
        response = cast(
            Mapping[str, Any],
            kubernetes.client.CustomObjectsApi(kube_client).list_cluster_custom_object(
                "cloud.google.com", "v1", "computeclasses"
            ),
        )
        compute_classes: Iterable[Mapping[str, Any]] = response.get("items", [])
    except ApiException as e:
        if e.status != 404:  # no ComputeClass CRD on this cluster
            raise
        compute_classes = []

    return build_cluster_nodepools(
        cluster,
        gke_cluster,
        nodes,
        pods,
        list(compute_classes),
        warn_threshold=warn_threshold,
    )
