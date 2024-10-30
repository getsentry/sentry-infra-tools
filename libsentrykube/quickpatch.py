import os
from pathlib import Path
from typing import MutableMapping, Optional, Sequence, Union
import yaml
import jsonpatch
from jinja2 import Template

from libsentrykube.service import (
    get_service_path,
    get_service_value_overrides_file_path,
)
from jsonschema import validate, ValidationError


def find_patch_file(service: str, patch: str) -> Optional[Path]:
    """
    Finds the patch file for the given service and patch name
    """
    base_path = get_service_path(service)
    expected_path = Path(base_path) / "quickpatches"

    if expected_path.exists() and expected_path.is_dir():
        patch_file = expected_path / f"{patch}.yaml.j2"
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
        raise FileNotFoundError(f"Patch file {patch}.yaml.j2 not found")
    patch_data = load_pure_yaml(patch_file)
    if patch_data["schema"] is None:
        raise FileNotFoundError(f"jsonschema not found in patch file {patch}.yaml.j2")
    return patch_data["schema"].get("required", [])


def apply_patch(
    service: str,
    region: str,
    resource: str,
    patch: str,
    arguments: MutableMapping[str, Union[str, int, bool]],
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
        raise FileNotFoundError(f"Patch file {patch}.yaml.j2 not found")
    resource_value_file = get_service_value_overrides_file_path(service, region)
    if not os.path.isfile(resource_value_file):
        raise FileNotFoundError(
            f"Resource value file not found for service {service} in region {region}"
        )

    # Check that the resource is allowed to be patched
    resource_mappings = {}
    patch_data = load_pure_yaml(patch_file)
    for k, v in patch_data.get("mappings", {}).items():
        resource_mappings[k] = v
    if resource not in resource_mappings.keys():
        raise ValueError(f"Resource {resource} is not allowed to be patched")

    # Validate the arguments via jsonschema
    schema = patch_data.get("schema")
    if schema is None:
        raise ValueError(f"Schema not found in patch file {patch}.yaml.j2")
    try:
        validate(instance=arguments, schema=schema)
    except ValidationError as e:
        raise ValidationError(f"Invalid arguments: {e.message}") from e

    # Add resource_name to the arguments and render the patch template
    arguments["resource"] = resource_mappings[resource]
    with open(patch_file, "r") as file:
        patch_template = Template(file.read())
    patch_data = yaml.safe_load(
        patch_template.render(arguments).split("---")[1]  # only render the 2nd yaml doc
    )

    # Load the patch
    patches = []
    for patch in patch_data.get("patches", [{}]):
        # mypy type inference bug, so this hack is needed
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
        resource_data = yaml.safe_load(resource_file)
    resource_data = json_patch.apply(resource_data)
    with open(resource_value_file, "w") as file:
        yaml.safe_dump(resource_data, file)
