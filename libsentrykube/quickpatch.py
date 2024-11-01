from pathlib import Path
import re
from typing import MutableMapping, Sequence
import click
import yaml
import jsonpatch

from libsentrykube.service import (
    get_managed_service_value_overrides,
    get_service_path,
    write_managed_values_overrides,
)
from jsonschema import validate, ValidationError


def find_patch_file(service: str, patch: str) -> Path:
    """
    Finds the patch file for the given service and patch name
    """
    try:
        base_path = get_service_path(service)
    except click.Abort:
        raise FileNotFoundError(f"Service {service} not found")
    expected_path = Path(base_path) / "quickpatches"

    if expected_path.exists() and expected_path.is_dir():
        patch_file = expected_path / f"{patch}.yaml"
        if patch_file.exists():
            return patch_file
    raise FileNotFoundError(f"Patch file {patch}.yaml not found")


def load_and_validate_yaml(file_path: Path, patch: str) -> dict:
    """
    Load the patch file and validate for required fields
    """
    try:
        with open(file_path, "r") as file:
            patch_data = yaml.safe_load(file)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid yaml in patch file {patch}.yaml: {e}") from e
    if "mappings" not in patch_data:
        raise ValueError(f"Resource mappings not found in patch file {patch}.yaml")
    if "schema" not in patch_data:
        raise ValueError(f"Schema not found in patch file {patch}.yaml")
    if "patches" not in patch_data:
        raise ValueError(f"Patches not found in patch file {patch}.yaml")

    # Validate the schema
    schema = patch_data.get("schema")
    if (
        "additionalProperties" not in schema
        or schema["additionalProperties"] is not False
    ):
        raise ValueError(
            f"Schema additionalProperties must be False in patch file {patch}.yaml"
        )

    # Find all variables enclosed in <> in the file content
    file_content = file_path.read_text()
    variables = set(re.findall(r"<\s*(\w+)\s*>", file_content))
    # Remove 'resource' as it's a special case handled separately
    variables.discard("resource")

    # Get required fields from schema
    required_fields = set(schema.get("required", []))

    # Check if all variables are in required fields
    missing_required = variables - required_fields
    if missing_required:
        raise ValueError(
            f"Variables {missing_required} found in patch file but not listed in schema.required"
        )

    return patch_data


def get_arguments(service: str, patch: str) -> Sequence[str]:
    """
    Returns the arguments required by the patch file
    """
    patch_file = find_patch_file(service, patch)
    patch_data = load_and_validate_yaml(patch_file, patch)
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

    # Check that the resource is allowed to be patched
    patch_data = load_and_validate_yaml(patch_file, patch)
    resource_mappings = patch_data.get("mappings", {})
    if resource not in resource_mappings.keys():
        raise ValueError(f"Resource {resource} is not allowed to be patched")

    # Validate the arguments via jsonschema
    schema = patch_data.get("schema")
    try:
        validate(instance=arguments, schema=schema)
    except ValidationError as e:
        raise ValidationError(f"Invalid arguments: {e.message}") from e

    # Replace <resource_name> with the actual resource name
    # Scan through the patch_data file and replace all matches of <resource_name>
    # with the corresponding value in the arguments dictionary
    variables = dict(arguments)
    variables["resource"] = resource_mappings[resource]
    patch_data_str = patch_file.read_text()
    for arg, arg_value in variables.items():
        pattern = r"<\s*" + re.escape(arg) + r"\s*>"
        patch_data_str = re.sub(pattern, str(arg_value), patch_data_str)

    # Load the patch
    patch_data = yaml.safe_load(patch_data_str)
    patches = patch_data.get("patches")
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
