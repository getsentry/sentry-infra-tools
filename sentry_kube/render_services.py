import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

import click
from libsentrykube.kube import materialize
from libsentrykube.reversemap import build_index
from libsentrykube.reversemap import extract_clusters
from libsentrykube.service import get_service_names
from libsentrykube.context import init_cluster_context

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _materialize_service_worker(
    customer_name: str, service_name: str, cluster_name: str, split_by_kind: bool
) -> tuple[str, str, str, bool]:
    os.environ["KUBERNETES_OFFLINE"] = "1"
    init_cluster_context(customer_name, cluster_name)
    changed = materialize(
        customer_name=customer_name,
        service_name=service_name,
        cluster_name=cluster_name,
        split_by_kind=split_by_kind,
    )
    return customer_name, cluster_name, service_name, changed


def _render_multithreaded(
    resources_to_render, split_by_kind: bool, workers: int
) -> bool:
    work_items = []
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
            services_to_materialize = get_service_names()

        logger.debug(f"Services to materialize: {services_to_materialize}")

        for service_name in services_to_materialize:
            work_items.append(
                (resource.customer_name, service_name, resource.cluster_name)
            )

    changes_made = False
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _materialize_service_worker,
                customer_name,
                service_name,
                cluster_name,
                split_by_kind,
            )
            for customer_name, service_name, cluster_name in work_items
        ]

        for future in as_completed(futures):
            customer_name, cluster_name, service_name, changed = future.result()
            if changed:
                changes_made = True
                click.echo(
                    f"Service materialized: {customer_name} : {cluster_name} : {service_name}"
                )
            else:
                click.echo(
                    f"Service unchanged: {customer_name} : {cluster_name} : {service_name}"
                )

    return changes_made


@click.command()
@click.option("--fast", is_flag=True, help="Only render the specified services")
@click.option("--debug", is_flag=True, help="Print debug information")
@click.option(
    "--split-by-kind", is_flag=True, help="Split the rendered service by kind"
)
@click.option(
    "--multithreaded", is_flag=True, default=False, help="Use multithreaded rendering"
)
@click.option(
    "--workers",
    type=int,
    default=os.cpu_count() or 1,
    help="Number of parallel workers",
)
@click.argument("filename", nargs=-1)
def render_services(
    fast: bool,
    debug: bool,
    split_by_kind: bool,
    multithreaded: bool,
    workers: int,
    filename: Sequence[str],
) -> None:
    """
    Identifies which services and clusters need to be re-rendered
    depending on the file names passed as arguments.

    Specifically, from each modified file name, it identifies
    if this file is part of a k8s service, it identifies customer,
    service and relevant clusters. After this, it re-renders the
    service in all the relevant clusters.
    """
    if debug:
        logger.setLevel(logging.DEBUG)

    index = build_index()
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

    if multithreaded:
        changes_made = _render_multithreaded(
            resources_to_render, split_by_kind, workers
        )

        if changes_made:
            click.echo(
                "I made changes to the materialized config. Please stage them and commit again."
            )
            exit(-1)
    else:
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
                services_to_materialize = get_service_names()

            logger.debug(f"Services to materialize: {services_to_materialize}")

            for s in services_to_materialize:
                logger.debug(f"Materializing service: {s}")
                changed = materialize(
                    customer_name=resource.customer_name,
                    service_name=s,
                    cluster_name=resource.cluster_name,
                    split_by_kind=split_by_kind,
                )
                if changed:
                    changes_made = True
                    click.echo(
                        f"Service materialized: {resource.customer_name} : {resource.cluster_name} : {s}"
                    )
                else:
                    click.echo(
                        f"Service unchanged: {resource.customer_name} : {resource.cluster_name} : {s}"
                    )

        if changes_made:
            click.echo(
                "I made changes to the materialized config. Please stage them and commit again."
            )
            exit(-1)


if __name__ == "__main__":
    render_services()
