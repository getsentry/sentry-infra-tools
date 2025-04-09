import os
import subprocess

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


def _is_external_connection_allowed(
    project: str, region: str, cluster_name: str
) -> bool:
    """
    Check if external connections are enabled for the cluster.
    """
    is_enabled = subprocess.check_output(
        [
            "gcloud",
            "container",
            "clusters",
            "describe",
            cluster_name,
            "--region",
            region,
            "--project",
            project,
            "--format",
            "value(controlPlaneEndpointsConfig.dnsEndpointConfig.allowExternalTraffic)",
        ],
    )
    res = is_enabled.decode("utf-8").strip()
    return True if res == "True" else False


def ensure_iap_tunnel(ctx: click.core.Context, quiet: bool = False) -> str:
    """
    Create an IAP tunnel and generate a temporary kubeconfig that uses it.

    If the tunnel already exists, it will be reused.
    Returns a file object of the temporary kubeconfig file.
    """
    port: int = ctx.obj.cluster.services_data["iap_local_port"]
    context = ctx.obj.context_name
    _, project, region, cluster = context.split("_")

    with open(KUBE_CONFIG_PATH) as kubeconfig_file:
        kubeconfig = yaml.safe_load(kubeconfig_file)

        for cluster in kubeconfig["clusters"]:
            if cluster["name"] == context:
                break
        else:
            # example: gke_internal-sentry_us-central1-b_zdpwkxst
            raise ValueError(
                f"Can't find k8s cluster for {context} context. You might need to run:\n"
                f"gcloud container clusters get-credentials {cluster} "
                f"--region {region} --project {project}"
            )
        if not _is_external_connection_allowed(project, region, cluster):
            raise ValueError(
                f"External connections are not allowed for cluster {cluster} in"
                f"region {region}, project {project}."
            )

        tmp_kubeconfig_path = os.path.join(
            os.path.dirname(KUBE_CONFIG_PATH), f"sentry-kube.config.{port}.yaml"
        )
        with open(tmp_kubeconfig_path, "w") as tmp_cf:
            yaml.dump(kubeconfig, tmp_cf)

    return tmp_kubeconfig_path
