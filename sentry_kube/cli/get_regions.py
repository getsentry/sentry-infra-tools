import click
from libsentrykube.cluster import list_clusters_for_customer
from libsentrykube.config import Config

__all__ = ("get_regions",)


@click.command()
@click.option(
    "--service", "-s", type=str, default=None, help="Get regions for a specific service"
)
@click.option(
    "--stage",
    type=str,
    default=None,
    help="Filter regions by stage. If not specified, uses the global --stage option.",
    envvar="SENTRY_KUBE_STAGE",
)
def get_regions(service: str | None = None, stage: str | None = None) -> None:
    """
    Gets the list of all available regions, optionally filtered by service and/or stage.
    """
    config = Config()

    # Get regions, filtered by stage if specified
    if stage:
        regions = config.get_regions_by_stage(stage)
    else:
        regions = config.silo_regions

    # Default to all regions (filtered by stage if applicable)
    ret = set(regions.keys())

    if service:
        ret = set()
        for region in regions:
            for cluster in list_clusters_for_customer(regions[region].k8s_config):
                if service in cluster.service_names:
                    ret.add(region)
                    # Avoid duplicating when we have services in multiple clusters in a region
                    break

    click.echo(" ".join(ret))
