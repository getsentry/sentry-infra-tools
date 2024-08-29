from datetime import datetime
from time import sleep

import click

from libsentrykube.kube import kube_get_client, rollout_status_deployment
from libsentrykube.utils import die

__all__ = ("restart",)


@click.command()
@click.option("--yes", "-y", is_flag=True)
@click.option("--namespace", "-n", default="default", required=False)
@click.option("--selector", "-l", required=True)
def restart(namespace, selector, yes):
    """
    Restarts deployment(s) by bumping the sentry-kube/restartedAt annotation.

    Specify the deployment(s) with a label selector, and optionally a namespace.
    """
    from kubernetes import client

    api = client.AppsV1Api(kube_get_client())
    deployments = api.list_namespaced_deployment(
        namespace=namespace,
        label_selector=selector,
    ).items

    if len(deployments) == 0:
        click.echo(
            "Nothing found to restart.",
            err=True,
        )
        return

    click.echo("The following deployments will be restarted, in order:")
    for deployment in deployments:
        click.echo(
            f"Deployment "
            f"{deployment.metadata.namespace}/{deployment.metadata.name}"
            f" with strategy {deployment.spec.strategy.type}"
        )

    if not (yes or click.confirm("Would you like to continue?")):
        die()

    ts = datetime.now().isoformat()
    for deployment in deployments:
        if deployment.spec.template.metadata.annotations is None:
            deployment.spec.template.metadata.annotations = {}
        deployment.spec.template.metadata.annotations["sentry-kube/restartedAt"] = ts
        name = deployment.metadata.name
        namespace = deployment.metadata.namespace
        click.echo(f"Patching {namespace}/{name}")
        api.patch_namespaced_deployment(name=name, namespace=namespace, body=deployment)

    deployment_statuses = [None] * len(deployments)
    while True:
        flag = True
        for i, deployment in enumerate(deployments):
            name = deployment.metadata.name
            namespace = deployment.metadata.namespace
            status, done = rollout_status_deployment(api, name, namespace)
            # only print statuses that have changed
            if status != deployment_statuses[i]:
                deployment_statuses[i] = status
                click.echo(f"{namespace}/{name}: {status}")
            if not done:
                flag = False
        if flag:
            break
        sleep(1)
