import subprocess
import urllib.error
from time import sleep

import click

from sentry_kube.cli.util import allow_for_all_services, _set_deployment_image_env
from sentry_kube.cli.render import _render
from sentry_kube.cli.diff import _diff_kubectl

from libsentrykube.datadog import check_monitors
from libsentrykube.datadog import MissingOverallStateException
from libsentrykube.datadog import MissingDataDogAppKeyException
from libsentrykube.events import report_event_for_service_list
from libsentrykube.utils import ensure_kubectl, macos_notify

__all__ = ("apply",)

# Number of objects to process in parallel when diffing against the live version.
# Larger number = faster, but more memory, I/O and CPU over that shorter period of
# time.
DEFAULT_SOAK_TIME_S = 120


@click.command()
@click.option("--yes", "-y", is_flag=True)
@click.option("--filter", "filters", multiple=True)
@click.option(
    "--server-side",
    type=bool,
    default=None,
    show_default=True,
    help="Use server-side apply",
)
@click.option(
    "--important-diffs-only",
    "-i",
    is_flag=True,
    help="Ignore diffs which consist only of generation, image, and configVersion",
)
@click.option(
    "--use-canary/--bypass-canary",
    is_flag=True,
    default=True,
    help="Skip deploying through canary first",
)
@click.option(
    "--soak-time",
    default=DEFAULT_SOAK_TIME_S,
    help=(
        "Amount of time (in seconds) to wait after applying changes to canaries "
        "before proceeding to remaining deployments."
    ),
)
@click.option(
    "--skip-monitor-checks",
    is_flag=True,
    default=False,
    help="Skip checking specified DataDog monitors.",
)
@click.option(
    "--soak-only",
    is_flag=True,
    default=False,
    help="Skip canary deploy and wait for soak before applying to everything.",
)
@click.option(
    "--allow-jobs",
    "-j",
    is_flag=True,
    help="Allows regular diff/apply to spawn Jobs",
)
@click.option(
    "--deployment-image",
    type=str,
    help="Override the deployment image for the services",
)
@click.pass_context
@allow_for_all_services
def apply(
    ctx,
    services,
    yes,
    filters,
    server_side,
    important_diffs_only: bool,
    use_canary: bool,
    soak_time: int,
    skip_monitor_checks: bool,
    soak_only: bool,
    allow_jobs: bool,
    deployment_image: str | None = None,
):
    _set_deployment_image_env(services, deployment_image)

    customer_name = ctx.obj.customer_name
    service_monitors = ctx.obj.service_monitors

    if use_canary:
        canary_applied = False

        # Start with canary if we haven't said to retry soak
        if not soak_only:
            click.secho("\nStarting by deploying to canaries only first.\n")
            canary_applied = _apply(
                ctx,
                services,
                yes,
                filters,
                server_side,
                important_diffs_only,
                allow_jobs,
                True,
                quiet=ctx.obj.quiet_mode,
            )

        has_soaked = False

        # For each service applied,
        # check specified monitors aren't in Warning or Alert state.
        # If no monitor IDs are available then default to a manual prompt.
        if not canary_applied:
            click.echo(
                f"\nNo canary changes for {services} skipping validation/soaking.\n"
            )
        else:
            for service in services:
                if skip_monitor_checks or service not in service_monitors:
                    if not click.confirm(
                        f"\nFinished deploying to canary for {service} and no monitors "
                        "specified to be checked, "
                        "do you want to proceed to all deployments?"
                    ):
                        return
                else:
                    monitor_ids_to_check = service_monitors[service]

                    # We only need to soak for the first service
                    if not has_soaked:
                        click.echo(
                            f"Waiting for {soak_time}s soak time, will check monitors {monitor_ids_to_check}..."
                        )
                        sleep(soak_time)
                        has_soaked = True

                    try:
                        monitor_check_result = check_monitors(monitor_ids_to_check)
                    except urllib.error.HTTPError as e:
                        raise SystemExit(
                            f"Unexpected HTTP response ({e.code}) while checking {monitor_ids_to_check}."
                        )
                    except MissingOverallStateException as e:
                        raise SystemExit(e)
                    except MissingDataDogAppKeyException as e:
                        raise SystemExit(e)

                    if not monitor_check_result:
                        return
                    click.echo(
                        f"Verified monitor ids {click.style(monitor_ids_to_check, fg='green')} "
                        f"are not in error state for customer: {click.style(customer_name, fg='green')}, "
                        f"service: {click.style(service, fg='green')}, proceeding."
                    )

    # Deploy to all if we confirm to proceed
    _apply(
        ctx,
        services,
        yes,
        filters,
        server_side,
        important_diffs_only,
        allow_jobs,
        False,
        quiet=ctx.obj.quiet_mode,
    )


def _apply(
    ctx,
    services,
    yes,
    filters,
    server_side,
    important_diffs_only: bool,
    allow_jobs: bool,
    use_canary: bool,
    quiet: bool = False,
) -> bool:
    """
    Apply a service(s) to production, using a basic confirmation wrapper around
    "kubectl apply".

    The regular "sentry-kube apply" currently has issues when dealing with custom
    resources, so this can be used as a workaround.
    """
    customer_name = ctx.obj.customer_name
    click.echo(f"Rendering services: {', '.join(services)}")
    skip_kinds = ("Job",) if not allow_jobs else None
    definitions = "".join(
        _render(
            ctx,
            services,
            skip_kinds=skip_kinds,
            filters=filters,
            use_canary=use_canary,
        ),
    ).encode("utf-8")

    if not _diff_kubectl(ctx, definitions, server_side, important_diffs_only):
        click.echo("Nothing to apply.")
        macos_notify("sentry-kube apply", "Nothing to apply.")
        return False

    if not (
        yes
        or click.confirm(
            "Are you sure you want to apply this for region "
            f"{click.style(customer_name, fg='yellow', bold=True)}"
            ", cluster "
            f"{click.style(ctx.obj.cluster_name, fg='yellow', bold=True)}"
            "?"
        )
    ):
        raise click.Abort()

    # Run "kubectl apply"
    apply_cmd = [
        f"{ensure_kubectl()}",
        "--context",
        ctx.obj.context_name,
        "apply",
        "-f",
        "/dev/stdin",
    ]
    if server_side is not None:
        apply_cmd.append(f"--server-side={str(bool(server_side)).lower()}")

    child_process = subprocess.Popen(apply_cmd, stdin=subprocess.PIPE)
    child_process.communicate(definitions)
    try:
        report_event_for_service_list(
            customer_name,
            ctx.obj.cluster_name,
            operation="apply",
            services=services,
            quiet=quiet,
        )
    except Exception as e:
        click.echo("!! Could not report an event to DataDog:")
        click.secho(e, bold=True)

    macos_notify("sentry-kube apply", "Apply complete.")

    return True
