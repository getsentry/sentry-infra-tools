import os

import click
import yaml


KUBE_CONFIG_PATH = os.getenv(
    "KUBECONFIG_PATH",
    os.path.expanduser("~/.kube/config"),
)


def ensure_iap_tunnel(ctx: click.core.Context) -> str:
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

        tmp_kubeconfig_path = os.path.join(
            os.path.dirname(KUBE_CONFIG_PATH), f"sentry-kube.config.{port}.yaml"
        )
        with open(tmp_kubeconfig_path, "w") as tmp_cf:
            yaml.dump(kubeconfig, tmp_cf)

    return tmp_kubeconfig_path
