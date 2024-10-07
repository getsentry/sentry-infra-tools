import logging
import re
import subprocess
import webbrowser

import click
from libsentrykube.config import Config
from libsentrykube.customer import get_project
from libsentrykube.customer import get_region
from libsentrykube.gcloud import lookup_zone

__all__ = ("tunnel",)


"""
A sentry-kube wrapper for gcloud iap tunnel command:

SYNOPSIS
    gcloud compute start-iap-tunnel INSTANCE_NAME INSTANCE_PORT
        [--iap-tunnel-disable-connection-check]
        [--local-host-port=LOCAL_HOST_PORT; default="localhost:0"]
        [--zone=ZONE]
        [--network=NETWORK --region=REGION : --dest-group=DEST_GROUP]
        [GCLOUD_WIDE_FLAG ...]
"""

logger = logging.getLogger(__name__)


def log_stderr(program: str, line: str) -> None:
    logger.debug("%s: %s", program, line.rstrip())


def build_command(ctx, host, host_port, project, verbose, region, zone, local_port):
    customer_name = ctx.obj.customer_name
    config = Config()
    cluster_name = config.silo_regions[customer_name].k8s_config.cluster_name

    if cluster_name is None:
        cluster_name = "default"

    if not project:
        project = get_project(config, customer_name, cluster_name)

    if not region:
        region = get_region(config, customer_name, cluster_name)

    if not zone:
        zone = lookup_zone(host, project, region)

    local_port_full = f"localhost:{local_port}"

    cmd = [
        "gcloud",
        "compute",
        "start-iap-tunnel",
        host,
        host_port,
        "--zone",
        f"{region}-{zone}",
        "--project",
        project,
        "--local-host-port",
        local_port_full,
    ]

    return cmd


@click.command()
@click.argument("host")
# TODO: Should this become one or more options?
@click.argument("host-port")
@click.option(
    "--project",
    "-p",
    required=False,
    help="GCP project for the instance, required if not in 'internal-sentry'",
)
@click.option("-v", "--verbose", count=True)
@click.option(
    "--region", "-r", show_default=True, required=False, help="The region to act on"
)
@click.option(
    "--zone", "-z", show_default=True, required=False, help="The zone to act on"
)
@click.option(
    "--local-port",
    "-l",
    default=0,
    show_default=True,
    required=False,
    help="The localhost port to tunnel through",
)
@click.option(
    "--browser",
    "-b",
    is_flag=True,
    default=False,
    show_default=True,
    required=False,
    help="Open the localhost port in your browser. Only works if an explicit local port is specified.",
)
@click.pass_context
def tunnel(ctx, host, host_port, project, verbose, region, zone, local_port, browser):
    """
    IAP tunnel into a host.
    """

    if host_port in ["ssh", "SSH"]:
        host_port = "22"

    if host_port in ["rabbitmq", "rabbit", "Rabbit", "RabbitMQ"]:
        host_port = "15672"

    if host_port in ["kafka", "Kafka"]:
        host_port = "9092"

    cmd = build_command(
        ctx, host, host_port, project, verbose, region, zone, local_port
    )
    click.echo(" ".join(cmd))

    # Inspired by https://blog.dalibo.com/2022/09/12/monitoring-python-subprocesses.html
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as proc:
        errs = []
        for line in proc.stderr:
            # click.echo(line)
            if "Picking local unused port" in line:
                pattern = r"\[(\d+)\]"
                local_port = re.search(pattern, line).group(1)
                url = f"localhost:{local_port}"
                click.echo(f"Tunnel now available at {url}")
                if browser:
                    click.echo(f"Found local port, launching browser to {url}...")
                    webbrowser.open(url)
            errs.append(line)
        stdout, _ = proc.communicate()
    result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, "\n".join(errs))
    click.echo(result)
