from pathlib import Path
import re
from typing import MutableMapping, Optional, Sequence
import yaml
import jsonpatch

from libsentrykube.service import (
    get_managed_service_value_overrides,
    get_service_path,
    write_managed_values_overrides,
)
from jsonschema import validate, ValidationError


def find_patch_file(service: str, patch: str) -> Optional[Path]:
    """
    Finds the patch file for the given service and patch name
    """
    base_path = get_service_path(service)
    expected_path = Path(base_path) / "quickpatches"

    if expected_path.exists() and expected_path.is_dir():
        patch_file = expected_path / f"{patch}.yaml"
        if patch_file.exists():
            return patch_file
    return None


def load_pure_yaml(file_path: Path) -> dict:
    """
    Load only the first section from the patch file that contains pure yaml and no jinja2
    """
    with open(file_path, "r") as file:
        content = file.read().split("---")[
            0
        ]  # Only use the first yaml doc which does not contain jinja2
        return yaml.safe_load(content)


def get_arguments(service: str, patch: str) -> Sequence[str]:
    """
    Returns the arguments required by the patch file
    """
    patch_file = find_patch_file(service, patch)
    if patch_file is None:
        raise FileNotFoundError(f"Patch file {patch}.yaml not found")
    patch_data = load_pure_yaml(patch_file)
    if patch_data.get("schema") is None:
        raise FileNotFoundError(f"jsonschema not found in patch file {patch}.yaml")
    return patch_data["schema"].get("required", [])


def apply_patch(
    service: str,
    region: str,
    resource: str,
    patch: str,
    arguments: MutableMapping[str, str],
    cluster: str = "default",
) -> None:
    """
    Finds the patch file, the resource and applies the patch
    Params:
        service: The service to be patched
        resource: The resource name to be patched
        arguments: Arguments to be passed to the patch file and applied
    """
    # Find files
    patch_file = find_patch_file(service, patch)
    if patch_file is None:
        raise FileNotFoundError(f"Patch file {patch}.yaml not found")

    # Check that the resource is allowed to be patched
    resource_mappings = {}
    pure_yaml_data = load_pure_yaml(patch_file)
    resource_mappings = pure_yaml_data.get("mappings", None)
    if resource_mappings is None:
        raise ValueError(f"Resource mappings not found in patch file {patch}.yaml")
    if resource not in resource_mappings.keys():
        raise ValueError(f"Resource {resource} is not allowed to be patched")

    # Validate the arguments via jsonschema
    schema = pure_yaml_data.get("schema")
    if schema is None:
        raise ValueError(f"Schema not found in patch file {patch}.yaml")
    try:
        validate(instance=arguments, schema=schema)
    except ValidationError as e:
        raise ValidationError(f"Invalid arguments: {e.message}") from e

    # Replace {{ resource_name }} with the actual resource name
    # Scan through the patch_data file and replace all matches of {{ resource_name }} with the corresponding value in the
    # arguments dictionary
    variables = dict(arguments)
    variables["resource"] = resource_mappings[resource]
    patch_data_str = patch_file.read_text()
    for arg, arg_value in variables.items():
        pattern = r"\{\{\s*" + re.escape(arg) + r"\s*\}\}"
        patch_data_str = re.sub(pattern, str(arg_value), patch_data_str)

    # Remove the --- separator & load the full yaml file
    patch_data_str = patch_data_str.replace("---", "")
    patch_data = yaml.safe_load(patch_data_str)

    # Load the patch
    patches = patch_data.get("patches", None)
    if patches is None:
        raise ValueError(f"Patches not found in patch file {patch}.yaml")
    json_patch = jsonpatch.JsonPatch(patches)

    # Finally, apply the patch
    resource_data = get_managed_service_value_overrides(
        service, region, cluster_name=cluster
    )
    if resource_data == {}:
        raise FileNotFoundError(
            f"Resource value file not found for service {service} in region {region}"
        )
    resource_data = json_patch.apply(resource_data)
    write_managed_values_overrides(resource_data, service, region, cluster_name=cluster)
