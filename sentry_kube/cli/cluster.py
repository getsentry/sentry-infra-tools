import subprocess
from typing import Any, Dict, List

import click

from libsentrykube.config import Config
from libsentrykube.customer import get_project
from libsentrykube.gcloud import (
    get_all_gke_clusters,
    get_channel_versions,
    get_release_channel,
)

from .network import gke_cluster_ref, network

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
    channel = get_release_channel(project, cluster_zone, release_channel)
    for version in channel.get("validVersions", []):
        default = " (default)" if version == channel.get("defaultVersion") else ""
        click.echo(f"{version}{default}")


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


def upgrade_plan(cluster_obj: Dict[str, Any], version: str) -> List[str]:
    """Describe what upgrade_cluster_to_version would change, one line per step."""
    steps = []
    if version != cluster_obj["currentMasterVersion"]:
        steps.append(
            f"control plane: {cluster_obj['currentMasterVersion']} -> {version}"
        )
    if version != cluster_obj["currentNodeVersion"]:
        steps.extend(
            f"node pool {pool['name']}: {pool['version']} -> {version} (async)"
            for pool in cluster_obj["nodePools"]
            if version != pool["version"]
        )
    return steps


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--list", is_flag=True, hidden=True)
@click.option("--upgrade", is_flag=True, hidden=True)
@click.option("--cluster", "cluster", type=str, default="", hidden=True)
@click.option("--version", "version", type=str, default="", hidden=True)
def cluster(ctx, list: bool, upgrade: bool, cluster: str, version: str = ""):
    """
    Inspect and manage the GKE cluster behind the selected sentry-kube cluster.
    """
    if ctx.invoked_subcommand is not None:
        return
    if not (list or upgrade):
        click.echo(ctx.get_help())
        return

    # Deprecated flag interface, kept for one release.
    replacement = "cluster versions" if list else "cluster upgrade VERSION"
    click.echo(
        f"Warning: `cluster --{'list' if list else 'upgrade'}` is deprecated; "
        f"use `sentry-kube {replacement}`.",
        err=True,
    )
    if not cluster:
        raise click.UsageError("Please specify --cluster")
    if upgrade and not version:
        raise click.UsageError("Please specify --version")

    project_name = get_project(Config(), ctx.obj.customer_name, cluster)
    if list:
        list_cluster_versions(project_name, cluster)
    else:
        upgrade_cluster_to_version(project_name, cluster, version)


@cluster.command("versions")
@click.pass_context
def versions(ctx: click.Context) -> None:
    """
    Show the cluster's current GKE versions and those available in its
    release channel.
    """
    ref = gke_cluster_ref(ctx)
    list_cluster_versions(ref.project, ref.name)


@cluster.command("upgrade")
@click.argument("version", metavar="VERSION")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
def upgrade(ctx: click.Context, version: str, yes: bool) -> None:
    """
    Upgrade the control plane, then every node pool (asynchronously), to
    VERSION. It must be available in the cluster's release channel; see
    `cluster versions`, which marks the channel's default.
    """
    ref = gke_cluster_ref(ctx)
    cluster_obj = get_cluster(ref.project, ref.name)
    available = get_channel_versions(
        ref.project, cluster_obj["zone"], cluster_obj["releaseChannel"]["channel"]
    )
    if version not in available:
        raise click.BadParameter(
            f"{version} is not available in the cluster's release channel. "
            f"Available: {', '.join(available)}",
            param_hint="VERSION",
        )

    steps = upgrade_plan(cluster_obj, version)
    if not steps:
        click.echo(f"{ref.name} is already at {version}.")
        return
    click.echo(f"Upgrade plan for {ref.name} ({ref.location}, project {ref.project}):")
    for step in steps:
        click.echo(f"  {step}")
    if not yes:
        click.confirm("Proceed?", abort=True)
    upgrade_cluster_to_version(ref.project, ref.name, version)


cluster.add_command(network)
