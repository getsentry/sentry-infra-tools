import click
import json
import yaml

from libsentrykube.kube import render_service_values
from sentry_kube.cli.apply import allow_for_all_services


__all__ = ("rendervalues",)


@click.command()
@click.pass_context
@click.option("--output", default="yaml")
@allow_for_all_services
def rendervalues(ctx, services, output):
    """
    Render the end state of variables (after merging defaults and overlays).

    This is non-destructive and just renders the variable values.
    """
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name
    for service_name in services:
        click.secho(f"{service_name}:", fg="green")
        out = render_service_values(
            customer_name,
            service_name,
            cluster_name,
        )
        if output == "yaml":
            click.echo(yaml.dump(out))
        else:
            click.echo(json.dumps(out, indent=2))
