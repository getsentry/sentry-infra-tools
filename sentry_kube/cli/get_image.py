import click

from libsentrykube.config import Config
from libsentrykube.customer import get_service_container_map
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
    service_container_map = get_service_container_map(
        config, ctx.obj.customer_name, ctx.obj.cluster_name
    )
    service_map = service_container_map[service]

    image = get_deployment_image(
        deployment=service_map["deployment"],
        container=service_map["container"],
        default="unknown",
        quiet=ctx.obj.quiet_mode,
    )

    click.echo(image)
