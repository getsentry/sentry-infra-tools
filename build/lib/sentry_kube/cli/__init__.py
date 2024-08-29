import os
import socket
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from pkgutil import walk_packages
from typing import Optional
from types import MappingProxyType

import click
import sentry_sdk
from libsentrykube.cluster import Cluster
from libsentrykube.cluster import load_cluster_configuration
from libsentrykube.config import Config
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
    cluster: Optional[Cluster] = None


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
    help="Cluster name to operate on. This is ignored if the customer has one cluster",
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

        if ctx.invoked_subcommand == "datadog-log-terragrunt":
            return

        if ctx.invoked_subcommand == "datadog-log":
            return

        if ctx.invoked_subcommand == "connect":
            ctx.obj = CliContext(
                context_name=None,  # type: ignore
                customer_name=None,  # type: ignore
                cluster_name=cluster_name,
                quiet_mode=quiet,
                service_monitors=None,  # type: ignore
            )
            return
        if ctx.invoked_subcommand == "get-customers":
            return

        if not customer:
            try:
                customer = SESSION_FILE.read_text().strip()
            except FileNotFoundError:
                # Backslashes not allowed in f-strings...
                newline_customers = "\n".join(config.get_customers())
                die(
                    f"""It doesn't seem like you have a bastion connection and customer wasn't specified.

In a separate shell, start `sentry-kube connect [customer].`

If you are already inside the network (perhaps you're running this on dumpster),
you must explicitly say sentry-kube [-C, --customer NAME] COMMAND...

Customers include:
{newline_customers}
"""
                )

        if not quiet:
            click.echo(f"Operating for customer {customer}.")

        if customer in config.silo_regions:
            customer_config = config.silo_regions[customer]
        else:
            print("Invalid region specified, must be one of:")
            for region in config.silo_regions:
                print(f"  {region}")
            exit(1)

        cluster_to_load = customer_config.k8s_config.cluster_name or cluster_name

        cluster = load_cluster_configuration(customer_config.k8s_config, cluster_to_load)

        context_name = cluster.services_data["context"]

        if ctx.invoked_subcommand == "ssh":
            ctx.obj = CliContext(
                context_name=context_name,
                customer_name=customer,
                cluster_name=cluster.name,
                quiet_mode=quiet,
                service_monitors={},  # type: ignore
            )
            return

        set_service_paths(cluster.services)

        if ctx.invoked_subcommand in ("rendervalues", "render", "lint", "validate"):
            if not customer:
                die(
                    f"Please pass in a customer to {ctx.invoked_subcommand} "
                    f"example: sentry-kube -C CUSTOMER {ctx.invoked_subcommand}"
                )

            # Force offline, we don't need connections for these subcommands
            os.environ["KUBERNETES_OFFLINE"] = "1"

            ctx.obj = CliContext(
                context_name=context_name,
                customer_name=customer,
                cluster_name=cluster.name,
                quiet_mode=quiet,
                service_monitors=customer_config.service_monitors,
            )
            return

        if not quiet:
            click.echo(f"Kube context: {context_name}")

        ctx.obj = CliContext(
            context_name=context_name,
            customer_name=customer,
            cluster_name=cluster.name,
            quiet_mode=quiet,
            cluster=cluster,
            service_monitors=customer_config.service_monitors,
        )

        os.environ["KUBECONFIG"] = kubeconfig = ensure_iap_tunnel(ctx, quiet)

        kube_set_context(context_name, kubeconfig=kubeconfig)

        _configure_colors(ctx)


for loader, module_name, is_pkg in walk_packages(__path__, __name__ + "."):
    module = import_module(module_name)
    for attr in getattr(module, "__all__", []):
        cmd = getattr(module, attr)
        if isinstance(cmd, click.Command):
            main.add_command(cmd)
