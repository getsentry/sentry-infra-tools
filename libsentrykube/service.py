from pathlib import Path
from typing import List, Mapping, Any

import click
import yaml

from collections import OrderedDict
from libsentrykube.config import Config
from libsentrykube.customer import load_customer_data, load_region_helm_data
from libsentrykube.utils import workspace_root, deep_merge_dict

_services: dict[str | None, dict[str, Any]] = {None: OrderedDict()}


class CustomerTooOftenDefinedException(Exception):
    def __init__(self, message):
        super().__init__(message)


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


def get_service_ctx(
    service_name: str,
    external: bool = False,
    namespace: str | None = None,
    src_file: str = "_values.yaml",
) -> dict:
    """
    For the given service, return the values specified in the corresponding {src_file}.

    If "external=True" is specified, treat the service name as the full service path.
    """
    if external:
        service_path = workspace_root() / service_name
    else:
        service_path = get_service_path(service_name, namespace=namespace)
    try:
        with open(service_path / src_file, "rb") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def get_service_values(service_name: str, external: bool = False) -> dict:
    return get_service_ctx(service_name, external=external, src_file="_values.yaml")


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
    cluster_name: str = "default",
    external: bool = False,
    namespace: str | None = None,
    src_file: str = "_values.yaml",
    cluster_as_folder: bool = False,
) -> dict:
    """
    For the given service, return the values specified in the corresponding _values.yaml.
    If "external=True" is specified, treat the service name as the full service path.
    """
    try:
        override_path = get_service_value_override_path(
            service_name, region_name, external, namespace=namespace
        )
        service_override_file = (
            override_path / cluster_name / src_file
            if cluster_as_folder
            else override_path / f"{cluster_name}.yaml"
        )

        with open(service_override_file, "rb") as f:
            values = yaml.safe_load(f) or {}

        return values
    except FileNotFoundError:
        return {}


def get_service_value_overrides(
    service_name: str,
    region_name: str,
    cluster_name: str = "default",
    external: bool = False,
) -> dict:
    return get_service_ctx_overrides(
        service_name, region_name, cluster_name=cluster_name, external=external
    )


def get_common_regional_override(
    service_name: str,
    region_name: str,
    external: bool = False,
    namespace: str | None = None,
    src_file: str = "_values.yaml",
) -> dict:
    """
    Helper function to load common regional configuration values.

    Looks for a '_values.yaml' file in the region's override directory that contains
    settings shared across all clusters in that region.
    """
    try:
        common_service_override_file = (
            get_service_value_override_path(
                service_name, region_name, external, namespace=namespace
            )
            / src_file
        )

        with open(common_service_override_file, "rb") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def get_hierarchical_value_overrides(
    service_name: str,
    region_name: str,
    cluster_name: str = "default",
    external: bool = False,
    namespace: str | None = None,
    src_file: str = "_values.yaml",
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
        service_regions_path = workspace_root() / service_name
    else:
        service_regions_path = get_service_path(service_name, namespace=namespace)

    service_regions_path = service_regions_path / "region_overrides"

    if not service_regions_path.exists():
        return {}

    for override_group in service_regions_path.iterdir():
        if not override_group.is_dir():
            continue

        try:
            service_override_file = (
                service_regions_path / override_group.name / src_file
            )

            with open(service_override_file, "rb") as f:
                base_values = yaml.safe_load(f) or {}
        except FileNotFoundError:
            base_values = {}

        if region_name == "saas":
            region_name = "us"

        region_path = f"{override_group.name}/{region_name}"
        region_values = get_service_ctx_overrides(
            service_name,
            region_path,
            cluster_name,
            external,
            namespace=namespace,
            src_file=src_file,
            cluster_as_folder=namespace == "helm",
        )

        common_service_values = get_common_regional_override(
            service_name, region_path, external, namespace=namespace, src_file=src_file
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
