import click

from libsentrykube.utils import execvp

__all__ = ("toolbox",)


@click.command()
@click.option("--namespace", "-n", required=False, help="Namespace to run toolbox in.")
@click.option(
    "--nodepool", default="default", required=False, help="Node pool to run toolbox in."
)
@click.option("--clean", is_flag=True, help="Delete dangling toolbox pods.")
@click.option(
    "--clean-all", is_flag=True, help="Delete dangling toolbox pods from ALL users."
)
@click.pass_context
def toolbox(ctx: click.core.Context, *, namespace, nodepool, clean, clean_all) -> None:
    """
    Run a toolbox pod.
    """
    import os

    from libsentrykube.toolbox import get_toolbox_cmd

    context = ctx.obj.context_name
    user = os.environ["USER"]

    execvp(
        get_toolbox_cmd(
            context, user, clean, clean_all, namespace=namespace, nodepool=nodepool
        ),
        verbose=True,
    )
