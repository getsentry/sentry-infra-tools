from pathlib import Path
from typing import List, Mapping, Any

import click
import yaml

from collections import OrderedDict
from libsentrykube.config import Config
from libsentrykube.customer import load_customer_data
from libsentrykube.utils import workspace_root

_services = OrderedDict()


def set_service_paths(service_paths: List[str]):
    for service_path in service_paths:
        # Globbing is supported
        paths = list(workspace_root().glob(service_path))
        if not paths:
            raise Exception(f"Invalid service path: {service_path}")
        for path in paths:
            if not path.is_dir():
                continue

            name = path.name
            if name in _services:
                raise Exception(f"Found duplicate service: {path}")

            if name == "customers":  # well-known; not an actual service
                continue

            _services[name] = path


def clear_service_paths() -> None:
    """
    This is a hack to allow features that scan all services for all clusters
    to reset the repo while we still have global states around in this
    library.
    """
    _services.clear()


def get_service_path(service_name) -> Path:
    if service_name not in _services:
        click.echo(f"Service named {service_name} was not found.", err=True)
        raise click.Abort()
    return _services[service_name]


def get_service_names() -> List[str]:
    return [s for s in _services.keys()]


def get_service_values(service_name: str, external: bool = False) -> dict:
    """
    For the given service, return the values specified in the corresponding _values.yaml.

    If "external=True" is specified, treat the service name as the full service path.
    """
    if external:
        service_path = workspace_root() / service_name
    else:
        service_path = get_service_path(service_name)
    try:
        with open(service_path / "_values.yaml", "rb") as f:
            values = yaml.safe_load(f)
    except FileNotFoundError:
        values = {}
    return values


def get_service_value_override_path(
    service_name: str,
    region_name: str,
    external: bool = False,
) -> Path:
    """
    For the given service, return the path to the override files.

    If "external=True" is specified, treat the service name as the full service path.
    """
    if external:
        service_regions_path = workspace_root() / service_name
    else:
        service_regions_path = get_service_path(service_name)

    service_regions_path = service_regions_path / "region_overrides"

    if region_name == "saas":
        region_name = "us"

    return service_regions_path / region_name


def get_service_value_overrides(
    service_name: str,
    region_name: str,
    cluster_name: str = "default",
    external: bool = False,
) -> dict:
    """
    For the given service, return the values specified in the corresponding _values.yaml.

    If "external=True" is specified, treat the service name as the full service path.
    """
    try:
        service_override_file = (
            get_service_value_override_path(service_name, region_name, external)
            / f"{cluster_name}.yaml"
        )

        with open(service_override_file, "rb") as f:
            values = yaml.safe_load(f)
    except FileNotFoundError:
        values = {}
    return values


def get_tools_managed_service_value_overrides(
    service_name: str,
    region_name: str,
    cluster_name: str = "default",
    external: bool = False,
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
        get_service_value_override_path(service_name, region_name, external)
        / f"{cluster_name}.managed.yaml"
    )

    if service_override_file.exists() and service_override_file.is_file():
        with open(service_override_file, "rb") as f:
            return yaml.safe_load(f)

    return {}


def write_managed_values_overrides(
    values: Mapping[str, Any],
    service_name: str,
    region_name: str,
    cluster_name: str = "default",
    external: bool = False,
) -> None:
    """
    Some tools like `quickpatch` allow us to write the the managed file after
    making changes.
    This is the functions that updates the file.
    """
    service_override_file = (
        get_service_value_override_path(service_name, region_name, external)
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


def get_service_template_files(service_name):
    service_dir = get_service_path(service_name)
    if not service_dir.is_dir():
        click.echo(f"Service directory {service_dir} not found.", err=True)
        raise click.Abort()

    for template in service_dir.iterdir():
        if not template.name.startswith("_") and template.name.endswith(
            (".yaml", ".yml")
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
