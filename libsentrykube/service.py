import os
from enum import Enum
from pathlib import Path
from typing import List, Mapping, Any, Self, Optional

import click
import yaml
from kubernetes.client.rest import ApiException
from kubernetes.client import AppsV1Api

from collections import OrderedDict
from libsentrykube.config import Config
from libsentrykube.customer import load_customer_data, load_region_helm_data
from libsentrykube.utils import (
    deep_merge_dict,
    kube_extract_namespace,
    kube_get_client,
    workspace_root,
)

_services: dict[str | None, dict[str, Any]] = {None: OrderedDict()}

KUBE_API_TIMEOUT_DEFAULT: int = 3
KUBE_API_TIMEOUT_ENV_NAME: str = "SK_KUBE_TIMEOUT"


class CustomerTooOftenDefinedException(Exception):
    def __init__(self, message):
        super().__init__(message)


class MergeConfig:
    class MergeStrategy(Enum):
        REJECT = "reject"
        OVERWRITE = "overwrite"
        APPEND = "append"

    @classmethod
    def load(cls, reader) -> dict[str, Any]:
        return yaml.safe_load(reader)

    @classmethod
    def from_file(cls, filename) -> Optional[Self]:
        """
        Loads a MergeConfig from a file

        Example `merge.yaml`:
        ```
        default: reject
        paths:
            worker_groups: append
        ```
        """
        try:
            with open(filename) as f:
                body = cls.load(f)
                return cls(body)

        except FileNotFoundError:
            return None

    @classmethod
    def defaults(cls) -> Self:
        return cls({})

    def __init__(self, body: dict[str, Any]):
        self.default = MergeConfig.MergeStrategy(body.get("default", "reject"))
        self.paths: dict[str, MergeConfig.MergeStrategy] = {
            path: MergeConfig.MergeStrategy(mode)
            for path, mode in body.get("paths", dict()).items()
        }


def assert_customer_is_defined_at_most_once(
    service_name: str,
    customer_name: str,
    external: bool = False,
    namespace: str | None = None,
) -> None:
    """
    Make sure that a customer directory is only defined a single time in a service.
    Because an explicit cluster_name.yaml is not necessary we will just check for the customer directory.

    This method exists to prevent different configurations for a single customer using different override methods.
    """
    if external:
        service_regions_path = workspace_root() / service_name
    else:
        service_regions_path = get_service_path(service_name, namespace=namespace)

    paths: List[Path] = []
    paths.extend(service_regions_path.glob(f"region_overrides/{customer_name}"))
    paths.extend(service_regions_path.glob(f"region_overrides/*/{customer_name}"))

    if len(paths) > 1:
        raise CustomerTooOftenDefinedException(
            f"Expected a single '{customer_name}' directory in service but found {len(paths)}"
        )


def set_service_paths(service_paths: List[str], **namespaced_service_paths: List[str]):
    targets = [
        (None, service_paths),
        *[(key, val) for key, val in namespaced_service_paths.items()],
    ]
    for collector_key, input_paths in targets:
        collector = _services[collector_key] = _services.get(
            collector_key, OrderedDict()
        )
        for service_path in input_paths:
            # Globbing is supported
            paths = list(workspace_root().glob(service_path))
            if not paths:
                raise Exception(f"Invalid service path: {service_path}")
            for path in paths:
                if not path.is_dir():
                    continue

                name = path.name
                if name in collector:
                    raise Exception(f"Found duplicate service: {path}")

                if name == "customers":  # well-known; not an actual service
                    continue

                collector[name] = path


def clear_service_paths() -> None:
    """
    This is a hack to allow features that scan all services for all clusters
    to reset the repo while we still have global states around in this
    library.
    """
    for key in _services.keys():
        _services[key].clear()


def get_service_path(service_name, namespace: str | None = None) -> Path:
    if namespace not in _services:
        click.echo(f"Service namespace named {namespace} was not found.", err=True)
    if service_name not in _services[namespace]:
        click.echo(f"Service named {service_name} was not found.", err=True)
        raise click.Abort()
    return _services[namespace][service_name]


def get_service_names(namespace: str | None = None) -> List[str]:
    return [s for s in _services.get(namespace, {}).keys()]


# TODO: Do this with OrderedDicts that preserve ordering from the source YAML
def merge_values_files_no_conflict(
    base: dict, new: dict, file_name: str, merge_config: MergeConfig
) -> dict:
    """
    All values files in each level of overriding should be flatly combined together.
    There should be no key conflics
    Example (these two files should have no key conflicts):
    _values.yaml
        workers:
            rabbit-worker-1:
                data

    _values_consumer.yaml
        consumer_groups:
            consumer-1:
                data
    """
    # TODO: make it work with nesting / recursively
    for key, new_value in new.items():
        if key in base:
            mode = merge_config.default
            if key in merge_config.paths.keys():
                mode = merge_config.paths[key]

            if mode == MergeConfig.MergeStrategy.REJECT:
                raise ValueError(
                    f"Conflict detected when merging file '{file_name}': duplicate key '{key}'"
                )
            elif mode == MergeConfig.MergeStrategy.OVERWRITE:
                base[key] = new[key]
            elif mode == MergeConfig.MergeStrategy.APPEND:
                if isinstance(base[key], Mapping) and isinstance(new[key], Mapping):
                    base[key] |= new[key]
                else:
                    raise ValueError("Cannot perform an append with non-dict values")
        else:
            base[key] = new_value

    return base


def get_service_ctx(
    service_name: str,
    merge_config: MergeConfig,
    external: bool = False,
    namespace: str | None = None,
    src_files_prefix: str = "_values",
) -> dict:
    """
    For the given service, return the combined values from all _values*.yaml files in the service directory.

    Raises an error if duplicate keys are found across files.

    If "external=True" is specified, treat the service name as the full service path.
    """
    if external:
        service_path_root = workspace_root() / service_name
    else:
        service_path_root = get_service_path(service_name, namespace=namespace)

    ctx: dict[str, dict[str, Any]] = {}
    for file in service_path_root.glob(f"{src_files_prefix}*.yaml"):
        try:
            with open(file, "rb") as f:
                values = yaml.safe_load(f) or {}
                ctx = merge_values_files_no_conflict(
                    ctx, values, file.name, merge_config
                )
        except FileNotFoundError:
            continue
    return ctx


def get_service_values(
    service_name: str, merge_config: MergeConfig, external: bool = False
) -> dict:
    return get_service_ctx(service_name, merge_config, external=external)


def get_service_value_override_path(
    service_name: str,
    region_name: str,
    external: bool = False,
    namespace: str | None = None,
) -> Path:
    """
    For the given service, return the path to the override files.

    If "external=True" is specified, treat the service name as the full service path.
    """
    if external:
        service_regions_path = workspace_root() / service_name
    else:
        service_regions_path = get_service_path(service_name, namespace=namespace)

    service_regions_path = service_regions_path / "region_overrides"

    if region_name == "saas":
        region_name = "us"

    return service_regions_path / region_name


def get_service_ctx_overrides(
    service_name: str,
    region_name: str,
    merge_config: MergeConfig,
    cluster_name: str = "default",
    external: bool = False,
    namespace: str | None = None,
    src_files_prefix: str = "_values",
    cluster_as_folder: bool = False,
) -> dict:
    """
    For the given service, return the values specified in the corresponding _values.yaml.
    If "external=True" is specified, treat the service name as the full service path.
    """

    ctx: dict[str, dict[str, Any]] = {}
    override_path = get_service_value_override_path(
        service_name, region_name, external, namespace=namespace
    )
    service_override_file_root = (
        override_path / cluster_name if cluster_as_folder else override_path
    )
    keyword_to_merge_files = (
        f"{src_files_prefix}*.yaml" if cluster_as_folder else f"{cluster_name}*.yaml"
    )
    for file in service_override_file_root.glob(keyword_to_merge_files):
        try:
            if file.name.endswith("managed.yaml"):
                continue
            with open(file, "rb") as f:
                values = yaml.safe_load(f) or {}
                ctx = merge_values_files_no_conflict(
                    ctx, values, file.name, merge_config
                )
        except FileNotFoundError:
            raise
    return ctx


def get_service_value_overrides(
    service_name: str,
    region_name: str,
    merge_config: MergeConfig,
    cluster_name: str = "default",
    external: bool = False,
) -> dict:
    return get_service_ctx_overrides(
        service_name,
        region_name,
        merge_config,
        cluster_name=cluster_name,
        external=external,
    )


def get_common_regional_override(
    service_name: str,
    region_name: str,
    merge_config: MergeConfig,
    external: bool = False,
    namespace: str | None = None,
    src_files_prefix: str = "_values",
) -> dict:
    """
    Helper function to load common regional configuration values.

    Looks for a '_values.yaml' file in the region's override directory that contains
    settings shared across all clusters in that region.
    """
    common_service_override_file_root = get_service_value_override_path(
        service_name, region_name, external, namespace=namespace
    )
    ctx: dict[str, dict[str, Any]] = {}
    for file in common_service_override_file_root.glob(f"{src_files_prefix}*.yaml"):
        try:
            with open(file, "rb") as f:
                values = yaml.safe_load(f) or {}
                ctx = merge_values_files_no_conflict(
                    ctx, values, file.name, merge_config
                )
        except FileNotFoundError:
            continue
    return ctx


def get_hierarchical_value_overrides(
    service_name: str,
    region_name: str,
    cluster_name: str = "default",
    external: bool = False,
    namespace: str | None = None,
    src_files_prefix: str = "_values",
) -> dict:
    """
    Enables hierarchical configuration overrides with shared base values.

    This function extends the standard region_overrides system by adding support for
    shared base configurations. This helps reduce duplication across region-specific
    configurations.

    Directory Structure:
        region_overrides/
        └── common_shared_config/      # Arbitrary name for the shared config group
            ├── _values.yaml           # Base values for this group
            └── {region_name}/         # Region-specific overrides
                └── {cluster_name}.yaml # Cluster-specific overrides

    Override Precedence (highest to lowest):
    1. region_name/cluster_name.yaml
    2. common_shared_config/_values.yaml
    3. Top-level configuration
    """
    if external:
        service_root_path = workspace_root() / service_name
    else:
        service_root_path = get_service_path(service_name, namespace=namespace)
    service_regions_path = service_root_path / "region_overrides"

    merge_config = MergeConfig.from_file(f"{service_root_path}/sentry-kube/merge.yaml")
    if merge_config is None:
        merge_config = MergeConfig.defaults()

    if not service_regions_path.exists():
        return {}

    for override_group in service_regions_path.iterdir():
        if not override_group.is_dir():
            continue

        service_override_file_root = service_regions_path / override_group.name
        base_values: dict[str, dict[str, Any]] = {}
        try:
            for file in service_override_file_root.glob(f"{src_files_prefix}*.yaml"):
                with open(file, "rb") as f:
                    values = yaml.safe_load(f) or {}
                base_values = merge_values_files_no_conflict(
                    base_values, values, file.name, merge_config
                )
        except FileNotFoundError:
            base_values = {}

        if region_name == "saas":
            region_name = "us"

        region_path = f"{override_group.name}/{region_name}"
        region_values = get_service_ctx_overrides(
            service_name,
            region_path,
            merge_config,
            cluster_name,
            external,
            namespace=namespace,
            src_files_prefix=src_files_prefix,
            cluster_as_folder=namespace == "helm",
        )

        common_service_values = get_common_regional_override(
            service_name,
            region_path,
            merge_config,
            external,
            namespace=namespace,
            src_files_prefix=src_files_prefix,
        )

        # There must be either a cluster specific override file a _values.yaml in the region dir
        if not region_values and not common_service_values:
            continue

        deep_merge_dict(base_values, common_service_values)
        deep_merge_dict(base_values, region_values)

        return base_values

    return {}


def get_tools_managed_service_value_overrides(
    service_name: str,
    region_name: str,
    cluster_name: str = "default",
    external: bool = False,
    namespace: str | None = None,
) -> dict:
    """
    We have two override files. Conceptually there is no difference
    but one is manually managed and the other is managed by automated
    tools.

    The manually managed one can have comments, most yaml parsers do
    not preserve comments, so it is safer to keep a separate file.

    The managed file is patched by tools like quickpatch. Though it can
    also be updated by hand knowing that comments would not be preserved.

    The managed file is applied last.
    """
    service_override_file = (
        get_service_value_override_path(
            service_name, region_name, external, namespace=namespace
        )
        / f"{cluster_name}.managed.yaml"
    )

    if service_override_file.exists() and service_override_file.is_file():
        with open(service_override_file, "rb") as f:
            return yaml.safe_load(f) or {}

    return {}


def write_managed_values_overrides(
    values: Mapping[str, Any],
    service_name: str,
    region_name: str,
    cluster_name: str = "default",
    external: bool = False,
    namespace: str | None = None,
) -> None:
    """
    Some tools like `quickpatch` allow us to write the the managed file after
    making changes.
    This is the functions that updates the file.
    """
    service_override_file = (
        get_service_value_override_path(
            service_name, region_name, external, namespace=namespace
        )
        / f"{cluster_name}.managed.yaml"
    )

    with open(service_override_file, "w") as file:
        file.write("# This file contains override value managed by tools\n")
        file.write("# \n")
        file.write("# It is discouraged to update it manually. Use the tool instead\n")
        file.write("# unless you really have to.\n")
        file.write("# Updating this manually is safe as long as:\n")
        file.write("# - you do not add comments. They are going to be erased\n")
        file.write("# - you do not turn it into a Jinja template. That breaks tools\n")
        file.write("# - you keep the structure intact and only change literals\n")
        file.write("\n")
        yaml.dump(values, file)


def get_service_flags(service_name: str, namespace: str | None = None) -> dict:
    service_dir = get_service_path(service_name, namespace=namespace)
    if not service_dir.is_dir():
        click.echo(f"Service directory {service_dir} not found.", err=True)
        raise click.Abort()

    flags_file = service_dir / "_sk_flags.yaml"
    if flags_file.exists():
        with open(flags_file, "rb") as f:
            flags = yaml.safe_load(f) or {}
            return flags
    return {}


def get_service_data(
    customer_name: str, service_name: str, cluster_name: str = "default"
):
    # Customer data is used as the render_data, or the initial data.

    # Then inside render_templates, get_service_values
    # puts values into render_data["values"], then the service_data
    # can override those.
    customer_data = load_customer_data(Config(), customer_name, cluster_name)
    service_data = customer_data.get(service_name, {})
    render_data = {"customer": customer_data}
    return service_data, render_data


def get_helm_service_data(
    region_name: str, service_name: str, cluster_name: str = "default"
):
    # Region data is used as the render_data, or the initial data.

    # Then inside render_templates, get_service_values
    # puts values into render_data["values"], then the service_data
    # can override those.
    region_data = load_region_helm_data(Config(), region_name, cluster_name)
    service_data = region_data.service_data(service_name)
    render_data = {"customer": region_data.global_data}
    return service_data, render_data


def get_service_template_files(service_name, namespace: str | None = None):
    service_dir = get_service_path(service_name, namespace=namespace)
    if not service_dir.is_dir():
        click.echo(f"Service directory {service_dir} not found.", err=True)
        raise click.Abort()

    for template in service_dir.iterdir():
        if not template.name.startswith("_") and template.name.endswith(
            (".yaml", ".yml", ".yaml.j2", ".yml.j2")
        ):
            yield template


def build_materialized_directory(
    customer_name: str, cluster_name: str, service_name: str
) -> Path:
    """
    Returns the directory where a service should be rendered when we
    materialize the rendered template.
    """
    config = Config().silo_regions[customer_name].k8s_config

    kube_config_dir = workspace_root() / config.root

    path = kube_config_dir / config.materialized_manifests / cluster_name / service_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_helm_materialized_directory(
    region_name: str, cluster_name: str, service_name: str, release: str | None = None
) -> Path:
    """
    Returns the directory where a service should be rendered when we
    materialize the rendered template.
    """
    config = Config().silo_regions[region_name].k8s_config

    kube_config_dir = workspace_root() / config.root

    path = (
        kube_config_dir / config.materialized_helm_values / cluster_name / service_name
    )
    if release:
        path = path / release
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_materialized_path(
    customer_name: str, cluster_name: str, service_name: str
) -> Path:
    """
    Returns the file name where to store a materialized service
    """
    return (
        build_materialized_directory(customer_name, cluster_name, service_name)
        / "deployment.yaml"
    )


def build_helm_materialized_path(
    region_name: str,
    cluster_name: str,
    service_name: str,
    release: str | None = None,
    target: str = "values.yaml",
) -> Path:
    """
    Returns the file name where to store a materialized service
    """
    return (
        build_helm_materialized_directory(
            region_name, cluster_name, service_name, release=release
        )
        / target
    )


def get_deployment_image(
    deployment: str, container: str, default: str, quiet: bool = False
):
    if not quiet:
        click.echo(f"Getting deployment image for {deployment}:{container}")

    if "KUBERNETES_OFFLINE" in os.environ:
        return default

    if "DEPLOYMENT_IMAGE" in os.environ:
        return os.getenv("DEPLOYMENT_IMAGE")

    namespace, name = kube_extract_namespace(deployment)
    client = kube_get_client()
    try:
        deployment_obj = AppsV1Api(client).read_namespaced_deployment(
            name,
            namespace,
            _request_timeout=os.getenv(
                KUBE_API_TIMEOUT_ENV_NAME, KUBE_API_TIMEOUT_DEFAULT
            ),
        )
    except ApiException as e:
        if e.status == 404:
            return default
        raise
    for c in deployment_obj.spec.template.spec.containers:
        if c.name == container:
            return c.image
    return default
