import sys

import click

from libsentrykube.statefulset import (
    apply_desired_annotation,
    build_desired_annotation,
    list_pvcs_for_statefulset,
    monitor_reconciliation,
    print_resize_preview,
    print_status,
    read_statefulset,
    remove_annotations,
    validate_storage_expansion,
)

__all__ = ("statefulset", "sts")


class _AliasedGroup(click.Group):
    """A click.Group subclass that supports lazy-copying commands from a source group."""

    def __init__(self, *args, source_group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._source_group = source_group

    def list_commands(self, ctx):
        if self._source_group:
            return self._source_group.list_commands(ctx)
        return super().list_commands(ctx)

    def get_command(self, ctx, cmd_name):
        if self._source_group:
            return self._source_group.get_command(ctx, cmd_name)
        return super().get_command(ctx, cmd_name)


@click.group(name="statefulset")
def statefulset():
    """Manage StatefulSet PVC reconciliation via kube-sts-reconciler."""
    pass


sts = _AliasedGroup(name="sts", help="Alias for 'statefulset'.", source_group=statefulset, hidden=True)


@statefulset.command()
@click.argument("namespace")
@click.argument("sts_name")
@click.pass_context
def status(ctx, namespace, sts_name):
    """Show current reconcile state of a StatefulSet."""
    sts_info = read_statefulset(namespace, sts_name)

    claim_names = [vct["name"] for vct in sts_info.volume_claim_templates]
    if not claim_names:
        click.echo(
            f"StatefulSet {namespace}/{sts_name} has no volumeClaimTemplates."
        )
        return

    pvcs = list_pvcs_for_statefulset(namespace, sts_name, claim_names)
    print_status(sts_info, pvcs)


@statefulset.command()
@click.argument("namespace")
@click.argument("sts_name")
@click.option("--claim", "claim_name", help="volumeClaimTemplate name (required if multiple exist)")
@click.option("--storage", help="Target storage size (e.g. 20Gi)")
@click.option("--vac", help="Target VolumeAttributesClass name")
@click.option("--batch-size", type=int, default=0, help="Patch N ordinals at a time (0 = all at once)")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.option("--no-monitor", is_flag=True, help="Apply annotation and exit without monitoring")
@click.pass_context
def resize(ctx, namespace, sts_name, claim_name, storage, vac, batch_size, yes, no_monitor):
    """Resize PVCs for a StatefulSet via kube-sts-reconciler.

    Requires --storage and/or --vac to specify the target PVC spec.
    """
    if not storage and not vac:
        raise click.UsageError(
            "At least one of --storage or --vac must be specified."
        )

    sts_info = read_statefulset(namespace, sts_name)

    if sts_info.desired_annotation:
        click.secho(
            f"A reconcile is already in progress for {namespace}/{sts_name}.",
            fg="red",
        )
        click.echo(
            "Use 'sentry-kube statefulset cancel' to stop it first, "
            "or 'sentry-kube statefulset status' to check progress."
        )
        sys.exit(1)

    if sts_info.skip_annotation:
        click.secho(
            f"StatefulSet {namespace}/{sts_name} has skip=true annotation. "
            "The controller will not act on it.",
            fg="yellow",
        )
        if not yes and not click.confirm("Continue anyway?"):
            raise click.Abort()

    vcts = sts_info.volume_claim_templates
    if not vcts:
        click.echo(f"StatefulSet {namespace}/{sts_name} has no volumeClaimTemplates.")
        sys.exit(1)

    if claim_name:
        matching = [v for v in vcts if v["name"] == claim_name]
        if not matching:
            available = ", ".join(v["name"] for v in vcts)
            raise click.BadParameter(
                f"Claim '{claim_name}' not found. Available: {available}",
                param_hint="--claim",
            )
    elif len(vcts) == 1:
        claim_name = vcts[0]["name"]
    else:
        available = ", ".join(v["name"] for v in vcts)
        raise click.UsageError(
            f"Multiple volumeClaimTemplates found ({available}). "
            "Use --claim to specify which one."
        )

    pvcs = list_pvcs_for_statefulset(namespace, sts_name, [claim_name])
    if not pvcs:
        click.echo(f"No PVCs found for claim '{claim_name}' on {namespace}/{sts_name}.")
        sys.exit(1)

    if storage:
        for pvc in pvcs:
            validate_storage_expansion(pvc.spec_storage, storage)

    print_resize_preview(sts_info, pvcs, claim_name, storage, vac, batch_size)

    click.echo()
    click.secho(
        "WARNING: The code has not been updated. Make sure the next deploy uses "
        "matching volumeClaimTemplate values.",
        fg="yellow",
    )

    if not yes and not click.confirm("\nProceed?"):
        raise click.Abort()

    annotation = build_desired_annotation(claim_name, storage, vac, batch_size)
    click.echo(f"\nApplying annotation to {namespace}/{sts_name}...")
    apply_desired_annotation(namespace, sts_name, annotation)
    click.echo("Annotation applied.")

    if no_monitor:
        click.echo(
            "Use 'sentry-kube statefulset status' to check progress, "
            "or re-run without --no-monitor to stream live updates."
        )
        return

    click.echo("Monitoring reconciliation (Ctrl-C to detach, operation continues)...")
    try:
        success = monitor_reconciliation(namespace, sts_name, [claim_name])
    except KeyboardInterrupt:
        click.echo("\nDetached. The controller continues working in the background.")
        click.echo("Use 'sentry-kube statefulset status' to check progress.")
        return

    if not success:
        sys.exit(1)


@statefulset.command()
@click.argument("namespace")
@click.argument("sts_name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def cancel(ctx, namespace, sts_name, yes):
    """Cancel an in-progress reconcile by removing annotations.

    PVCs already patched stay patched (storage expansion is irreversible).
    """
    sts_info = read_statefulset(namespace, sts_name)

    if not sts_info.desired_annotation and not sts_info.status_annotation:
        click.echo(f"No reconcile in progress for {namespace}/{sts_name}.")
        return

    if sts_info.status_annotation:
        state = sts_info.status_annotation.get("state", "?")
        click.echo(f"Current state: {state}")

        if state == "Deleting":
            click.secho(
                "\nWARNING: StatefulSet may have already been orphan-deleted. "
                "Canceling will leave it deleted.\n"
                "You must manually recreate it or trigger a deploy.",
                fg="red",
            )

    claim_names = [vct["name"] for vct in sts_info.volume_claim_templates]
    pvcs = list_pvcs_for_statefulset(namespace, sts_name, claim_names)

    patched = [p for p in pvcs if p.spec_storage != p.status_storage]
    if patched:
        click.echo(f"\n{len(patched)} PVC(s) have pending storage changes (irreversible).")

    if not yes and not click.confirm(
        f"\nRemove reconciler annotations from {namespace}/{sts_name}?"
    ):
        raise click.Abort()

    remove_annotations(namespace, sts_name)
    click.echo("Annotations removed. Controller will stop acting on this StatefulSet.")
