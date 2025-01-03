from pathlib import Path
import re
from typing import Any, List, MutableMapping, Sequence, TypedDict, Union
import click
import yaml

from libsentrykube.service import (
    get_tools_managed_service_value_overrides,
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

    # Check that properties in schema are also in required
    if "required" in schema:
        for required in schema["required"]:
            if required not in schema["properties"]:
                raise ValueError(
                    f"Required field {required} not found in schema.properties"
                )

    # Find all variables enclosed in <> in the file content
    file_content = file_path.read_text()
    variables = set(re.findall(r"(?<!\\)<\s*([\w-]+)\s*>", file_content))
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


class PatchOperation(TypedDict):
    path: str
    value: Union[str, int, float, bool]


def patch_json(
    patches: List[PatchOperation], resource: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """
    This function applies the patch to the resource json object
    in-place.
    It assumes resource has a hierarchy of nested dictionaries.
    Patches should be a list of PatchOperations with the following keys:
        path: The path to the value to be patched
        value: The value to be patched
    Currently, the only supported
    operations are replacement & creation of new objects.

    jsonpatch was not used because it does not support creation of new objects
    along the specified path. For example: if resource is an empty obj
    and the patch specifies path /a/b/c, then jsonpatch will error and not create
    the object { "a": { "b": { "c": "value" } } }. This function will create
    the object in this case.

    Semantics:
    - Paths are relative to the resource root and are separated by /
    - The / separates keys in the path. Each / denotes a new level of nesting
    and assumes the current level is a json object.
    - The final level is the key to be replaced.
    - If the key does not exist at the current level, this function creates and
    assigns the object to the key, then traverses down the object.
    - If the path exists, this function traverses the resource along the path
    and replaces the value at the final level.
    - The value of the final key in the path will be replaced with the value
    specified in the patch. So, to avoid overwriting existing values, the
    path should contain the full path to the key to be replaced.

    Example Patches:
    [{"path": "a/b/c", "value": 1}] + {"a": {"b": {"c": 0}}} -> {"a": {"b": {"c": 1}}} # Overwrite existing value
    [{"path": "a/b/c", "value": {"d": 1}}] + {"a": {"b": {"c": 0}}} -> {"a": {"b": {"c": {"d": 1}}}} # Overwrite existing value with a dict
    [{"path": "a/c", "value": 1}] + {"a": {"b": 0}} -> {"a": {"b": 0, "c": 1}} # Create new key-value
    [{"path": "a/b", "value": {"d": 1}}] + {"a": {"b": {"f": 2}}} -> {"a": {"b": {"d": 1}}} # Overwrite existing dict with a dict
    """
    for patch in patches:
        data = resource
        if not isinstance(data, dict):
            raise ValueError("resource must be a dict")
        path = patch.get("path", None)
        value = patch.get("value")
        if path is not None:
            stripped_path = path.strip("/")
            if stripped_path == "":
                raise ValueError("Path cannot be empty or just contain/")
            paths = stripped_path.split("/")
            for path in paths[:-1]:
                if path not in data:
                    data[path] = {}
                elif not isinstance(data[path], dict):
                    raise ValueError(
                        f"Cannot traverse path '{path}' as it points to a non-dictionary value"
                    )
                data = data[path]
            data[paths[-1]] = value
        else:
            raise ValueError("Path must be specified for all patches")
    return resource


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
    variables = dict(arguments)  # make a copy of the arguments
    variables["resource"] = resource_mappings[resource]
    patch_data_str = patch_file.read_text()
    for arg, arg_value in variables.items():
        pattern = r"(?<!\\)<\s*" + re.escape(arg) + r"\s*>"
        patch_data_str = re.sub(pattern, str(arg_value), patch_data_str)

    # Load the patch
    patch_data = yaml.safe_load(patch_data_str)
    patches = patch_data.get("patches", [])

    # Finally, apply the patch
    resource_data = get_tools_managed_service_value_overrides(
        service, region, cluster_name=cluster
    )
    if resource_data is None:  # If the .yaml file is empty
        resource_data = {}
    modified_data = patch_json(patches, resource_data)
    write_managed_values_overrides(modified_data, service, region, cluster_name=cluster)
