import click
from libsentrykube.service import get_service_deployment_image

__all__ = ("get_image",)


@click.command()
@click.argument("service", type=str)
@click.argument("container", type=str)
def get_image(service: str, container: str) -> None:
    """
    Gets the deployment image for a specific service.
    """
    image = get_service_deployment_image(
        service=service, container=container, default="unknown"
    )
    click.echo(image)
