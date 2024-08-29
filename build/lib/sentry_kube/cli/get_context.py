import click

__all__ = ("get_context",)


@click.command()
@click.pass_context
def get_context(ctx: click.core.Context) -> None:
    """
    Get the kubernetes context value for use in other tools
    """

    click.echo(ctx.obj.context_name)
