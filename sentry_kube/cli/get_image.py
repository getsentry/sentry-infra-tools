import click
from libsentrykube.service import get_service_deployment_image

__all__ = ("get_image",)


SERVICE_DEPLOYMENT_CONTAINER_MAP = {
    "chartcuterie": ("chartcuterie-web-production", "chartcuterie"),
    "getsentry": ("getsentry-web-default-common-production", "sentry"),
    "getsentry-control": ("getsentry-control-web-default-common-production", "sentry"),
    "relay": ("relay-default-production", "relay"),
    "relay-pop": ("relay-pop", "relay"),
    "release-registry": ("release-registry", "release-registry"),
    "reload": ("reload", "reload"),
    "script-runner": ("script-runner/script-runner-region", "script-runner-region"),
    "seer": ("seer-web-severity", "seer"),
    "seer-gpu": ("seer-gpu-web-group-ingest", "seer-gpu"),
    "snuba": ("snuba-api-production", "api"),
    "super-big-consumers": ("sbc-arroyo-errors", "super-big-consumers"),
    "symbol-collector": ("symbol-collector", "symbol-collector"),
    "tempest": ("tempest-default", "tempest"),
    "vroom": ("vroom-default-production", "vroom"),
}


@click.command()
@click.argument("service", type=str)
def get_image(service: str) -> None:
    """
    Gets the deployment image for a specific service.
    """
    service, container = SERVICE_DEPLOYMENT_CONTAINER_MAP[service]

    image = get_service_deployment_image(
        service=service, container=container, default="unknown"
    )
    click.echo(image)
