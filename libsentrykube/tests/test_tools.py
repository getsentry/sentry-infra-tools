from unittest.mock import patch

import pytest
import yaml

from libsentrykube.context import init_cluster_context
from libsentrykube.service import get_service_path, get_service_value_override_path
from libsentrykube.tools import ToolError, build_tools

REGION = "saas"
CLUSTER = "customer"


@pytest.fixture
def tools():
    init_cluster_context(REGION, CLUSTER)
    return {tool.name: tool for tool in build_tools(REGION, CLUSTER)}


def _invoke(tool, **kwargs) -> str:
    """Calls a tool the way the agent does, through its schema."""
    return tool.invoke(kwargs)


def test_build_tools_exposes_every_tool(tools) -> None:
    assert set(tools) == {
        "list_service_files",
        "read_service_file",
        "list_resources",
        "render_resource",
        "read_region_override",
        "update_region_override",
    }


def test_list_service_files(tools) -> None:
    output = _invoke(tools["list_service_files"], service="service1")

    files = output.splitlines()
    assert "deployment.yaml" in files
    assert "_values.yaml" in files
    # Nested override files are reachable too.
    assert "region_overrides/us/customer.yaml" in files


def test_list_service_files_unknown_service(tools) -> None:
    with pytest.raises(ToolError, match="no service named 'nope'"):
        _invoke(tools["list_service_files"], service="nope")


def test_read_service_file(tools) -> None:
    output = _invoke(
        tools["read_service_file"], service="service1", path="_values.yaml"
    )

    assert output == (get_service_path("service1") / "_values.yaml").read_text()


def test_read_service_file_rejects_traversal(tools) -> None:
    with pytest.raises(ToolError, match="outside of"):
        _invoke(
            tools["read_service_file"],
            service="service1",
            path="../service2/deployment.yaml",
        )


def test_read_service_file_rejects_absolute_path(tools) -> None:
    with pytest.raises(ToolError, match="outside of"):
        _invoke(tools["read_service_file"], service="service1", path="/etc/passwd")


def test_read_service_file_rejects_other_file_types(tools, tmp_path) -> None:
    secret = get_service_path("service1") / "secret.txt"
    secret.write_text("nope")
    try:
        with pytest.raises(ToolError, match="not a readable file type"):
            _invoke(tools["read_service_file"], service="service1", path="secret.txt")
    finally:
        secret.unlink()


def test_read_service_file_missing(tools) -> None:
    with pytest.raises(ToolError, match="does not exist"):
        _invoke(tools["read_service_file"], service="service1", path="nope.yaml")


# The fixture services render to nothing, so rendering is exercised against a
# stubbed render_templates. What matters here is that the tools bind the right
# region/cluster and post-process the manifest correctly.
RENDERED = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-deployment
---
apiVersion: v1
kind: Service
metadata:
  name: my-service
"""


def test_list_resources(tools) -> None:
    with patch("libsentrykube.tools.render_templates") as mock_render:
        mock_render.return_value = RENDERED

        output = _invoke(tools["list_resources"], service="service1")

    assert output.splitlines() == ["Deployment/my-deployment", "Service/my-service"]
    # The region and cluster are the ones the tools were built with, not
    # anything the model supplied.
    mock_render.assert_called_once_with(REGION, "service1", CLUSTER)


def test_list_resources_unknown_service(tools) -> None:
    with pytest.raises(ToolError, match="no service named 'nope'"):
        _invoke(tools["list_resources"], service="nope")


def test_list_resources_render_failure(tools) -> None:
    with patch("libsentrykube.tools.render_templates") as mock_render:
        mock_render.side_effect = ValueError("undefined variable")

        with pytest.raises(ToolError, match="Could not render"):
            _invoke(tools["list_resources"], service="service1")


def test_render_resource(tools) -> None:
    with patch("libsentrykube.tools.render_templates") as mock_render:
        # render_templates filters, so it only returns the requested document.
        mock_render.return_value = RENDERED.split("---")[0]

        output = _invoke(
            tools["render_resource"], service="service1", resource_name="my-deployment"
        )

    document = yaml.safe_load(output)
    assert document["kind"] == "Deployment"
    assert document["metadata"]["name"] == "my-deployment"
    mock_render.assert_called_once_with(
        REGION, "service1", CLUSTER, filters=["metadata.name=my-deployment"]
    )


def test_render_resource_unknown_name(tools) -> None:
    with patch("libsentrykube.tools.render_templates") as mock_render:
        # Everything was filtered out, leaving empty documents behind.
        mock_render.return_value = "\n---\n"

        with pytest.raises(ToolError, match="no resource named 'nope'"):
            _invoke(tools["render_resource"], service="service1", resource_name="nope")


def test_read_region_override(tools) -> None:
    output = _invoke(
        tools["read_region_override"], service="service1", file_name="customer.yaml"
    )

    expected = get_service_value_override_path("service1", REGION) / "customer.yaml"
    assert output == expected.read_text()


def test_read_region_override_missing_lists_what_exists(tools) -> None:
    with pytest.raises(ToolError, match="customer.yaml"):
        _invoke(
            tools["read_region_override"], service="service1", file_name="nope.yaml"
        )


def test_update_region_override(tools) -> None:
    target = get_service_value_override_path("service1", REGION) / "agent_test.yaml"
    try:
        result = _invoke(
            tools["update_region_override"],
            service="service1",
            file_name="agent_test.yaml",
            content="key: value\n",
        )

        assert "agent_test.yaml" in result
        assert target.read_text() == "key: value\n"
    finally:
        target.unlink(missing_ok=True)


def test_update_region_override_rejects_invalid_yaml(tools) -> None:
    target = get_service_value_override_path("service1", REGION) / "agent_test.yaml"

    with pytest.raises(ToolError, match="not valid yaml"):
        _invoke(
            tools["update_region_override"],
            service="service1",
            file_name="agent_test.yaml",
            content="key: [unclosed\n",
        )

    assert not target.exists()


def test_update_region_override_rejects_traversal(tools) -> None:
    with pytest.raises(ToolError, match="outside of"):
        _invoke(
            tools["update_region_override"],
            service="service1",
            file_name="../../_values.yaml",
            content="key: value\n",
        )


def test_update_region_override_rejects_subdirectory(tools) -> None:
    with pytest.raises(ToolError, match="not in a subdirectory"):
        _invoke(
            tools["update_region_override"],
            service="service1",
            file_name="nested/file.yaml",
            content="key: value\n",
        )


def test_update_region_override_rejects_non_yaml(tools) -> None:
    with pytest.raises(ToolError, match="must be a yaml file"):
        _invoke(
            tools["update_region_override"],
            service="service1",
            file_name="notes.txt",
            content="key: value\n",
        )


def test_tools_are_scoped_to_the_bound_region() -> None:
    """A tool built for one region must not write into another one."""
    init_cluster_context(REGION, CLUSTER)
    tools = {tool.name: tool for tool in build_tools(REGION, CLUSTER)}

    target = get_service_value_override_path("service1", REGION) / "agent_test.yaml"
    try:
        _invoke(
            tools["update_region_override"],
            service="service1",
            file_name="agent_test.yaml",
            content="key: value\n",
        )
        # The write landed under the region the tools were built for, and the
        # agent had no way to name a different one.
        assert target.exists()
    finally:
        target.unlink(missing_ok=True)
