import os
import subprocess

import click
import yaml


KUBE_CONFIG_PATH = os.getenv(
    "KUBECONFIG_PATH",
    os.path.expanduser("~/.kube/config"),
)


def _get_cluster_credentials(context: str) -> None:
    """
    Run gcloud to fetch cluster credentials and populate kubeconfig.
    """
    # example context: gke_internal-sentry_us-central1-b_zdpwkxst
    _, project, region, cluster = context.split("_")
    cmd = [
        "gcloud",
        "container",
        "clusters",
        "get-credentials",
        cluster,
        "--dns-endpoint",
        "--region",
        region,
        "--project",
        project,
    ]
    click.echo(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def ensure_iap_tunnel(ctx: click.core.Context) -> str:
    """
    Create an IAP tunnel and generate a temporary kubeconfig that uses it.

    If the tunnel already exists, it will be reused.
    Returns a file object of the temporary kubeconfig file.
    """
    port: int = ctx.obj.cluster.services_data["iap_local_port"]
    context = ctx.obj.context_name

    kube_dir = os.path.dirname(KUBE_CONFIG_PATH)
    if not os.path.isdir(kube_dir):
        os.makedirs(kube_dir, mode=0o700)

    if not os.path.isfile(KUBE_CONFIG_PATH):
        _get_cluster_credentials(context)

    def load_kubeconfig() -> dict:
        with open(KUBE_CONFIG_PATH) as f:
            return yaml.safe_load(f)

    kubeconfig = load_kubeconfig()

    context_found = any(c["name"] == context for c in kubeconfig["clusters"])
    if not context_found:
        _get_cluster_credentials(context)
        kubeconfig = load_kubeconfig()

    tmp_kubeconfig_path = os.path.join(
        os.path.dirname(KUBE_CONFIG_PATH), f"sentry-kube.config.{port}.yaml"
    )
    with open(tmp_kubeconfig_path, "w") as tmp_cf:
        yaml.dump(kubeconfig, tmp_cf)

    return tmp_kubeconfig_path
