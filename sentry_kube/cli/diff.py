import click
import os
import yaml
import contextlib
import tempfile
import functools
import concurrent.futures
import copy
import shutil
import subprocess
from typing import Iterator, List
from sentry_kube.cli.util import allow_for_all_services, _set_deployment_image_env
from sentry_kube.cli.render import _render
from libsentrykube.utils import (
    KUBECTL_VERSION,
    chunked,
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
