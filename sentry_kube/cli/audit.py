import functools

import click
import kubernetes.client
from yaml import safe_load_all

from libsentrykube.kube import render_templates
from libsentrykube.utils import kube_get_client

from .apply import allow_for_all_services

__all__ = ("audit",)


@click.command()
@click.pass_context
@allow_for_all_services
def audit(ctx, services):
    """
    Generates a list of objects that exist serverside but not locally.

    Needs an ephmeral bastion connection, otherwise it won't work.
    Use `connect` to get a short-lived sshuttle tunnel.
    """
    if not ctx.obj.quiet_mode:
        click.echo("Loading local files for services")
    docs = list(
        safe_load_all(
            "".join(
                render_templates(ctx.obj.customer_name, service, ctx.obj.cluster_name)
                for service in services
            )
        )
    )
    if "getsentry" in services:
        services.append("sentry")
    client = kube_get_client()
    apis = {
        "AppsV1": kubernetes.client.AppsV1Api(client),
        "CoreV1": kubernetes.client.CoreV1Api(client),
        "BatchV1": kubernetes.client.BatchV1Api(client),
        "AutoscalingV1": kubernetes.client.AutoscalingV1Api(client),
        "RbacAuthorizationV1": kubernetes.client.RbacAuthorizationV1Api(client),
        "PolicyV1": kubernetes.client.PolicyV1Api(client),
        "CustomObjects": kubernetes.client.CustomObjectsApi(client),
    }
    listing_funcs = {
        "Deployment": apis["AppsV1"].list_deployment_for_all_namespaces,
        "DaemonSet": apis["AppsV1"].list_daemon_set_for_all_namespaces,
        "PersistentVolume": apis["CoreV1"].list_persistent_volume,
        "PersistentVolumeClaim": apis[
            "CoreV1"
        ].list_persistent_volume_claim_for_all_namespaces,
        "CronJob": apis["BatchV1"].list_cron_job_for_all_namespaces,
        "Service": apis["CoreV1"].list_service_for_all_namespaces,
        "ConfigMap": apis["CoreV1"].list_config_map_for_all_namespaces,
        "ServiceAccount": apis["CoreV1"].list_service_account_for_all_namespaces,
        "HorizontalPodAutoscaler": apis[
            "AutoscalingV1"
        ].list_horizontal_pod_autoscaler_for_all_namespaces,
        "Role": apis["RbacAuthorizationV1"].list_role_for_all_namespaces,
        "ClusterRole": apis["RbacAuthorizationV1"].list_cluster_role,
        "RoleBinding": apis["RbacAuthorizationV1"].list_role_binding_for_all_namespaces,
        "ClusterRoleBinding": apis["RbacAuthorizationV1"].list_cluster_role_binding,
        "PodDisruptionBudget": apis["PolicyV1"].list_pod_disruption_budget_for_all_namespaces,
        "ManagedCertificate": functools.partial(
            apis["CustomObjects"].list_cluster_custom_object,
            group="networking.gke.io",
            version="v1",
            plural="managedcertificates",
        ),
        "BackendConfig": functools.partial(
            apis["CustomObjects"].list_cluster_custom_object,
            group="cloud.google.com",
            version="v1",
            plural="backendconfigs",
        ),
        "VerticalPodAutoscaler": functools.partial(
            apis["CustomObjects"].list_cluster_custom_object,
            group="autoscaling.k8s.io",
            version="v1",
            plural="verticalpodautoscalers",
        ),
        "ScaledObject": functools.partial(
            apis["CustomObjects"].list_cluster_custom_object,
            group="keda.sh",
            version="v1alpha1",
            plural="scaledobjects",
        ),
    }
    crds = [
        name
        for name, config in listing_funcs.items()
        if isinstance(config, functools.partial)
    ]
    # This might miss some kinds if there are no more such objects locally.
    # May need to check for a list of kinds unconditionally.
    return_code = 0
    for kind in sorted({doc["kind"] for doc in docs if doc is not None}):
        if kind not in listing_funcs:
            if not ctx.obj.quiet_mode:
                click.echo(f"Need to set up api mapping entry for {kind}")
            continue
        if not ctx.obj.quiet_mode:
            click.echo(f"getting {kind} local names")
        local_names = {
            (
                (
                    doc["metadata"].get("namespace", "default")
                    if kind != "PersistentVolume"
                    else None
                ),
                doc["metadata"]["name"],
            )
            for doc in docs
            if doc is not None and doc["kind"] == kind
        }
        if not ctx.obj.quiet_mode:
            click.echo(f"getting {kind} remote names")
        remote_names = set()
        selector = f"service in ({','.join(services)})"
        if kind == "HorizontalPodAutoscaler":
            selector += ",!scaledobject.keda.sh/name"
        items = listing_funcs[kind](label_selector=selector, limit=100)
        while True:
            if kind in crds:
                itemlist = items["items"]
            else:
                itemlist = items.items
            remote_names.update(
                (item["metadata"]["namespace"], item["metadata"]["name"])
                if kind in crds
                else (item.metadata.namespace, item.metadata.name)
                for item in itemlist
            )
            if kind in crds:
                cont = items["metadata"]["continue"]
            else:
                cont = items.metadata._continue
            if cont:
                items = listing_funcs[kind](
                    label_selector=selector, limit=100, _continue=cont
                )
            else:
                break
        if not ctx.obj.quiet_mode:
            click.echo(
                f"Objects of type {kind} present serverside which are not present locally:"
            )
        diff = remote_names - local_names
        if not diff:
            if not ctx.obj.quiet_mode:
                click.secho("\tNone", fg="green")
        else:
            return_code = 1
            if not ctx.obj.quiet_mode:
                for name in sorted(diff):
                    click.secho(f"\t({name[0]}) {name[1]}", fg="red", bold=True)
        if not ctx.obj.quiet_mode:
            click.echo(
                f"Objects of type {kind} present locally which are not present serverside:"
            )
        diff = local_names - remote_names
        if not diff:
            if not ctx.obj.quiet_mode:
                click.secho("\tNone", fg="green")
        else:
            return_code = 1
            if not ctx.obj.quiet_mode:
                for name in sorted(diff):
                    click.secho(f"\t({name[0]}) {name[1]}", fg="red", bold=True)
    if return_code:
        ctx.exit(return_code)
