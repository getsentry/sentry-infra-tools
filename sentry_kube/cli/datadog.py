import click
from libsentrykube.events import (
    SENTRY_KUBE_EVENT_SOURCE_CATEGORY,
    report_event_to_datadog,
    report_terragrunt_event,
)

__all__ = ("datadog_log_terragrunt", "datadog_log")


@click.command(
    add_help_option=False,
    context_settings=dict(allow_extra_args=True, ignore_unknown_options=True),
)
@click.option("--cli-args", default="")
@click.pass_context
def datadog_log_terragrunt(ctx, cli_args: str):
    """
    Submits terraform events to DataDog.
    """
    try:
        report_terragrunt_event(cli_args)
    except Exception as e:
        click.echo("!! Could not report an event to DataDog:")
        click.secho(e, bold=True)


@click.command(
    add_help_option=True,
    context_settings=dict(allow_extra_args=True, ignore_unknown_options=True),
)
@click.option("--title", default="")
@click.option("--message", default="")
@click.option("--source", default="sentry-kube")
@click.option(
    "--tag", "-t", "custom_tags", multiple=True, help="format: -t tag=value", default=[]
)
@click.pass_context
def datadog_log(ctx, title: str, message: str, source: str, custom_tags):
    """
    Submits events to DataDog.
    """

    tags = {
        "source": source,
        "source_tool": source,
        "source_category": SENTRY_KUBE_EVENT_SOURCE_CATEGORY,
    }
    try:
        custom_tags = dict([tag.split("=") for tag in custom_tags])
        tags.update(custom_tags)

    except Exception as e:
        raise click.BadArgumentUsage(
            f"Tag format incorrect use -t tag=value ex:( -t user=$USER ) \nERROR: \n {e}"
        )

    try:
        report_event_to_datadog(title, message, tags)
        pass
    except Exception as e:
        click.echo("!! Could not report an salt event to DataDog:")
        click.secho(e, bold=True)
