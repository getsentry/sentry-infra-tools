import shutil
import subprocess
import sys

import click
from typing import Any

__all__ = ("k9s",)


def ensure_k9s() -> str:
    executable_name = "k9s"
    if not shutil.which(executable_name):
        click.echo(
            "> k9s binary is missing, install it from https://k9scli.io/topics/install/"
        )
        sys.exit(1)
    return executable_name


@click.command(
    context_settings=dict(
        ignore_unknown_options=True,
    )
)
@click.argument(
    "k9s_args",
    nargs=-1,
    type=click.UNPROCESSED,
)
@click.pass_context
def k9s(ctx: click.core.Context, k9s_args: Any) -> None:
    """
    Start k9s (a terminal based Kubernetes UI)
    """
    cmd = [
        ensure_k9s(),
        "--context",
        ctx.obj.context_name,
    ]

    # Treat all the unknown arguments as k9s arguments, and pass them verbatim
    cmd += k9s_args

    click.echo(f"Starting k9s:\n+ {' '.join(cmd)}")

    subprocess.run(cmd)
