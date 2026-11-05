import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import click
import kubernetes.client

from libsentrykube.gcloud import (
    get_all_gke_clusters,
    get_channel_versions,
    get_gke_cluster,
    get_release_channel,
    list_gke_operations,
)
from libsentrykube.gke_upgrade import (
    Clients,
    Node,
    UpgradeRunner,
    watch_report,
)
from libsentrykube.network import GkeClusterRef

from .network import gke_cluster_ref, network
from .nodepools import connect_kube, nodepools

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
    channel = get_release_channel(project, cluster_zone, release_channel)

    def entry(version: str) -> str:
        labels = []
        if version == channel.get("defaultVersion"):
            labels.append("default")
        if version == master:
            labels.append("current control plane")
        return f"  {version} ({', '.join(labels)})" if labels else f"  {version}"

    valid = channel.get("validVersions", [])
    targets = [v for v in valid if not _is_older(v, master)]
    older = [v for v in valid if _is_older(v, master)]
    click.echo("Upgrade targets:")
    for version in targets:
        click.echo(entry(version))
    if older:
        click.echo()
        click.echo(
            click.style(
                "Older than the control plane (`cluster upgrade` refuses these):",
                dim=True,
            )
        )
        for version in older:
            click.echo(click.style(entry(version), dim=True))


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
    # (name, current version) of node pools that will be upgraded, in order.
    node_pools: List[Tuple[str, str]] = field(default_factory=list)
    # (name, current version) of node pools already newer than the target.
    newer_node_pools: List[Tuple[str, str]] = field(default_factory=list)

    def describe(self) -> List[str]:
        lines = []
        if self.control_plane:
            lines.append(f"control plane: {self.control_plane} -> {self.version}")
        lines.extend(
            f"node pool {name}: {current} -> {self.version}"
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


def plan_upgrade(
    cluster_obj: Mapping[str, Any],
    version: str,
    pools: Sequence[str] = (),
    old_nodes: Optional[Mapping[str, int]] = None,
    pools_only: bool = False,
) -> UpgradePlan:
    """
    What upgrading to `version` changes. Never downgrades: an older target
    than the control plane is refused, and node pools already newer than the
    target are left alone. With `pools_only` the control plane is left as is
    and the target may be older than it (node pools may trail the control
    plane) but not newer. `pools` limits and orders the node pools; the
    default is every pool behind the target in the order GKE lists them.
    A pool whose version reads `version` but still has old nodes (per
    `old_nodes`) is included, since the version flips when an upgrade starts.
    """
    master = cluster_obj["currentMasterVersion"]
    if pools_only and _is_older(master, version):
        raise click.UsageError(
            f"--pools-only but {version} is newer than the control plane "
            f"({master}); node pools cannot run a newer version than the "
            "control plane"
        )
    if not pools_only and _is_older(version, master):
        raise click.UsageError(
            f"{version} is older than the control plane ({master}); "
            "refusing to downgrade. Pick one of the upgrade targets listed by "
            "`cluster versions`."
        )
    plan = UpgradePlan(version=version)
    if version != master and not pools_only:
        plan.control_plane = master
    by_name = {p["name"]: p for p in cluster_obj["nodePools"]}
    unknown = [p for p in pools if p not in by_name]
    if unknown:
        raise click.BadParameter(
            f"no such node pool(s): {', '.join(unknown)}", param_hint="--pool"
        )
    for name in pools or list(by_name):
        current = by_name[name]["version"]
        if current == version and not (old_nodes or {}).get(name):
            continue
        if _is_older(version, current):
            plan.newer_node_pools.append((name, current))
        else:
            plan.node_pools.append((name, current))
    return plan


def _gcloud(argv: Sequence[str]) -> tuple[int, str]:
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.returncode, proc.stderr


def _log(msg: str) -> None:
    click.echo(f"[{datetime.now(timezone.utc):%H:%M:%SZ}] {msg}")


def _kube_nodes(kube_client: kubernetes.client.ApiClient) -> List[Node]:
    response = kubernetes.client.CoreV1Api(kube_client).list_node(
        _preload_content=False
    )
    nodes = []
    for item in json.loads(response.data).get("items", []):
        nodes.append(
            Node(
                name=item["metadata"]["name"],
                pool=item["metadata"]
                .get("labels", {})
                .get("cloud.google.com/gke-nodepool", ""),
                kubelet_version=item.get("status", {})
                .get("nodeInfo", {})
                .get("kubeletVersion", ""),
                unschedulable=bool(item.get("spec", {}).get("unschedulable")),
            )
        )
    return nodes


def make_clients(ctx: click.Context, ref: GkeClusterRef) -> Clients:
    kube_client = connect_kube(ctx)
    return Clients(
        get_cluster=lambda: get_gke_cluster(ref.project, ref.location, ref.name),
        list_ops=lambda: list_gke_operations(ref.project, ref.location),
        list_nodes=lambda: _kube_nodes(kube_client),
        gcloud=_gcloud,
        log=_log,
    )


def _op_started(op: Mapping[str, Any]) -> float:
    return datetime.fromisoformat(op["startTime"].replace("Z", "+00:00")).timestamp()


def old_node_counts(nodes: Sequence[Node], version: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for node in nodes:
        if node.kubelet_version != f"v{version}":
            counts[node.pool] = counts.get(node.pool, 0) + 1
    return counts


@click.group(invoke_without_command=True)
@click.pass_context
def cluster(ctx: click.Context) -> None:
    """
    Inspect and manage the GKE cluster behind the selected sentry-kube cluster.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


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
@click.option(
    "--pool",
    "pools",
    multiple=True,
    metavar="NAME",
    help="Only this node pool, in this order (repeatable). "
    "Default: every pool behind VERSION.",
)
@click.option("--pools-only", is_flag=True, help="Skip the control plane.")
@click.option(
    "--control-plane-only", is_flag=True, help="Stop after the control plane."
)
@click.option(
    "--fixed-capacity",
    is_flag=True,
    help="Upgrade each pool with max surge 0 / max unavailable 1 (in-place "
    "reimage, needs no spare VM, gets through zone stockouts) and restore "
    "its surge settings afterwards.",
)
@click.option(
    "--max-attempts",
    type=click.IntRange(1, 20),
    default=3,
    show_default=True,
    help="Retries per pool when its operation fails or leaves old nodes behind.",
)
@click.option(
    "--interval",
    type=click.IntRange(10, 3600),
    default=60,
    show_default=True,
    help="Seconds between progress checks.",
)
@click.option(
    "--dry-run", is_flag=True, help="Print the gcloud commands without running them."
)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
def upgrade(
    ctx: click.Context,
    version: str,
    pools: Tuple[str, ...],
    pools_only: bool,
    control_plane_only: bool,
    fixed_capacity: bool,
    max_attempts: int,
    interval: int,
    dry_run: bool,
    yes: bool,
) -> None:
    """
    Upgrade the control plane, then node pools one at a time, to VERSION.

    Each step is started with --async and polled until done, so gcloud's
    one-hour wait and the terminal's auth session do not matter. A pool
    counts as done only when every node's kubelet reports VERSION; the pool
    version alone flips as soon as its operation starts. A failed operation
    (stockout, autoscaler racing it) is retried on the same pool, which only
    touches the nodes still behind. Pools with another upgrade running are
    picked up after the rest, pools removed by the autoscaler mid-run are
    skipped. VERSION must be in the cluster's release channel and not older
    than the control plane; see `cluster versions`.
    """
    if pools_only and control_plane_only:
        raise click.UsageError(
            "--pools-only and --control-plane-only exclude each other"
        )
    ref = gke_cluster_ref(ctx)
    clients = make_clients(ctx, ref)
    cluster_obj = clients.get_cluster()
    available = get_channel_versions(
        ref.project, ref.location, cluster_obj["releaseChannel"]["channel"]
    )
    if version not in available:
        raise click.BadParameter(
            f"{version} is not available in the cluster's release channel. "
            f"Available: {', '.join(available)}",
            param_hint="VERSION",
        )

    plan = plan_upgrade(
        cluster_obj,
        version,
        pools,
        old_node_counts(clients.list_nodes(), version),
        pools_only=pools_only,
    )
    if control_plane_only:
        plan.node_pools = []
        plan.newer_node_pools = []
    if plan.empty:
        click.echo(f"{ref.name} is already at {version}.")
        return
    click.echo(f"Upgrade plan for {ref.name} ({ref.location}, project {ref.project}):")
    for line in plan.describe():
        click.echo(f"  {line}")
    if fixed_capacity:
        click.echo("  node pools upgraded in place (max surge 0 / max unavailable 1)")
    if not yes and not dry_run:
        click.confirm("Proceed?", abort=True)

    runner = UpgradeRunner(
        clients,
        ref.project,
        ref.location,
        ref.name,
        version,
        interval=interval,
        max_attempts=max_attempts,
        fixed_capacity=fixed_capacity,
        dry_run=dry_run,
    )
    try:
        if plan.control_plane:
            runner.upgrade_control_plane()
        if plan.node_pools:
            runner.upgrade_pools([name for name, _ in plan.node_pools])
    except RuntimeError as e:
        raise click.ClickException(str(e))
    except KeyboardInterrupt:
        raise click.ClickException(
            "interrupted; operations already started keep running, "
            "`cluster watch` shows them and re-running `cluster upgrade` resumes"
        )
    failed = [r for r in runner.results if r.outcome == "failed"]
    if failed:
        raise click.ClickException(
            "pools not fully upgraded: "
            + ", ".join(f"{r.name} ({r.detail})" for r in failed)
        )


@cluster.command("watch")
@click.option(
    "--version",
    "version",
    metavar="VERSION",
    help="Target version to measure against. Default: the control plane version.",
)
@click.option(
    "--interval",
    type=click.IntRange(10, 3600),
    default=300,
    show_default=True,
    help="Seconds between refreshes.",
)
@click.option("--once", is_flag=True, help="Print one report and exit.")
@click.pass_context
def watch(
    ctx: click.Context, version: Optional[str], interval: int, once: bool
) -> None:
    """
    Follow a node pool rollout: running upgrade operations with elapsed time,
    live new/old kubelet counts, cordoned nodes and PDB delay, plus pools that
    report the target version but still run old nodes.
    """
    ref = gke_cluster_ref(ctx)
    clients = make_clients(ctx, ref)
    while True:
        cluster_obj = clients.get_cluster()
        target = version or cluster_obj["currentMasterVersion"]
        report = watch_report(
            cluster_obj,
            clients.list_ops(),
            clients.list_nodes(),
            target,
            time.time(),
            _op_started,
        )
        if not once:
            click.clear()
        click.echo(f"{datetime.now(timezone.utc):%H:%M:%SZ}  {report}")
        if once:
            return
        time.sleep(interval)


cluster.add_command(network)
cluster.add_command(nodepools)
