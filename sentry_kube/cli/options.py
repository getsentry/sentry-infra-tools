"""Read and make emergency fleet updates for values served by sentry-options.

Normal option changes belong in sentry-options-automator. This command exists
solely for incidents: it patches the already-deployed ConfigMaps directly and
the next normal deployment reconciles them back to the declarative values.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import click

from libsentrykube.cluster import Cluster, list_clusters_for_customer
from libsentrykube.config import Config
from libsentrykube.customer import get_region_config
from libsentrykube.utils import ensure_kubectl

__all__ = ("options",)


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
    generated_at: str
    values_json: str


def _configmap_name(options_namespace: str, target: ConfigMapTarget) -> str:
    suffix = f"-{target.configmap_suffix}" if target.configmap_suffix else ""
    return f"sentry-options-{options_namespace}{suffix}"


def _find_targets(
    config: Config,
    regions: Iterable[str],
    excluded_regions: Iterable[str],
    services: Iterable[str],
) -> list[ConfigMapTarget]:
    """Find mounted Getsentry ConfigMaps from sentry-kube's live topology.

    A control-silo ConfigMap is mounted by ``getsentry-control`` in both the
    US and control clusters. These are separate ConfigMap resources and must
    both be patched for an all-region emergency change.
    """

    wanted_regions = _resolve_regions(config, regions, excluded_regions)
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
        scope = ", ".join(sorted(wanted_regions))
        raise click.ClickException(f"No sentry-options ConfigMaps found for {scope}")
    return targets


def _resolve_regions(
    config: Config, regions: Iterable[str], excluded_regions: Iterable[str]
) -> set[str]:
    """Resolve the included or excluded configured region names and aliases.

    Silently dropping one misspelled ``--region`` while changing every other
    requested region is unsafe during an incident, so reject the entire
    invocation before it invokes kubectl.
    """

    requested_regions = set(regions)
    requested_excluded_regions = set(excluded_regions)
    if requested_regions and requested_excluded_regions:
        raise click.UsageError(
            "Use either --region or --exclude-region, not both."
        )

    if requested_regions:
        return _resolve_region_names(config, requested_regions)

    selected_regions = set(config.silo_regions)
    if requested_excluded_regions:
        selected_regions.difference_update(
            _resolve_region_names(config, requested_excluded_regions)
        )
    if not selected_regions:
        raise click.UsageError("All configured regions were excluded.")
    return selected_regions


def _resolve_region_names(config: Config, requested_regions: set[str]) -> set[str]:
    """Use sentry-kube's normal configured-name and alias resolver."""

    resolved_regions = set()
    unknown_regions = []
    for region in sorted(requested_regions):
        try:
            canonical_region, _ = get_region_config(config, region)
        except ValueError:
            unknown_regions.append(region)
        else:
            resolved_regions.add(canonical_region)

    if unknown_regions:
        raise click.ClickException(
            "Unknown region(s): " + ", ".join(unknown_regions)
        )
    return resolved_regions


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
) -> tuple[str, dict[str, Any], bool]:
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
        metadata = configmap["metadata"]
        resource_version = metadata["resourceVersion"]
        values = json.loads(configmap["data"]["values.json"])
        annotations = metadata.get("annotations", {})
    except (AttributeError, KeyError, TypeError, json.JSONDecodeError) as exc:
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
    has_generated_at_annotation = isinstance(annotations, dict) and isinstance(
        annotations.get("generated_at"), str
    )
    return resource_version, values, has_generated_at_annotation


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
    resource_version, values, has_generated_at_annotation = _read_values(
        kubectl, target, kubernetes_namespace, configmap_name
    )
    if not has_generated_at_annotation:
        raise click.ClickException(
            f"{target.name}: ConfigMap {configmap_name} has no generated_at annotation"
        )

    values["options"][option] = value
    # Mounted ConfigMap updates trigger a reload by mtime. Keeping generated_at
    # current also lets the client report meaningful propagation delay.
    generated_at = _generated_at()
    values["generated_at"] = generated_at
    return PreparedPatch(
        target=target,
        configmap_name=configmap_name,
        resource_version=resource_version,
        generated_at=generated_at,
        values_json=json.dumps(values, separators=(",", ":"), ensure_ascii=False),
    )


def _generated_at() -> str:
    """Return a source-compatible, unique-enough timestamp for reload metrics."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
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
            {
                "op": "replace",
                "path": "/metadata/annotations/generated_at",
                "value": prepared.generated_at,
            },
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


def _validate_option_key(
    _ctx: click.Context, _param: click.Parameter, option_key: str
) -> str:
    if not option_key or option_key != option_key.strip():
        raise click.BadParameter("must not be blank or have surrounding whitespace")
    return option_key


def _parse_json_value(value_json: str) -> Any:
    def reject_nonstandard_constant(constant: str) -> None:
        raise ValueError(f"{constant} is not valid JSON")

    try:
        return json.loads(value_json, parse_constant=reject_nonstandard_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise click.BadParameter(
            'must be valid JSON; quote strings, for example --value \'"disabled"\'',
            param_hint="--value",
        ) from exc


OPTIONS_HELP = """\
\b
Read deployed sentry-options values or make a temporary, incident-only update.

`set` talks directly to the selected Kubernetes ConfigMaps. It never starts a
GoCD pipeline or GitHub Action. It is a dry run by default and requires
`--apply` after every selected ConfigMap has passed preflight.

Scope defaults to every configured Getsentry ConfigMap, including both
control-silo ConfigMaps. Use either repeated `--region` to include only named
regions, or repeated `--exclude-region` to start with the fleet and omit named
regions. Configured aliases (such as `saas` for `us`) are accepted.

Examples:

\b
# Inspect a value everywhere.
$ sentry-kube --root ~/dev/ops options get --option billing.quota-enforcement

\b
# Preview a fleet-wide emergency change (the default; no write occurs).
$ sentry-kube --root ~/dev/ops options set \\
    --option billing.quota-enforcement --value false

\b
# Apply only to US and DE after inspecting the generated plan.
$ sentry-kube --root ~/dev/ops options set \\
    --region us --region de --option billing.quota-enforcement \\
    --value false --apply

\b
# Apply to all configured regions except single tenants.
$ sentry-kube --root ~/dev/ops options set \\
    --exclude-region geico --exclude-region goldmansachs --exclude-region ly \\
    --option billing.quota-enforcement --value false --apply
"""


SET_HELP = """\
\b
Set OPTION in every selected live sentry-options ConfigMap.

This is an incident-only override. It fully preflights `get` and `patch` access
and validates `values.json` in every selected ConfigMap before the first write.
Without `--apply`, it prints the exact fleet plan and makes no changes. Each
write uses the ConfigMap resource version read during preflight, so it refuses
to overwrite a concurrent change.

VALUE must be strict JSON. Quote JSON strings (for example, `--value '"on"'`).
The option and value must already be valid for the schema deployed in every
selected region.

Examples:

\b
# Dry run across the entire fleet.
$ sentry-kube --root ~/dev/ops options set --option sample-rate --value 0.1

\b
# Apply only to a pair of named regions.
$ sentry-kube --root ~/dev/ops options set \\
    --region us --region de --option sample-rate --value 0.1 --apply

\b
# Apply everywhere except one region.
$ sentry-kube --root ~/dev/ops options set \\
    --exclude-region geico --option sample-rate --value 0.1 --apply
"""


GET_HELP = """\
\b
Read OPTION from every selected live sentry-options ConfigMap.

`<unset>` means the ConfigMap does not declare the option. The application may
therefore use a legacy fallback or another configured default. The command
requires read access to every selected ConfigMap and does not change anything.

Examples:

\b
# Read from the full fleet.
$ sentry-kube --root ~/dev/ops options get --option sample-rate

\b
# Read only the control-silo ConfigMaps in US and control.
$ sentry-kube --root ~/dev/ops options get \\
    --region us --region control --service getsentry-control \\
    --option sample-rate
"""


def _target_scope_options(command: Callable[..., Any]) -> Callable[..., Any]:
    """Apply the shared fleet-targeting interface to an options subcommand."""

    command = click.option(
        "--service",
        "services",
        type=click.Choice((GETSENTRY_SERVICE, GETSENTRY_CONTROL_SERVICE)),
        multiple=True,
        help=(
            "Limit targets to a service (default: both). `getsentry` selects "
            "regional ConfigMaps; `getsentry-control` selects the control-silo "
            "ConfigMaps in US and control."
        ),
    )(command)
    command = click.option(
        "--exclude-region",
        "excluded_regions",
        multiple=True,
        help=(
            "Start with the whole fleet and omit this configured region or alias; "
            "repeat to omit several. Cannot be combined with --region."
        ),
    )(command)
    command = click.option(
        "--region",
        "regions",
        multiple=True,
        help=(
            "Include only this configured region or alias; repeat to include "
            "several. Cannot be combined with --exclude-region."
        ),
    )(command)
    command = click.option(
        "--kubernetes-namespace",
        default=DEFAULT_KUBERNETES_NAMESPACE,
        show_default=True,
        help="Kubernetes namespace containing the ConfigMaps.",
    )(command)
    return click.option(
        "--options-namespace",
        default="getsentry",
        show_default=True,
        help="sentry-options namespace used in the ConfigMap name.",
    )(command)


def _selected_targets(
    regions: Iterable[str], excluded_regions: Iterable[str], services: Iterable[str]
) -> list[ConfigMapTarget]:
    selected_services = services or (GETSENTRY_SERVICE, GETSENTRY_CONTROL_SERVICE)
    return _find_targets(Config(), regions, excluded_regions, selected_services)


@click.group(help=OPTIONS_HELP)
def options() -> None:
    pass


@options.command("set", help=SET_HELP)
@click.option(
    "--option",
    "option_key",
    required=True,
    callback=_validate_option_key,
    help="Option key to set.",
)
@click.option(
    "--value",
    "value_json",
    required=True,
    help='Value as JSON, for example false, 10, or ' + "'\"text\"'.",
)
@_target_scope_options
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
    excluded_regions: tuple[str, ...],
    services: tuple[str, ...],
    apply: bool,
) -> None:
    """Set OPTION in every selected live ConfigMap.

    The command fully preflights every selected ConfigMap before it issues the
    first mutation. If another writer changes a ConfigMap after preflight, the
    resource-version assertion rejects that individual patch rather than
    replacing an unseen change.
    """

    value = _parse_json_value(value_json)

    targets = _selected_targets(regions, excluded_regions, services)
    kubectl = str(ensure_kubectl())
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


def _read_option(
    kubectl: str,
    targets: Iterable[ConfigMapTarget],
    kubernetes_namespace: str,
    options_namespace: str,
    option_key: str,
) -> list[tuple[ConfigMapTarget, str, bool, Any]]:
    """Read one option from every target, failing rather than hiding gaps."""

    values_by_target = []
    errors = []
    for target in targets:
        configmap_name = _configmap_name(options_namespace, target)
        try:
            _require_permission(
                kubectl, target, kubernetes_namespace, configmap_name, "get"
            )
            _, values, _ = _read_values(
                kubectl, target, kubernetes_namespace, configmap_name
            )
        except click.ClickException as exc:
            errors.append(exc.message)
        else:
            configured = option_key in values["options"]
            values_by_target.append(
                (
                    target,
                    configmap_name,
                    configured,
                    values["options"].get(option_key),
                )
            )

    if errors:
        raise click.ClickException(
            "Could not read every selected ConfigMap:\n" + "\n".join(errors)
        )
    return values_by_target


@options.command("get", help=GET_HELP)
@click.option(
    "--option",
    "option_key",
    required=True,
    callback=_validate_option_key,
    help="Option key to read.",
)
@_target_scope_options
def get_option(
    option_key: str,
    options_namespace: str,
    kubernetes_namespace: str,
    regions: tuple[str, ...],
    excluded_regions: tuple[str, ...],
    services: tuple[str, ...],
) -> None:
    """Read OPTION from every selected live ConfigMap.

    ``<unset>`` means the ConfigMap does not declare the option, so the
    application may use its legacy fallback or another configured default.
    """

    targets = _selected_targets(regions, excluded_regions, services)
    kubectl = str(ensure_kubectl())
    values_by_target = _read_option(
        kubectl,
        targets,
        kubernetes_namespace,
        options_namespace,
        option_key,
    )

    for target, configmap_name, configured, value in values_by_target:
        value_description = (
            json.dumps(value, separators=(",", ":"), ensure_ascii=False)
            if configured
            else "<unset>"
        )
        click.echo(
            f"{target.name}: {value_description} ({configmap_name}; {target.context})"
        )
