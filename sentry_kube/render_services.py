import os
from pathlib import Path
from typing import Sequence

import click
from libsentrykube.kube import materialize
from libsentrykube.reversemap import build_index
from libsentrykube.reversemap import extract_clusters
from libsentrykube.service import get_service_names
from libsentrykube.context import init_cluster_context


@click.command()
@click.argument("filename", nargs=-1)
def render_services(filename: Sequence[str]) -> None:
    """
    Identifies which services and clusters need to be re-rendered
    depending on the file names passed as arguments.

    Specifically, from each modified file name, it identifies
    if this file is part of a k8s service, it identifies customer,
    service and relevant clusters. After this, it re-renders the
    service in all the relevant clusters.
    """
    index = build_index()
    resources_to_render = set()

    for file in filename:
        if Path(file).exists():
            path = Path(file)
            resources_to_render.update(index.get_resources_for_path(path))

    # We aggressively render the whole cluster for each modified service.
    # This guarantees correctness in case of cross references between
    # services.
    resources_to_render = extract_clusters(resources_to_render)

    os.environ["KUBERNETES_OFFLINE"] = "1"
    changes_made = False
    for resource in resources_to_render:
        init_cluster_context(resource.customer_name, resource.cluster_name)

        if resource.service_name is not None:
            services_to_materialize = [resource.service_name]
        else:
            services_to_materialize = get_service_names()

        for s in services_to_materialize:
            changed = materialize(
                customer_name=resource.customer_name,
                service_name=s,
                cluster_name=resource.cluster_name,
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
        click.echo("I made changes to the materialized config. Please stage them and commit again.")
        exit(-1)


if __name__ == "__main__":
    render_services()
