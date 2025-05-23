import copy

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
from libsentrykube.helm import HelmData
from libsentrykube.utils import deep_merge_dict
from libsentrykube.utils import die
from libsentrykube.utils import workspace_root
from yaml import safe_load


@dataclass(frozen=True)
class Cluster:
    name: str
    services: List[str]
    services_data: dict[str, Any]
    helm_services: HelmData

    @property
    def service_names(self) -> List[str]:
        return [Path(p).name for p in self.services]

    @property
    def helm_service_names(self) -> List[str]:
        return self.helm_services.service_names


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
    except FileNotFoundError:
        die(f"Cluster '{cluster_name}' not found.")
    except TemplateNotFound as e:
        print(e)
        die("Failed to render template.")

    services = data.pop("services")
    helm_spec = data.pop("helm", {})
    helm_data = copy.deepcopy(data)
    deep_merge_dict(helm_data, helm_spec.get("values", {}))
    helm_svclist = []
    helm_svcdata: dict[str, dict[str, Any]] = {}
    for svc in helm_spec.get("services", []):
        if isinstance(svc, str):
            helm_svclist.append(svc)
            helm_svcdata[svc] = {}
            continue
        svc_key = svc.get("path")
        if not svc_key:
            continue
        helm_svclist.append(svc_key)
        helm_svcdata[svc_key] = svc.get("values", {})

    return Cluster(
        cluster_name, services, data, HelmData(helm_svclist, helm_data, helm_svcdata)
    )
