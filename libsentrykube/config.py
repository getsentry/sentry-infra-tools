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
    # Same thing as `materialized_manifests`, but for helm values
    materialized_helm_values: str

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

            materialized_helm_values = (
                conf["materialized_helm_values"]
                if "materialized_helm_values" in conf
                else materialized_manifests.replace(
                    "materialized_manifests", "materialized_helm_values"
                )
            )

        return K8sConfig(
            root=root,
            cluster_def_root=cluster_def_root,
            cluster_name=cluster_name,
            materialized_manifests=materialized_manifests,
            materialized_helm_values=materialized_helm_values,
        )


@dataclass(frozen=True)
class SiloRegion:
    k8s_config: K8sConfig
    sentry_region: str
    service_monitors: MappingProxyType[str, list[int]]

    @classmethod
    def from_conf(
        cls,
        region_name: str,
        silo_regions_conf: Mapping[str, Any] | None,
    ) -> SiloRegion:
        if silo_regions_conf is not None:
            name_from_conf = silo_regions_conf.get("sentry_region", region_name)
            k8s_config = silo_regions_conf.get("k8s", None)
            service_monitors = silo_regions_conf.get("service_monitors", {})
        else:
            name_from_conf = region_name
            k8s_config = None
            service_monitors = {}
        return SiloRegion(
            k8s_config=K8sConfig.from_conf(name_from_conf, k8s_config),
            sentry_region=name_from_conf,
            service_monitors=service_monitors,
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
                region_name: SiloRegion.from_conf(region_name, region_conf)
                for region_name, region_conf in configuration["silo_regions"].items()
            }

        self.silo_regions: Mapping[str, SiloRegion] = silo_regions

    @cache
    def get_regions(self) -> Sequence[str]:
        """
        Returns list of regions
        """
        return list(self.silo_regions.keys())
