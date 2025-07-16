import click

from libsentrykube.config import Config
from libsentrykube.service import get_deployment_image

__all__ = ("get_image",)


@click.command()
@click.pass_context
@click.argument("service", type=str)
def get_image(ctx, service: str) -> None:
    """
    Gets the deployment image for a specific service.
    """

    config = Config()

    try:
        service_map = config.service_container_map[service]
    except KeyError:
        # If the service is not found in the service_container_map, return "undefined".
        # This means we can call this function even for services which don't use the deployment_image macro in our automation.
        click.echo("undefined")
    else:
        image = get_deployment_image(
            deployment=service_map["deployment"],
            container=service_map["container"],
            default="unknown",
            quiet=ctx.obj.quiet_mode,
        )
        click.echo(image)
