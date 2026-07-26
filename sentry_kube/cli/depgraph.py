import json

import click

from libsentrykube.depgraph import build_dependency_graph

__all__ = ("depgraph",)


@click.command()
@click.option(
    "--stage",
    type=str,
    default=None,
    help="Stage to filter regions. If not specified, all regions are included.",
    envvar="SENTRY_KUBE_STAGE",
)
def depgraph(stage: str | None = None) -> None:
    """
    Compute and output the service dependency graph as JSON.

    Renders all services across all customers/clusters, tracking
    cross-service references via values_of(), and outputs the
    resulting dependency graph.
    """
    graph = build_dependency_graph(stage=stage)
    click.echo(json.dumps(graph.to_dict(), indent=2))
