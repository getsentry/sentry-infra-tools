import subprocess

import click

from libsentrykube.events import report_event_for_service
from libsentrykube.utils import ensure_kubectl, should_run_with_empty_context

__all__ = ("kubectl",)


@click.command(
    add_help_option=False,
    context_settings=dict(allow_extra_args=True, ignore_unknown_options=True),
)
@click.option("--yes", "-y", is_flag=True)
@click.option("--quiet", "-q", is_flag=True)
@click.pass_context
def kubectl(ctx, quiet, yes):
    """
    Confirmation wrapper for kubectl, for when you need to use it.
    """
    args = ctx.args
    context = ctx.obj.context_name
    customer = ctx.obj.customer_name
    quiet = ctx.obj.quiet_mode or quiet

    cmd = [f"{ensure_kubectl()}"]
    # todo untested
    if not should_run_with_empty_context():
        cmd += ["--context", context]

    cmd += list(args)

    if not quiet:
        click.echo(
            "Running the following for customer "
            f"{click.style(customer, fg='yellow', bold=True)}:\n\n"
            f"+ {' '.join(cmd)}"
        )

    def _confirm_dangerous_action():
        if not (
            yes
            or click.confirm(
                "Are you sure you want to do this to customer "
                f"{click.style(customer, fg='yellow', bold=True)}?"
            )
        ):
            raise click.Abort()

    for dangerous_token in ("delete",):
        if dangerous_token in args:
            click.secho(
                "\nWait! This seems like a DANGEROUS command.", fg="red", bold=True
            )
            _confirm_dangerous_action()
            break

    # confirm scale down to 0
    if "scale" in args:
        try:
            if args[args.index("--replicas") + 1] == "0":
                _confirm_dangerous_action()
        except (ValueError, IndexError):
            # incomplete command? kubectl shows help/usage
            pass

    if not quiet:
        click.echo()

    for recordable_token in (
        "annotate",
        "apply",
        "cordon",
        "create",
        "delete",
        "edit",
        "label",
        "patch",
        "replace",
        "rollout",
        "scale",
        "taint",
    ):
        if recordable_token in args:
            try:
                report_event_for_service(
                    ctx.obj.customer_name,
                    ctx.obj.cluster_name,
                    operation=f"kubectl {recordable_token}",
                    service_name="kubectl",
                    quiet=ctx.obj.quiet_mode,
                )
            except Exception as e:
                click.echo("!! Could not report an event to DataDog:")
                click.secho(e, bold=True)

    subprocess.run(cmd, check=False)
