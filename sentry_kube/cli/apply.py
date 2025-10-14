import concurrent.futures
import contextlib
import copy
import functools
import os
import subprocess
import tempfile
import urllib.error
from functools import wraps
from typing import Iterator, List, Sequence
from time import sleep
import shutil

import click
import yaml

from libsentrykube.datadog import check_monitors
from libsentrykube.datadog import MissingOverallStateException
from libsentrykube.datadog import MissingDataDogAppKeyException
from libsentrykube.events import report_event_for_service_list
from libsentrykube.kube import materialize, render_templates
from libsentrykube.service import get_service_names
from libsentrykube.utils import (
    KUBECTL_VERSION,
    chunked,
    ensure_kubectl,
    macos_notify,
    pretty,
)

__all__ = (
    "apply",
    "render",
    "diff",
)

# Number of objects to process in parallel when diffing against the live version.
# Larger number = faster, but more memory, I/O and CPU over that shorter period of
# time.
KUBECTL_DIFF_CONCURRENCY: int = int(
    os.environ.get("SENTRY_KUBE_KUBECTL_DIFF_CONCURRENCY", 1)
)
DEFAULT_SOAK_TIME_S = 120


def allow_for_all_services(f):
    """
    This decorator adds `--all` and `--exclude`.
    """

    @wraps(f)
    @click.argument("services", nargs=-1, type=str)
    @click.option("--all", "-a", "all_", is_flag=True, help="Select all services.")
    @click.option(
        "--exclude",
        default="",
        type=str,
        help="Comma-delimited string of service names to exclude.",
    )
    def wrapper(*args, **kwargs):
        services = list(kwargs.get("services"))
        all_services = get_service_names()
        if not all_services:
            raise click.UsageError("No services found.")

        all_services_pretty = "\n".join(f"- {s}" for s in sorted(all_services))

        if kwargs.pop("all_"):
            if services:
                raise click.BadArgumentUsage(
                    "You specified '--all' along with some service names, "
                    "what do you actually want?\n"
                    f"Services:\n{all_services_pretty}"
                )
            services = all_services
        elif not services:
            raise click.BadArgumentUsage(
                f"No service names provided. Services:\n{all_services_pretty}"
            )

        excludes = kwargs.pop("exclude")
        if excludes:
            for svc in excludes.split(","):
                services.remove(svc)

        kwargs["services"] = services

        return f(*args, **kwargs)

    return wrapper


def _render(
    ctx,
    services,
    raw=False,
    skip_kinds=None,
    filters=None,
    use_canary: bool = False,
):
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name

    for service_name in services:
        if service_name == "snuba":
            canary_filter = tuple(["metadata.labels.is_canary=true"])
        else:
            canary_filter = tuple(["metadata.labels.env=canary"])

        if use_canary and filters is None:
            filters = canary_filter
        elif use_canary:
            filters = filters + canary_filter  # type: ignore
        out = render_templates(
            customer_name,
            service_name,
            cluster_name,
            skip_kinds=skip_kinds,
            filters=filters,  # type: ignore
        )
        yield out if raw else pretty(out)


def _materialize(ctx, services: Sequence[str]) -> None:
    """
    Render a service and saves it to a file.
    """
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name
    for service in services:
        materialize(customer_name, service, cluster_name)


def _run_kubectl_diff(kubectl_cmd: List[str], important_diffs_only: bool) -> str:
    """
    Run kubectl diff with --important-diffs-only support
    """
    new_env = None
    if important_diffs_only:
        new_env = copy.deepcopy(os.environ)

        # Since we inject our wrapper tool into KUBECTL_EXTERNAL_DIFF env
        # variable, this can conflict when user uses KUBECTL_EXTERNAL_DIFF
        # along with --important-diffs-only
        #
        # To honor user defined KUBECTL_EXTERNAL_DIFF, we need to preserve
        # original KUBECTL_EXTERNAL_DIFF into ORIG_KUBECTL_EXTERNAL_DIFF env
        # variable. This allow us to run user defined diff tool in the wrapper
        #
        orig_kubectl_external_diff = new_env.get("KUBECTL_EXTERNAL_DIFF")
        if orig_kubectl_external_diff:
            new_env["ORIG_KUBECTL_EXTERNAL_DIFF"] = orig_kubectl_external_diff

        # Inject our wrapper into KUBECTL_EXTERNAL_DIFF env to filter out unwanted info

        # Find out where the important-diffs-only script is located
        binary_name = "important-diffs-only"
        kubectl_external_diff_cmd = shutil.which(binary_name)
        if not kubectl_external_diff_cmd:
            raise click.ClickException(f"Could not find {binary_name} in PATH")

        new_env["KUBECTL_EXTERNAL_DIFF"] = kubectl_external_diff_cmd

    child_process = subprocess.Popen(
        kubectl_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=new_env
    )
    child_process_output = child_process.communicate()[0].decode("utf-8")

    if not child_process_output and child_process.returncode != 0:
        raise click.ClickException("'kubectl diff' aborted")

    return child_process_output


def _diff_kubectl(
    ctx,
    definitions,
    server_side=None,
    important_diffs_only: bool = False,
    return_output: bool = False,
):
    """
    Run kubectl-based diff concurrently and print out the results in color.
    """
    # Handle scenarios where an empty definitions is passed in, like when filters
    # don't have any matches
    if not definitions:
        if return_output:
            return (False, None)
        return False

    click.echo("Waiting on kubectl diff.")
    cmd = [
        f"{ensure_kubectl()}",
        "--context",
        ctx.obj.context_name,
        "diff",
    ]
    if server_side is not None:
        cmd.append(f"--server-side={str(bool(server_side)).lower()}")

    # kubectl diff --concurrency won't have any effect if the input is STDIN
    # (due to its internal visitor implementation).
    # It needs multiple files to fully utilize concurrency implementation.
    yaml_docs = [
        yaml.dump(yaml_doc)
        for yaml_doc in yaml.load_all(
            definitions.decode("utf-8"), Loader=yaml.SafeLoader
        )
    ]

    @contextlib.contextmanager
    def _dump_yaml_docs_to_tmpdir(yaml_docs: List[str]) -> Iterator[str]:
        with tempfile.TemporaryDirectory() as tmpdirname:
            for yaml_doc in yaml_docs:
                with tempfile.NamedTemporaryFile(
                    delete=False, prefix=f"{tmpdirname}/", suffix=".yaml"
                ) as f:
                    f.write(yaml_doc.encode("utf-8"))

            yield tmpdirname

    # --concurrency was introduced since kubectl 1.28
    # TODO(hubertchan): Once we're on kubectl 1.28 everywhere, we can probably
    # remove my manual concurrency hack
    if KUBECTL_VERSION >= "1.28":
        cmd.append(f"--concurrency={KUBECTL_DIFF_CONCURRENCY}")
        with _dump_yaml_docs_to_tmpdir(yaml_docs) as tmpdirname:
            output = _run_kubectl_diff(
                cmd + ["-f", tmpdirname],
                important_diffs_only=important_diffs_only,
            )
    else:
        # For older kubectl version, using threading to increase concurrency
        #
        # NOTE: our dummy threading implementation might change the order of diff output.
        # If you really need sorted diff like native kubectl diff, then you would need
        # to set concurrency to 1
        with (
            contextlib.ExitStack() as stack,
            concurrent.futures.ThreadPoolExecutor(
                max_workers=KUBECTL_DIFF_CONCURRENCY
            ) as executor,
        ):
            chunk_size = max(len(yaml_docs) // KUBECTL_DIFF_CONCURRENCY, 1)
            kubectl_diff_cmds = [
                cmd
                + [
                    "-f",
                    stack.enter_context(_dump_yaml_docs_to_tmpdir(chunked_yaml_docs)),
                ]
                for chunked_yaml_docs in chunked(yaml_docs, chunk_size)
            ]
            output = "".join(
                executor.map(
                    functools.partial(
                        _run_kubectl_diff,
                        important_diffs_only=important_diffs_only,
                    ),
                    kubectl_diff_cmds,
                )
            )

    # Output is empty or just whitespaces/newlines
    if not output or output.isspace():
        if return_output:
            return (False, None)
        return False

    # Print the colored diff
    lines = output.split("\n")
    for line in lines:
        # blocking garbage output
        if all(
            [keyword in line for keyword in ['"apiVersion"', '"kind"', '"metadata"']]
        ) or any(
            [
                "kubectl.kubernetes.io/last-applied-configuration" in line,
                "diff -u -N" in line,
            ]
        ):
            continue
        # start of new block, leave a newline
        if "---" in line:
            click.echo("\n")
        if line.startswith("+"):
            click.secho(line, fg="green")
        elif line.startswith("-"):
            click.secho(line, fg="red")
        else:
            click.echo(line)

    macos_notify("sentry-kube diff", "Diff complete.")
    has_diffs = len(lines) > 0
    if return_output:
        return (has_diffs, lines if has_diffs else None)
    return has_diffs


@click.command()
@click.option("--raw", is_flag=True)
@click.option("--pager/--no-pager", default=True)
@click.option("--filter", "filters", multiple=True)
@click.option("--materialize", is_flag=True)
@click.option("--use-canary", is_flag=True, default=False)
@click.pass_context
@allow_for_all_services
def render(ctx, services, raw, pager, filters, materialize, use_canary: bool):
    """
    Render a service(s).

    This is non-destructive and just renders the service(s) to stdout.
    """
    if materialize:
        _materialize(ctx, services)
    else:
        rendered = _render(ctx, services, raw, filters=filters, use_canary=use_canary)
        if pager:
            click.echo_via_pager(rendered)
        else:
            click.echo("".join(rendered))


def _set_deployment_image(services: List[str], deployment_image: str | None) -> None:
    if len(services) > 1 and deployment_image:
        raise click.BadArgumentUsage(
            "Cannot specify --deployment-image with multiple services"
        )
    elif deployment_image:
        os.environ["DEPLOYMENT_IMAGE"] = deployment_image


@click.command()
@click.pass_context
@click.option("--filter", "filters", multiple=True)
@click.option(
    "--server-side",
    type=bool,
    default=None,
    show_default=True,
    help="Use server-side rendering",
)
@click.option(
    "--important-diffs-only",
    "-i",
    is_flag=True,
    help="Ignore diffs which consist only of generation, image, and configVersion",
)
@click.option("--use-canary", is_flag=True, default=False)
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
@allow_for_all_services
def diff(
    ctx,
    services,
    filters,
    server_side,
    important_diffs_only: bool,
    use_canary: bool,
    allow_jobs: bool,
    deployment_image: str | None = None,
    exit_with_result: bool = True,
):
    """
    Render a diff between production and local configs, using a wrapper around
    "kubectl diff".

    This is non-destructive and tells you what would be applied, if
    anything, with your current changes.
    """
    _set_deployment_image(services, deployment_image)

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

    if use_canary:
        click.secho(
            "--use-canary specificed, limiting to canaries.",
            fg="red",
        )

    if exit_with_result:
        diff_result = _diff_kubectl(
            ctx=ctx,
            definitions=definitions,
            server_side=server_side,
            important_diffs_only=important_diffs_only,
        )
        ctx.exit(diff_result)
    else:
        diff_result, output_lines = _diff_kubectl(
            ctx=ctx,
            definitions=definitions,
            server_side=server_side,
            important_diffs_only=important_diffs_only,
            return_output=True,
        )
        return output_lines


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
    help="Amount of time (in seconds) to wait after applying changes to canaries before proceeding to remaining deployments.",
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
    _set_deployment_image(services, deployment_image)

    customer_name = ctx.obj.customer_name
    service_monitors = ctx.obj.service_monitors

    if use_canary:
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

        # For each service applied, check specified monitors aren't in Warning or Alert state.
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
                        "specified to be checked, do you want to proceed to all deployments?"
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
):
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

    ctx.exit(0)
