import logging
import os
import subprocess

import click
import yaml

logging.basicConfig(level=os.getenv("SENTRY_KUBE_LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)

KUBE_CONFIG_PATH = os.getenv(
    "KUBECONFIG_PATH",
    os.path.expanduser("~/.kube/config"),
)


def _get_cluster_credentials(context: str) -> None:
    """
    Run gcloud to fetch cluster credentials and populate kubeconfig.

    Raises click.ClickException on failure with a user-friendly message.
    """
    logger.debug("Fetching cluster credentials for context: %s", context)

    # example context: gke_internal-sentry_us-central1-b_zdpwkxst
    parts = context.split("_")
    if len(parts) != 4 or parts[0] != "gke":
        logger.info(
            "Context %s is not in gke_PROJECT_REGION_CLUSTER format, skipping automatic credential fetch",
            context,
        )
        return

    _, project, region, cluster = parts
    logger.debug(
        "Parsed context: project=%s, region=%s, cluster=%s", project, region, cluster
    )

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
    # Set KUBECONFIG to ensure gcloud writes to the same path we read from
    env = os.environ.copy()
    env["KUBECONFIG"] = KUBE_CONFIG_PATH
    logger.debug("Running gcloud with KUBECONFIG=%s", KUBE_CONFIG_PATH)

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    logger.debug(
        "gcloud returncode=%d, stdout=%s, stderr=%s",
        result.returncode,
        result.stdout,
        result.stderr,
    )

    if result.returncode != 0:
        raise click.ClickException(
            f"Failed to get cluster credentials:\n{result.stderr or result.stdout}"
        )
    logger.debug("Successfully fetched cluster credentials")


def ensure_iap_tunnel(ctx: click.core.Context) -> str:
    """
    Ensure kubeconfig exists with the required cluster context.

    Creates ~/.kube directory and fetches cluster credentials via gcloud
    if needed. Returns the path to the kubeconfig file.
    """
    context = ctx.obj.context_name
    logger.debug("Ensuring kubeconfig for context: %s", context)
    logger.debug("KUBE_CONFIG_PATH=%s", KUBE_CONFIG_PATH)

    kube_dir = os.path.dirname(KUBE_CONFIG_PATH)
    if not os.path.isdir(kube_dir):
        logger.debug("Creating kube directory: %s", kube_dir)
        os.makedirs(kube_dir, mode=0o700)

    def _get_cluster_server() -> str | None:
        """Return the server URL for the context, or None if not found."""
        if not os.path.isfile(KUBE_CONFIG_PATH):
            logger.debug("Kubeconfig file does not exist: %s", KUBE_CONFIG_PATH)
            return None
        with open(KUBE_CONFIG_PATH) as f:
            kubeconfig = yaml.safe_load(f) or {}
        clusters = kubeconfig.get("clusters") or []
        cluster_names = [c.get("name") for c in clusters]
        logger.debug(
            "Found %d clusters in kubeconfig: %s", len(clusters), cluster_names
        )
        for c in clusters:
            if c.get("name") == context:
                server = c.get("cluster", {}).get("server", "")
                logger.debug("Context %s server: %s", context, server)
                return server
        logger.debug("Context %s not found", context)
        return None

    def _needs_credential_fetch() -> bool:
        """Check if credentials need to be fetched or re-fetched."""
        server = _get_cluster_server()
        if server is None:
            logger.debug("Context not found, need to fetch credentials")
            return True
        parts = context.split("_")
        if len(parts) == 4 and parts[0] == "gke":
            if not server.endswith("gke.goog"):
                logger.debug(
                    "Server %s does not end with gke.goog, need to re-fetch with --dns-endpoint",
                    server,
                )
                return True
        return False

    if _needs_credential_fetch():
        logger.debug("Fetching credentials")
        _get_cluster_credentials(context)
        # Verify credentials were added successfully with DNS endpoint
        if _needs_credential_fetch():
            server = _get_cluster_server()
            if server is None:
                logger.debug("Context still not found after credential fetch")
                raise click.ClickException(
                    f"Context '{context}' not found in kubeconfig and could not be fetched automatically.\n"
                    "Ensure the context exists in your kubeconfig or is in gke_PROJECT_REGION_CLUSTER format."
                )
            else:
                logger.debug(
                    "Server still not using DNS endpoint after fetch: %s", server
                )
                raise click.ClickException(
                    f"Failed to configure DNS endpoint for {context}. Server: {server}"
                )

    logger.debug("Returning kubeconfig path: %s", KUBE_CONFIG_PATH)
    return KUBE_CONFIG_PATH
