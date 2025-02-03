from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence
from types import MappingProxyType
from functools import cache

from yaml import SafeLoader, load

from libsentrykube.utils import workspace_root

DEFAULT_CONFIG = "cli_config/configuration.yaml"


@dataclass(frozen=True)
class K8sConfig:
    """
    Represents the configuration to access Kuberentes clusters on
    a silo-region. Each of these object can represent one single
    cluster (like for single tenants) or a directory containing
    multiple cluster configs like for saas.
    """

    # The root directory where all the kuberentes config is
    root: str
    # The root directory where all the cluster config files
    # are
    cluster_def_root: str
    # The cluster name if the configuration references one
    # single cluster.
    cluster_name: Optional[str]
    # Contains the path (relative to the k8s root) where we
    # materialize the rendered manifest. Each cluster is a
    # subdirectory in this directory.
    materialized_manifests: str

    @classmethod
    def from_conf(cls, conf: Mapping[str, Any]) -> K8sConfig:
        return K8sConfig(
            root=str(conf["root"]),
            cluster_def_root=str(conf["cluster_def_root"]),
            cluster_name=str(conf.get("cluster_name"))
            if "cluster_name" in conf
            else None,
            materialized_manifests=str(conf["materialized_manifests"]),
        )


@dataclass(frozen=True)
class SiloRegion:
    k8s_config: K8sConfig
    sentry_region: str
    service_monitors: MappingProxyType[str, list[int]]

    @classmethod
    def from_conf(cls, silo_regions_conf: Mapping[str, Any]) -> SiloRegion:
        k8s_config = silo_regions_conf["k8s"]
        return SiloRegion(
            k8s_config=K8sConfig.from_conf(k8s_config),
            sentry_region=silo_regions_conf.get("sentry_region", "unknown"),
            service_monitors=silo_regions_conf.get("service_monitors", {}),
        )


class Config:
    def __init__(self) -> None:
        config_file_name = os.environ.get(
            "SENTRY_KUBE_CONFIG_FILE", workspace_root() / DEFAULT_CONFIG
        )

        with open(config_file_name) as file:
            configuration = load(file, Loader=SafeLoader)

            assert "silo_regions" in configuration, (
                "silo_regions entry not present in the config"
            )
            silo_regions = {
                name: SiloRegion.from_conf(conf)
                for name, conf in configuration["silo_regions"].items()
            }

        self.silo_regions: Mapping[str, SiloRegion] = silo_regions

    @cache
    def get_customers(self) -> Sequence[str]:
        """
        Returns list of customers
        """
        return list(self.silo_regions.keys())
