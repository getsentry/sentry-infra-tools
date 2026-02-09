import os
import socket
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from pkgutil import walk_packages
from typing import Iterator, Optional, Sequence
from types import MappingProxyType

import click
import sentry_sdk
from libsentrykube.cluster import Cluster
from libsentrykube.cluster import list_clusters_for_customer
from libsentrykube.cluster import load_cluster_configuration
from libsentrykube.config import Config
from libsentrykube.config import K8sConfig
from libsentrykube.customer import get_region_config
from libsentrykube.events import ensure_datadog_api_key_set
from libsentrykube.iap import ensure_iap_tunnel
from libsentrykube.service import set_service_paths
from libsentrykube.utils import die
from libsentrykube.utils import kube_set_context
from libsentrykube.utils import set_workspace_root_start

SESSION_FILE = Path("/") / "tmp" / "sentry-kube-session"
sentry_sdk.init(
    dsn="https://6b86f72181e2484f949994447137d64d@o1.ingest.sentry.io/4504373448540160",
    traces_sample_rate=1.0,
    environment=socket.gethostname(),
)


@dataclass(frozen=True)
class CliContext:
    context_name: str
    customer_name: str
    cluster_name: str
    quiet_mode: bool
    service_monitors: MappingProxyType
    service_class: Optional[str] = None
    cluster: Optional[Cluster] = None
    # List of all cluster names for the region when using "*" wildcard
    all_cluster_names: Optional[Sequence[str]] = None
    # K8s config for loading additional clusters when iterating
    k8s_config: Optional[K8sConfig] = None

    def get_cluster_names_to_iterate(self) -> Sequence[str]:
        """
        Returns the list of cluster names to iterate over.
        If all_cluster_names is set (wildcard was used), returns all clusters.
        Otherwise, returns just the current cluster_name.
        """
        if self.all_cluster_names:
            return self.all_cluster_names
        return (self.cluster_name,)


def iterate_clusters(ctx) -> Iterator["CliContext"]:
    """
    Generator that yields CliContext objects for each cluster to iterate over.

    When a wildcard cluster ("*") was specified, this iterates through all clusters
    in the region, setting up the proper context (IAP tunnel, kubectl context, etc.)
    for each one.

    When a specific cluster was specified, this yields just that cluster's context.

    Usage:
        for cluster_ctx in iterate_clusters(ctx):
            # cluster_ctx has the proper context for this cluster
            pass
    """
    cli_ctx = ctx.obj
    cluster_names = cli_ctx.get_cluster_names_to_iterate()

    for cluster_name in cluster_names:
        if cluster_name == cli_ctx.cluster_name:
            # Already set up for this cluster, just yield the existing context
            yield cli_ctx
        else:
            # Need to set up context for a different cluster
            if cli_ctx.k8s_config is None:
                die("k8s_config is required to iterate over multiple clusters")

            cluster = load_cluster_configuration(cli_ctx.k8s_config, cluster_name)
            context_name = cluster.services_data["context"]

            set_service_paths(cluster.services, helm=cluster.helm_services.services)

            new_ctx = CliContext(
                context_name=context_name,
                customer_name=cli_ctx.customer_name,
                cluster_name=cluster.name,
                quiet_mode=cli_ctx.quiet_mode,
                cluster=cluster,
                service_monitors=cli_ctx.service_monitors,
                service_class=cli_ctx.service_class,
                all_cluster_names=cli_ctx.all_cluster_names,
                k8s_config=cli_ctx.k8s_config,
            )

            # Set up IAP tunnel and kubectl context for this cluster
            kubeconfig = ensure_iap_tunnel_for_context(ctx, new_ctx)
            os.environ["KUBECONFIG"] = kubeconfig
            kube_set_context(context_name, kubeconfig=kubeconfig)

            if not cli_ctx.quiet_mode:
                click.echo(f"\nSwitching to cluster: {cluster_name}")
                click.echo(f"Kube context: {context_name}")

            yield new_ctx


def ensure_iap_tunnel_for_context(ctx, cli_ctx: "CliContext") -> str:
    """
    Wrapper around ensure_iap_tunnel that works with a CliContext object
    instead of requiring ctx.obj to be set.
    """
    # Temporarily set ctx.obj to the new context
    original_obj = ctx.obj
    ctx.obj = cli_ctx
    try:
        return ensure_iap_tunnel(ctx)
    finally:
        ctx.obj = original_obj


def _configure_colors(ctx):
    """Allow to force on/off colored output"""
    color = os.environ.get("FORCE_COLOR", "").lower()
    if color in {"1", "true", "yes", "on"}:
        ctx.color = True
    elif color in {"0", "false", "no", "off"}:
        ctx.color = False


@click.group()
@click.option(
    "-c",
    "--cluster",
    "cluster_name",
    type=str,
    help="Cluster name to operate on. Use '*' to operate on all clusters in the region. This is ignored if the customer has one cluster",
    envvar="SENTRY_KUBE_CLUSTER",
    default="default",
)
@click.option(
    "--root",
    type=click.Path(dir_okay=True, file_okay=False, exists=True),
    help="Skip auto-detection of root directory.",
    envvar="SENTRY_KUBE_ROOT",
)
@click.option(
    "-C",
    "--customer",
    type=str,
    help="Customer name. Does not override the customer for `connect`.",
    envvar="SENTRY_KUBE_CUSTOMER",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help="Do not output informational messages",
    envvar="SENTRY_KUBE_QUIET",
)
@click.option(
    "-s",
    "--no-sentry",
    is_flag=True,
    envvar="SENTRY_KUBE_NO_SENTRY",
    help="Disables sending errors/transactions to Sentry when running",
)
@click.pass_context
def main(ctx, *, cluster_name, root, customer, quiet, no_sentry):
    """\b
 __                  __
/  |                /  |
$$ |   __  __    __ $$ |____    ______
$$ |  /  |/  |  /  |$$      \\  /      \\
$$ |_/$$/ $$ |  $$ |$$$$$$$  |/$$$$$$  |
$$   $$<  $$ |  $$ |$$ |  $$ |$$    $$ |
$$$$$$  \\ $$ \\__$$ |$$ |__$$ |$$$$$$$$/
$$ | $$  |$$    $$/ $$    $$/ $$       |
$$/   $$/  $$$$$$/  $$$$$$$/   $$$$$$$/

Get kubed.
"""
    if root is not None:
        set_workspace_root_start(root)

    config = Config()
    ensure_datadog_api_key_set()

    if no_sentry:
        # https://github.com/getsentry/sentry-python/issues/659#issuecomment-604014817
        sentry_sdk.init()

    with sentry_sdk.start_transaction(
        op="function",
        name="main()",
    ) as transaction:
        transaction.set_tag(key="subcommand", value=ctx.invoked_subcommand)

        if (
            ctx.invoked_subcommand == "datadog-log-terragrunt"
            or ctx.invoked_subcommand == "datadog-log"
            or ctx.invoked_subcommand == "get-regions"
        ):
            return

        newline_customers = "\n".join(config.get_regions())
        if not customer:
            die(
                f"""Region was not specified, please use `-C` to specify a region.

Valid regions:
{newline_customers}
"""
            )

        try:
            customer_name, customer_config = get_region_config(config, customer)
        except ValueError:
            die(
                f"""Invalid region specified, must be one of:
{newline_customers}
"""
            )

        if not quiet:
            click.echo(f"Operating for customer {customer_name}.")

        # Handle "*" wildcard to operate on all clusters in the region
        all_cluster_names: Optional[Sequence[str]] = None
        if cluster_name == "*":
            clusters = list_clusters_for_customer(customer_config.k8s_config)
            if not clusters:
                die("No clusters found for this region.")
            all_cluster_names = tuple(c.name for c in clusters)
            # Use the first cluster for initial context setup
            cluster_to_load = all_cluster_names[0]
            if not quiet:
                click.echo(
                    f"Wildcard cluster specified. Found clusters: {', '.join(all_cluster_names)}"
                )
        else:
            cluster_to_load = customer_config.k8s_config.cluster_name or cluster_name

        cluster = load_cluster_configuration(
            customer_config.k8s_config, cluster_to_load
        )

        context_name = cluster.services_data["context"]
        service_class = customer_config.k8s_config.service_class

        if ctx.invoked_subcommand == "ssh":
            ctx.obj = CliContext(
                context_name=context_name,
                customer_name=customer_name,
                cluster_name=cluster.name,
                quiet_mode=quiet,
                service_monitors={},  # type: ignore
                all_cluster_names=all_cluster_names,
                k8s_config=customer_config.k8s_config,
            )
            return

        set_service_paths(cluster.services, helm=cluster.helm_services.services)

        if ctx.invoked_subcommand in (
            "rendervalues",
            "render",
            "lint",
            "validate",
        ):
            # Force offline, we don't need connections for these subcommands
            os.environ["KUBERNETES_OFFLINE"] = "1"

            ctx.obj = CliContext(
                context_name=context_name,
                customer_name=customer_name,
                cluster_name=cluster.name,
                quiet_mode=quiet,
                service_monitors=customer_config.service_monitors,
                service_class=service_class,
                all_cluster_names=all_cluster_names,
                k8s_config=customer_config.k8s_config,
            )
            return

        if not quiet:
            click.echo(f"Kube context: {context_name}")

        ctx.obj = CliContext(
            context_name=context_name,
            customer_name=customer_name,
            cluster_name=cluster.name,
            quiet_mode=quiet,
            cluster=cluster,
            service_monitors=customer_config.service_monitors,
            service_class=service_class,
            all_cluster_names=all_cluster_names,
            k8s_config=customer_config.k8s_config,
        )

        os.environ["KUBECONFIG"] = kubeconfig = ensure_iap_tunnel(ctx)

        kube_set_context(context_name, kubeconfig=kubeconfig)

        _configure_colors(ctx)


for loader, module_name, is_pkg in walk_packages(__path__, __name__ + "."):
    module = import_module(module_name)
    for attr in getattr(module, "__all__", []):
        cmd = getattr(module, attr)
        if isinstance(cmd, click.Command):
            main.add_command(cmd)
