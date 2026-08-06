import click
from libsentrykube.agent import AgentConfigurationError
from libsentrykube.agent import run_agent
from libsentrykube.utils import die

__all__ = ("agent",)


@click.command()
@click.argument("query", type=str)
@click.pass_context
def agent(ctx: click.core.Context, query: str) -> None:
    """
    Ask the sentry-kube AI agent a question.
    """
    try:
        response = run_agent(
            query,
            region=ctx.obj.customer_name,
            cluster=ctx.obj.cluster_name,
        )
    except AgentConfigurationError as e:
        die(str(e))
    else:
        click.echo(response)
