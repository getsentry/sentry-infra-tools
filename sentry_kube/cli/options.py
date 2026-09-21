"""Read and make emergency fleet updates for values served by sentry-options.

Normal option changes belong in sentry-options-automator. This command exists
solely for incidents: it patches the already-deployed ConfigMaps directly and
the next normal deployment reconciles them back to the declarative values.
"""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import click

from libsentrykube.cluster import Cluster, list_clusters_for_customer
from libsentrykube.config import Config
from libsentrykube.customer import get_region_config
from libsentrykube.utils import ensure_kubectl

if TYPE_CHECKING:
    from sentry_options import OptionValue
else:
    OptionValue = Any

__all__ = ("options",)


DEFAULT_KUBERNETES_NAMESPACE = "default"
DEFAULT_OPTIONS_NAMESPACE = "getsentry"
GETSENTRY_SERVICE = "getsentry"
GETSENTRY_CONTROL_SERVICE = "getsentry-control"
CONTROL_SILO_CONFIGMAP_SUFFIX = "control-silo"
SCHEMAS_ENVVAR = "SENTRY_KUBE_OPTIONS_SCHEMAS"
REPOS_CONFIG_ENVVAR = "SENTRY_KUBE_OPTIONS_REPOS_CONFIG"
REPOS_CONFIG_URL = (
    "https://raw.githubusercontent.com/getsentry/sentry-options-automator/main/repos.json"
)


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

    @property
    def short_label(self) -> str:
        """The region, plus a `/control-silo` suffix only when needed.

        The cluster is always named `default` and the service is implied by
        the suffix, so neither adds information in the common case: a bare
        region name (`de`) is enough to identify a getsentry ConfigMap. The
        control-silo ConfigMap needs the suffix to disambiguate it from the
        regional one in the same region.
        """

        if self.service == GETSENTRY_SERVICE:
            return self.region
        return f"{self.region}/{CONTROL_SILO_CONFIGMAP_SUFFIX}"


@dataclass(frozen=True)
class PreparedPatch:
    target: ConfigMapTarget
    configmap_name: str
    resource_version: str
    generated_at: str
    values_json: str


def _configmap_name(target: ConfigMapTarget) -> str:
    suffix = f"-{target.configmap_suffix}" if target.configmap_suffix else ""
    return f"sentry-options-{DEFAULT_OPTIONS_NAMESPACE}{suffix}"


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
        if region not in wanted_regions:
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
    return sorted(
        targets,
        key=lambda target: (target.region, target.cluster, target.service),
    )


def _resolve_regions(
    config: Config, regions: Iterable[str], excluded_regions: Iterable[str]
) -> set[str]:
    """Resolve the included or excluded configured region names and aliases.

    Silently dropping one misspelled ``--include`` while changing every other
    requested region is unsafe during an incident, so reject the entire
    invocation before it invokes kubectl.
    """

    requested_regions = set(regions)
    requested_excluded_regions = set(excluded_regions)
    if requested_regions and requested_excluded_regions:
        raise click.UsageError("Use either --include or --exclude, not both.")

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
    kubectl: str, target: ConfigMapTarget, *args: str
) -> list[str]:
    return [
        kubectl,
        "--context",
        target.context,
        "--namespace",
        DEFAULT_KUBERNETES_NAMESPACE,
        *args,
    ]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, check=False, text=True)


def _report(message: str) -> None:
    """Print a progress line to stderr, out of the way of plan/data on stdout."""

    click.echo(message, err=True)


@contextmanager
def _timed_step(label: str) -> Iterator[None]:
    """Report a step's start, then how long it took, regardless of outcome."""

    _report(f"{label}...")
    start = time.monotonic()
    try:
        yield
    finally:
        _report(f"{label}: {time.monotonic() - start:.1f}s")


def _fetch_schemas_with_client(config_path: Path, output: Path) -> None:
    try:
        from sentry_options import OptionsError, fetch_schemas
    except ImportError as exc:
        raise click.ClickException(
            "Installed sentry_options does not provide schema fetching; install a "
            "release with fetch_schemas or pass --schemas"
        ) from exc

    try:
        with _timed_step(
            f"Fetching sentry-options schemas (using {config_path})"
        ):
            fetch_schemas(config_path, output)
    except OptionsError as exc:
        raise click.ClickException(
            f"Unable to fetch sentry-options schemas: {exc}"
        ) from exc


def _repos_config_path(explicit_path: Path | None) -> Path | None:
    if explicit_path is not None:
        return explicit_path

    candidates = (
        Path.cwd() / "repos.json",
        Path.home() / "dev" / "sentry-options-automator" / "repos.json",
    )
    return next((path for path in candidates if path.is_file()), None)


def _download_repos_config(destination: Path) -> None:
    try:
        with _timed_step(f"Downloading repos.json from {REPOS_CONFIG_URL}"):
            request = Request(REPOS_CONFIG_URL, headers={"User-Agent": "sentry-kube"})
            with urlopen(request, timeout=15) as response:
                destination.write_bytes(response.read())
    except (OSError, URLError) as exc:
        raise click.ClickException(
            f"Unable to fetch the sentry-options repository list: {exc}"
        ) from exc


def _fetch_schemas(repos_config: Path | None, output: Path) -> None:
    config_path = _repos_config_path(repos_config)
    if config_path is not None:
        _report(f"Using repos.json at {config_path}")
        _fetch_schemas_with_client(config_path, output)
        return

    with tempfile.TemporaryDirectory(prefix="sentry-kube-options-") as temp_dir:
        config_path = Path(temp_dir) / "repos.json"
        _download_repos_config(config_path)
        _fetch_schemas_with_client(config_path, output)


@contextmanager
def _schema_directory(
    schemas_dir: Path | None, repos_config: Path | None
) -> Iterator[Path]:
    """Yield a schema snapshot, fetching one when no local snapshot is supplied."""

    if schemas_dir is not None:
        _report(f"Using local schema snapshot at {schemas_dir}")
        yield schemas_dir
        return

    with tempfile.TemporaryDirectory(prefix="sentry-kube-schemas-") as temp_dir:
        fetched_schemas = Path(temp_dir) / "schemas"
        _fetch_schemas(repos_config, fetched_schemas)
        yield fetched_schemas


def _command_error(
    target: ConfigMapTarget, action: str, result: subprocess.CompletedProcess[str]
) -> click.ClickException:
    detail = result.stderr.strip() or result.stdout.strip() or "no output"
    return click.ClickException(f"{target.name}: unable to {action}: {detail}")


def _require_patch_permission(
    kubectl: str, target: ConfigMapTarget, configmap_name: str
) -> None:
    result = _run(
        _kubectl_command(
            kubectl,
            target,
            "auth",
            "can-i",
            "patch",
            f"configmap/{configmap_name}",
        )
    )
    if result.returncode != 0:
        raise _command_error(
            target, f"check patch access to ConfigMap {configmap_name}", result
        )
    if result.stdout.strip().lower() != "yes":
        raise click.ClickException(
            f"{target.name}: cannot patch ConfigMap {configmap_name}"
        )


def _read_values(
    kubectl: str,
    target: ConfigMapTarget,
    configmap_name: str,
) -> tuple[str, dict[str, Any], bool]:
    result = _run(
        _kubectl_command(
            kubectl,
            target,
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
    if not isinstance(values.get("generated_at"), str):
        raise click.ClickException(
            f"{target.name}: ConfigMap {configmap_name} values.json has no generated_at "
            "timestamp"
        )
    has_generated_at_annotation = isinstance(annotations, dict) and isinstance(
        annotations.get("generated_at"), str
    )
    return resource_version, values, has_generated_at_annotation


def _prepare_patch(
    kubectl: str,
    target: ConfigMapTarget,
    option: str,
    value: OptionValue,
) -> PreparedPatch:
    configmap_name = _configmap_name(target)

    # Reading the ConfigMap below proves read access. Check patch access up
    # front too, so a successful dry run has the permissions needed to apply.
    _require_patch_permission(kubectl, target, configmap_name)
    resource_version, values, has_generated_at_annotation = _read_values(
        kubectl, target, configmap_name
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


def _apply_patch(kubectl: str, prepared: PreparedPatch) -> None:
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


def _apply_patches(kubectl: str, prepared: list[PreparedPatch]) -> None:
    """Apply every prepared patch in parallel, reporting all failures together.

    Each patch's `test` op is scoped to its own ConfigMap's resourceVersion,
    so one target's write can never affect another's; applying them
    concurrently is as safe as applying them one at a time, just faster.
    """

    report_lock = threading.Lock()
    errors: list[str] = []

    def apply_one(patch: PreparedPatch) -> None:
        start = time.monotonic()
        try:
            _apply_patch(kubectl, patch)
        except click.ClickException as exc:
            with report_lock:
                errors.append(exc.message)
                _report(
                    f"Apply {patch.target.name}: failed ({time.monotonic() - start:.1f}s)"
                )
            return
        with report_lock:
            _report(f"Apply {patch.target.name}: ok ({time.monotonic() - start:.1f}s)")

    with ThreadPoolExecutor(
        max_workers=len(prepared) or 1
    ) as executor:
        futures = [executor.submit(apply_one, patch) for patch in prepared]
        for future in as_completed(futures):
            future.result()

    if errors:
        raise click.ClickException(
            "Some ConfigMaps were not patched:\n" + "\n".join(errors)
        )


def _preflight_patches(
    kubectl: str,
    targets: Iterable[ConfigMapTarget],
    option: str,
    value: OptionValue,
) -> list[PreparedPatch]:
    """Prepare every patch in parallel, reporting all failures together.

    Each target's preflight (permission check + read) only touches that
    target's own ConfigMap, so targets are independent and safe to run
    concurrently. Results are collected keyed by target and replayed in the
    original, sorted target order, so the returned list (and the plan it
    drives) stays deterministic even though the work ran out of order.
    """

    targets = list(targets)
    report_lock = threading.Lock()
    results: dict[ConfigMapTarget, PreparedPatch] = {}
    errors: list[str] = []

    def preflight_one(target: ConfigMapTarget) -> None:
        start = time.monotonic()
        try:
            patch = _prepare_patch(kubectl, target, option, value)
        except click.ClickException as exc:
            with report_lock:
                errors.append(exc.message)
                _report(
                    f"Preflight {target.name}: failed ({time.monotonic() - start:.1f}s)"
                )
            return
        with report_lock:
            results[target] = patch
            _report(f"Preflight {target.name}: ok ({time.monotonic() - start:.1f}s)")

    with ThreadPoolExecutor(
        max_workers=len(targets) or 1
    ) as executor:
        futures = [executor.submit(preflight_one, target) for target in targets]
        for future in as_completed(futures):
            future.result()

    if errors:
        raise click.ClickException(
            "Preflight failed; no ConfigMaps were patched:\n" + "\n".join(errors)
        )
    return [results[target] for target in targets]


def _validate_option_key(
    _ctx: click.Context, _param: click.Parameter, option_key: str
) -> str:
    if not option_key or option_key != option_key.strip():
        raise click.BadParameter("must not be blank or have surrounding whitespace")
    return option_key


def _parse_json_value(value_json: str) -> OptionValue:
    """Parse VALUE as JSON, treating unquoted text as a string.

    `foo` is accepted as shorthand for the JSON string `"foo"` so callers
    do not need to shell-quote plain strings. Anything that parses as JSON
    (numbers, booleans, null, quoted strings, objects, arrays) keeps its
    JSON meaning; only text that fails to parse falls back to being a
    literal string. Schema validation still rejects it if the option
    expects a different type.
    """

    def reject_nonstandard_constant(constant: str) -> None:
        raise ValueError(f"{constant} is not valid JSON")

    def parse_finite_float(number: str) -> float:
        value = float(number)
        if not math.isfinite(value):
            raise ValueError(f"{number} is not a finite JSON number")
        return value

    try:
        return json.loads(
            value_json,
            parse_constant=reject_nonstandard_constant,
            parse_float=parse_finite_float,
        )
    except json.JSONDecodeError:
        # Not JSON-shaped at all (for example `foo` or `on`): treat the raw
        # text as a JSON string so callers do not have to shell-quote it.
        return value_json
    except ValueError as exc:
        # JSON-shaped but explicitly disallowed (NaN, Infinity, overflow):
        # this is almost certainly a mistake, so reject it rather than
        # silently turning it into a string.
        raise click.BadParameter(
            'must be valid JSON; quote strings, for example \'"disabled"\'',
            param_hint="VALUE",
        ) from exc


def _validate_against_schema(
    schemas_dir: Path, option_key: str, value: OptionValue
) -> None:
    """Validate one prospective write with sentry-options' canonical validator.

    This deliberately loads only schemas. Loading a runtime `Options` instance
    would also require a values tree and process-global initialization, neither
    of which belongs in a fleet-management CLI.
    """

    try:
        from sentry_options import OptionsError, SchemaRegistry
    except ImportError as exc:
        raise click.ClickException(
            "sentry-options schema validation is unavailable; install "
            "sentry_options>=1.2.11"
        ) from exc

    try:
        with _timed_step(f"Validating {DEFAULT_OPTIONS_NAMESPACE}.{option_key} against schema"):
            registry = SchemaRegistry.from_directory(schemas_dir)
            registry.validate_option(DEFAULT_OPTIONS_NAMESPACE, option_key, value)
    except OptionsError as exc:
        raise click.ClickException(
            "Schema validation failed for "
            f"{DEFAULT_OPTIONS_NAMESPACE}.{option_key} using {schemas_dir}: {exc}. "
            "No clusters were contacted."
        ) from exc


OPTIONS_HELP = """\
\b
Read deployed sentry-options values or make a temporary, incident-only update.

`set` talks directly to the selected Kubernetes ConfigMaps. It never starts a
GoCD pipeline or GitHub Action. It is a dry run by default and requires
`--apply` after every selected ConfigMap has passed preflight. It validates the
requested JSON value with the same native validator the application uses.

When `--schemas` (or `SENTRY_KUBE_OPTIONS_SCHEMAS`) is not supplied, the
command fetches a fresh snapshot through the explicit `sentry_options` client
API. It uses `--repos-config` when supplied, a nearby `repos.json` when
available, or the automator's published `repos.json` as a last resort.

Scope defaults to every configured Getsentry ConfigMap, including both
control-silo ConfigMaps. Use either repeated `--include` to include only named
regions, or repeated `--exclude` to start with the fleet and omit named
regions. Configured aliases (such as `saas` for `us`) are accepted.

The target is fixed to the Getsentry values ConfigMaps in Kubernetes'
`default` namespace: `sentry-options-getsentry`, plus
`sentry-options-getsentry-control-silo` for control workloads.

Examples:

\b
# Inspect a value everywhere. Each line includes the region and value.
$ sentry-kube --root ~/dev/ops options get billing.quotas.exceeded.enabled

\b
# Preview a fleet-wide emergency change (the default; no write occurs).
$ sentry-kube --root ~/dev/ops options set \\
    billing.quotas.exceeded.enabled false

\b
# Apply only to US and DE after inspecting the generated plan.
$ sentry-kube --root ~/dev/ops options set \\
    --include us --include de billing.quotas.exceeded.enabled false \\
    --apply

\b
# Apply to all configured regions except single tenants.
$ sentry-kube --root ~/dev/ops options set \\
    --exclude geico --exclude goldmansachs --exclude ly \\
    billing.quotas.exceeded.enabled false --apply
"""


SET_HELP = """\
\b
Set OPTION in every selected live sentry-options ConfigMap.

This is an incident-only override. Before contacting a cluster, it validates
OPTION and VALUE against a local snapshot supplied by `--schemas` (or
`SENTRY_KUBE_OPTIONS_SCHEMAS`), or fetches one with
the explicit `sentry_options.fetch_schemas` client API when no snapshot is
supplied. It then uses the native sentry-options validator before it reads
every selected ConfigMap and confirms patch access before the first write.
Without `--apply`, it prints the exact fleet plan and makes no changes.
Each write uses the ConfigMap resource version read during preflight, so it
refuses to overwrite a concurrent change.

VALUE is parsed as JSON when possible (numbers, `true`/`false`/`null`,
quoted strings, objects, arrays). Anything else, such as `on`, is treated
as a plain string, equivalent to `'"on"'`. The option and value must be
valid for the supplied schema snapshot.

Examples:

\b
# Dry run across the entire fleet.
$ sentry-kube --root ~/dev/ops options set \\
    billing.quotas.exceeded.enabled false

\b
# Apply only to a pair of named regions.
$ sentry-kube --root ~/dev/ops options set \\
    --include us --include de billing.quotas.exceeded.enabled false \\
    --apply

\b
# Apply everywhere except one region.
$ sentry-kube --root ~/dev/ops options set \\
    --exclude geico billing.quotas.exceeded.enabled false \\
    --apply
"""


GET_HELP = """\
\b
Read OPTION from every selected live sentry-options ConfigMap.

`<unset>` means the ConfigMap does not declare the option. The command
requires read access to every selected ConfigMap and does not change anything.
Each output line is `<region>: <value>` (for example `de: 42`). A
control-silo ConfigMap adds a `/control-silo` suffix to the region to tell it
apart from the regional ConfigMap in the same region (for example
`us/control-silo: 0`). Pass `--verbose` to also print each line's ConfigMap
name and kubectl context.

Examples:

\b
# Read from the full fleet. Each line shows the region and value.
$ sentry-kube --root ~/dev/ops options get billing.quotas.exceeded.enabled

\b
# Read only the control-silo ConfigMaps in US and control.
$ sentry-kube --root ~/dev/ops options get \\
    --include us --include control --service getsentry-control \\
    billing.quotas.exceeded.enabled
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
        "--exclude",
        "excluded_regions",
        multiple=True,
        help=(
            "Start with the whole fleet and omit this configured region or alias; "
            "repeat to omit several. Cannot be combined with --include."
        ),
    )(command)
    command = click.option(
        "--include",
        "regions",
        multiple=True,
        help=(
            "Include only this configured region or alias; repeat to include "
            "several. Cannot be combined with --exclude."
        ),
    )(command)
    return command


def _selected_targets(
    regions: Iterable[str], excluded_regions: Iterable[str], services: Iterable[str]
) -> list[ConfigMapTarget]:
    selected_services = services or (GETSENTRY_SERVICE, GETSENTRY_CONTROL_SERVICE)
    return _find_targets(Config(), regions, excluded_regions, selected_services)


@click.group(help=OPTIONS_HELP)
def options() -> None:
    pass


@options.command("set", help=SET_HELP)
@click.argument("option_key", metavar="OPTION", callback=_validate_option_key)
@click.argument("value_json", metavar="VALUE")
@click.option(
    "--schemas",
    "schemas_dir",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True, readable=True),
    required=False,
    envvar=SCHEMAS_ENVVAR,
    help=(
        "Optional schema snapshot root containing {namespace}/schema.json. If omitted, "
        "fetches one with sentry_options."
    ),
)
@click.option(
    "--repos-config",
    type=click.Path(path_type=Path, dir_okay=False, readable=True),
    envvar=REPOS_CONFIG_ENVVAR,
    help=(
        "repos.json to use when fetching schemas (default: nearby or published "
        f"automator config; ignored with --schemas)."
    ),
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
    schemas_dir: Path | None,
    repos_config: Path | None,
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
    with _schema_directory(schemas_dir, repos_config) as schema_path:
        _validate_against_schema(schema_path, option_key, value)

    targets = _selected_targets(regions, excluded_regions, services)
    _report(f"Preflighting {len(targets)} ConfigMap target(s)")
    kubectl = str(ensure_kubectl())
    prepared = _preflight_patches(
        kubectl,
        targets,
        option_key,
        value,
    )

    value_description = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    configmap_count = f"{len(prepared)} ConfigMap{'s' if len(prepared) != 1 else ''}"
    mode = "APPLYING" if apply else "DRY RUN"
    verb = "will set" if apply else "would set"
    click.echo(
        f"{mode}: {verb} {option_key}={value_description} in {configmap_count}"
    )
    for patch in prepared:
        click.echo(
            f"  {patch.target.name}: {patch.configmap_name} ({patch.target.context})"
        )

    if not apply:
        return

    _apply_patches(kubectl, prepared)
    click.echo(
        f"APPLIED: set {option_key}={value_description} in {configmap_count}"
    )


def _read_one_option(
    kubectl: str, target: ConfigMapTarget, option_key: str, verbose: bool
) -> tuple[ConfigMapTarget, str, str | None]:
    """Read one option from one target.

    Returns ``(target, line, error)``: ``line`` is the ready-to-print result
    line, or empty when ``error`` is set.
    """

    configmap_name = _configmap_name(target)
    try:
        _, values, _ = _read_values(kubectl, target, configmap_name)
    except click.ClickException as exc:
        return target, "", exc.message

    configured = option_key in values["options"]
    value_description = (
        json.dumps(
            values["options"].get(option_key), separators=(",", ":"), ensure_ascii=False
        )
        if configured
        else "<unset>"
    )
    if verbose:
        line = f"{target.name}: {value_description} ({configmap_name}; {target.context})"
    else:
        line = f"{target.short_label}: {value_description}"
    return target, line, None


def _read_and_print_option(
    kubectl: str,
    targets: Iterable[ConfigMapTarget],
    option_key: str,
    verbose: bool,
) -> None:
    """Read one option from every target in parallel, printing as results land.

    Each target is printed as soon as it is read, so slow or failing targets
    never hold up or hide results from targets that finished first. Printing
    is serialized with a lock so concurrent worker threads never interleave
    partial lines.
    """

    targets = list(targets)
    print_lock = threading.Lock()
    errors: list[str] = []

    with ThreadPoolExecutor(
        max_workers=len(targets) or 1
    ) as executor:
        futures = [
            executor.submit(_read_one_option, kubectl, target, option_key, verbose)
            for target in targets
        ]
        for future in as_completed(futures):
            _, line, error = future.result()
            with print_lock:
                if error is not None:
                    errors.append(error)
                else:
                    click.echo(line)

    if errors:
        raise click.ClickException(
            "Could not read every selected ConfigMap:\n" + "\n".join(errors)
        )


@options.command("get", help=GET_HELP)
@click.argument("option_key", metavar="OPTION", callback=_validate_option_key)
@_target_scope_options
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Also print each target's ConfigMap name and kubectl context.",
)
def get_option(
    option_key: str,
    regions: tuple[str, ...],
    excluded_regions: tuple[str, ...],
    services: tuple[str, ...],
    verbose: bool,
) -> None:
    """Read OPTION from every selected live ConfigMap.

    ``<unset>`` means the ConfigMap does not declare the option.
    """

    targets = _selected_targets(regions, excluded_regions, services)
    kubectl = str(ensure_kubectl())
    _read_and_print_option(kubectl, targets, option_key, verbose)
