from libsentrykube.config import K8sConfig
from libsentrykube.config import Config
from libsentrykube.config import SiloRegion
from libsentrykube.config import Site
from types import MappingProxyType


def test_config_load() -> None:
    conf = Config()

    assert conf.sites == {
        "saas_us": Site(
            name="us",
            region="us-central1",
            zone="b",
            network="global/networks/sentry",
            subnetwork="regions/us-central1/subnetworks/sentry-default",
        )
    }

    assert conf.silo_regions == {
        "saas": SiloRegion(
            bastion_spawner_endpoint="https://bastion-spawner.app",
            bastion_site=Site(
                name="us",
                region="us-central1",
                zone="b",
                network="global/networks/sentry",
                subnetwork="regions/us-central1/subnetworks/sentry-default",
            ),
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
            bastion_spawner_endpoint="https://bastion-spawner2.app",
            bastion_site=Site(
                name="us",
                region="us-central1",
                zone="b",
                network="global/networks/sentry",
                subnetwork="regions/us-central1/subnetworks/sentry-default",
            ),
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
            bastion_spawner_endpoint="https://bastion-spawner3.app",
            bastion_site=Site(
                name="us",
                region="us-central1",
                zone="b",
                network="global/networks/sentry",
                subnetwork="regions/us-central1/subnetworks/sentry-default",
            ),
            k8s_config=K8sConfig(
                root="k8s_root",
                cluster_def_root="clusters/my_other_customer",
                cluster_name=None,
                materialized_manifests="rendered_services",
            ),
            sentry_region="st-my_other_customer",
            service_monitors=MappingProxyType({}),
        ),
    }
