import click

from libsentrykube.config import Config

__all__ = ("get_customers", "get_live_regions", "get_all_regions")


@click.command()
def get_live_regions():
    """
    Gets the list of all avaliable customers.
    """
    click.echo(" ".join(Config().get_live_regions()))


@click.command()
def get_all_regions():
    """
    Gets the list of all avaliable customers.
    """
    click.echo(" ".join(Config().get_all_regions()))


@click.command()
def get_customers():
    """
    Legacy command wrapper to get all live regions.
    """
    get_live_regions()
