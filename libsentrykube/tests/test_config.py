from libsentrykube.config import K8sConfig
from libsentrykube.config import Config
from libsentrykube.config import SiloRegion
from types import MappingProxyType


def test_config_load() -> None:
    conf = Config()

    assert conf.silo_regions == {
        "saas": SiloRegion(
            k8s_config=K8sConfig(
                root="k8s_root",
                cluster_def_root="clusters/saas",
                cluster_name=None,
                materialized_manifests="rendered_services",
            ),
            sentry_region="us",
            service_monitors=MappingProxyType({}),
        ),
        "my_customer": SiloRegion(
            k8s_config=K8sConfig(
                root="k8s_root",
                cluster_def_root="clusters/my_customer",
                cluster_name=None,
                materialized_manifests="rendered_services",
            ),
            sentry_region="st-my_customer",
            service_monitors=MappingProxyType({}),
        ),
        "my_other_customer": SiloRegion(
            k8s_config=K8sConfig(
                root="k8s_root",
                cluster_def_root="clusters/my_other_customer",
                cluster_name=None,
                materialized_manifests="rendered_services",
            ),
            sentry_region="st-my_other_customer",
            service_monitors=MappingProxyType({}),
        ),
        "region2": SiloRegion(
            k8s_config=K8sConfig(
                root="k8s",
                cluster_def_root="clusters/region2",
                cluster_name=None,
                materialized_manifests="materialized_manifests/region2",
            ),
            sentry_region="region2",
            service_monitors=MappingProxyType({}),
        ),
        "region3": SiloRegion(
            k8s_config=K8sConfig(
                root="k8s",
                cluster_def_root="clusters/region3",
                cluster_name=None,
                materialized_manifests="materialized_manifests/region3",
            ),
            sentry_region="region3",
            service_monitors=MappingProxyType({}),
        ),
    }
