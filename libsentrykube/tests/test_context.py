from libsentrykube.context import init_cluster_context
from libsentrykube.service import get_service_names


def test_init() -> None:
    config, cluster = init_cluster_context("saas", "customer")
    assert config.cluster_def_root == "clusters/saas"
    assert set(get_service_names()) == {
        "service1",
        "service2",
        "service4",
    }
    assert cluster.name == "customer"
    assert set(cluster.services) == {
        "k8s_root/services/service1",
        "k8s_root/services/service2",
        "k8s_root/services/service4",
    }

    # re-init
    config, cluster = init_cluster_context("my_customer", "default")
    assert config.cluster_def_root == "clusters/my_customer"
    assert set(get_service_names()) == {
        "service1",
        "service2",
        "service3",
    }
    assert cluster.name == "default"
    assert set(cluster.services) == {
        "k8s_root/services/service1",
        "k8s_root/services/service2",
        "k8s_root/services/service3",
    }
