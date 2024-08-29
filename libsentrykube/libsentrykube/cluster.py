from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from typing import List
from typing import MutableSequence
from typing import Sequence

from jinja2 import Environment
from jinja2 import FileSystemLoader
from jinja2 import StrictUndefined
from jinja2 import TemplateNotFound
from libsentrykube.config import Config
from libsentrykube.config import K8sConfig
from libsentrykube.utils import die
from libsentrykube.utils import workspace_root
from yaml import safe_load


@dataclass(frozen=True)
class Cluster:
    name: str
    services: List[str]
    services_data: dict[str, Any]

    @property
    def service_names(self) -> List[str]:
        return [Path(p).name for p in self.services]


def list_clusters(config: Config) -> Sequence[Cluster]:
    """
    This method reinitializes the k8s root directory for each cluster
    as that is needed to load the cluster configuration and this
    needs to load all of them.
    """

    ret: MutableSequence[Cluster] = []

    for _, silo_config in config.silo_regions.items():
        k8s_config = silo_config.k8s_config
        ret.extend(list_clusters_for_customer(k8s_config))
    return ret


def list_clusters_for_customer(config: K8sConfig) -> Sequence[Cluster]:
    kube_config_dir = workspace_root() / config.root

    if not config.cluster_name:
        customer_dir = kube_config_dir / config.cluster_def_root
        ret = (
            [
                load_cluster_configuration(config, p.name.rsplit(".", maxsplit=1)[0])
                for p in customer_dir.iterdir()
                if (not p.name.startswith("_") and not p.is_dir())
            ]
            if customer_dir.exists()
            else []
        )
    else:
        ret = [load_cluster_configuration(config)]
    return ret


@cache
def load_cluster_configuration(config: K8sConfig, cluster_name: str) -> Cluster:
    kube_config_dir = workspace_root() / config.root
    try:
        template = Environment(
            loader=FileSystemLoader(str(kube_config_dir / config.cluster_def_root)),
            undefined=StrictUndefined,
        ).get_template(f"{cluster_name}.yaml")

        data = safe_load(template.render())
    except (FileNotFoundError, TemplateNotFound):
        die(f"Cluster '{cluster_name}' not found.")

    services = data.pop("services")

    return Cluster(cluster_name, services, data)
