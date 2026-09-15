import click
from libsentrykube.agent import AgentConfigurationError
from libsentrykube.agent import AgentStep
from libsentrykube.agent import run_agent
from libsentrykube.utils import die

__all__ = ("agent",)

# How much of a tool's arguments to show on the step line.
MAX_REPORTED_ARGS = 120


def _format_args(tool_input: dict) -> str:
    parts = []
    for key, value in tool_input.items():
        text = str(value).replace("\n", " ")
        if len(text) > 40:
            text = text[:40] + "..."
        parts.append(f"{key}={text}")

    joined = ", ".join(parts)
    if len(joined) > MAX_REPORTED_ARGS:
        joined = joined[:MAX_REPORTED_ARGS] + "..."
    return joined


def _echo_step(step: AgentStep) -> None:
    """
    Prints one step of the agent's work to stderr.

    Progress goes to stderr so that piping the command still gives you just the
    agent's answer on stdout.
    """
    if step.kind == "thought":
        click.secho(f"* {step.message}", fg="cyan", err=True)
    elif step.kind == "tool_call":
        args = _format_args(step.tool_input or {})
        click.secho(f"  -> {step.tool}({args})", fg="yellow", err=True)
    elif step.kind == "tool_result":
        first_line = (step.detail or "").splitlines()
        summary = first_line[0] if first_line else "(no output)"
        if len(summary) > 60:
            summary = summary[:60] + "..."
        click.secho(f"  <- {step.message}: {summary}", fg="green", err=True)


@click.command()
@click.argument("query", type=str)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Only print the final answer, not what the agent is doing.",
)
@click.pass_context
def agent(ctx: click.core.Context, query: str, quiet: bool) -> None:
    """
    Ask the sentry-kube AI agent a question.
    """
    try:
        response = run_agent(
            query,
            region=ctx.obj.customer_name,
            cluster=ctx.obj.cluster_name,
            on_step=None if quiet else _echo_step,
        )
    except AgentConfigurationError as e:
        die(str(e))
    else:
        if not quiet:
            click.echo("", err=True)
        click.echo(response)
