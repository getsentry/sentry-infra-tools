from libsentrykube.config import Config
from libsentrykube.customer import load_customer_data


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
