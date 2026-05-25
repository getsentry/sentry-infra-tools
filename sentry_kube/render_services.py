import logging
import os
import traceback
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
) -> tuple[bool, list[tuple[str, str, str, Exception]]]:
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
    errors: list[tuple[str, str, str, Exception]] = []
    future_to_work = {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for customer_name, service_name, cluster_name in work_items:
            future = executor.submit(
                _materialize_service_worker,
                customer_name,
                service_name,
                cluster_name,
                split_by_kind,
            )
            future_to_work[future] = (customer_name, service_name, cluster_name)

        for future in as_completed(future_to_work):
            cust, svc, clust = future_to_work[future]
            try:
                _, _, _, changed = future.result()
            except Exception as exc:
                errors.append((cust, clust, svc, exc))
                click.echo(
                    f"Service FAILED: {cust} : {clust} : {svc} — {type(exc).__name__}: {exc}",
                    err=True,
                )
                logger.debug(
                    f"Traceback for {cust} : {clust} : {svc}:\n{traceback.format_exc()}"
                )
                continue
            if changed:
                changes_made = True
                click.echo(f"Service materialized: {cust} : {clust} : {svc}")
            else:
                click.echo(f"Service unchanged: {cust} : {clust} : {svc}")

    if errors:
        click.echo(
            f"\n{len(errors)} service(s) failed to render:",
            err=True,
        )
        for cust, clust, svc, err in errors:
            click.echo(f"  - {cust} : {clust} : {svc} — {err}", err=True)

    return changes_made, errors


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
@click.option(
    "--stage",
    type=str,
    default=None,
    help="Stage to operate on. Only regions with matching stage will be rendered.",
    envvar="SENTRY_KUBE_STAGE",
)
@click.argument("filename", nargs=-1)
def render_services(
    fast: bool,
    debug: bool,
    split_by_kind: bool,
    multithreaded: bool,
    workers: int,
    stage: str | None,
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

    index = build_index(stage=stage)
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
        changes_made, errors = _render_multithreaded(
            resources_to_render, split_by_kind, workers
        )

        if errors:
            raise SystemExit(1)

        if changes_made:
            click.echo(
                "I made changes to the materialized config. Please stage them and commit again."
            )
            exit(-1)
    else:
        errors = []
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
                cust = resource.customer_name
                clust = resource.cluster_name
                try:
                    changed = materialize(
                        customer_name=cust,
                        service_name=s,
                        cluster_name=clust,
                        split_by_kind=split_by_kind,
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
                "I made changes to the materialized config. Please stage them and commit again."
            )
            exit(-1)


if __name__ == "__main__":
    render_services()
