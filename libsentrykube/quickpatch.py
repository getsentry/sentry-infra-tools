import os
from typing import MutableMapping, Optional, Sequence
from ruamel.yaml import YAML
import jsonpatch
from jinja2 import Template

from libsentrykube.context import init_cluster_context
from libsentrykube.service import (
    get_service_path,
    get_service_value_overrides_file_path,
)
from libsentrykube.utils import set_workspace_root_start, workspace_root


def find_patch_files(service: str, patch: str) -> Optional[str]:
    """
    Finds the patch file for the given service and patch name
    """
    base_path = get_service_path(service)

    for root, dirs, files in os.walk(base_path):
        if "quickpatches" in root:
            for file in files:
                if file.endswith(f"{patch}.yaml.j2"):
                    print(file)
                    return os.path.join(root, file)
    return None


def get_arguments(service: str, patch: str) -> Sequence[str]:
    """
    Returns the arguments required by the patch file
    """
    yaml = YAML()
    patch_file = find_patch_files(service, patch)
    if patch_file is None:
        raise FileNotFoundError(f"Patch file {patch}.yaml.j2 not found")
    with open(patch_file, "r") as file:
        patch_data = yaml.load(file)
    return patch_data.get("args", [])


def apply_patch(
    service: str,
    region: str,
    resource: str,
    patch: str,
    arguments: MutableMapping[str, str | int | bool],
) -> None:
    """
    Finds the patch file, the resource and applies the patch
    Params:
        service: The service to be patched
        resource: The resource name to be patched
        arguments: Arguments to be passed to the patch file and applied
    """
    # Check that the arguments match the required arguments 1:1
    args = get_arguments(service, patch)
    for arg in args:
        if arg not in arguments:
            raise ValueError(f"Missing argument: {arg}")
    for arg in arguments.keys():
        if arg not in args:
            raise ValueError(f"Extra argument: {arg}")

    # Find files
    patch_file = find_patch_files(service, patch)
    if patch_file is None:
        raise FileNotFoundError(f"Patch file {patch}.yaml.j2 not found")
    resource_value_file = get_service_value_overrides_file_path(service, region)
    if not os.path.isfile(resource_value_file):
        raise FileNotFoundError(
            f"Resource value file not found for service {service} in region {region}"
        )

    # Load the patch file and render the template
    yaml = YAML()
    with open(patch_file, "r") as file:
        patch_template = Template(file.read())
    patch_data = yaml.load(
        patch_template.render(arguments)
    )  # will be incomplete, but we need to validate the resource_name first

    # Check that the resource_name resource is allowed to be patched
    resource_mappings = {}
    for mapping in patch_data.get("mappings", []):
        for k, v in mapping.items():
            resource_mappings[k] = v
    if resource not in resource_mappings.keys():
        raise ValueError(f"Resource {resource} not allowed to be patched")

    # Add resource_name to the arguments and re-render (since we needed to validate the resource first)
    arguments["resource"] = resource_mappings[resource]
    patch_data = yaml.load(patch_template.render(arguments))

    # Load the patch
    patches = []
    for patch in patch_data.get("patches", [{}]):
        patch_obj: dict[str, str] = patch  # type: ignore
        patches.append(
            {
                "op": patch_obj["op"],
                "path": patch_obj["path"],
                "value": patch_obj["value"],
            }
        )
    json_patch = jsonpatch.JsonPatch(patches)

    # Finally, apply the patch
    with open(resource_value_file, "r") as resource_file:
        resource_data = yaml.load(resource_file)
    resource_data = json_patch.apply(resource_data)
    with open(resource_value_file, "w") as file:
        yaml.dump(resource_data, file)


# Local testing only
if __name__ == "__main__":
    start_workspace_root = workspace_root().as_posix()
    set_workspace_root_start((workspace_root() / "libsentrykube/tests").as_posix())
    os.environ["SENTRY_KUBE_CONFIG_FILE"] = str(workspace_root() / "config.yaml")

    region = "saas"
    cluster = "customer"
    for service in ["service1"]:
        init_cluster_context(region, cluster)
        print(get_arguments("service1", "test-patch"))
        apply_patch(
            "service1",
            "us",
            "test-consumer-prod",
            "test-patch",
            {
                "replicas1": 2221,
                "replicas2": 2221,
            },
        )
