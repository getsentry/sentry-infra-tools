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
from yaml import safe_dump, safe_load_all

from libsentrykube.loader import load_macros
from libsentrykube.utils import deep_merge_dict, pretty


@dataclass(frozen=True)
class HelmChart:
    name: str
    repo: str | None
    version: str | None
    dynamic_app_version: bool
    dynamic_version_path: str

    @property
    def is_local(self) -> bool:
        return self.repo is None

    @property
    def is_oci(self):
        return self.repo is not None and self.repo.startswith("oci://")

    def cmd_target(self, cwd: Path) -> str:
        if self.is_local:
            return str(self.local_path(cwd))
        if self.is_oci:
            return self.repo  # type: ignore
        return self.name

    def local_path(self, parent: Path) -> Path | None:
        if not self.is_local:
            return None
        return parent / self.name


@dataclass(frozen=True)
class HelmRelease:
    name: str
    chart: HelmChart
    namespace: str
    templates: list[str]

    def filter_template_files(self, cwd: Path, template_files):
        if not self.templates:
            return template_files
        return list(
            filter(lambda v: str(v.relative_to(cwd)) in self.templates, template_files)
        )


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


def _consolidate_ctx(
    region_name, service_name, cluster_name, src_files_prefix="_values"
):
    from libsentrykube.service import (
        get_service_ctx,
        get_service_ctx_overrides,
        get_hierarchical_value_overrides,
        get_common_regional_override,
        get_helm_service_data,
        assert_customer_is_defined_at_most_once,
    )

    if src_files_prefix == "_values":
        assert_customer_is_defined_at_most_once(
            service_name, region_name, namespace="helm"
        )

    ctx = get_service_ctx(
        service_name, namespace="helm", src_files_prefix=src_files_prefix
    )
    ctx_overrides = get_service_ctx_overrides(
        service_name,
        region_name,
        cluster_name,
        namespace="helm",
        src_files_prefix=src_files_prefix,
        cluster_as_folder=True,
    )
    ctx_common = get_common_regional_override(
        service_name, region_name, namespace="helm", src_files_prefix=src_files_prefix
    )
    if ctx_overrides or ctx_common:
        deep_merge_dict(ctx, ctx_common)
        deep_merge_dict(ctx, ctx_overrides)
    else:
        ctx_hierarchical = get_hierarchical_value_overrides(
            service_name,
            region_name,
            cluster_name,
            namespace="helm",
            src_files_prefix=src_files_prefix,
        )
        deep_merge_dict(ctx, ctx_hierarchical)
    # get_tools_managed_service_value_overrides?
    if src_files_prefix == "_values":
        ctx_region, _ = get_helm_service_data(region_name, service_name, cluster_name)
        deep_merge_dict(ctx, ctx_region)
    return ctx


def _helm_chart_ctx(region_name, service_name, cluster_name) -> list[HelmRelease]:
    rv = []
    ctx = _consolidate_ctx(
        region_name, service_name, cluster_name, src_files_prefix="_helm"
    )
    chart_spec = ctx.get("chart", {})
    if isinstance(chart_spec, str):
        # assume local path
        chart = HelmChart(
            name=chart_spec,
            repo=None,
            version=None,
            dynamic_app_version=True,
            dynamic_version_path="image.tag",
        )
    else:
        try:
            chart = HelmChart(
                name=chart_spec.get("name"),
                repo=chart_spec.get("repository"),
                version=chart_spec.get("version"),
                dynamic_app_version=chart_spec.get("dynamic_app_version", True),
                dynamic_version_path=chart_spec.get(
                    "dynamic_version_path", "image.tag"
                ),
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
        render_data["_helm"] = {"release": helm_release.name}
        release_templates = []
        for template in helm_release.filter_template_files(
            service_path, template_files
        ):
            path = f"{template.relative_to(service_path)}"
            rendered = env.get_template(path).render(render_data)
            release_templates.append((path, rendered))
        rendered_releases.append((helm_release, release_templates))

    return rendered_releases


def render_values(
    region_name, service_name, cluster_name="default", release=None, raw=True, **kwargs
):
    rendered = _render_values(region_name, service_name, cluster_name, release=release)
    out_filter = pretty if not raw else lambda v: v
    return "\n".join([out_filter(v[1]) for _, rel in rendered for v in rel])


def materialize_values(region_name, service_name, cluster_name="default", release=None):
    from libsentrykube.service import build_helm_materialized_path

    rv = False
    for helm_release, rendered_contents in _render_values(
        region_name, service_name, cluster_name, release=release
    ):
        materialize_rel_path = helm_release.name != service_name and helm_release.name
        for src_path, rendered_content in rendered_contents:
            output_path = build_helm_materialized_path(
                region_name,
                cluster_name,
                service_name,
                release=materialize_rel_path,
                target=src_path,
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


def get_remote_app_version(release: HelmRelease, tmpdir, targets, kctx=None):
    if not release.chart.dynamic_app_version:
        return

    helm_params = [
        "get",
        "values",
        release.name,
        "--namespace",
        release.namespace,
        "--output",
        "yaml",
    ]
    if kctx:
        helm_params.extend(["--kube-context", kctx])
    dynamic_version_path = release.chart.dynamic_version_path.split(".")
    values: dict[str, Any] = {}
    data_w = values
    for path in dynamic_version_path[:-1]:
        data_w = data_w[path] = {}

    try:
        previous_values = _run_helm(helm_params)
        data_r = list(safe_load_all(previous_values))[0]
        for path in dynamic_version_path[:-1]:
            data_r = data_r[path]
        previous_tag = data_r[dynamic_version_path[-1]]
    except Exception:
        previous_tag = "latest"

    data_w[dynamic_version_path[-1]] = previous_tag
    with tempfile.NamedTemporaryFile(
        delete=False, prefix=f"{tmpdir}/", suffix=".yaml"
    ) as f:
        f.write(safe_dump(values).encode("utf8"))
        targets.append(f.name)


def set_app_version(release: HelmRelease, target_cmd: list[str], app_version=None):
    if app_version is None:
        return
    dynamic_version_path = release.chart.dynamic_version_path
    target_cmd.extend(["--set-string", f"{dynamic_version_path}={app_version}"])


def helm_release_ctx(
    region_name,
    service_name,
    cluster_name="default",
    release=None,
    app_version=None,
    kctx=None,
):
    for rendered_release, rendered_contents in _render_values(
        region_name, service_name, cluster_name, release=release
    ):
        with tempfile.TemporaryDirectory() as tmpdirname:
            release_targets = []
            for src_path, rendered_content in rendered_contents:
                with tempfile.NamedTemporaryFile(
                    delete=False, prefix=f"{tmpdirname}/", suffix=".yaml"
                ) as f:
                    f.write(rendered_content.encode("utf8"))
                    release_targets.append(f.name)
            if app_version is None:
                get_remote_app_version(
                    rendered_release, tmpdirname, release_targets, kctx
                )
            yield rendered_release, release_targets


def render(
    region_name, service_name, cluster_name="default", release=None, raw=True, kctx=None
):
    from libsentrykube.service import get_service_path

    outputs = []

    service_path = get_service_path(service_name, namespace="helm")
    for helm_release, targets in helm_release_ctx(
        region_name, service_name, cluster_name, release=release, kctx=kctx
    ):
        helm_params = [
            "template",
            helm_release.name,
            helm_release.chart.cmd_target(service_path),
            "--namespace",
            helm_release.namespace,
        ]
        if kctx:
            helm_params.extend(["--kube-context", kctx])
        if not helm_release.chart.is_local and not helm_release.chart.is_oci:
            helm_params.extend(["--repo", helm_release.chart.repo])
        if helm_release.chart.version:
            helm_params.extend(["--version", helm_release.chart.version])
        for target in targets:
            helm_params.extend(["-f", target])
        outputs.append(_run_helm(helm_params))

    out_filter = pretty if not raw else lambda v: v
    return "\n".join([out_filter(v) for v in outputs])


def diff(
    region_name,
    service_name,
    cluster_name="default",
    release=None,
    app_version=None,
    kctx=None,
):
    from libsentrykube.service import get_service_path

    outputs = []

    service_path = get_service_path(service_name, namespace="helm")
    for helm_release, targets in helm_release_ctx(
        region_name,
        service_name,
        cluster_name,
        release=release,
        app_version=app_version,
        kctx=kctx,
    ):
        helm_params = [
            "diff",
            "upgrade",
            helm_release.name,
            helm_release.chart.cmd_target(service_path),
            "--namespace",
            helm_release.namespace,
            "--install",
        ]
        if kctx:
            helm_params.extend(["--kube-context", kctx])
        if not helm_release.chart.is_local and not helm_release.chart.is_oci:
            helm_params.extend(["--repo", helm_release.chart.repo])
        if helm_release.chart.version:
            helm_params.extend(["--version", helm_release.chart.version])
        for target in targets:
            helm_params.extend(["-f", target])
        set_app_version(helm_release, helm_params, app_version=app_version)
        try:
            output = _run_helm(helm_params)
        except HelmException:
            # helm diff has no output and exit code 1 on no diff
            output = ""
        outputs.append(output)

    return "\n".join(outputs)


def apply(
    region_name,
    service_name,
    cluster_name="default",
    release=None,
    app_version=None,
    kctx=None,
    atomic=True,
    timeout=300,
):
    from libsentrykube.service import get_service_path

    outputs = []

    service_path = get_service_path(service_name, namespace="helm")
    for helm_release, targets in helm_release_ctx(
        region_name,
        service_name,
        cluster_name,
        release=release,
        app_version=app_version,
        kctx=kctx,
    ):
        helm_params = [
            "upgrade",
            helm_release.name,
            helm_release.chart.cmd_target(service_path),
            "--namespace",
            helm_release.namespace,
            "--install",
            "--timeout",
            f"{timeout}s",
        ]
        if kctx:
            helm_params.extend(["--kube-context", kctx])
        if not helm_release.chart.is_local and not helm_release.chart.is_oci:
            helm_params.extend(["--repo", helm_release.chart.repo])
        if helm_release.chart.version:
            helm_params.extend(["--version", helm_release.chart.version])
        if atomic:
            helm_params.extend(["--atomic"])
        for target in targets:
            helm_params.extend(["-f", target])
        set_app_version(helm_release, helm_params, app_version=app_version)
        outputs.append(_run_helm(helm_params))

    return "\n".join(outputs)
