from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping, NamedTuple, Optional, Sequence, Set, Tuple

from libsentrykube.cluster import list_clusters_for_customer
from libsentrykube.config import Config
from libsentrykube.service import (
    clear_service_paths,
    get_service_names,
    get_service_path,
    set_service_paths,
)
from libsentrykube.utils import workspace_root


class ResourceReference(NamedTuple):
    """
    Identifies a resource in the Kubernetes definition
    configuration.

    Mostly used to identify clusters/services to render
    when collecting the services that need to be re-rendered
    after a code change.
    """

    customer_name: str
    cluster_name: str
    # None if this reference points to a whole cluster
    service_name: Optional[str]


def merge_references(resources: Set[ResourceReference]) -> Set[ResourceReference]:
    """
    Reduces a set of ResourceReference to remove overlapping resources.

    A ResourceReference can reference a specific service in a clsuter or
    the entire cluster. This method removes from a set of ResourceReferences
    specific services if the set also contains a reference to the whole
    cluster.
    """

    full_clusters: Set[Tuple[str, str]] = {
        (c.customer_name, c.cluster_name) for c in resources if c.service_name is None
    }

    ret: Set[ResourceReference] = set()
    for res in resources:
        if res.service_name is None or (
            res.service_name is not None
            and (res.customer_name, res.cluster_name) not in full_clusters
        ):
            ret.add(res)

    return ret


def extract_clusters(resources: Set[ResourceReference]) -> Set[ResourceReference]:
    """
    Reduces a set of ResourceReference to replace individual services with
    the cluster that contains them.
    """

    ret = set()
    for r in resources:
        if r.service_name is None:
            ret.add(r)
        else:
            ret.add(
                ResourceReference(
                    customer_name=r.customer_name,
                    cluster_name=r.cluster_name,
                    service_name=None,
                )
            )

    return ret


@dataclass()
class TrieNode:
    """
    Node in a Trie data structure that containins Path objects.
    This is used to create an index of file system paths.

    This data structure is useful as the index contains
    subpaths of those we try to resolve.
    The most common operation, given a full path, is to
    resolve the longest subpath contained in the index
    that starts with the same root.
    """

    name: Optional[str]
    descendents: MutableMapping[str, TrieNode]

    def add_descendents(self, path: Path) -> None:
        """
        Adds a path to the Trie starting at the current node.
        """

        if len(path.parts) == 0:
            return
        prefix = path.parts[0]
        if prefix not in self.descendents:
            self.descendents[prefix] = TrieNode(prefix, {})
        next_node = self.descendents[prefix]
        next_node.add_descendents(path.relative_to(Path(prefix)))

    def longest_subpath(self, path: Path) -> Optional[Path]:
        """
        Finds the longest subpath of the `path` argument that
        starts with the same root.
        """

        def iterate_path(
            prefix: Sequence[str], suffix: Sequence[str], node: TrieNode
        ) -> Optional[Path]:
            if len(node.descendents) == 0:
                return Path(*prefix)
            if not suffix or suffix[0] not in node.descendents:
                return None
            next_node = node.descendents[suffix[0]]
            prefix = [*prefix, *[suffix[0]]]
            return iterate_path(prefix, suffix[1:], next_node)

        return iterate_path([], path.parts, self)


@dataclass()
class ResourceIndex:
    """
    Reverse index between paths and k8s resources (customer/cluster/service).

    The main usage of this class is, given a full path of a k8s manifest file,
    to resolve all the (Customer, Cluster, Service) tuple that reference such
    file.

    The common scenario is to resolve the list of clusters and services to
    re-render based on the changeset of a git commit.

    It contains two data structures to achieve this: a mapping between
    service definition path and resources, and a Trie to resolve the service
    manifest directory given a full file path.

    The index contains all the services and clusters for all customers.
    Services can be shared between clusters and service manifest (templates) can
    be shared between customers
    """

    path_trie: TrieNode
    index: Mapping[Path, Set[ResourceReference]]

    def get_resources_for_path(self, path: Path) -> Set[ResourceReference]:
        """
        Returns all the resources (customer, cluster, service_name) that reference
        the file path provided as argument.
        """

        index_path = self.path_trie.longest_subpath(path)
        if index_path is None:
            return set()
        else:
            return self.index[index_path]


def build_index() -> ResourceIndex:
    """
    Scans the entire k8s configuration and generates the reverse index `ResourceIndex`.

    For each customer we add both the service paths and the cluster definition path.
    The cluster definition path are needed because a change to a cluster definition
    causes re-rendering all services in such cluster.
    """

    partial_index: MutableMapping[Path, Set[ResourceReference]] = defaultdict(set)
    trie = TrieNode(None, {})

    config = Config().silo_regions
    for customer_name, conf in config.items():
        clusters = list_clusters_for_customer(conf.k8s_config)
        clusters_root = Path(conf.k8s_config.root) / conf.k8s_config.cluster_def_root
        for c in clusters:
            clear_service_paths()
            cluster_name = c.name
            partial_index[clusters_root].add(
                ResourceReference(customer_name, cluster_name, None)
            )
            trie.add_descendents(clusters_root)

            set_service_paths(c.services)
            for service_name in get_service_names():
                full_path = get_service_path(service_name).resolve()
                git_relative_path = full_path.relative_to(workspace_root())
                partial_index[git_relative_path].add(
                    ResourceReference(customer_name, cluster_name, service_name)
                )
                trie.add_descendents(git_relative_path)

    return ResourceIndex(trie, partial_index)


def build_helm_index() -> ResourceIndex:
    """
    Same as `build_index` but for helm services.
    """

    partial_index: MutableMapping[Path, Set[ResourceReference]] = defaultdict(set)
    trie = TrieNode(None, {})

    config = Config().silo_regions
    for customer_name, conf in config.items():
        clusters = list_clusters_for_customer(conf.k8s_config)
        clusters_root = Path(conf.k8s_config.root) / conf.k8s_config.cluster_def_root
        for c in clusters:
            clear_service_paths()
            cluster_name = c.name
            partial_index[clusters_root].add(
                ResourceReference(customer_name, cluster_name, None)
            )
            trie.add_descendents(clusters_root)

            set_service_paths([], helm=c.helm_services.services)
            for service_name in get_service_names(namespace="helm"):
                full_path = get_service_path(service_name, namespace="helm").resolve()
                git_relative_path = full_path.relative_to(workspace_root())
                partial_index[git_relative_path].add(
                    ResourceReference(customer_name, cluster_name, service_name)
                )
                trie.add_descendents(git_relative_path)

    return ResourceIndex(trie, partial_index)
