import pytest

from libsentrykube.config import Config
from libsentrykube.customer import get_region_config, load_customer_data


def test_config_load() -> None:
    """
    Test loading a configuration both through a single cluster config
    and through a multi cluster config.
    """

    config = Config()

    conf = load_customer_data(
        config, customer_name="my_customer", cluster_name="default"
    )

    assert conf["context"] == "gke_st-kube_us-west1-c_primary"

    conf = load_customer_data(config, customer_name="saas", cluster_name="customer")

    assert conf["context"] == "gke_something-kube_us-west1-c_primary"


@pytest.mark.parametrize("name", ["saas", "saasalias"])
def test_get_region_config_resolves_aliases(name: str) -> None:
    """
    Aliases resolve to the canonical region name before anything reads
    region_overrides, so loaders like get_service_flags only ever see
    canonical names.
    """
    region_name, region_config = get_region_config(Config(), name)

    assert region_name == "saas"
    assert region_config.aliases == ["saasalias"]


def test_get_region_config_rejects_unknown_region() -> None:
    with pytest.raises(ValueError, match="'nope' not found"):
        get_region_config(Config(), "nope")
