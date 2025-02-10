import base64
import difflib
import json
import operator
from dataclasses import dataclass
from pprint import pformat
from typing import Any, List, Optional, Sequence, Tuple, cast, Generator

import click
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from kubernetes import client
from kubernetes.client.rest import ApiException
from yaml import dump_all, safe_dump, safe_load, safe_load_all

from libsentrykube.loader import load_macros
from libsentrykube.service import (
    build_materialized_path,
    get_service_data,
    get_service_path,
    get_service_template_files,
    get_service_values,
    get_service_value_overrides,
    get_tools_managed_service_value_overrides,
    get_hierarchical_value_overrides,
    assert_customer_is_defined_at_most_once,
    get_common_regional_override,
)
from libsentrykube.utils import (
    deep_merge_dict,
    kube_classes_for_data,
    kube_convert_kind_to_func,
    kube_get_client,
    pretty,
)


@dataclass
class KubeResource:
    name: str
    namespace: str
    kind: str
    func: str
    api: None
    new: bool
    local_doc: dict
    local_resource: None
    local_yaml: str
    remote_yaml: str | None
    patched_yaml: str | None


def _get_nested_key(doc: dict, key_path: str) -> Optional[str]:
    for k in key_path.split("."):
        try:
            doc = doc[k]
        except (KeyError, TypeError):
            return None
    if not isinstance(doc, str):
        raise ValueError(f"Value at `{key_path}' not a string but: `{doc}`")
    return cast(str, doc)


def _match_filters(doc: dict, filters: List[str]) -> bool:
    for _filter in filters:
        # split filter like
        # metadata.name=getsentry-worker-glob-production
        try:
            key_path, match_value = _filter.split("=", maxsplit=1)
        except ValueError:
            raise ValueError(
                f"Filter must be in form `path.to.key=value` or `path.to.key!=value` "
                f"not `{_filter}`"
            )

        # reversed operations because we jump out early
        filterop = operator.ne
        if key_path.endswith("!"):
            key_path = key_path[:-1]
            filterop = operator.eq

        if filterop(_get_nested_key(doc=doc, key_path=key_path), match_value):
            return False
    return True


def _sort_important_files_first(template_files: List[Any]) -> List[Any]:
    return sorted(
        template_files,
        key=lambda template: -1
        if "configmap" in template.name or "serviceaccount" in template.name
        else 0,
    )


def _get_path(obj, *pathparts, default=None):
    for pathpart in pathparts[:-1]:
        obj = obj.get(pathpart, {})
    return obj.get(pathparts[-1], default)


def _consolidate_variables(
    customer_name: str,
    service_name: str,
    cluster_name: str = "default",
    external: bool = False,
) -> dict:
    """
    We have multiple levels of overrides for our value files.
    1. The values defined inside the service directory as values.yaml.
    2. overridden by creating a hierarchical structure. Adding an intermediate directory
       in 'region_override' with a '_values.yaml' file allows to have a common config
       between a set of regions. This is useful if regions in a service are different, but
       subset of them are similar.
    3. overridden by a common '_values.yaml' within a region folder that has a shared config
       between all clusters in the region.
    4. overridden by the regional overrides in
       `service/region_override/region/cluster.yaml`
    5. overridden by the managed override file. This is like point 2. Conceptually
       there is no difference, practically this is managed by tools while region
       overrides are managed manually and they can contain comments. Tools cannot
       preserve comments.
    6. overridden by the cluster file. Which is likely going to be replaced by 2 and 3.

    TODO: write the minimum components of a yaml parser to remove step 3 and
          patch the regional override preserving comments.
    """

    # check that there is a single customer dir per service
    assert_customer_is_defined_at_most_once(service_name, customer_name, external)

    # Service defaults from _values
    # Always gets loaded even if no region_override exists
    service_values = get_service_values(service_name, external)

    # Service data overrides from services/SERVICE/region_overrides/
    service_value_overrides = get_service_value_overrides(
        service_name, customer_name, cluster_name, external
    )

    # Service data overrides from services/SERVICE/region_overrides/REGION/_values.yaml
    common_service_values = get_common_regional_override(
        service_name, customer_name, external
    )

    # If a cluster or region common config exists in region_overrides/REGION/
    if service_value_overrides or common_service_values:
        # Override service default config with region specific common config if exists
        deep_merge_dict(service_values, common_service_values)

        # Override with region specific cluster config if exists
        deep_merge_dict(service_values, service_value_overrides)

    # Otherwise merge service data from the within region_overrides/GROUP
    else:
        # Merged data from region_overrides/GROUP/_values.yaml,
        # region_overrides/GROUP/REGION/_values.yaml and
        # region_overrides/GROUP/REGION/{cluster_name}.yaml
        hierarchical_values = get_hierarchical_value_overrides(
            service_name, customer_name, cluster_name, external
        )

        deep_merge_dict(service_values, hierarchical_values)

    # Override files managed by tools
    managed_values = get_tools_managed_service_value_overrides(
        service_name, customer_name, cluster_name, external
    )
    deep_merge_dict(service_values, managed_values)

    # Service data overrides from clusters/
    customer_values, _ = get_service_data(
        customer_name,
        service_name,
        cluster_name,
    )
    deep_merge_dict(service_values, customer_values)

    return service_values


def render_service_values(
    customer_name: str,
    service_name: str,
    cluster_name: str = "default",
    external: bool = False,
) -> dict:
    return _consolidate_variables(customer_name, service_name, cluster_name, external)


def render_services(
    customer_name: str,
    cluster_name: str,
    services: Sequence[str],
    raw: bool = False,
    skip_kinds=None,
    filters=None,
) -> Generator[str, None, None]:
    for service_name in services:
        out = render_templates(
            customer_name,
            service_name,
            cluster_name,
            skip_kinds=skip_kinds,
            filters=filters,
        )
        yield out if raw else pretty(out)


def render_templates(
    customer_name,
    service_name,
    cluster_name="default",
    skip_kinds: Optional[Tuple] = None,
    filters: Optional[List[str]] = None,
) -> str:
    service_path = get_service_path(service_name)
    template_files = sorted(list(get_service_template_files(service_name)))

    # Sort files because configmaps need to be first
    template_files = _sort_important_files_first(template_files)

    _, render_data = get_service_data(
        customer_name,
        service_name,
        cluster_name,
    )

    render_data["values"] = _consolidate_variables(
        customer_name, service_name, cluster_name
    )

    extensions = ["jinja2.ext.do", "jinja2.ext.loopcontrols"]
    extensions.extend(load_macros())
    env = Environment(
        extensions=extensions,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
        loader=FileSystemLoader(str(service_path)),
    )

    # Add custom jinja filters here
    env.filters["b64encode"] = lambda x: base64.b64encode(x.encode("utf-8")).decode(
        "utf-8"
    )
    env.filters["yaml"] = safe_dump
    # debugging filter which prints a var to console
    env.filters["echo"] = lambda x: click.echo(pformat(x, indent=4))
    # helper to safely get nested path or default
    env.filters["get_path"] = _get_path

    rendered_templates = []
    for template in template_files:
        path = f"{template.relative_to(service_path)}"
        rendered = env.get_template(path).render(render_data)

        if skip_kinds is not None or filters is not None:
            documents: Sequence[Any] = list(safe_load_all(rendered))
            if skip_kinds:
                selected_documents = []
                for doc in documents:
                    if doc and doc["kind"] not in skip_kinds:
                        selected_documents.append(doc)
                documents = selected_documents

            if filters:
                selected_documents = []
                for doc in documents:
                    if doc and _match_filters(doc, filters):
                        selected_documents.append(doc)
                documents = selected_documents

            rendered = dump_all(documents)

        rendered_templates.append(rendered)

    return "\n---\n".join(rendered_templates)


def materialize(customer_name: str, service_name: str, cluster_name: str) -> bool:
    """
    Render a service and saves it to a file.

    Return False if the file on disk has not changed and we are not writing anything.
    """
    rendered_service = pretty(
        render_templates(
            customer_name,
            service_name,
            cluster_name,
        )
    )
    output_path = build_materialized_path(customer_name, cluster_name, service_name)
    try:
        existing_content = open(output_path).read()
    except Exception:
        existing_content = None

    if existing_content != rendered_service:
        with open(output_path, "w") as f:
            f.write(rendered_service)
        return True
    else:
        return False


def collect_kube_resources(
    customer_name,
    service_name,
    cluster_name="default",
    skip_kinds: Optional[Tuple] = ("Job",),
    kind_matches: Optional[Tuple] = None,
    name_matches: Optional[Tuple] = None,
    filters: Optional[List[str]] = None,
    extra_args: Optional[List] = None,
    extra_kwargs: Optional[List] = None,
):
    client = kube_get_client()

    # First, collect all of the local files and convert them into
    # real Kubernetes API objects. This allows some schema validation
    # to happen at this step and is a truer representation
    # if what is sent to the API.
    for doc in safe_load_all(
        render_templates(
            customer_name,
            service_name,
            cluster_name,
            skip_kinds=skip_kinds,
            filters=filters,
        )
    ):
        if not doc:
            continue
        api_cls, kind_cls = kube_classes_for_data(doc)

        # HACK(mattrobenolt): This is... doing bad things, but it's the
        # only method to convert a plain dictionary into an actual API
        # object. But this is definitely dipping into APIs that aren't
        # meant to be dipped into.
        resource = client._ApiClient__deserialize_model(doc, kind_cls)

        kind = resource.kind

        # if kind_matches isn't specified, filter items by skipping skip_kinds
        if kind_matches is None:
            if skip_kinds is not None and resource.kind in skip_kinds:
                continue
        else:
            # otherwise, only include kinds in kind_matches.
            if kind not in kind_matches:
                continue

        # and if name_matches is specified, we want to only include those names
        name = resource.metadata.name
        if name_matches is not None and name not in name_matches:
            continue

        # HACK(bmckerry): This is doing even more bad things, but it allows
        # sending extra args to a run-job command
        if kind == "Job":
            if extra_args:
                for arg in extra_args:
                    resource.spec.template.spec.containers[0].args.append("--" + arg)
            if extra_kwargs:
                for kwarg in extra_kwargs:
                    [k, v] = kwarg.split(":")
                    resource.spec.template.spec.containers[0].args.append("--" + k)
                    resource.spec.template.spec.containers[0].args.append(v)

        item = KubeResource(
            name=name,
            namespace=resource.metadata.namespace or "default",
            kind=kind,
            func=kube_convert_kind_to_func(kind),
            api=api_cls(client),
            new=False,
            local_doc=doc,
            local_resource=resource,
            local_yaml=safe_dump(client.sanitize_for_serialization(resource)),
            remote_yaml=None,
            patched_yaml=None,
        )

        yield item


def _load_resources(kube_resources):
    client = kube_get_client()

    # Load the current state of resources as known by the server.
    # If the resource 404's, we know this is a new resource.
    for item in kube_resources:
        try:
            if hasattr(item.api, f"read_namespaced_{item.func}"):
                try:
                    resource = getattr(item.api, f"read_namespaced_{item.func}")(
                        item.name, item.namespace
                    )
                except ValueError as exception:
                    if (
                        str(exception)
                        == "Invalid value for `conditions`, must not be `None`"
                    ):
                        click.echo(
                            "Some resources are still being created, "
                            "please wait a few seconds..."
                        )  # https://github.com/kubernetes-client/python/issues/1098
                        continue
                    else:
                        raise exception
            else:
                resource = getattr(item.api, f"read_{item.func}")(item.name)
        except ApiException as e:
            if e.status == 404:
                item.new = True
                yield item
                continue
            raise

        item.remote_yaml = safe_dump(client.sanitize_for_serialization(resource))

        # For existing resources, we need to determine what
        # the patched version looks like. We can do this by calling
        # PATCH with dryRun=All in the API.
        try:
            if hasattr(item.api, f"patch_namespaced_{item.func}"):
                resource = getattr(item.api, f"patch_namespaced_{item.func}")(
                    item.name, item.namespace, item.local_doc, dry_run="All"
                )
            else:
                resource = getattr(item.api, f"patch_{item.func}")(
                    item.name, item.local_doc, dry_run="All"
                )
        except ApiException as e:
            if e.status == 422:
                body = json.loads(e.body)
                click.secho("Failed to patch resource", fg="red")
                click.echo(json.dumps(body, indent=2))
                click.echo()
                raise click.ClickException(body["message"])
            raise

        item.patched_yaml = safe_dump(client.sanitize_for_serialization(resource))
        yield item


def __filter_metrics(data):
    # Removes duplicated resource items with the same metric name + selector
    # to avoid permanent drift on HorizontalPodAutoscaler.
    listed_metrics = set()
    metrics = []
    for metric in data["spec"]["metrics"]:
        if "external" not in metric:
            metrics.append(metric)
            continue

        # JSON representation of metric name + selector is a unique key.
        # Inspired by https://stackoverflow.com/a/22003440/2160657
        metric_hash = json.dumps(metric["external"]["metric"], sort_keys=True)
        if metric_hash not in listed_metrics:
            listed_metrics.add(metric_hash)
            metrics.append(metric)
    data["spec"]["metrics"] = metrics


def count_important_diffs(diff):
    ignored_changes = [
        "generation",
        "image",
        "configVersion",
    ]
    important_diffs = 0
    ignored_diffs = set()
    for item in diff:
        if item.startswith("+++") or item.startswith("---"):
            continue
        if item.startswith("+") or item.startswith("-"):
            stripped_item = item.lstrip("+ - ")
            item_name = stripped_item.split(":")[0]
            if item_name in ignored_changes:
                ignored_diffs.add(item_name)
            else:
                important_diffs += 1

    return important_diffs, ignored_diffs


def collect_diffs(kube_resources, markdown=False, important_diffs_only=False):
    # Render a unified diff of each item that has changed.
    for item in _load_resources(kube_resources):
        # For items that are new, we assume an empty live state,
        # just so we can view a diff that adds everything.
        if item.new:
            live = ""
            merged = item.local_yaml
        else:
            live = item.remote_yaml
            merged = item.patched_yaml

        live_data = safe_load(live)
        if live_data:
            live_data["metadata"].get("annotations", {}).pop(
                "kubectl.kubernetes.io/last-applied-configuration", None
            )
            live = safe_dump(live_data)
        else:
            live = ""

        merged_data = safe_load(merged)
        if merged_data:
            if (
                merged_data["kind"] == "HorizontalPodAutoscaler"
                and "spec" in merged_data
                and "metrics" in merged_data["spec"]
            ):
                __filter_metrics(merged_data)

            merged_data["metadata"].get("annotations", {}).pop(
                "kubectl.kubernetes.io/last-applied-configuration", None
            )
            merged = safe_dump(merged_data)
        else:
            merged = ""

        diff = [
            *difflib.unified_diff(
                live.splitlines(True),
                merged.splitlines(True),
                f"{item.namespace}/{item.name}-{item.kind}.yaml.live",
                f"{item.namespace}/{item.name}-{item.kind}.yaml.merged",
            )
        ]

        if len(diff) > 0:
            if important_diffs_only:
                num_important_diffs, ignored_diffs = count_important_diffs(diff)
                if num_important_diffs == 0:
                    if markdown:
                        click.echo(
                            f"`{item.namespace}/{item.name}-{item.kind}` has ignored "
                            f"diffs {ignored_diffs} that will be applied.\n"
                        )
                    else:
                        click.echo(
                            f"{item.namespace}/{item.name}-{item.kind} has ignored "
                            f"diffs {ignored_diffs} that will be applied."
                        )
                    continue
            out = ""

            if markdown:
                entity = None
                for line in diff:
                    if line.startswith("--- "):
                        entity = line.replace("--- ", "").replace(".yaml.live\n", "")
                    if line != "\n":
                        out += line
                if entity:
                    out = f"""<details>
<summary>{entity}</summary>

```diff
{out}
```
</details>
"""

            else:
                for line in diff:
                    if line.startswith("+"):
                        out += click.style(line, fg="green")
                    elif line.startswith("-"):
                        out += click.style(line, fg="red")
                    else:
                        out += line

            click.echo(out)
            yield item


def _sort_configs_first(items: List[KubeResource]) -> List[KubeResource]:
    return sorted(items, key=lambda item: -1 if item.kind == "ConfigMap" else 1)


def apply(items: List[KubeResource]):
    # Apply our changes to the server. New items need to be
    # created, while existing items need to be patched.

    # Sort items because configmaps need to be patched first
    sorted_items = _sort_configs_first(items)

    for item in sorted_items:
        if item.new:
            if hasattr(item.api, f"create_namespaced_{item.func}"):
                try:
                    getattr(item.api, f"create_namespaced_{item.func}")(
                        item.namespace, item.local_doc
                    )
                except ValueError as exception:
                    if (
                        str(exception)
                        == "Invalid value for `conditions`, must not be `None`"
                    ):
                        click.echo(
                            "Ignoring empty 'conditions' value for a new resource..."
                        )  # https://github.com/kubernetes-client/python/issues/1098
                    else:
                        raise exception
                click.echo(f'{item.kind} "{item.namespace}/{item.name}" created')

            else:
                getattr(item.api, f"create_{item.func}")(item.local_doc)
                click.echo(f'{item.kind} "{item.name}" created')
        else:
            if hasattr(item.api, f"patch_namespaced_{item.func}"):
                getattr(item.api, f"patch_namespaced_{item.func}")(
                    item.name, item.namespace, item.local_doc
                )
                click.echo(f'{item.kind} "{item.namespace}/{item.name}" updated')

            else:
                getattr(item.api, f"patch_{item.func}")(item.name, item.local_doc)
                click.echo(f'{item.kind} "{item.name}" updated')


def rollout_status_deployment(
    api: client.AppsV1Api, name: str, namespace: str
) -> Tuple[str, bool]:
    deployment = api.read_namespaced_deployment(name=name, namespace=namespace)
    if deployment.metadata.generation > deployment.status.observed_generation:
        # the desired generation is greater than the live (observed) generation,
        # so we're waiting on k8s to recognize changes.
        return (
            f"Waiting for deployment {repr(name)} spec update to be observed...",
            False,
        )

    # TimedOutReason is added in a deployment when its newest replica set
    # fails to show any progress within the given deadline (progressDeadlineSeconds).
    for condition in deployment.status.conditions:
        if condition.type == "Progressing":
            if condition.reason == "ProgressDeadlineExceeded":
                return f"deployment {repr(name)} exceeded its progress deadline", False

    spec_replicas = deployment.spec.replicas
    status_replicas = deployment.status.replicas or 0
    updated_replicas = deployment.status.updated_replicas or 0
    available_replicas = deployment.status.available_replicas or 0

    if updated_replicas < spec_replicas:
        return (
            f"Waiting for deployment {repr(name)} rollout to finish: "
            f"{updated_replicas} out of {spec_replicas} new "
            "replicas have been updated...",
            False,
        )
    if status_replicas > updated_replicas:
        return (
            f"Waiting for deployment {repr(name)} rollout to finish: "
            f"{status_replicas - updated_replicas} old replicas "
            "are pending termination...",
            False,
        )
    if available_replicas < updated_replicas:
        return (
            f"Waiting for deployment {repr(name)} rollout to finish: "
            f"{available_replicas} of {updated_replicas} "
            "updated replicas are available...",
            False,
        )
    return f"Deployment {repr(name)} successfully rolled out", True
