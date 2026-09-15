"""Emergency fleet updates for values served by sentry-options.

Normal option changes belong in sentry-options-automator. This command exists
solely for incidents: it patches the already-deployed ConfigMaps directly and
the next normal deployment reconciles them back to the declarative values.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import click

from libsentrykube.cluster import Cluster, list_clusters_for_customer
from libsentrykube.config import Config
from libsentrykube.utils import ensure_kubectl

__all__ = ("break_glass",)


DEFAULT_KUBERNETES_NAMESPACE = "default"
GETSENTRY_SERVICE = "getsentry"
GETSENTRY_CONTROL_SERVICE = "getsentry-control"
CONTROL_SILO_CONFIGMAP_SUFFIX = "control-silo"


@dataclass(frozen=True)
class ConfigMapTarget:
    """A ConfigMap mounted by a Getsentry workload in one Kubernetes cluster."""

    region: str
    cluster: str
    service: str
    context: str
    configmap_suffix: str | None = None

    @property
    def name(self) -> str:
        return f"{self.region}/{self.cluster}/{self.service}"


@dataclass(frozen=True)
class PreparedPatch:
    target: ConfigMapTarget
    configmap_name: str
    resource_version: str
    values_json: str


def _configmap_name(options_namespace: str, target: ConfigMapTarget) -> str:
    suffix = f"-{target.configmap_suffix}" if target.configmap_suffix else ""
    return f"sentry-options-{options_namespace}{suffix}"


def _find_targets(
    config: Config,
    regions: Iterable[str],
    services: Iterable[str],
) -> list[ConfigMapTarget]:
    """Find mounted Getsentry ConfigMaps from sentry-kube's live topology.

    A control-silo ConfigMap is mounted by ``getsentry-control`` in both the
    US and control clusters. These are separate ConfigMap resources and must
    both be patched for an all-region emergency change.
    """

    wanted_regions = set(regions)
    wanted_services = set(services)
    targets: list[ConfigMapTarget] = []

    for region, region_config in config.silo_regions.items():
        if wanted_regions and region not in wanted_regions:
            continue
        for cluster in list_clusters_for_customer(region_config.k8s_config):
            if not wanted_services.intersection(cluster.service_names):
                continue
            context = cluster.services_data.get("context")
            if not isinstance(context, str) or not context:
                raise click.ClickException(
                    f"{region}/{cluster.name}: cluster configuration has no Kubernetes "
                    "context"
                )
            targets.extend(
                _targets_for_cluster(region, cluster, context, wanted_services)
            )

    if not targets:
        scope = ", ".join(sorted(wanted_regions)) or "configured regions"
        raise click.ClickException(f"No sentry-options ConfigMaps found for {scope}")
    return targets


def _targets_for_cluster(
    region: str, cluster: Cluster, context: str, wanted_services: set[str]
) -> list[ConfigMapTarget]:
    targets = []
    service_names = set(cluster.service_names)
    if GETSENTRY_SERVICE in wanted_services and GETSENTRY_SERVICE in service_names:
        targets.append(ConfigMapTarget(region, cluster.name, GETSENTRY_SERVICE, context))
    if (
        GETSENTRY_CONTROL_SERVICE in wanted_services
        and GETSENTRY_CONTROL_SERVICE in service_names
    ):
        targets.append(
            ConfigMapTarget(
                region,
                cluster.name,
                GETSENTRY_CONTROL_SERVICE,
                context,
                CONTROL_SILO_CONFIGMAP_SUFFIX,
            )
        )
    return targets


def _kubectl_command(
    kubectl: str, target: ConfigMapTarget, kubernetes_namespace: str, *args: str
) -> list[str]:
    return [
        kubectl,
        "--context",
        target.context,
        "--namespace",
        kubernetes_namespace,
        *args,
    ]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, check=False, text=True)


def _command_error(
    target: ConfigMapTarget, action: str, result: subprocess.CompletedProcess[str]
) -> click.ClickException:
    detail = result.stderr.strip() or result.stdout.strip() or "no output"
    return click.ClickException(f"{target.name}: unable to {action}: {detail}")


def _require_permission(
    kubectl: str,
    target: ConfigMapTarget,
    kubernetes_namespace: str,
    configmap_name: str,
    verb: str,
) -> None:
    result = _run(
        _kubectl_command(
            kubectl,
            target,
            kubernetes_namespace,
            "auth",
            "can-i",
            verb,
            f"configmap/{configmap_name}",
        )
    )
    if result.returncode != 0:
        raise _command_error(
            target, f"check {verb} access to ConfigMap {configmap_name}", result
        )
    if result.stdout.strip().lower() != "yes":
        raise click.ClickException(
            f"{target.name}: cannot {verb} ConfigMap {configmap_name}"
        )


def _read_values(
    kubectl: str,
    target: ConfigMapTarget,
    kubernetes_namespace: str,
    configmap_name: str,
) -> tuple[str, dict[str, Any]]:
    result = _run(
        _kubectl_command(
            kubectl,
            target,
            kubernetes_namespace,
            "get",
            "configmap",
            configmap_name,
            "--output=json",
        )
    )
    if result.returncode != 0:
        raise _command_error(target, f"read ConfigMap {configmap_name}", result)

    try:
        configmap = json.loads(result.stdout)
        resource_version = configmap["metadata"]["resourceVersion"]
        values = json.loads(configmap["data"]["values.json"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise click.ClickException(
            f"{target.name}: ConfigMap {configmap_name} does not contain a valid "
            "values.json"
        ) from exc

    if not isinstance(resource_version, str):
        raise click.ClickException(
            f"{target.name}: ConfigMap {configmap_name} has no resourceVersion"
        )
    if not isinstance(values, dict) or not isinstance(values.get("options"), dict):
        raise click.ClickException(
            f"{target.name}: ConfigMap {configmap_name} values.json must contain an "
            "options object"
        )
    return resource_version, values


def _prepare_patch(
    kubectl: str,
    target: ConfigMapTarget,
    kubernetes_namespace: str,
    options_namespace: str,
    option: str,
    value: Any,
) -> PreparedPatch:
    configmap_name = _configmap_name(options_namespace, target)

    # Check both capabilities even when dry-running. A successful dry run is
    # evidence that the same invocation has the access it needs to apply.
    _require_permission(kubectl, target, kubernetes_namespace, configmap_name, "get")
    _require_permission(kubectl, target, kubernetes_namespace, configmap_name, "patch")
    resource_version, values = _read_values(
        kubectl, target, kubernetes_namespace, configmap_name
    )

    values["options"][option] = value
    # Mounted ConfigMap updates trigger a reload by mtime. Keeping generated_at
    # current also lets the client report meaningful propagation delay.
    values["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return PreparedPatch(
        target=target,
        configmap_name=configmap_name,
        resource_version=resource_version,
        values_json=json.dumps(values, separators=(",", ":"), ensure_ascii=False),
    )


def _apply_patch(
    kubectl: str, kubernetes_namespace: str, prepared: PreparedPatch
) -> None:
    patch = json.dumps(
        [
            {
                "op": "test",
                "path": "/metadata/resourceVersion",
                "value": prepared.resource_version,
            },
            {"op": "replace", "path": "/data/values.json", "value": prepared.values_json},
        ],
        separators=(",", ":"),
    )
    result = _run(
        _kubectl_command(
            kubectl,
            prepared.target,
            kubernetes_namespace,
            "patch",
            "configmap",
            prepared.configmap_name,
            "--type=json",
            "--patch",
            patch,
        )
    )
    if result.returncode != 0:
        raise _command_error(
            prepared.target, f"patch ConfigMap {prepared.configmap_name}", result
        )


def _preflight_patches(
    kubectl: str,
    targets: Iterable[ConfigMapTarget],
    kubernetes_namespace: str,
    options_namespace: str,
    option: str,
    value: Any,
) -> list[PreparedPatch]:
    """Prepare every patch, reporting all inaccessible or invalid targets together."""

    prepared: list[PreparedPatch] = []
    errors = []
    for target in targets:
        try:
            prepared.append(
                _prepare_patch(
                    kubectl,
                    target,
                    kubernetes_namespace,
                    options_namespace,
                    option,
                    value,
                )
            )
        except click.ClickException as exc:
            errors.append(exc.message)

    if errors:
        raise click.ClickException(
            "Preflight failed; no ConfigMaps were patched:\n" + "\n".join(errors)
        )
    return prepared


@click.group()
def break_glass() -> None:
    """Make a temporary, direct update to all live sentry-options ConfigMaps."""


@break_glass.command("set")
@click.option("--option", "option_key", required=True, help="Option key to set.")
@click.option(
    "--value",
    "value_json",
    required=True,
    help='Value as JSON, for example false, 10, or ' + "'\"text\"'.",
)
@click.option(
    "--options-namespace",
    default="getsentry",
    show_default=True,
    help="sentry-options namespace used in the ConfigMap name.",
)
@click.option(
    "--kubernetes-namespace",
    default=DEFAULT_KUBERNETES_NAMESPACE,
    show_default=True,
    help="Kubernetes namespace containing the ConfigMaps.",
)
@click.option(
    "--region",
    "regions",
    multiple=True,
    help="Limit the update to a region; repeat to select several (default: all).",
)
@click.option(
    "--configmap-target",
    "services",
    type=click.Choice((GETSENTRY_SERVICE, GETSENTRY_CONTROL_SERVICE)),
    multiple=True,
    help="Limit the update to one mounted ConfigMap type (default: both).",
)
@click.option(
    "--apply",
    is_flag=True,
    help="Apply the patches. Without this flag, only preflight and show the plan.",
)
def set_option(
    option_key: str,
    value_json: str,
    options_namespace: str,
    kubernetes_namespace: str,
    regions: tuple[str, ...],
    services: tuple[str, ...],
    apply: bool,
) -> None:
    """Set OPTION in every selected live ConfigMap.

    The command fully preflights every selected ConfigMap before it issues the
    first mutation. If another writer changes a ConfigMap after preflight, the
    resource-version assertion rejects that individual patch rather than
    replacing an unseen change.
    """

    try:
        value = json.loads(value_json)
    except json.JSONDecodeError as exc:
        raise click.BadParameter(
            'must be JSON; quote strings, for example --value \'"disabled"\'',
            param_hint="--value",
        ) from exc

    selected_services = services or (GETSENTRY_SERVICE, GETSENTRY_CONTROL_SERVICE)
    targets = _find_targets(Config(), regions, selected_services)
    kubectl = ensure_kubectl()
    prepared = _preflight_patches(
        kubectl,
        targets,
        kubernetes_namespace,
        options_namespace,
        option_key,
        value,
    )

    value_description = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    mode = "APPLYING" if apply else "DRY RUN"
    verb = "will set" if apply else "would set"
    click.echo(
        f"{mode}: {verb} {option_key}={value_description} in {len(prepared)} ConfigMaps"
    )
    for patch in prepared:
        click.echo(
            f"  {patch.target.name}: {patch.configmap_name} ({patch.target.context})"
        )

    if not apply:
        return

    errors = []
    for patch in prepared:
        try:
            _apply_patch(kubectl, kubernetes_namespace, patch)
        except click.ClickException as exc:
            errors.append(exc.message)
    if errors:
        raise click.ClickException(
            "Some ConfigMaps were not patched:\n" + "\n".join(errors)
        )
    click.echo(
        f"APPLIED: set {option_key}={value_description} in {len(prepared)} ConfigMaps"
    )
