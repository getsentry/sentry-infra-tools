import shutil
import subprocess
import sys

import click
from typing import Any

__all__ = ("argonaut",)


def ensure_argonaut() -> str:
    executable_name = "argonaut"
    if not shutil.which(executable_name):
        click.echo(
            "> argonaut binary is missing, install it from "
            "https://github.com/darksworm/argonaut"
        )
        sys.exit(1)
    return executable_name


@click.command(
    context_settings=dict(
        ignore_unknown_options=True,
    )
)
@click.argument(
    "argonaut_args",
    nargs=-1,
    type=click.UNPROCESSED,
)
def argonaut(argonaut_args: Any) -> None:
    """
    Start argonaut (a terminal based Argo CD UI)

    Runs against the region's ArgoCD hub cluster as the current kube context,
    so argonaut's port-forward mode reaches argocd-server through the
    sentry-kube tunnel. Requires an ArgoCD CLI login, e.g.
    `argocd login --port-forward --port-forward-namespace argocd`.
    """
    # argonaut has no --context flag; it picks up the KUBECONFIG and current
    # context that sentry-kube already set for the hub cluster.
    cmd = [ensure_argonaut(), *argonaut_args]

    click.echo(f"Starting argonaut:\n+ {' '.join(cmd)}")

    subprocess.run(cmd)
