import click
from typing import Sequence
from libsentrykube.kube import render_templates
from libsentrykube.utils import pretty
from sentry_kube.cli.util import allow_for_all_services
from libsentrykube.kube import materialize

__all__ = ("render",)


def _materialize(ctx, services: Sequence[str]) -> None:
    """
    Render a service and saves it to a file.
    """
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name
    for service in services:
        materialize(customer_name, service, cluster_name)


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
