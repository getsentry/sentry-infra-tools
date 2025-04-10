from libsentrykube.cluster import load_cluster_configuration, Cluster
from libsentrykube.config import Config
from libsentrykube.service import clear_service_paths
from libsentrykube.service import set_service_paths
from libsentrykube.config import K8sConfig
from typing import Tuple


def init_cluster_context(
    customer_name: str, cluster_name: str
) -> Tuple[K8sConfig, Cluster]:
    """
    This abomination is needed to reset the context of a script when we need to
    operate on multiple customers and cluster within a single script.

    `sentry-kube` is inherently stateful in its functionalities: the initializer
    of the script identifies customer and cluster and initializes all the config
    needed storing it in global variables that are accessed by multiple modules.

    This does not prevent sentry-kube to work in general as each operation is
    restricted to one customer and cluster. Though we are adding more cross
    customer operations, in those cases the state stored in global variables is
    quite dangerous as it subtly changes the behavior of several functionalities
    in a silent way depending on initialization.

    This method allows to, at least, not have to copy paste this everywhere.

    The real fix would be to remove the global variables and pass a context around.
    """
    config = Config().silo_regions
    customer_config = config[customer_name].k8s_config
    clear_service_paths()
    # If the customer has only one cluster, just use the value from config
    cluster_name = customer_config.cluster_name or cluster_name
    cluster = load_cluster_configuration(customer_config, cluster_name)
    set_service_paths(cluster.services, helm=cluster.helm_services.services)

    return (customer_config, cluster)
