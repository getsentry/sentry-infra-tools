import click
from libsentrykube.cluster import list_clusters_for_customer
from libsentrykube.config import Config

__all__ = ("get_regions",)


@click.command()
@click.option(
    "--service", "-s", type=str, default=None, help="Get regions for a specific service"
)
def get_regions(service: str | None = None) -> None:
    """
    Gets the list of all avaliable regions, if service is provided it will
    filter regions with just that service enabled.
    """
    regions = Config().silo_regions
    # Default to all regions
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
