from typing import Optional
from typing import Sequence

import pytest
from libsentrykube.cluster import list_clusters
from libsentrykube.cluster import list_clusters_for_customer
from libsentrykube.cluster import load_cluster_configuration
from libsentrykube.config import Config
from click import Abort

TEST_CASES = [
    pytest.param(
        "saas",
        "customer",
        "customer",
        [
            "k8s_root/services/service1",
            "k8s_root/services/service2",
            "k8s_root/services/service4",
        ],
        "gke_something-kube_us-west1-c_primary",
        id="Load cluster template",
    ),
]


@pytest.mark.parametrize(
    "silo_region, cluster_name, ret_cluster, ret_services, ret_context", TEST_CASES
)
def test_clusters_load(
    silo_region: str,
    cluster_name: Optional[str],
    ret_cluster: str,
    ret_services: Sequence[str],
    ret_context: str,
) -> None:
    """
    Test loading the cluster configuration both when we load from a directory
    containing templates and when we load an individual cluster.
    """

    conf = Config().silo_regions[silo_region]
    cluster = load_cluster_configuration(conf.k8s_config, cluster_name)

    assert cluster.name == ret_cluster

    assert set(cluster.services) == set(ret_services)
    assert cluster.services_data["context"] == ret_context


def test_fail_load() -> None:
    conf = Config().silo_regions["my_customer"]

    with pytest.raises(Abort):
        load_cluster_configuration(conf.k8s_config, "Bad parameter")


def test_list_clusters() -> None:
    conf = Config()

    clusters = list_clusters(conf)
    assert len(clusters) == 4
    names = {c.name for c in clusters}
    assert names == {"pop", "customer", "default"}


def test_list_clusters_for_customer() -> None:
    conf = Config()

    clusters = list_clusters_for_customer(conf.silo_regions["saas"].k8s_config)
    assert len(clusters) == 2
    names = {c.name for c in clusters}
    assert names == {"pop", "customer"}
