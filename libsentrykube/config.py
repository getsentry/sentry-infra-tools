from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, ClassVar, Mapping, Optional, Sequence
from types import MappingProxyType
from functools import cache

from yaml import SafeLoader, load

from libsentrykube.utils import workspace_root

DEFAULT_CONFIG = "cli_config/configuration.yaml"


@dataclass(frozen=True)
class Site:
    _all: ClassVar[dict[str, Site]] = {}
    name: str
    region: str
    zone: str
    network: str = ""
    subnetwork: str = ""

    def __post_init__(self) -> None:
        self._all[self.name] = self

    @classmethod
    def get(cls, name: str) -> Site:
        return cls._all[name]

    @classmethod
    def names(cls) -> Sequence[str]:
        return [k for k in cls._all.keys()]

    @classmethod
    def from_conf(cls, conf: Mapping[str, str]) -> Site:
        return Site(
            name=conf["name"],
            region=conf["region"],
            zone=conf["zone"],
            network=conf.get("network", ""),
            subnetwork=conf.get("subnetwork", ""),
        )


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
    bastion_spawner_endpoint: str
    bastion_site: Site
    k8s_config: K8sConfig
    sentry_region: str
    service_monitors: MappingProxyType[str, list[int]]

    @classmethod
    def from_conf(
        cls, silo_regions_conf: Mapping[str, Any], sites: Mapping[str, Site]
    ) -> SiloRegion:
        bastion_config = silo_regions_conf["bastion"]
        assert (
            bastion_config["site"] in sites
        ), f"Undefined site {bastion_config['site']}"
        k8s_config = silo_regions_conf["k8s"]
        return SiloRegion(
            bastion_spawner_endpoint=bastion_config["spawner_endpoint"],
            bastion_site=sites[bastion_config["site"]],
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

            assert "sites" in configuration, "sites entry not present in the config"
            sites = {
                name: Site.from_conf(conf)
                for name, conf in configuration["sites"].items()
            }

            assert (
                "silo_regions" in configuration
            ), "silo_regions entry not present in the config"
            silo_regions = {
                name: SiloRegion.from_conf(conf, sites)
                for name, conf in configuration["silo_regions"].items()
            }

        self.sites: Mapping[str, Site] = sites
        self.silo_regions: Mapping[str, SiloRegion] = silo_regions

    @cache
    def get_customers(self) -> Sequence[str]:
        """
        Returns list of customers
        """
        return list(self.silo_regions.keys())
