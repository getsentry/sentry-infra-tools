import json
import os

import click
import kubernetes.client
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from kubernetes.client.exceptions import ApiException
from tabulate import tabulate

from libsentrykube.iap import ensure_iap_tunnel
from libsentrykube.nodepools import (
    ClusterNodePools,
    NodePool,
    fetch_cluster_nodepools,
)
from libsentrykube.utils import kube_get_client, kube_set_context

from .network import gke_cluster_ref

# Registered as `sentry-kube cluster nodepools` (see cluster.py), not top level.


def connect_kube(ctx: click.Context) -> kubernetes.client.ApiClient:
    """
    The `cluster` group skips the main group's kube setup because most of its
    subcommands only call GCP APIs; set it up here for the ones that need it.
    """
    os.environ.pop("KUBERNETES_OFFLINE", None)
    kubeconfig = ensure_iap_tunnel(ctx)
    os.environ["KUBECONFIG"] = kubeconfig
    kube_set_context(ctx.obj.context_name, kubeconfig=kubeconfig)
    return kube_get_client()


@click.command()
@click.option(
    "--output",
    "-o",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--warn-threshold",
    type=click.IntRange(1, 100),
    default=80,
    show_default=True,
    help="Report auto-provisioning limits at or above this utilization percentage.",
)
@click.option(
    "--check",
    is_flag=True,
    help="Exit with status 1 if anything is reported under Findings.",
)
@click.pass_context
def nodepools(
    ctx: click.Context, output: str, warn_threshold: int, check: bool
) -> None:
    """
    Show the cluster's node pools grouped by compute class (read-only): machine
    type, live node count, autoscaling bounds, requested CPU and memory, and
    version, plus each compute class's machine priorities and the cluster's
    node auto-provisioning limits.

    Requested is the sum of the effective requests of running pods on the
    pool's nodes, against the nodes' allocatable capacity.
    """
    cluster = gke_cluster_ref(ctx)
    try:
        kube_client = connect_kube(ctx)
        result = fetch_cluster_nodepools(
            cluster, kube_client, warn_threshold=warn_threshold / 100
        )
    except HttpError as e:
        raise click.ClickException(
            f"GCP API request failed ({e.status_code}): {e.reason}"
        )
    except GoogleAuthError as e:
        raise click.ClickException(
            f"GCP authentication failed: {e}\n"
            "Run `gcloud auth application-default login` and try again."
        )
    except ApiException as e:
        raise click.ClickException(
            f"Kubernetes API request failed ({e.status}): {e.reason}"
        )

    if output == "json":
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        click.echo(format_text(result))

    if check and result.findings:
        ctx.exit(1)


# Long ComputeClass fallback lists are cut short in text output (JSON has all).
MAX_PRIORITIES_SHOWN = 6


def _priorities(priorities: list[str]) -> str:
    if not priorities:
        return "(no priorities)"
    shown = " → ".join(priorities[:MAX_PRIORITIES_SHOWN])
    hidden = len(priorities) - MAX_PRIORITIES_SHOWN
    return f"{shown} → … (+{hidden} more)" if hidden > 0 else shown


def _bound(value: int | None) -> str:
    return "?" if value is None else str(value)


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def _pod_range(pool: NodePool) -> str:
    if not pool.pod_range:
        return "-"
    if pool.pod_block_prefix is None:
        return pool.pod_range
    return f"{pool.pod_range} (/{pool.pod_block_prefix})"


def _pool_table(pools: list[NodePool]) -> str:
    # Most clusters put every pool on the default pod range; only show the
    # column once pools are spread across more than one.
    show_pod_range = len({p.pod_range for p in pools if p.pod_range}) > 1
    rows = [
        [
            pool.compute_class or "-",
            pool.name,
            pool.sentry_name or "-",
            pool.machine_type,
            pool.nodes,
            f"{_bound(pool.min_nodes)}-{_bound(pool.max_nodes)}"
            + (" (max)" if pool.at_max else "")
            if pool.autoscaling
            else "off",
            _pct(pool.cpu_utilization),
            _pct(pool.memory_utilization),
            *([_pod_range(pool)] if show_pod_range else []),
            pool.version,
        ]
        for pool in pools
    ]
    return tabulate(
        rows,
        headers=[
            "COMPUTE CLASS",
            "POOL",
            "SENTRY NAME",
            "MACHINE",
            "NODES",
            "AUTOSCALING",
            "CPU REQ",
            "MEM REQ",
            *(["POD RANGE"] if show_pod_range else []),
            "VERSION",
        ],
        colalign=(
            "left",
            "left",
            "left",
            "left",
            "right",
            "left",
            "right",
            "right",
            *(["left"] if show_pod_range else []),
            "left",
        ),
    )


def format_text(result: ClusterNodePools) -> str:
    cluster = result.cluster
    lines = [
        f"Cluster: {cluster.name} ({cluster.location}, project {cluster.project})",
        f"Control plane: {result.control_plane_version}",
    ]
    if result.autoprovisioning:
        lines.append("Node auto-provisioning: enabled")
        for limit in result.resource_limits:
            lines.append(
                f"  {limit.resource:<7} {limit.used:.0f} / {limit.maximum:.0f} "
                f"{limit.unit} ({_pct(limit.utilization)})"
            )
    elif any(cc.auto_create for cc in result.compute_classes):
        lines.append(
            "Node auto-provisioning: disabled cluster-wide "
            "(compute classes still auto-create node pools)"
        )
    else:
        lines.append("Node auto-provisioning: disabled")
    lines.append("")

    # tabulate < 0.10 raises on colalign with no rows.
    lines.append(_pool_table(result.pools) if result.pools else "No node pools")
    lines.append("")

    used = [cc for cc in result.compute_classes if cc.nodes]
    unused = [cc.name for cc in result.compute_classes if not cc.nodes]
    if used or unused:
        lines.append("Compute classes:")
        for cc in used:
            create = "auto-creates pools" if cc.auto_create else "existing pools only"
            lines.append(f"  {cc.name}: {_priorities(cc.priorities)}")
            lines.append(f"    {create}; {cc.pools} pool(s), {cc.nodes} node(s)")
        if unused:
            lines.append(click.style(f"  No nodes: {', '.join(unused)}", dim=True))
        lines.append("")

    for note in result.notes:
        lines.append(click.style(f"ℹ {note}", dim=True))
    if result.notes:
        lines.append("")

    if result.findings:
        lines.append(click.style("Findings:", bold=True))
        lines.extend(
            f"  {click.style('⚠', fg='yellow')} {finding}"
            for finding in result.findings
        )
    else:
        lines.append(f"{click.style('✔', fg='green')} No findings")
    return "\n".join(lines)
