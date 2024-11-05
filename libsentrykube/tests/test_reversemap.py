from pathlib import Path
from typing import Optional
from typing import Sequence
from typing import Set

import pytest
from libsentrykube.reversemap import build_index
from libsentrykube.reversemap import merge_references
from libsentrykube.reversemap import ResourceReference
from libsentrykube.reversemap import TrieNode

TEST_CASES = [
    pytest.param([], Path("mypath", "mysubpath"), Path(), id="Empty trie"),
    pytest.param(
        [Path("k8s", "service")],
        Path("mypath", "mysubpath"),
        None,
        id="Path outside of the trie",
    ),
    pytest.param([Path("k8s", "service")], Path("k8s"), None, id="Incomplete path"),
    pytest.param(
        [Path("k8s", "service")],
        Path("k8s", "service"),
        Path("k8s", "service"),
        id="Contains exact path",
    ),
    pytest.param(
        [Path("k8s", "service")],
        Path("k8s", "service", "snuba"),
        Path("k8s", "service"),
        id="Contains subpath",
    ),
    pytest.param(
        [Path("k8s", "service", "snuba")],
        Path("k8s", "service"),
        None,
        id="Incomplete path - multiple levels",
    ),
    pytest.param(
        [
            Path("k8s", "services", "snuba"),
            Path("k8s", "services", "getsentry"),
            Path("k8s", "services", "symbolicator"),
            Path("k8s", "clusters"),
        ],
        Path("k8s", "services", "snuba", "deployment.yaml"),
        Path("k8s", "services", "snuba"),
        id="Multi level, subpath present",
    ),
    pytest.param(
        [
            Path("k8s", "services", "snuba"),
            Path("k8s", "services", "getsentry"),
            Path("k8s", "services", "symbolicator"),
            Path("k8s", "clusters"),
        ],
        Path("k8s", "services", "snuba"),
        Path("k8s", "services", "snuba"),
        id="Multi level, exact path",
    ),
]


@pytest.mark.parametrize("content, queried_path, expected_result", TEST_CASES)
def test_trie(
    content: Sequence[Path], queried_path: Path, expected_result: Optional[Path]
) -> None:
    trie = TrieNode(None, {})
    for p in content:
        trie.add_descendents(p)

    ret = trie.longest_subpath(queried_path)
    assert ret == expected_result


TRIE_TEST_CASES = [
    pytest.param(
        Path("k8s_root", "services", "service4", "deployment.yaml"),
        {ResourceReference("saas", "customer", "service4")},
        id="One existing service in one cluster",
    ),
    pytest.param(
        Path("k8s_root", "services", "service1", "deployment.yaml"),
        {
            ResourceReference(
                customer_name="my_customer",
                cluster_name="default",
                service_name="service1",
            ),
            ResourceReference(
                customer_name="my_other_customer",
                cluster_name="default",
                service_name="service1",
            ),
            ResourceReference("saas", "customer", "service1"),
            ResourceReference("saas", "pop", "service1"),
        },
        id="One existing service in four clusters",
    ),
    pytest.param(
        Path("k8s_root", "services", "service1"),
        {
            ResourceReference(
                customer_name="my_customer",
                cluster_name="default",
                service_name="service1",
            ),
            ResourceReference(
                customer_name="my_other_customer",
                cluster_name="default",
                service_name="service1",
            ),
            ResourceReference("saas", "customer", "service1"),
            ResourceReference("saas", "pop", "service1"),
        },
        id="One existing service in four clusters, exact path",
    ),
    pytest.param(
        Path("k8s_root", "services", "service3", "deployment.yaml"),
        {
            ResourceReference(
                customer_name="my_customer",
                cluster_name="default",
                service_name="service3",
            ),
            ResourceReference(
                customer_name="saas", cluster_name="pop", service_name="service3"
            ),
        },
        id="One symlinked service in multiple clusters",
    ),
]


@pytest.mark.parametrize("requested_path, result", TRIE_TEST_CASES)
def test_resource_index(requested_path: Path, result: Set[ResourceReference]) -> None:
    index = build_index()
    assert index.get_resources_for_path(requested_path) == result


def test_merge_references() -> None:
    references = {
        ResourceReference("customer1", "cluster1", "service1"),
        ResourceReference("customer1", "cluster2", "service1"),
        ResourceReference("customer1", "cluster1", "service2"),
        ResourceReference("customer1", "cluster1", None),
    }
    merged = merge_references(references)

    assert merged == {
        ResourceReference("customer1", "cluster2", "service1"),
        ResourceReference("customer1", "cluster1", None),
    }
