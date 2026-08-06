"""
The tools the sentry-kube agent is given.

Every tool is scoped to a single service directory, resolved through
`libsentrykube.service.get_service_path`, and refuses to touch anything outside
of it. Writes are further restricted to the region override directory of the
region the CLI is operating on.
"""

from pathlib import Path
from typing import Any, Optional

import click
import yaml
from langchain_core.tools import BaseTool, tool

from libsentrykube.kube import render_templates
from libsentrykube.service import (
    get_service_names,
    get_service_path,
    get_service_value_override_path,
)

# What the agent is allowed to read out of a service directory.
READABLE_SUFFIXES = (".yaml", ".yml", ".j2", ".jinja", ".jinja2")

# What the agent is allowed to write into a region override directory.
WRITABLE_SUFFIXES = (".yaml", ".yml")


class ToolError(Exception):
    """
    A tool was asked to do something it will not do.

    The message is handed back to the model so it can correct itself, so it
    should say what went wrong and what the valid options are.
    """


def _resolve_service_path(service: str) -> Path:
    """
    Returns the directory of `service`, or raises if there is no such service.
    """
    try:
        return get_service_path(service).resolve()
    except click.Abort:
        raise ToolError(
            f"There is no service named '{service}'. "
            f"Available services: {', '.join(sorted(get_service_names()))}"
        )


def _resolve_within(root: Path, relative_path: str) -> Path:
    """
    Resolves `relative_path` against `root` and asserts it stayed inside it.

    This is what stops `../../` and absolute paths from escaping the service
    directory.
    """
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolError(
            f"'{relative_path}' is outside of {root.name}, which is not allowed."
        )
    return candidate


def _resource_names(rendered: str) -> list[str]:
    """
    Pulls every `metadata.name` out of a rendered multi document manifest.
    """
    names = []
    for doc in yaml.safe_load_all(rendered):
        if not doc:
            continue
        name = (doc.get("metadata") or {}).get("name")
        if name:
            names.append(f"{doc.get('kind', 'Unknown')}/{name}")
    return names


def build_tools(region: str, cluster: Optional[str] = None) -> list[BaseTool]:
    """
    Builds the tools for one agent run.

    `region` and `cluster` are bound here rather than being tool arguments: the
    operator already chose them on the command line, and the agent must not be
    able to reach into a different region.
    """
    cluster_name = cluster or "default"

    @tool
    def list_service_files(service: str) -> str:
        """List the template and yaml files that make up a service.

        Use this to discover what a service is built from before reading
        anything. Returns one path per line, relative to the service directory.

        Args:
            service: Name of the service, for example 'snuba'.
        """
        root = _resolve_service_path(service)

        paths = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file() and path.suffix in READABLE_SUFFIXES
        )
        if not paths:
            return f"Service '{service}' has no template or yaml files."
        return "\n".join(paths)

    @tool
    def read_service_file(service: str, path: str) -> str:
        """Read one template or yaml file belonging to a service.

        Only files inside the service directory can be read. Use
        list_service_files first to find out which paths exist.

        Args:
            service: Name of the service, for example 'snuba'.
            path: Path of the file relative to the service directory.
        """
        root = _resolve_service_path(service)
        target = _resolve_within(root, path)

        if target.suffix not in READABLE_SUFFIXES:
            raise ToolError(
                f"'{path}' is not a readable file type. "
                f"Readable types: {', '.join(READABLE_SUFFIXES)}"
            )
        if not target.is_file():
            raise ToolError(f"'{path}' does not exist in service '{service}'.")

        return target.read_text()

    @tool
    def list_resources(service: str) -> str:
        """List the Kubernetes resources a service renders to.

        Renders the service for the region and cluster currently being operated
        on and returns one 'Kind/name' per line. Use this to pick a resource
        before rendering it in full.

        Args:
            service: Name of the service, for example 'snuba'.
        """
        # Fail early with a useful message rather than deep inside jinja.
        _resolve_service_path(service)

        try:
            rendered = render_templates(region, service, cluster_name)
        except Exception as e:
            raise ToolError(f"Could not render service '{service}': {e}")

        names = _resource_names(rendered)
        if not names:
            return f"Service '{service}' renders no resources."
        return "\n".join(names)

    @tool
    def render_resource(service: str, resource_name: str) -> str:
        """Render a single Kubernetes resource of a service to yaml.

        Rendering a whole service produces far too much output, so this renders
        only the named resource. Get the name from list_resources first, and
        pass just the name, without the 'Kind/' prefix.

        Args:
            service: Name of the service, for example 'snuba'.
            resource_name: The metadata.name of the resource to render.
        """
        _resolve_service_path(service)

        try:
            rendered = render_templates(
                region,
                service,
                cluster_name,
                filters=[f"metadata.name={resource_name}"],
            )
        except Exception as e:
            raise ToolError(f"Could not render service '{service}': {e}")

        # Filtered out templates still render as empty documents.
        documents = [doc for doc in yaml.safe_load_all(rendered) if doc]
        if not documents:
            raise ToolError(
                f"Service '{service}' has no resource named '{resource_name}'. "
                f"Use list_resources to see what is available."
            )

        return yaml.dump_all(documents, default_flow_style=False)

    @tool
    def read_region_override(service: str, file_name: str) -> str:
        """Read one of the region override files of a service.

        Reads from the override directory of the region currently being
        operated on. Read the file before updating it so the update keeps the
        values that should not change.

        Args:
            service: Name of the service, for example 'snuba'.
            file_name: Name of the override file, for example 'default.yaml'.
        """
        target = _region_override_file(service, file_name)

        if not target.is_file():
            existing = _list_region_overrides(service)
            raise ToolError(
                f"'{file_name}' does not exist for service '{service}' in region "
                f"'{region}'. Existing override files: {existing}"
            )

        return target.read_text()

    @tool
    def update_region_override(service: str, file_name: str, content: str) -> str:
        """Overwrite one of the region override files of a service.

        Writes to the override directory of the region currently being operated
        on, and nowhere else. `content` replaces the whole file, so read the
        file first and send it back with your changes applied. Comments in the
        existing file are not preserved unless you include them in `content`.

        Args:
            service: Name of the service, for example 'snuba'.
            file_name: Name of the override file, for example 'default.yaml'.
            content: The full new yaml content of the file.
        """
        target = _region_override_file(service, file_name)

        try:
            yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise ToolError(f"The content is not valid yaml, nothing was written: {e}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

        return f"Wrote {target}."

    def _region_override_file(service: str, file_name: str) -> Path:
        """
        Resolves an override file name inside the region override directory.
        """
        # Raises if the service does not exist.
        _resolve_service_path(service)

        override_root = get_service_value_override_path(service, region).resolve()
        target = _resolve_within(override_root, file_name)

        if target.parent != override_root:
            raise ToolError(
                f"'{file_name}' must be a file directly in the region override "
                f"directory, not in a subdirectory."
            )
        if target.suffix not in WRITABLE_SUFFIXES:
            raise ToolError(
                f"'{file_name}' must be a yaml file ({', '.join(WRITABLE_SUFFIXES)})."
            )
        return target

    def _list_region_overrides(service: str) -> str:
        override_root = get_service_value_override_path(service, region)
        if not override_root.is_dir():
            return "none"
        names = sorted(path.name for path in override_root.iterdir() if path.is_file())
        return ", ".join(names) if names else "none"

    tools: list[Any] = [
        list_service_files,
        read_service_file,
        list_resources,
        render_resource,
        read_region_override,
        update_region_override,
    ]
    return tools
