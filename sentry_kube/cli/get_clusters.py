import json

import click
import yaml
from libsentrykube.cluster import list_clusters_for_customer
from libsentrykube.config import Config
from libsentrykube.customer import get_region_config

__all__ = ("get_clusters",)


@click.command()
@click.option(
    "--stage",
    type=str,
    default=None,
    help="Filter regions by stage. If not specified, uses the global --stage option.",
    envvar="SENTRY_KUBE_STAGE",
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["text", "json", "yaml"]),
    default="text",
    help="Output format.",
)
@click.pass_context
def get_clusters(ctx, stage: str | None = None, output: str = "text") -> None:
    """
    Gets the list of clusters, optionally scoped to a region via -C.

    Without -C, lists all clusters grouped by region.
    With -C, lists cluster names for that region.
    """
    config = Config()
    customer = ctx.obj.get("customer") if ctx.obj else None

    data: list[str] | dict[str, list[str]]

    if customer:
        _, region_config = get_region_config(config, customer)
        clusters = list_clusters_for_customer(region_config.k8s_config)
        data = [c.name for c in clusters]
    else:
        stage = stage or (ctx.obj.get("stage") if ctx.obj else None)
        if stage:
            region_names = config.get_regions(stage)
        else:
            region_names = list(config.silo_regions.keys())

        data = {
            region_name: [
                c.name
                for c in list_clusters_for_customer(
                    config.silo_regions[region_name].k8s_config
                )
            ]
            for region_name in region_names
        }

    if output == "json":
        click.echo(json.dumps(data, indent=2))
    elif output == "yaml":
        click.echo(yaml.dump(data, default_flow_style=False), nl=False)
    else:
        if isinstance(data, list):
            click.echo(" ".join(data))
        else:
            for region_name, cluster_names in data.items():
                click.echo(f"{region_name}: {' '.join(cluster_names)}")
