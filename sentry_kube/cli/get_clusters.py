import click
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
@click.pass_context
def get_clusters(ctx, stage: str | None = None) -> None:
    """
    Gets the list of clusters, optionally scoped to a region via -C.

    Without -C, lists all clusters grouped by region.
    With -C, lists cluster names for that region.
    """
    config = Config()
    customer = ctx.obj.get("customer") if ctx.obj else None

    if customer:
        _, region_config = get_region_config(config, customer)
        clusters = list_clusters_for_customer(region_config.k8s_config)
        click.echo(" ".join(c.name for c in clusters))
    else:
        stage = stage or (ctx.obj.get("stage") if ctx.obj else None)
        if stage:
            region_names = config.get_regions(stage)
        else:
            region_names = list(config.silo_regions.keys())

        for region_name in region_names:
            region = config.silo_regions[region_name]
            clusters = list_clusters_for_customer(region.k8s_config)
            cluster_names = " ".join(c.name for c in clusters)
            click.echo(f"{region_name}: {cluster_names}")
