from functools import wraps
import click
import os
from typing import List
from libsentrykube.service import get_service_names


def allow_for_all_services(f):
    """
    This decorator adds `--all` and `--exclude`.
    """

    @wraps(f)
    @click.argument("services", nargs=-1, type=str)
    @click.option("--all", "-a", "all_", is_flag=True, help="Select all services.")
    @click.option(
        "--exclude",
        default="",
        type=str,
        help="Comma-delimited string of service names to exclude.",
    )
    def wrapper(*args, **kwargs):
        services = list(kwargs.get("services"))
        all_services = get_service_names()
        if not all_services:
            raise click.UsageError("No services found.")

        all_services_pretty = "\n".join(f"- {s}" for s in sorted(all_services))

        if kwargs.pop("all_"):
            if services:
                raise click.BadArgumentUsage(
                    "You specified '--all' along with some service names, "
                    "what do you actually want?\n"
                    f"Services:\n{all_services_pretty}"
                )
            services = all_services
        elif not services:
            raise click.BadArgumentUsage(
                f"No service names provided. Services:\n{all_services_pretty}"
            )

        excludes = kwargs.pop("exclude")
        if excludes:
            for svc in excludes.split(","):
                services.remove(svc)

        kwargs["services"] = services

        return f(*args, **kwargs)

    return wrapper


def _set_deployment_image_env(
    services: List[str], deployment_image: str | None
) -> None:
    if len(services) > 1 and deployment_image:
        raise click.BadArgumentUsage(
            "Cannot specify --deployment-image with multiple services"
        )
    elif deployment_image:
        os.environ["DEPLOYMENT_IMAGE"] = deployment_image
