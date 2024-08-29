import subprocess
from typing import Any, Dict

import click

from libsentrykube.config import Config
from libsentrykube.customer import get_project
from libsentrykube.gcloud import get_all_gke_clusters, get_channel_versions

__all__ = ("cluster",)


def get_cluster(project: str, cluster_name: str) -> Dict[str, Any]:
    clusters_list = get_all_gke_clusters(project)
    try:
        return [c for c in clusters_list if c["name"] == cluster_name][0]
    except IndexError:
        raise Exception("Unknown cluster name")


def list_cluster_versions(project: str, cluster_name: str) -> None:
    cluster_obj = get_cluster(project, cluster_name)

    release_channel = cluster_obj["releaseChannel"]["channel"]
    cluster_zone = cluster_obj["zone"]

    click.echo("=" * 10)
    click.echo(f"cluster: {cluster_name}, zone: {cluster_zone}")
    click.echo(f"control: {cluster_obj['currentMasterVersion']}")
    click.echo(f"nodes: {cluster_obj['currentNodeVersion']}")
    click.echo(f"release channel: {release_channel}")
    click.echo("=" * 10)
    click.echo("Available versions:")
    versions = get_channel_versions(project, cluster_zone, release_channel)
    click.echo("\n".join(versions))


def upgrade_cluster_to_version(project: str, cluster_name: str, version: str) -> None:
    cluster_obj = get_cluster(project, cluster_name)
    release_channel = cluster_obj["releaseChannel"]["channel"]
    cluster_zone = cluster_obj["zone"]
    versions = get_channel_versions(project, cluster_zone, release_channel)
    assert version in versions
    if version != cluster_obj["currentMasterVersion"]:
        subprocess.check_output(
            [
                "gcloud",
                "container",
                "clusters",
                "upgrade",
                "--project",
                project,
                cluster_name,
                "--master",
                "--location",
                cluster_zone,
                "--cluster-version",
                version,
            ],
        )
    if version != cluster_obj["currentNodeVersion"]:
        for pool_obj in cluster_obj["nodePools"]:
            if version != pool_obj["version"]:
                subprocess.check_output(
                    [
                        "gcloud",
                        "container",
                        "clusters",
                        "upgrade",
                        "--project",
                        project,
                        cluster_name,
                        "--location",
                        cluster_zone,
                        "--cluster-version",
                        version,
                        "--node-pool",
                        pool_obj["name"],
                        "--async",
                    ],
                )


@click.command()
@click.pass_context
@click.option("--list", is_flag=True)
@click.option("--upgrade", is_flag=True)
@click.option("--cluster", "cluster", type=str, default="")
@click.option("--version", "version", type=str, default="")
def cluster(ctx, list: bool, upgrade: bool, cluster: str, version: str = ""):
    customer_name = ctx.obj.customer_name
    config = Config()
    project_name = get_project(config, customer_name, cluster)
    if not cluster:
        click.echo("Error: Please specify --cluster")
        click.Abort()
    if list:
        list_cluster_versions(project_name, cluster)
    elif upgrade:
        if not version:
            click.echo("Error: Please specify --version")
            click.Abort()
        upgrade_cluster_to_version(project_name, cluster, version)
    else:
        click.echo(
            "Error: Please specify command. Either --list or --upgrade",
            err=True,
            color=True,
        )
        click.Abort()
