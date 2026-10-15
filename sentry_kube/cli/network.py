import json

import click
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from tabulate import tabulate

from libsentrykube.network import ClusterNetwork, GkeClusterRef, fetch_cluster_network

# Registered as `sentry-kube cluster network` (see cluster.py), not top level.


def gke_cluster_ref(ctx: click.Context) -> GkeClusterRef:
    """The GKE cluster behind the selected sentry-kube cluster's kube context."""
    try:
        return GkeClusterRef.from_context(ctx.obj.context_name)
    except ValueError as e:
        raise click.ClickException(str(e))


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
    help="Report ranges at or above this utilization percentage.",
)
@click.option(
    "--check",
    is_flag=True,
    help="Exit with status 1 if anything is reported under Findings.",
)
@click.pass_context
def network(ctx: click.Context, output: str, warn_threshold: int, check: bool) -> None:
    """
    Show the GKE cluster's networking (read-only): node, pod, service and
    control plane ranges with their utilization, plus the authorized networks
    and intra-node firewall rules that must cover the node range.

    Node range usage counts IPs held by VMs, internal load balancers and
    address reservations of the cluster's project.
    """
    cluster = gke_cluster_ref(ctx)
    try:
        result = fetch_cluster_network(cluster, warn_threshold=warn_threshold / 100)
    except HttpError as e:
        raise click.ClickException(
            f"GCP API request failed ({e.status_code}): {e.reason}"
        )
    except GoogleAuthError as e:
        raise click.ClickException(
            f"GCP authentication failed: {e}\n"
            "Run `gcloud auth application-default login` and try again."
        )

    if output == "json":
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        click.echo(format_text(result))

    if check and result.findings:
        ctx.exit(1)


def format_text(result: ClusterNetwork) -> str:
    cluster = result.cluster
    rows = [
        [
            usage.role,
            usage.name or "-",
            usage.cidr,
            "-" if usage.used is None else usage.used,
            usage.capacity,
            "-" if usage.utilization is None else f"{usage.utilization:.1%}",
        ]
        for usage in result.ranges
    ]
    lines = [
        f"Cluster: {cluster.name} ({cluster.location}, project {cluster.project})",
        f"Subnet:  {result.subnet} in network {result.network} ({result.subnet_region})",
        "",
        tabulate(
            rows,
            headers=["ROLE", "RANGE", "CIDR", "USED", "CAPACITY", "UTIL"],
            colalign=("left", "left", "left", "right", "right", "right"),
        ),
        "",
    ]

    if result.authorized_networks_enabled:
        lines.append(
            f"Authorized networks: enabled ({len(result.authorized_networks)})"
        )
        for authorized in result.authorized_networks:
            label = f"  ({authorized.display_name})" if authorized.display_name else ""
            lines.append(f"  {authorized.cidr}{label}")
        for cidr in result.original_node_ranges:
            lines.append(f"  {cidr}  (original node range, allowed implicitly)")
    else:
        lines.append("Authorized networks: disabled")
    lines.append(
        "Intra-node firewall rules: "
        + (", ".join(result.node_firewall_rules) or "none found")
    )
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
