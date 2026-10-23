from typing import Any, Optional

import click
from kubernetes import client
from kubernetes.client.rest import ApiException

from libsentrykube.cluster import Cluster
from libsentrykube.utils import die, kube_get_client

__all__ = ("argocd_sync",)

APPLICATION = {
    "group": "argoproj.io",
    "version": "v1alpha1",
    "namespace": "argocd",
    "plural": "applications",
}


def argocd_app_name(service: str, cluster: Cluster, hub_context: str) -> str:
    context = cluster.services_data["context"]
    if context == hub_context:
        return service
    tail = context.rsplit("_", 1)[-1] if context.startswith("gke_") else None
    return (
        f"{cluster.services_data.get('cluster_name') or tail or cluster.name}-{service}"
    )


def autosync_enabled(automated: Optional[dict[str, Any]]) -> bool:
    return automated is not None and automated.get("enabled", True)


def sync_patch(
    automated: Optional[dict[str, Any]],
    autosync: Optional[bool],
    self_heal: Optional[bool],
) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "enabled": autosync_enabled(automated) if autosync is None else autosync
    }
    if self_heal is not None:
        patch["selfHeal"] = self_heal
    return {"spec": {"syncPolicy": {"automated": patch}}}


def describe(app: dict[str, Any]) -> str:
    automated = (app["spec"].get("syncPolicy") or {}).get("automated")
    autosync = "on" if autosync_enabled(automated) else "off"
    self_heal = "on" if (automated or {}).get("selfHeal") else "off"
    return f"{app['metadata']['name']}: autosync={autosync} self-heal={self_heal}"


def on_off(ctx, param, value: Optional[str]) -> Optional[bool]:
    return None if value is None else value == "on"


@click.command()
@click.argument("service")
@click.option(
    "--autosync",
    type=click.Choice(["on", "off"]),
    callback=on_off,
    help="Toggle automated sync.",
)
@click.option(
    "--self-heal",
    type=click.Choice(["on", "off"]),
    callback=on_off,
    help="Toggle self-heal.",
)
@click.pass_context
def argocd_sync(
    ctx, service: str, autosync: Optional[bool], self_heal: Optional[bool]
) -> None:
    """
    Show or toggle ArgoCD autosync and self-heal for a service.

    Targets the Application for SERVICE on the cluster selected with -c.
    With no toggles, prints the current state.
    """
    api = client.CustomObjectsApi(kube_get_client())
    name = argocd_app_name(service, ctx.obj.cluster, ctx.obj.context_name)

    try:
        app = api.get_namespaced_custom_object(name=name, **APPLICATION)
        if autosync is not None or self_heal is not None:
            automated = (app["spec"].get("syncPolicy") or {}).get("automated")
            app = api.patch_namespaced_custom_object(
                name=name,
                body=sync_patch(automated, autosync, self_heal),
                **APPLICATION,
            )
    except ApiException as e:
        if e.status == 404:
            die(f"ArgoCD Application '{name}' not found.")
        raise

    click.echo(describe(app))
