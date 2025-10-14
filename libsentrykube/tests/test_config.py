from libsentrykube.config import K8sConfig
from libsentrykube.config import Config
from libsentrykube.config import SiloRegion
from types import MappingProxyType


def test_config_load() -> None:
    conf = Config()

    assert conf.silo_regions == {
        "saas": SiloRegion(
            aliases=["saasalias"],
            k8s_config=K8sConfig(
                root="k8s_root",
                cluster_def_root="clusters/saas",
                cluster_name=None,
                materialized_manifests="rendered_services",
                materialized_helm_values="rendered_helm_values",
            ),
            sentry_region="us",
            service_monitors=MappingProxyType({}),
        ),
        "my_customer": SiloRegion(
            aliases=[],
            k8s_config=K8sConfig(
                root="k8s_root",
                cluster_def_root="clusters/my_customer",
                cluster_name=None,
                materialized_manifests="rendered_services",
                materialized_helm_values="rendered_helm_values",
            ),
            sentry_region="st-my_customer",
            service_monitors=MappingProxyType({}),
        ),
        "my_other_customer": SiloRegion(
            aliases=[],
            k8s_config=K8sConfig(
                root="k8s_root",
                cluster_def_root="clusters/my_other_customer",
                cluster_name=None,
                materialized_manifests="rendered_services",
                materialized_helm_values="rendered_helm_values",
            ),
            sentry_region="st-my_other_customer",
            service_monitors=MappingProxyType({}),
        ),
    }
