import click
import os
import yaml
import contextlib
import tempfile
import copy
import shutil
import subprocess
from typing import Iterator, List, Tuple
from sentry_kube.cli.util import allow_for_all_services, _set_deployment_image_env
from sentry_kube.cli.render import _render
from libsentrykube.kube import resolve_ssa_flags
from libsentrykube.utils import (
    ensure_kubectl,
    macos_notify,
)

__all__ = ("diff",)


KUBECTL_DIFF_CONCURRENCY: int = int(
    os.environ.get("SENTRY_KUBE_KUBECTL_DIFF_CONCURRENCY", 1)
)


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
        kubectl_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=new_env,
    )
    stdout, stderr = child_process.communicate()
    child_process_output = stdout.decode("utf-8")
    child_process_error = stderr.decode("utf-8") if stderr else ""

    # kubectl diff exit codes:
    #   0 = no differences
    #   1 = differences found (normal)
    #   >1 = actual error
    #   <0 = killed by signal (POSIX)
    if child_process.returncode not in (0, 1):
        error_msg = "'kubectl diff' returned an error"
        if child_process_error:
            error_msg += f"\n{child_process_error}"
        raise click.ClickException(error_msg)

    if child_process_error:
        click.echo(child_process_error, err=True)

    return child_process_output


def should_skip_line(line: str) -> bool:
    return any(
        [keyword in line for keyword in ['"apiVersion"', '"kind"', '"metadata"']]
    ) or any(
        [
            "kubectl.kubernetes.io/last-applied-configuration" in line,
            "diff -u -N" in line,
        ]
    )


def print_diff_string(line: str) -> None:
    if "---" in line:
        click.echo("\n")

    if line.startswith("+"):
        click.secho(line, fg="green")
    elif line.startswith("-"):
        click.secho(line, fg="red")
    else:
        click.echo(line)


def print_diff(output: List[str]) -> None:
    for line in output:
        if should_skip_line(line):
            continue
        print_diff_string(line)


@contextlib.contextmanager
def _dump_yaml_docs_to_tmpdir(yaml_docs: List[str]) -> Iterator[str]:
    with tempfile.TemporaryDirectory() as tmpdirname:
        for yaml_doc in yaml_docs:
            with tempfile.NamedTemporaryFile(
                delete=False, prefix=f"{tmpdirname}/", suffix=".yaml"
            ) as f:
                f.write(yaml_doc.encode("utf-8"))

        yield tmpdirname


def _diff_kubectl(
    ctx,
    definitions,
    server_side: bool = False,
    force_conflicts: bool = False,
    important_diffs_only: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Run kubectl-based diff concurrently and print out the results in color.
    """
    # Handle scenarios where an empty definitions is passed in, like when filters
    # don't have any matches
    if not definitions:
        return (False, [])

    click.echo("Waiting on kubectl diff.")
    cmd = [
        f"{ensure_kubectl()}",
        "--context",
        ctx.obj.context_name,
        "diff",
        f"--server-side={str(bool(server_side)).lower()}",
    ]
    if server_side and force_conflicts:
        cmd.append("--force-conflicts")

    # kubectl diff --concurrency won't have any effect if the input is STDIN
    # (due to its internal visitor implementation).
    # It needs multiple files to fully utilize concurrency implementation.
    yaml_docs = [
        yaml.dump(yaml_doc)
        for yaml_doc in yaml.load_all(
            definitions.decode("utf-8"), Loader=yaml.SafeLoader
        )
    ]

    cmd.append(f"--concurrency={KUBECTL_DIFF_CONCURRENCY}")
    with _dump_yaml_docs_to_tmpdir(yaml_docs) as tmpdirname:
        output = _run_kubectl_diff(
            cmd + ["-f", tmpdirname],
            important_diffs_only=important_diffs_only,
        )

    if not output or output.isspace():
        return (False, [])

    lines = output.split("\n")
    print_diff(lines)
    macos_notify("sentry-kube diff", "Diff complete.")

    has_diffs = len(lines) > 0
    return (has_diffs, lines)


def _diff(
    ctx,
    services,
    filters,
    server_side: bool | None = None,
    force_conflicts: bool | None = None,
    important_diffs_only: bool = False,
    use_canary: bool = False,
    allow_jobs: bool = False,
    deployment_image: str | None = None,
) -> Tuple[bool, List[str]]:
    effective_server_side, effective_force_conflicts = resolve_ssa_flags(
        services, server_side, force_conflicts
    )

    if not ctx.obj.quiet_mode:
        mode = "server-side" if effective_server_side else "client-side"
        if effective_server_side and effective_force_conflicts:
            mode += " (force-conflicts)"
        click.echo(f"Using {mode} apply.")

    _set_deployment_image_env(services, deployment_image)

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

    return _diff_kubectl(
        ctx=ctx,
        definitions=definitions,
        server_side=effective_server_side,
        force_conflicts=effective_force_conflicts,
        important_diffs_only=important_diffs_only,
    )


@click.command()
@click.pass_context
@click.option("--filter", "filters", multiple=True)
# NOTE(dfedorov): Should be flag, but not sure where
# it is used, so keeping it this way to avoid breaking changes.
@click.option(
    "--server-side/--no-server-side",
    is_flag=True,
    default=None,
    help="Use server-side diff (overrides service-level flag)",
)
@click.option(
    "--force-conflicts/--no-force-conflicts",
    is_flag=True,
    default=None,
    help="Force conflicts resolution during server-side diff (overrides service-level flag)",
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
    server_side: bool | None,
    force_conflicts: bool | None,
    important_diffs_only: bool,
    use_canary: bool,
    allow_jobs: bool,
    deployment_image: str | None = None,
):
    """
    Render a diff between production and local configs, using a wrapper around
    "kubectl diff".

    This is non-destructive and tells you what would be applied, if
    anything, with your current changes.
    """

    (has_diffs, _) = _diff(
        ctx=ctx,
        services=services,
        filters=filters,
        server_side=server_side,
        force_conflicts=force_conflicts,
        important_diffs_only=important_diffs_only,
        use_canary=use_canary,
        allow_jobs=allow_jobs,
        deployment_image=deployment_image,
    )
    ctx.exit(has_diffs)
