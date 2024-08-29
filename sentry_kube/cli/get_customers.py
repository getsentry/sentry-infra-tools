import click

from libsentrykube.config import Config

__all__ = ("get_customers",)


@click.command()
def get_customers():
    """
    Gets the list of all avaliable customers.
    """
    click.echo(" ".join(Config().get_customers()))
