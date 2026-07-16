import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import click
from kubernetes import client
from kubernetes.client.rest import ApiException

from libsentrykube.utils import kube_get_client

ANNOTATION_DESIRED = "sts-reconciler.sentry.io/desired-pvc-spec"
ANNOTATION_STATUS = "sts-reconciler.sentry.io/status"
ANNOTATION_SKIP = "sts-reconciler.sentry.io/skip"

TERMINAL_STATES = {"Failed", "DryRun"}
ACTIVE_STATES = {"Blocked", "Patching", "AwaitingConvergence", "Deleting"}


@dataclass
class PVCInfo:
    name: str
    ordinal: int
    spec_storage: str
    status_storage: Optional[str]
    spec_vac: Optional[str]
    status_vac: Optional[str]
    storage_class: Optional[str]


@dataclass
class StatefulSetInfo:
    name: str
    namespace: str
    replicas: int
    ready_replicas: int
    volume_claim_templates: list
    desired_annotation: Optional[dict]
    status_annotation: Optional[dict]
    skip_annotation: bool


def get_apps_api():
    return client.AppsV1Api(kube_get_client())


def get_core_api():
    return client.CoreV1Api(kube_get_client())


def read_statefulset(namespace: str, name: str) -> StatefulSetInfo:
    apps_api = get_apps_api()
    sts = apps_api.read_namespaced_stateful_set(name, namespace)

    annotations = sts.metadata.annotations or {}

    desired_raw = annotations.get(ANNOTATION_DESIRED)
    desired = json.loads(desired_raw) if desired_raw else None

    status_raw = annotations.get(ANNOTATION_STATUS)
    status = json.loads(status_raw) if status_raw else None

    skip = annotations.get(ANNOTATION_SKIP, "").lower() == "true"

    vcts = []
    if sts.spec.volume_claim_templates:
        for vct in sts.spec.volume_claim_templates:
            vcts.append(
                {
                    "name": vct.metadata.name,
                    "storage": vct.spec.resources.requests.get("storage", ""),
                    "storageClassName": vct.spec.storage_class_name or "",
                    "volumeAttributesClassName": getattr(
                        vct.spec, "volume_attributes_class_name", None
                    )
                    or "",
                }
            )

    return StatefulSetInfo(
        name=name,
        namespace=namespace,
        replicas=sts.spec.replicas or 0,
        ready_replicas=sts.status.ready_replicas or 0,
        volume_claim_templates=vcts,
        desired_annotation=desired,
        status_annotation=status,
        skip_annotation=skip,
    )


def list_pvcs_for_statefulset(
    namespace: str, sts_name: str, claim_names: list[str]
) -> list[PVCInfo]:
    core_api = get_core_api()
    pvcs = []

    for claim_name in claim_names:
        label_selector = (
            f"app.kubernetes.io/name={sts_name}"
        )
        try:
            pvc_list = core_api.list_namespaced_persistent_volume_claim(
                namespace=namespace,
                label_selector=label_selector,
            )
        except ApiException:
            pvc_list = type("obj", (object,), {"items": []})()

        prefix = f"{claim_name}-{sts_name}-"
        matched = []
        for pvc in pvc_list.items:
            if pvc.metadata.name.startswith(prefix):
                try:
                    ordinal = int(pvc.metadata.name[len(prefix) :])
                except ValueError:
                    continue
                matched.append((ordinal, pvc))

        if not matched:
            for ordinal in range(100):
                pvc_name = f"{claim_name}-{sts_name}-{ordinal}"
                try:
                    pvc = core_api.read_namespaced_persistent_volume_claim(
                        pvc_name, namespace
                    )
                    matched.append((ordinal, pvc))
                except ApiException as e:
                    if e.status == 404:
                        break
                    raise

        for ordinal, pvc in sorted(matched, key=lambda x: x[0]):
            spec_storage = pvc.spec.resources.requests.get("storage", "?")
            status_storage = None
            if pvc.status and pvc.status.capacity:
                status_storage = pvc.status.capacity.get("storage")

            spec_vac = getattr(pvc.spec, "volume_attributes_class_name", None)
            status_vac = getattr(
                pvc.status, "current_volume_attributes_class_name", None
            )
            storage_class = pvc.spec.storage_class_name

            pvcs.append(
                PVCInfo(
                    name=pvc.metadata.name,
                    ordinal=ordinal,
                    spec_storage=spec_storage,
                    status_storage=status_storage,
                    spec_vac=spec_vac,
                    status_vac=status_vac,
                    storage_class=storage_class,
                )
            )

    return pvcs


def build_desired_annotation(
    claim_name: str,
    storage: Optional[str] = None,
    vac: Optional[str] = None,
    batch_size: int = 0,
) -> dict:
    claim_spec = {}
    if storage:
        claim_spec["storage"] = storage
    if vac:
        claim_spec["volumeAttributesClassName"] = vac

    annotation = {
        "version": 1,
        "claims": {claim_name: claim_spec},
    }
    if batch_size > 0:
        annotation["batchSize"] = batch_size

    return annotation


def apply_desired_annotation(namespace: str, sts_name: str, annotation: dict) -> None:
    apps_api = get_apps_api()
    body = {
        "metadata": {
            "annotations": {
                ANNOTATION_DESIRED: json.dumps(annotation),
            }
        }
    }
    apps_api.patch_namespaced_stateful_set(sts_name, namespace, body)


def remove_annotations(namespace: str, sts_name: str) -> None:
    apps_api = get_apps_api()
    body = {
        "metadata": {
            "annotations": {
                ANNOTATION_DESIRED: None,
                ANNOTATION_STATUS: None,
            }
        }
    }
    apps_api.patch_namespaced_stateful_set(sts_name, namespace, body)


def parse_storage_quantity(quantity: str) -> int:
    """Parse a Kubernetes storage quantity string into bytes."""
    quantity = quantity.strip()
    suffixes = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }
    for suffix, multiplier in sorted(suffixes.items(), key=lambda x: -len(x[0])):
        if quantity.endswith(suffix):
            return int(quantity[: -len(suffix)]) * multiplier
    return int(quantity)


def validate_storage_expansion(current: str, target: str) -> None:
    current_bytes = parse_storage_quantity(current)
    target_bytes = parse_storage_quantity(target)
    if target_bytes < current_bytes:
        raise click.BadParameter(
            f"Target storage {target} is smaller than current {current}. "
            "Kubernetes does not support shrinking PVCs."
        )
    if target_bytes == current_bytes:
        raise click.BadParameter(
            f"Target storage {target} is the same as current {current}. "
            "Nothing to do."
        )


def format_timestamp(ts_str: Optional[str]) -> str:
    if not ts_str:
        return "?"
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - ts
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return f"{total_seconds}s ago"
        elif total_seconds < 3600:
            return f"{total_seconds // 60}m ago"
        else:
            return f"{total_seconds // 3600}h {(total_seconds % 3600) // 60}m ago"
    except (ValueError, TypeError):
        return ts_str


def print_status(sts_info: StatefulSetInfo, pvcs: list[PVCInfo]) -> None:
    click.echo(
        f"StatefulSet: {sts_info.namespace}/{sts_info.name} "
        f"({sts_info.replicas} replicas, {sts_info.ready_replicas} ready)"
    )

    if sts_info.skip_annotation:
        click.secho("Skip:        true (controller will ignore this StatefulSet)", fg="yellow")

    status = sts_info.status_annotation
    if status:
        state = status.get("state", "?")
        since = format_timestamp(status.get("lastTransition"))
        click.echo(f"State:       {state} (since {since})")

        wave = status.get("waveOrdinals")
        if wave is not None:
            click.echo(f"Batch:       ordinals {wave}")

        reason = status.get("reason")
        if reason:
            click.secho(f"Reason:      {reason}", fg="red")

        click.echo()

        pvc_statuses = status.get("pvcs", {})
        header = f"  {'PVC':<30} {'Spec':<10} {'Status':<10} {'VAC':<20} {'State':<15}"
        click.echo(header)
        for pvc in pvcs:
            pvc_state = pvc_statuses.get(pvc.name, "")
            status_storage = pvc.status_storage or ""

            spec_match = ""
            if pvc.status_storage and pvc.spec_storage:
                if pvc.status_storage == pvc.spec_storage:
                    spec_match = click.style(pvc.status_storage, fg="green")
                else:
                    spec_match = click.style(pvc.status_storage + " ...", fg="yellow")
            else:
                spec_match = status_storage

            vac_display = pvc.spec_vac or "-"
            click.echo(
                f"  {pvc.name:<30} {pvc.spec_storage:<10} "
                f"{spec_match:<10} {vac_display:<20} {pvc_state:<15}"
            )
    elif sts_info.desired_annotation:
        click.secho(
            "State:       pending (annotation set, controller has not started)",
            fg="yellow",
        )
        click.echo()
        _print_pvc_table_idle(pvcs)
    else:
        click.echo("State:       idle (no reconcile in progress)")
        click.echo()
        _print_pvc_table_idle(pvcs)


def _print_pvc_table_idle(pvcs: list[PVCInfo]) -> None:
    header = f"  {'PVC':<30} {'Storage':<10} {'VAC':<20}"
    click.echo(header)
    for pvc in pvcs:
        vac = pvc.spec_vac or "-"
        click.echo(f"  {pvc.name:<30} {pvc.spec_storage:<10} {vac:<20}")


def print_resize_preview(
    sts_info: StatefulSetInfo,
    pvcs: list[PVCInfo],
    claim_name: str,
    target_storage: Optional[str],
    target_vac: Optional[str],
    batch_size: int,
) -> None:
    click.echo(
        f"StatefulSet: {sts_info.namespace}/{sts_info.name} "
        f"({sts_info.replicas} replicas, {sts_info.ready_replicas} ready)"
    )

    storage_class = ""
    for pvc in pvcs:
        if pvc.storage_class:
            storage_class = pvc.storage_class
            break

    sc_display = f" (storageClass: {storage_class})" if storage_class else ""
    click.echo(f"\n  Claim: {claim_name}{sc_display}\n")

    header = f"  {'PVC':<30} {'Current':<12} {'Target':<12} {'Change'}"
    click.echo(header)

    for pvc in pvcs:
        changes = []
        target_col = ""

        if target_storage:
            target_col = target_storage
            if pvc.spec_storage != target_storage:
                changes.append("expand storage")
            else:
                changes.append("(no change)")
        else:
            target_col = pvc.spec_storage

        if target_vac:
            if pvc.spec_vac != target_vac:
                changes.append(f"set VAC -> {target_vac}")

        change_str = ", ".join(changes) if changes else "(no change)"
        click.echo(f"  {pvc.name:<30} {pvc.spec_storage:<12} {target_col:<12} {change_str}")

    if batch_size > 0:
        ordinals = [pvc.ordinal for pvc in pvcs]
        batches = []
        for i in range(0, len(ordinals), batch_size):
            batches.append(ordinals[i : i + batch_size])
        batch_desc = ", then ".join(str(b) for b in batches)
        click.echo(f"\n  Batch size: {batch_size} (ordinals {batch_desc})")

    click.echo(
        "\n  After PVCs converge, the StatefulSet will be orphan-deleted"
        "\n  (pods keep running) and recreated with updated volumeClaimTemplates."
    )


def monitor_reconciliation(
    namespace: str,
    sts_name: str,
    claim_names: list[str],
    poll_interval: float = 3.0,
) -> bool:
    last_state = None
    last_pvc_states: dict[str, str] = {}

    click.echo()
    while True:
        try:
            sts_info = read_statefulset(namespace, sts_name)
        except ApiException as e:
            if e.status == 404:
                _log("StatefulSet not found (may have been orphan-deleted)")
                _log("Waiting for recreation...")
                if _wait_for_sts_recreation(namespace, sts_name, poll_interval):
                    _log("StatefulSet recreated")
                    sts_info = read_statefulset(namespace, sts_name)
                    if sts_info.ready_replicas == sts_info.replicas:
                        _log(
                            f"Rollout complete "
                            f"({sts_info.ready_replicas}/{sts_info.replicas} replicas ready)"
                        )
                        click.secho("Done", fg="green", bold=True)
                        return True
                else:
                    click.secho(
                        "Timed out waiting for StatefulSet recreation.", fg="red"
                    )
                    return False
            raise

        status = sts_info.status_annotation

        if not status and not sts_info.desired_annotation:
            _log("Reconciliation complete (annotations cleared)")
            click.secho("Done", fg="green", bold=True)
            return True

        if not status:
            time.sleep(poll_interval)
            continue

        state = status.get("state", "?")
        if state != last_state:
            _log(f"State: {state}")
            last_state = state

        if state in TERMINAL_STATES:
            reason = status.get("reason", "unknown")
            click.secho(f"  Reason: {reason}", fg="red")
            return state != "Failed"

        pvc_statuses = status.get("pvcs", {})
        for pvc_name, pvc_state in pvc_statuses.items():
            if pvc_state != last_pvc_states.get(pvc_name):
                _log(f"  {pvc_name}: {pvc_state}")
                last_pvc_states[pvc_name] = pvc_state

        time.sleep(poll_interval)


def _wait_for_sts_recreation(
    namespace: str, sts_name: str, poll_interval: float, timeout: float = 600
) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            read_statefulset(namespace, sts_name)
            return True
        except ApiException as e:
            if e.status == 404:
                time.sleep(poll_interval)
                continue
            raise
    return False


def _log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    click.echo(f"[{ts}] {message}")
