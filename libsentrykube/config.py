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
    def from_conf(cls, region_name: str, conf: Mapping[str, Any] | None) -> K8sConfig:
        DEFAULT_CONFIG_ROOT = "k8s"
        DEFAULT_CONFIG_CLUSTER_DEF_ROOT = f"clusters/{region_name}"
        DEFAULT_CONFIG_CLUSTER_NAME = None
        DEFAULT_CONFIG_MATERIALIZED_MANIFESTS = f"materialized_manifests/{region_name}"

        if conf is None:
            root = DEFAULT_CONFIG_ROOT
            cluster_def_root = DEFAULT_CONFIG_CLUSTER_DEF_ROOT
            cluster_name = DEFAULT_CONFIG_CLUSTER_NAME
            materialized_manifests = DEFAULT_CONFIG_MATERIALIZED_MANIFESTS

        else:
            root = conf["root"] if "root" in conf else DEFAULT_CONFIG_ROOT

            cluster_def_root = (
                conf["cluster_def_root"]
                if "cluster_def_root" in conf
                else DEFAULT_CONFIG_CLUSTER_DEF_ROOT
            )

            cluster_name = (
                conf.get("cluster_name")
                if "cluster_name" in conf
                else DEFAULT_CONFIG_CLUSTER_NAME
            )

            materialized_manifests = (
                conf["materialized_manifests"]
                if "materialized_manifests" in conf
                else DEFAULT_CONFIG_MATERIALIZED_MANIFESTS
            )

        return K8sConfig(
            root=root,
            cluster_def_root=cluster_def_root,
            cluster_name=cluster_name,
            materialized_manifests=materialized_manifests,
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
        cls,
        region_name: str,
        silo_regions_conf: Mapping[str, Any],
        sites: Mapping[str, Site],
    ) -> SiloRegion:
        name_from_conf = silo_regions_conf.get("sentry_region", region_name)
        bastion_config = silo_regions_conf["bastion"]
        assert (
            bastion_config["site"] in sites
        ), f"Undefined site {bastion_config['site']}"
        k8s_config = silo_regions_conf["k8s"] if "k8s" in silo_regions_conf else None
        return SiloRegion(
            bastion_spawner_endpoint=bastion_config["spawner_endpoint"],
            bastion_site=sites[bastion_config["site"]],
            k8s_config=K8sConfig.from_conf(name_from_conf, k8s_config),
            sentry_region=name_from_conf,
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
                region_name: SiloRegion.from_conf(region_name, region_conf, sites)
                for region_name, region_conf in configuration["silo_regions"].items()
            }

        self.sites: Mapping[str, Site] = sites
        self.silo_regions: Mapping[str, SiloRegion] = silo_regions

    @cache
    def get_customers(self) -> Sequence[str]:
        """
        Returns list of customers
        """
        return list(self.silo_regions.keys())
