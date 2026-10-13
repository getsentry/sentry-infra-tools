import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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


_GKE_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-gke\.(\d+))?")


def version_key(version: str) -> tuple[int, ...]:
    """Sortable form of a GKE version like 1.37.0-gke.3503000."""
    match = _GKE_VERSION.match(version)
    if match is None:
        raise ValueError(f"Unrecognized GKE version: {version}")
    return tuple(int(part or 0) for part in match.groups())


def list_cluster_versions(project: str, cluster_name: str) -> None:
    cluster_obj = get_cluster(project, cluster_name)

    release_channel = cluster_obj["releaseChannel"]["channel"]
    cluster_zone = cluster_obj["zone"]
    master = cluster_obj["currentMasterVersion"]

    click.echo("=" * 10)
    click.echo(f"cluster: {cluster_name}, zone: {cluster_zone}")
    click.echo(f"control: {master}")
    click.echo(f"nodes: {cluster_obj['currentNodeVersion']}")
    click.echo(f"release channel: {release_channel}")
    click.echo("=" * 10)
    click.echo("Available versions:")
    channel = get_release_channel(project, cluster_zone, release_channel)
    for version in channel.get("validVersions", []):
        labels = []
        if version == channel.get("defaultVersion"):
            labels.append("default")
        if version == master:
            labels.append("current control plane")
        elif _is_older(version, master):
            labels.append("older than control plane")
        click.echo(f"{version} ({', '.join(labels)})" if labels else version)


def _is_older(version: str, than: str) -> bool:
    try:
        return version_key(version) < version_key(than)
    except ValueError:
        return False


@dataclass
class UpgradePlan:
    version: str
    # Current control plane version, if it will be upgraded.
    control_plane: Optional[str] = None
    # (name, current version) of node pools that will be upgraded.
    node_pools: List[Tuple[str, str]] = field(default_factory=list)
    # (name, current version) of node pools already newer than the target.
    newer_node_pools: List[Tuple[str, str]] = field(default_factory=list)

    def describe(self) -> List[str]:
        lines = []
        if self.control_plane:
            lines.append(f"control plane: {self.control_plane} -> {self.version}")
        lines.extend(
            f"node pool {name}: {current} -> {self.version} (async)"
            for name, current in self.node_pools
        )
        lines.extend(
            f"node pool {name}: {current} (newer than {self.version}, unchanged)"
            for name, current in self.newer_node_pools
        )
        return lines

    @property
    def empty(self) -> bool:
        return self.control_plane is None and not self.node_pools


def plan_upgrade(cluster_obj: Dict[str, Any], version: str) -> UpgradePlan:
    """
    What upgrading to `version` changes. Never downgrades: an older target
    than the control plane is refused, and node pools already newer than the
    target are left alone.
    """
    master = cluster_obj["currentMasterVersion"]
    if _is_older(version, master):
        raise click.UsageError(
            f"{version} is older than the control plane ({master}); "
            "refusing to downgrade. Pick a version from `cluster versions` that "
            "isn't marked older than the control plane."
        )
    plan = UpgradePlan(version=version)
    if version != master:
        plan.control_plane = master
    for pool in cluster_obj["nodePools"]:
        current = pool["version"]
        if current == version:
            continue
        if _is_older(version, current):
            plan.newer_node_pools.append((pool["name"], current))
        else:
            plan.node_pools.append((pool["name"], current))
    return plan


def upgrade_cluster_to_version(project: str, cluster_name: str, version: str) -> None:
    cluster_obj = get_cluster(project, cluster_name)
    release_channel = cluster_obj["releaseChannel"]["channel"]
    cluster_zone = cluster_obj["zone"]
    versions = get_channel_versions(project, cluster_zone, release_channel)
    assert version in versions
    plan = plan_upgrade(cluster_obj, version)
    if plan.control_plane:
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
    for pool_name, _ in plan.node_pools:
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
                pool_name,
                "--async",
            ],
        )


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
    VERSION. It must be available in the cluster's release channel and not
    older than the control plane; see `cluster versions`. Node pools already
    newer than VERSION are left unchanged.
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

    plan = plan_upgrade(cluster_obj, version)
    if plan.empty:
        click.echo(f"{ref.name} is already at {version}.")
        return
    click.echo(f"Upgrade plan for {ref.name} ({ref.location}, project {ref.project}):")
    for line in plan.describe():
        click.echo(f"  {line}")
    if not yes:
        click.confirm("Proceed?", abort=True)
    upgrade_cluster_to_version(ref.project, ref.name, version)


cluster.add_command(network)
