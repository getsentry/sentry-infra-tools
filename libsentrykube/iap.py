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

    Raises click.ClickException on failure with a user-friendly message.
    """
    # example context: gke_internal-sentry_us-central1-b_zdpwkxst
    parts = context.split("_")
    if len(parts) != 4 or parts[0] != "gke":
        raise click.ClickException(
            f"Invalid GKE context format: {context}\n"
            "Expected format: gke_PROJECT_REGION_CLUSTER"
        )

    _, project, region, cluster = parts
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
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException(
            f"Failed to get cluster credentials:\n{result.stderr or result.stdout}"
        )


def ensure_iap_tunnel(ctx: click.core.Context) -> str:
    """
    Ensure kubeconfig exists with the required cluster context.

    Creates ~/.kube directory and fetches cluster credentials via gcloud
    if needed. Returns the path to the kubeconfig file.
    """
    context = ctx.obj.context_name

    kube_dir = os.path.dirname(KUBE_CONFIG_PATH)
    if not os.path.isdir(kube_dir):
        os.makedirs(kube_dir, mode=0o700)

    def _context_exists() -> bool:
        if not os.path.isfile(KUBE_CONFIG_PATH):
            return False
        with open(KUBE_CONFIG_PATH) as f:
            kubeconfig = yaml.safe_load(f) or {}
        clusters = kubeconfig.get("clusters", [])
        return any(c.get("name") == context for c in clusters)

    if not _context_exists():
        _get_cluster_credentials(context)
        # Verify credentials were added successfully
        if not _context_exists():
            raise click.ClickException(
                f"Failed to add context {context} to kubeconfig after credential fetch"
            )

    return KUBE_CONFIG_PATH
