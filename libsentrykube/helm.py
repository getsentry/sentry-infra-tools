import base64
import copy
import subprocess

from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from pprint import pformat
from typing import Any

import click
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from yaml import safe_dump

from libsentrykube.loader import load_macros
from libsentrykube.utils import deep_merge_dict


@dataclass(frozen=True)
class HelmRelease:
    name: str
    chart: str
    chart_repo: tuple[str, str] | None
    chart_version: str | None
    values: dict[str, Any]

    @property
    def is_chart_local(self) -> bool:
        return self.chart_repo is None

    @property
    def chart_local_path(self) -> Path | None:
        if not self.is_chart_local:
            return None
        return Path(self.chart)


@dataclass(frozen=True)
class HelmData:
    services: list[str]
    global_data: dict[str, Any]
    svc_data: dict[str, dict[str, Any]]

    @property
    def service_names(self) -> list[str]:
        return [Path(p).name for p in self.services]

    def service_data(self, service_name) -> dict[str, Any]:
        rv = copy.deepcopy(self.global_data)
        deep_merge_dict(rv, self.svc_data.get(service_name, {}))
        return rv


class HelmException(RuntimeError): ...


def _run_helm(cmd: list[str]) -> str:
    helm_cmd = ["helm"] + cmd
    helm_env = None
    child_process = subprocess.Popen(
        helm_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=helm_env
    )
    child_output = child_process.communicate()[0].decode("utf-8")

    if not child_output and child_process != 0:
        raise HelmException
    return child_output


def check_helm_bin(f):
    @wraps(f)
    def inner(*args, **kwargs):
        try:
            _run_helm(["version"])
        except HelmException:
            raise click.ClickException("'helm' command not available")
        return f(*args, **kwargs)

    return inner


def _consolidate_ctx(region_name, service_name, cluster_name):
    from libsentrykube.service import (
        get_service_ctx,
        get_service_ctx_overrides,
        get_hierarchical_value_overrides,
        get_common_regional_override,
        get_helm_service_data,
        assert_customer_is_defined_at_most_once,
    )

    assert_customer_is_defined_at_most_once(service_name, region_name)

    service_values = get_service_ctx(service_name, namespace="helm")
    service_values_overrides = get_service_ctx_overrides(
        service_name,
        region_name,
        cluster_name,
        namespace="helm",
        cluster_as_folder=True,
    )
    common_service_values = get_common_regional_override(
        service_name, region_name, namespace="helm"
    )
    if service_values_overrides or common_service_values:
        deep_merge_dict(service_values, common_service_values)
        deep_merge_dict(service_values, service_values_overrides)
    else:
        hierarchical_values = get_hierarchical_value_overrides(
            service_name, region_name, cluster_name, namespace="helm"
        )
        deep_merge_dict(service_values, hierarchical_values)
    # get_tools_managed_service_value_overrides?
    region_values, _ = get_helm_service_data(region_name, service_name, cluster_name)
    deep_merge_dict(service_values, region_values)
    return service_values


def _get_path(obj, *pathparts, default=None):
    for pathpart in pathparts[:-1]:
        obj = obj.get(pathpart, {})
    return obj.get(pathparts[-1], default)


def render_values(
    region_name,
    service_name,
    cluster_name="default",
):
    from libsentrykube.service import (
        get_service_path,
        get_service_template_files,
        get_helm_service_data,
    )

    service_path = get_service_path(service_name)
    template_files = sorted(list(get_service_template_files(service_name)))

    _, render_data = get_helm_service_data(
        region_name,
        service_name,
        cluster_name,
    )

    render_data["values"] = _consolidate_ctx(region_name, service_name, cluster_name)

    extensions = ["jinja2.ext.do", "jinja2.ext.loopcontrols"]
    extensions.extend(load_macros())
    env = Environment(
        extensions=extensions,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
        loader=FileSystemLoader(str(service_path)),
    )

    # Add custom jinja filters here
    env.filters["b64encode"] = lambda x: base64.b64encode(x.encode("utf-8")).decode(
        "utf-8"
    )
    env.filters["yaml"] = safe_dump
    # debugging filter which prints a var to console
    env.filters["echo"] = lambda x: click.echo(pformat(x, indent=4))
    # helper to safely get nested path or default
    env.filters["get_path"] = _get_path

    rendered_templates = []
    for template in template_files:
        path = f"{template.relative_to(service_path)}"
        rendered = env.get_template(path).render(render_data)
        rendered_templates.append(rendered)

    return "\n---\n".join(rendered_templates)


def materialize_values(region_name, service_name, cluster_name="default"): ...


def render(): ...
