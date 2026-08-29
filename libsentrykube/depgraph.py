from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Set

logger = logging.getLogger(__name__)


_lock = threading.Lock()
_current_service: threading.local = threading.local()
_edges: Dict[str, Set[str]] = defaultdict(set)


def start_tracking(service_name: str) -> None:
    """Begin tracking dependencies for a service being rendered."""
    _current_service.name = service_name


def stop_tracking() -> None:
    """Stop tracking the current service."""
    _current_service.name = None


def record_dependency(target_service: str) -> None:
    """
    Record that the currently-tracked service depends on target_service.
    No-op if tracking is not active or if target is the current service.
    """
    source = getattr(_current_service, "name", None)
    if source is None or source == target_service:
        return
    with _lock:
        _edges[source].add(target_service)


def get_dependencies() -> Dict[str, Set[str]]:
    """Return the forward dependency graph: {service -> set of services it depends on}."""
    with _lock:
        return {k: set(v) for k, v in _edges.items()}


def get_reverse_dependencies() -> Dict[str, Set[str]]:
    """Return the reverse graph: {service -> set of services that depend on it}."""
    with _lock:
        rev: Dict[str, Set[str]] = defaultdict(set)
        for source, targets in _edges.items():
            for target in targets:
                rev[target].add(source)
        return dict(rev)


def reset_tracking() -> None:
    """Clear all tracked state. Useful for tests."""
    with _lock:
        _edges.clear()
    _current_service.name = None


@dataclass
class DependencyGraph:
    """
    Immutable snapshot of the service dependency graph.
    Supports serialization to/from dict for caching as JSON.
    """

    _forward: Dict[str, Set[str]] = field(default_factory=dict)
    _reverse: Dict[str, Set[str]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._reverse = defaultdict(set)
        for source, targets in self._forward.items():
            for target in targets:
                self._reverse[target].add(source)
        self._reverse = dict(self._reverse)

    def dependencies_of(self, service: str) -> Set[str]:
        """Services that `service` depends on."""
        return set(self._forward.get(service, set()))

    def dependents_of(self, service: str) -> Set[str]:
        """Services that depend on `service`."""
        return set(self._reverse.get(service, set()))

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict with sorted lists."""
        return {
            "dependencies": {k: sorted(v) for k, v in sorted(self._forward.items())},
            "reverse_dependencies": {
                k: sorted(v) for k, v in sorted(self._reverse.items())
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> DependencyGraph:
        """Deserialize from a dict (as produced by to_dict)."""
        forward = {k: set(v) for k, v in data["dependencies"].items()}
        return cls(forward)


def build_dependency_graph(
    stage: Optional[str] = None,
) -> DependencyGraph:
    """
    Build the full dependency graph by rendering all services across
    all customers/clusters and collecting values_of() calls.
    """
    from libsentrykube.cluster import list_clusters_for_customer
    from libsentrykube.config import Config, SiloRegion
    from libsentrykube.context import init_cluster_context
    from libsentrykube.kube import render_templates
    from libsentrykube.service import get_service_names

    os.environ["KUBERNETES_OFFLINE"] = "1"
    reset_tracking()

    config = Config()
    regions: Mapping[str, SiloRegion]
    if stage is not None:
        region_names = config.get_regions(stage)
        regions = {name: config.silo_regions[name] for name in region_names}
    else:
        regions = config.silo_regions

    for customer_name, conf in regions.items():
        clusters = list_clusters_for_customer(conf.k8s_config)
        for c in clusters:
            cluster_name = c.name
            init_cluster_context(customer_name, cluster_name)

            for service_name in get_service_names():
                try:
                    render_templates(customer_name, service_name, cluster_name)
                except Exception:
                    logger.warning(
                        "Failed to render %s/%s/%s, skipping",
                        customer_name,
                        cluster_name,
                        service_name,
                        exc_info=True,
                    )

    graph = DependencyGraph(get_dependencies())
    reset_tracking()
    return graph
