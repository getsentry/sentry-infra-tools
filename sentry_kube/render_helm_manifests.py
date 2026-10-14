import logging
import os
import traceback
from pathlib import Path
from typing import Sequence

import click
from libsentrykube.helm import materialize_manifests
from libsentrykube.reversemap import build_helm_index
from libsentrykube.reversemap import extract_clusters
from libsentrykube.service import get_service_names
from libsentrykube.context import init_cluster_context

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@click.command()
@click.option("--fast", is_flag=True, help="Only render the specified services")
@click.option("--debug", is_flag=True, help="Print debug information")
@click.option(
    "--stage",
    type=str,
    default=None,
    help="Stage to operate on. Only regions with matching stage will be rendered.",
    envvar="SENTRY_KUBE_STAGE",
)
@click.option(
    "--kube-version",
    type=str,
    default=None,
    help="Kubernetes version passed to `helm template --kube-version`. "
    "Defaults to a pinned version so output is machine-independent.",
)
@click.option(
    "--api-versions",
    type=str,
    multiple=True,
    help="Extra Capabilities.APIVersions passed to `helm template`. "
    "Can be specified multiple times.",
)
@click.argument("filename", nargs=-1)
def render_helm_manifests(
    fast: bool,
    debug: bool,
    stage: str | None,
    kube_version: str | None,
    api_versions: tuple[str, ...],
    filename: Sequence[str],
) -> None:
    """
    Materializes the fully rendered manifests (`helm template` output)
    of helm services, the same way render_services materializes non-helm
    services.

    Identifies which services and clusters need to be re-rendered
    depending on the file names passed as arguments: from each modified
    file name, it identifies if this file is part of a helm service, it
    identifies customer, service and relevant clusters. After this, it
    re-renders the service in all the relevant clusters.

    A service whose chart cannot be fetched or rendered fails the run
    loudly instead of being skipped silently.
    """
    if debug:
        logger.setLevel(logging.DEBUG)

    index = build_helm_index(stage=stage)
    resources_to_render = set()

    for file in filename:
        if Path(file).exists():
            path = Path(file)
            resources_to_render.update(index.get_resources_for_path(path))

    if not fast:
        # We aggressively render the whole cluster for each modified service.
        # This guarantees correctness in case of cross references between
        # services.
        resources_to_render = extract_clusters(resources_to_render)

    os.environ["KUBERNETES_OFFLINE"] = "1"
    changes_made = False
    errors: list[tuple[str, str, str, Exception]] = []
    for resource in resources_to_render:
        logger.debug(
            f"Initializing cluster context for {resource.customer_name} : {resource.cluster_name}"
        )
        init_cluster_context(resource.customer_name, resource.cluster_name)

        if resource.service_name is not None:
            logger.debug(f"Materializing service: {resource.service_name}")
            services_to_materialize = [resource.service_name]
        else:
            logger.debug("Getting all service names")
            services_to_materialize = get_service_names(namespace="helm")

        logger.debug(f"Services to materialize: {services_to_materialize}")

        for s in services_to_materialize:
            logger.debug(f"Materializing service: {s}")
            cust = resource.customer_name
            clust = resource.cluster_name
            try:
                changed = materialize_manifests(
                    region_name=cust,
                    service_name=s,
                    cluster_name=clust,
                    kube_version=kube_version,
                    api_versions=api_versions or None,
                )
            except Exception as exc:
                errors.append((cust, clust, s, exc))
                click.echo(
                    f"Service FAILED: {cust} : {clust} : {s} — {type(exc).__name__}: {exc}",
                    err=True,
                )
                logger.debug(
                    f"Traceback for {cust} : {clust} : {s}:\n{traceback.format_exc()}"
                )
                continue
            if changed:
                changes_made = True
                click.echo(f"Service materialized: {cust} : {clust} : {s}")
            else:
                click.echo(f"Service unchanged: {cust} : {clust} : {s}")

    if errors:
        click.echo(
            f"\n{len(errors)} service(s) failed to render:",
            err=True,
        )
        for cust, clust, svc, err in errors:
            click.echo(f"  - {cust} : {clust} : {svc} — {err}", err=True)
        raise SystemExit(1)

    if changes_made:
        click.echo(
            "I made changes to the materialized manifests. Please stage them and commit again."
        )
        exit(-1)


if __name__ == "__main__":
    render_helm_manifests()
