import re
from dataclasses import dataclass
from typing import Any, List, Set, Union, Dict, Optional
import click
import os
import logging
import kubernetes.client
from kubernetes.client.exceptions import ApiException
from yaml import safe_load_all

from libsentrykube.kube import render_templates
from libsentrykube.utils import kube_get_client

from .apply import allow_for_all_services

__all__ = ("audit",)

logging.basicConfig(level=os.getenv("SENTRY_KUBE_LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)


@dataclass
class AuditRecord:
    api: str
    kind: str
    namespace: str
    name: str
    service: str
    local: bool
    remote: bool


@dataclass
class RegisteredResource:
    plural: str
    group: str
    versions: List[str]
    scope: str


CAMEL_TO_SNAKE_REGEX = re.compile(r"(?<!^)(?=[A-Z])")

# You either specify the list of API's to use
# or to skip. I chose to go with the latter.
# List of skipped API's is almost as long as list of used ones now,
# but during development it was easier to go from 'show me all' and
# filter out what we don't need.
APIS_TO_SKIP = [
    "ApiregistrationV1Api",
    "ApiextensionsV1Api",
    "AuthenticationV1Api",
    "AuthenticationV1beta1Api",
    "AuthenticationV1alpha1Api",
    "AuthorizationV1Api",
    "CoordinationV1Api",
    "CustomObjectsApi",  # special case, handled with separate set of functions
    "DiscoveryV1Api",
    "EventsV1Api",
    "FlowcontrolApiserverV1Api",
    "FlowcontrolApiserverV1beta3Api",
    "InternalApiserverV1alpha1Api",
    "NetworkingV1Api",
    "NetworkingV1alpha1Api",
    "NodeV1Api",
    "ResourceV1alpha2Api",
    "SchedulingV1Api",
    # Storage*Api - its mostly drivers, so maintained by GCP,
    # and VolumeAttachment, but its Deployment counterpart
    # is covered by Core::PersistentVolume
    "StorageV1Api",
    "StorageV1alpha1Api",
    "StoragemigrationV1alpha1Api",
]

# Mostly CoreAPI resources that either derrived from other resources
# (like ReplicaSet and Pod), represent resources that controlled by tf
# like Node, or out of our control (like ReplicationController and ControllerRevision)
KINDS_TO_SKIP = [
    "ControllerRevision",
    "ComponentStatus",
    "Event",
    "Endpoints",
    "LimitRange",
    "Pod",
    "PodTemplate",
    "ReplicaSet",
    "ResourceQuota",
    "ReplicationController",
    "Node",
]

Metadata = Union[kubernetes.client.V1ObjectMeta, Dict[str, str]]


def camel_to_snake(name: str) -> str:
    """
    Converts CamelCase to snake_case.
    Used to convert Kind CamelCase names to snake_case names to get
    list function from api dynamically.
    """
    return CAMEL_TO_SNAKE_REGEX.sub("_", name).lower()


def to_api_name(name: str) -> str:
    if name == "v1":
        return "CoreV1Api"
    (dns, version) = name.split("/")
    dns = dns.replace(".k8s.io", "")
    dns = "".join([word.capitalize() for word in dns.split(".")])
    return f"{dns}{version.capitalize()}Api"


def is_api_class(name: str) -> bool:
    """Kubernetes.client module exposes all kind of classes.
    Api classes mostly follow pattern of <Group>(<Version>)?Api,
    but not all of them related to resource creation (eg VersionAPI),
    so additionally we check for get_api_resources
    method presence (returns list of associated kinds)
    """
    if not name.endswith("Api"):
        return False
    clazz = getattr(kubernetes.client, name)
    if not hasattr(clazz, "get_api_resources"):
        return False
    return name not in APIS_TO_SKIP


def get_available_api():
    return [name for name in dir(kubernetes.client) if is_api_class(name)]


def get_api_resources(api: kubernetes.client.ApiClient, **kwargs) -> Set[str]:
    """
    Returns list of associated kinds for a given api.
    """
    try:
        available_resources_response = api.get_api_resources(**kwargs)
    except ApiException as e:
        # kubernetes clients returns 404 through raising ApiException.
        # This can happen if api exists (newer client version) but no resources are not
        # present (older cluster version) or vice versa.
        if e.status == 404:
            return set()
        raise e
    return set(
        [
            resource.kind
            for resource in available_resources_response.resources
            if resource.kind not in KINDS_TO_SKIP
        ]
    )


def get_all_namespaces(client: kubernetes.client.ApiClient) -> List[str]:
    core = kubernetes.client.CoreV1Api(client)
    namespaces_response = core.list_namespace()
    return [namespace.metadata.name for namespace in namespaces_response.items]


def get_resource_list(api: Any, kind: str) -> List[Any]:
    """
    Returns list of resources for a given kind and api.
    Works for most API's, CustomObjectsApi is an exception.
    """
    kind_snake_name = camel_to_snake(kind)

    # Kind can be Cluster or Namespace scoped.
    # For example, webhooks are cluster scoped
    # and API doesn't have '*_for_all_namespaces' functions.
    list_function_name = f"list_{kind_snake_name}"
    list_for_all_namespaces_function_name = f"list_{kind_snake_name}_for_all_namespaces"

    if hasattr(api, list_for_all_namespaces_function_name):
        list_function = getattr(api, list_for_all_namespaces_function_name)
    elif hasattr(api, list_function_name):
        list_function = getattr(api, list_function_name)
    else:
        # This mostly happens with CoreAPI, that has resources like
        # Eviction, Binding, PodAttachOptions and others internal resources.
        # change to debug log
        logger.debug(f"No list function found. {api.__class__.__name__}:{kind}")
        return []

    try:
        resources_response = list_function()
        return resources_response.items
    except ApiException as e:
        raise e


def get_registered_crds(
    client: kubernetes.client.ApiClient,
) -> List[RegisteredResource]:
    """
    Returns list of registered Custom Resource Definitions.
    Custom Resource Definitions versioning scheme is slightly different from regular API's
    There is only one version of regular resource per API,
    but CRD can have multiple versions.
    """
    ext = kubernetes.client.ApiextensionsV1Api(client)
    crds_response = ext.list_custom_resource_definition()
    return [
        RegisteredResource(
            plural=crd.spec.names.plural,
            group=crd.spec.group,
            scope=crd.spec.scope,
            versions=[version.name for version in crd.spec.versions],
        )
        for crd in crds_response.items
    ]


def get_crds_resource_list(
    api: kubernetes.client.CustomObjectsApi,
    resource: RegisteredResource,
    namespaces: List[str],
) -> List[Any]:
    """
    Returns list of resources for a given Custom Resource Definition.
    CustomResource can have multiple versions, so we need to iterate over them.
    Also, there no '*_for_all_namespaces' functions for CRD,
    we need to iterate over namespaces manually.
    """
    resources = []
    for version in resource.versions:
        try:
            if resource.scope == "Cluster":
                response = api.list_cluster_custom_object(
                    group=resource.group, version=version, plural=resource.plural
                )
                resources.extend(response["items"])
            else:
                for namespace in namespaces:
                    response = api.list_namespaced_custom_object(
                        namespace=namespace,
                        group=resource.group,
                        version=version,
                        plural=resource.plural,
                    )
                    resources.extend(response["items"])
        except ApiException as e:
            if e.status == 404:
                logger.debug(f"CRD {resource.plural} {version} not found")
                continue
            raise e
    return resources


def get_general_resource_audit_records(
    client: kubernetes.client.ApiClient,
) -> List[AuditRecord]:
    api_classes = get_available_api()
    result = []
    for clazz in api_classes:
        api = getattr(kubernetes.client, clazz)(client)
        available_kinds = get_api_resources(api)
        for kind in available_kinds:
            items = get_resource_list(api, kind)
            for item in items:
                labels = getattr(item.metadata, "labels", {}) or {}
                result.append(
                    AuditRecord(
                        api=clazz,
                        kind=item.kind or kind,
                        namespace=item.metadata.namespace,
                        name=item.metadata.name,
                        service=labels.get("service", ""),
                        local=False,
                        remote=True,
                    )
                )
    return result


def get_crds_audit_records(
    client: kubernetes.client.ApiClient, namespaces: Optional[List[str]] = None
) -> List[AuditRecord]:
    """
    Issue with CRD's is that in many cases they maintained by operator,
    and many of them are low-level, ie you have some RabbitCluster resource to define,
    and operator creates a bunch or resources to manage.


    """
    registered_crds = get_registered_crds(client)
    api = kubernetes.client.CustomObjectsApi(client)
    namespaces = namespaces or get_all_namespaces(client)
    result = []
    for resource in registered_crds:
        items = get_crds_resource_list(api, resource, namespaces)
        for item in items:
            result.append(
                AuditRecord(
                    api="CustomObjects",
                    kind=item["kind"],
                    namespace=item["metadata"].get("namespace", None),
                    name=item["metadata"].get("name", None),
                    service=item["metadata"].get("labels", {}).get("service", ""),
                    local=False,
                    remote=True,
                )
            )
    return result


def get_cluster_resource_audit_records(
    services: List[str], namespaces: Optional[List[str]] = None
):
    client = kube_get_client()
    audit_records: List[AuditRecord] = get_general_resource_audit_records(client)
    audit_records.extend(get_crds_audit_records(client, namespaces))
    filtered_audit_records = [
        record for record in audit_records if record.service in services
    ]
    return filtered_audit_records


def audit_tenant(ctx, services: List[str]):
    # NOTE(dfedorov): One day we might have services in service namespace,
    # but for now its either get all namespaces or limit it to some
    # hardcoded ones. Going over all namespaces take some time due to crds.
    # Which unfotunatelly may actually be scattered across them
    # due to operator deployments.
    # Anywho, gonna roll with this for now and see if change needed.
    namespaces = ["default", "sentry-system"]
    audit_records = get_cluster_resource_audit_records(services, namespaces)
    return audit_records


def print_audit_records_table(records: List[AuditRecord]) -> None:
    """
    Print audit records as a formatted table.

    Args:
        records: List of AuditRecord objects to display
    """
    if not records:
        click.echo("No audit records to display.")
        return

    # Define column headers and their widths
    headers = ["API", "Kind", "Namespace", "Name", "Service", "Local", "Remote"]

    # Max width for Name field
    MAX_NAME_WIDTH = 60

    # Calculate column widths based on content
    col_widths = {
        "API": max(len("API"), max(len(r.api) for r in records)),
        "Kind": max(len("Kind"), max(len(r.kind) for r in records)),
        "Namespace": max(
            len("Namespace"), max(len(r.namespace or "") for r in records)
        ),
        "Name": min(
            MAX_NAME_WIDTH, max(len("Name"), max(len(r.name) for r in records))
        ),
        "Service": max(len("Service"), max(len(r.service) for r in records)),
        "Local": len("Local"),
        "Remote": len("Remote"),
    }

    # Create format string for rows
    row_format = (
        f"{{:<{col_widths['API']}}}  "
        f"{{:<{col_widths['Kind']}}}  "
        f"{{:<{col_widths['Namespace']}}}  "
        f"{{:<{col_widths['Name']}}}  "
        f"{{:<{col_widths['Service']}}}  "
        f"{{:<{col_widths['Local']}}}  "
        f"{{:<{col_widths['Remote']}}}"
    )

    # Print header
    click.echo(row_format.format(*headers))

    # Print separator line
    separator = (
        "-" * col_widths["API"]
        + "  "
        + "-" * col_widths["Kind"]
        + "  "
        + "-" * col_widths["Namespace"]
        + "  "
        + "-" * col_widths["Name"]
        + "  "
        + "-" * col_widths["Service"]
        + "  "
        + "-" * col_widths["Local"]
        + "  "
        + "-" * col_widths["Remote"]
    )
    click.echo(separator)

    # Print records
    for record in records:
        # Truncate name if it's too long
        name_text = record.name
        if len(name_text) > MAX_NAME_WIDTH:
            name_text = name_text[: MAX_NAME_WIDTH - 3] + "..."

        # Color the check/x marks
        local_mark = (
            click.style("✓", fg="green") if record.local else click.style("✗", fg="red")
        )
        remote_mark = (
            click.style("✓", fg="green")
            if record.remote
            else click.style("✗", fg="red")
        )

        # Build the row with proper spacing before applying colors to name
        # We need to pad the name field first, then apply color
        name_padded = name_text.ljust(col_widths["Name"])

        # Color the name based on local/remote status
        if record.local and record.remote:
            name_colored = click.style(name_padded, fg="green")
        elif record.local or record.remote:
            name_colored = click.style(name_padded, fg="yellow")
        else:
            name_colored = name_padded

        # For other fields, format them with proper width
        api_field = record.api.ljust(col_widths["API"])
        kind_field = record.kind.ljust(col_widths["Kind"])
        namespace_field = (record.namespace or "").ljust(col_widths["Namespace"])
        service_field = record.service.ljust(col_widths["Service"])

        # Print the row (name is already padded and colored)
        click.echo(
            f"{api_field}  "
            f"{kind_field}  "
            f"{namespace_field}  "
            f"{name_colored}  "
            f"{service_field}  "
            f"{local_mark.ljust(col_widths['Local'])}  "
            f"{remote_mark}"
        )


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

    local_resources = list(
        safe_load_all(
            "".join(
                render_templates(ctx.obj.customer_name, service, ctx.obj.cluster_name)
                for service in services
            )
        )
    )
    if "getsentry" in services:
        services.append("sentry")
    audit_records = audit_tenant(ctx, services)
    audit_records_dict = {
        # kind-namespace-name - gives a unique key for each record
        f"{record.kind}-{record.namespace}-{record.name}": record
        for record in audit_records
    }
    for resource in local_resources:
        if not resource:
            # Due to --- usage in some documents and conditional rendering,
            # of some jinja blocks, we may have empty yaml documents.
            continue
        metadata = resource.get("metadata", {}) or {}
        namespace = metadata.get("namespace", "default")
        name = metadata["name"]
        key = f"{resource['kind']}-{namespace}-{name}"
        if key in audit_records_dict:
            audit_records_dict[key].local = True
        else:
            audit_records_dict[key] = AuditRecord(
                api=to_api_name(resource["apiVersion"]),
                kind=resource["kind"],
                namespace=namespace,
                name=name,
                service=metadata.get("labels", {}).get("service", ""),
                local=True,
                remote=False,
            )
    audit_records = sorted(list(audit_records_dict.values()), key=lambda x: x.api)
    print_audit_records_table(audit_records)
