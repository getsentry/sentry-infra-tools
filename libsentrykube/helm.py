import base64
import copy
import subprocess
import tempfile

from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from pprint import pformat
from typing import Any

import click
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from yaml import safe_dump

from libsentrykube.loader import load_macros
from libsentrykube.utils import deep_merge_dict, pretty


@dataclass(frozen=True)
class HelmChart:
    name: str
    repo: tuple[str, str] | None
    version: str | None
    dynamic_app_version: bool

    @property
    def full_name(self) -> str:
        if self.is_local:
            return self.name
        return f"{self.repo[0]}/{self.name}"  # type: ignore

    @property
    def is_local(self) -> bool:
        return self.repo is None

    @property
    def local_path(self) -> Path | None:
        if not self.is_local:
            return None
        return Path(self.name)


@dataclass(frozen=True)
class HelmRelease:
    name: str
    chart: HelmChart
    namespace: str
    templates: list[str]

    def filter_template_files(self, template_files):
        if not self.templates:
            return template_files
        return list(filter(lambda v: v in self.templates, template_files))


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


# NOTE: might not need this if use `--repo {url}` param in cmds directly
def ensure_chart(chart: HelmChart):
    if chart.is_local:
        return
    # TODO: check repo installed; if not install and update


def _consolidate_ctx(region_name, service_name, cluster_name, src_file="_values.yaml"):
    from libsentrykube.service import (
        get_service_ctx,
        get_service_ctx_overrides,
        get_hierarchical_value_overrides,
        get_common_regional_override,
        get_helm_service_data,
        assert_customer_is_defined_at_most_once,
    )

    if src_file == "_values.yaml":
        assert_customer_is_defined_at_most_once(
            service_name, region_name, namespace="helm"
        )

    ctx = get_service_ctx(service_name, namespace="helm", src_file=src_file)
    ctx_overrides = get_service_ctx_overrides(
        service_name,
        region_name,
        cluster_name,
        namespace="helm",
        src_file=src_file,
        cluster_as_folder=True,
    )
    ctx_common = get_common_regional_override(
        service_name, region_name, namespace="helm", src_file=src_file
    )
    if ctx_overrides or ctx_common:
        deep_merge_dict(ctx, ctx_common)
        deep_merge_dict(ctx, ctx_overrides)
    else:
        ctx_hierarchical = get_hierarchical_value_overrides(
            service_name, region_name, cluster_name, namespace="helm", src_file=src_file
        )
        deep_merge_dict(ctx, ctx_hierarchical)
    # get_tools_managed_service_value_overrides?
    if src_file == "_values.yaml":
        ctx_region, _ = get_helm_service_data(region_name, service_name, cluster_name)
        deep_merge_dict(ctx, ctx_region)
    return ctx


def _helm_chart_ctx(region_name, service_name, cluster_name) -> list[HelmRelease]:
    rv = []
    ctx = _consolidate_ctx(
        region_name, service_name, cluster_name, src_file="_helm.yaml"
    )
    chart_spec = ctx.get("chart", {})
    if isinstance(chart_spec, str):
        # assume local path
        chart = HelmChart(
            name=chart_spec, repo=None, version=None, dynamic_app_version=True
        )
    else:
        try:
            repo_spec = chart_spec.get("repository", {})
            chart = HelmChart(
                name=chart_spec.get("name"),
                repo=(repo_spec.get("name"), repo_spec.get("url")),
                version=chart_spec.get("version"),
                dynamic_app_version=chart_spec.get("dynamic_app_version", True),
            )
        except Exception:
            click.echo(f"Invalid chart spec for service {service_name}.", err=True)
            raise click.Abort()
    releases_spec = ctx.get("releases", [])
    if not releases_spec:
        # easy, just one release
        rv.append(
            HelmRelease(service_name, chart=chart, namespace="default", templates=[])
        )
        return rv
    for release_spec in releases_spec:
        if isinstance(release_spec, str):
            rv.append(
                HelmRelease(
                    release_spec, chart=chart, namespace="default", templates=[]
                )
            )
            continue
        try:
            rv.append(
                HelmRelease(
                    name=release_spec.get("name"),
                    chart=chart,
                    namespace=release_spec.get("namespace", "default"),
                    templates=release_spec.get("use", []),
                )
            )
        except Exception:
            click.echo(f"Invalid release spec for service {service_name}.", err=True)
            raise click.Abort()
    return rv


def _get_path(obj, *pathparts, default=None):
    for pathpart in pathparts[:-1]:
        obj = obj.get(pathpart, {})
    return obj.get(pathparts[-1], default)


def _render_values(
    region_name,
    service_name,
    cluster_name="default",
    release=None,
):
    from libsentrykube.service import (
        get_service_path,
        get_service_template_files,
        get_helm_service_data,
    )

    service_path = get_service_path(service_name, namespace="helm")
    template_files = sorted(
        list(get_service_template_files(service_name, namespace="helm"))
    )

    _, render_data = get_helm_service_data(
        region_name,
        service_name,
        cluster_name,
    )
    helm_releases = _helm_chart_ctx(region_name, service_name, cluster_name)
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

    rendered_releases = []
    for helm_release in helm_releases:
        if release is not None and helm_release.name != release:
            continue
        release_templates = []
        for template in helm_release.filter_template_files(template_files):
            path = f"{template.relative_to(service_path)}"
            rendered = env.get_template(path).render(render_data)
            release_templates.append((path, rendered))
        rendered_releases.append((helm_release, release_templates))

    return rendered_releases


def render_values(
    region_name, service_name, cluster_name="default", release=None, raw=True
):
    rendered = _render_values(region_name, service_name, cluster_name, release=release)
    out_filter = pretty if not raw else lambda v: v
    return "\n".join([out_filter(v[1]) for _, rel in rendered for v in rel])


def materialize_values(region_name, service_name, cluster_name="default", release=None):
    from libsentrykube.service import build_helm_materialized_path

    rv = False
    for _, rendered_contents in _render_values(
        region_name, service_name, cluster_name, release=release
    ):
        for src_path, rendered_content in rendered_contents:
            output_path = build_helm_materialized_path(
                region_name, cluster_name, service_name, target=src_path
            )
            rendered_content = pretty(rendered_content)
            try:
                existing_content = open(output_path).read()
            except Exception:
                existing_content = None

            if existing_content != rendered_content:
                with open(output_path, "w") as f:
                    f.write(rendered_content)
                rv = True
    return rv


def get_remote_app_version(release: HelmRelease, tmpdir, targets):
    if not release.chart.dynamic_app_version:
        return
    # TODO: `helm get values`, parse version, store in tempfile, add to targets


def helm_release_ctx(region_name, service_name, cluster_name="default", release=None):
    for rendered_release, rendered_contents in _render_values(
        region_name, service_name, cluster_name, release=release
    ):
        ensure_chart(rendered_release.chart)
        with tempfile.TemporaryDirectory() as tmpdirname:
            release_targets = []
            for src_path, rendered_content in rendered_contents:
                with tempfile.NamedTemporaryFile(
                    delete=False, prefix=f"{tmpdirname}/", suffix=".yaml"
                ) as f:
                    f.write(rendered_content.encode("utf8"))
                    release_targets.append(f.name)
            get_remote_app_version(rendered_release, tmpdirname, release_targets)
            yield rendered_release, release_targets


def render(region_name, service_name, cluster_name="default", release=None, raw=True):
    outputs = []

    for helm_release, targets in helm_release_ctx(
        region_name, service_name, cluster_name, release=release
    ):
        helm_params = [
            "template",
            helm_release.name,
            helm_release.chart.full_name,
            "--namespace",
            helm_release.namespace,
        ]
        if helm_release.chart.version:
            helm_params.extend(["--version", helm_release.chart.version])
        for target in targets:
            helm_params.extend(["-f", target])
        outputs.append(_run_helm(helm_params))

    out_filter = pretty if not raw else lambda v: v
    return "\n".join([out_filter(v) for v in outputs])


def diff(region_name, service_name, cluster_name="default", release=None, raw=True): ...


def apply(region_name, service_name, cluster_name="default", release=None): ...
