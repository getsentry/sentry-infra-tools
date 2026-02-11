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
                service_class="multi-tenant",
            ),
            sentry_region="us",
            service_monitors=MappingProxyType({}),
            stage="production",
        ),
        "my_customer": SiloRegion(
            aliases=[],
            k8s_config=K8sConfig(
                root="k8s_root",
                cluster_def_root="clusters/my_customer",
                cluster_name=None,
                materialized_manifests="rendered_services",
                materialized_helm_values="rendered_helm_values",
                service_class="single-tenant",
            ),
            sentry_region="st-my_customer",
            service_monitors=MappingProxyType({}),
            stage="staging",
        ),
        "my_other_customer": SiloRegion(
            aliases=[],
            k8s_config=K8sConfig(
                root="k8s_root",
                cluster_def_root="clusters/my_other_customer",
                cluster_name=None,
                materialized_manifests="rendered_services",
                materialized_helm_values="rendered_helm_values",
                service_class="single-tenant",
            ),
            sentry_region="st-my_other_customer",
            service_monitors=MappingProxyType({}),
            stage="production",
        ),
    }


def test_get_regions_by_stage() -> None:
    conf = Config()

    # Test filtering by production stage
    production_regions = conf.get_regions(stage="production")
    assert set(production_regions) == {"saas", "my_other_customer"}

    # Test filtering by staging stage
    staging_regions = conf.get_regions(stage="staging")
    assert staging_regions == ["my_customer"]

    # Test getting all regions without stage filter
    all_regions = conf.get_regions()
    assert set(all_regions) == {"saas", "my_customer", "my_other_customer"}
