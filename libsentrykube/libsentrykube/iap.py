import os
import socket
import subprocess
import time
from contextlib import closing
from urllib.parse import urlparse

from libsentrykube.ssh import build_ssh_command

import click
import yaml

KUBE_CONFIG_PATH = os.getenv(
    "KUBECONFIG_PATH",
    os.path.expanduser("~/.kube/config"),
)

KUBECTL_JUMP_HOST = os.getenv(
    "SENTRY_KUBE_KUBECTL_JUMP_HOST",
    "scratch",
)


def _tcp_port_check(port: int) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        # The error indicator is 0 if the operation succeeded, otherwise the value of the
        # errno variable.
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _dns_check() -> None:
    try:
        socket.gethostbyname("kubernetes")
    except socket.gaierror:
        raise RuntimeError(
            "Need to make host 'kubernetes' resolved to localhost\n\n"
            "Action:\n"
            "- You need to add an entry to /etc/hosts, like\n\n"
            "127.0.0.1       kubernetes\n"
        )


def ensure_iap_tunnel(ctx: click.core.Context, quiet: bool = False) -> str:
    """
    Create an IAP tunnel and generate a temporary kubeconfig that uses it.

    If the tunnel already exists, it will be reused.
    Returns a file object of the temporary kubeconfig file.
    """
    port: int = ctx.obj.cluster.services_data["iap_local_port"]
    context = ctx.obj.context_name

    with open(KUBE_CONFIG_PATH) as kubeconfig_file:
        kubeconfig = yaml.safe_load(kubeconfig_file)

        for cluster in kubeconfig["clusters"]:
            if cluster["name"] == context:
                break
        else:
            # example: gke_internal-sentry_us-central1-b_zdpwkxst
            _, project, region, cluster = context.split("_")
            raise ValueError(
                f"Can't find k8s cluster for {context} context. You might need to run:\n"
                f"gcloud container clusters get-credentials {cluster} "
                f"--region {region} --project {project}"
            )

        _dns_check()
        control_plane_ip = urlparse(cluster["cluster"]["server"]).hostname
        cluster["cluster"]["server"] = f"https://kubernetes:{port}"

        tmp_kubeconfig_path = os.path.join(
            os.path.dirname(KUBE_CONFIG_PATH), f"sentry-kube.config.{port}.yaml"
        )
        with open(tmp_kubeconfig_path, "w") as tmp_cf:
            yaml.dump(kubeconfig, tmp_cf)

    port_fwd = f"{port}:{control_plane_ip}:443"

    if not _tcp_port_check(port):
        if not quiet:
            click.echo(f"Spawning port forwarding for {port_fwd}")

        subprocess.Popen(
            build_ssh_command(
                ctx,
                host=KUBECTL_JUMP_HOST,
                project=None,
                user=None,
                ssh_key_file=None,
                region=None,
                zone=None,
                # -N -- Do not execute remote command
                # -T -- do not allocate tty
                # -f -- go to background, before the command execution
                # ExitOnForwardFailure=yes
                # Terminate the connection if it cannot set up all requested dynamic,
                # tunnel, local, and remote port forwardings.
                ssh_args=(
                    "-NTf",
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-L",
                    port_fwd,
                ),
            ),
            # spawn as detached process
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        # try port_check_attempts times if the port forwarding is in place
        port_check_attempts = 10
        for _ in range(port_check_attempts):
            if not quiet:
                click.echo("poking on port ...")
            if _tcp_port_check(port):
                if not quiet:
                    click.echo("port forwarding in place")
                break
            else:
                time.sleep(3)

    return tmp_kubeconfig_path
