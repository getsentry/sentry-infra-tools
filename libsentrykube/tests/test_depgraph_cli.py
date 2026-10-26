import json
from unittest.mock import patch

from click.testing import CliRunner

from libsentrykube.depgraph import DependencyGraph


def test_depgraph_command_exists():
    """The depgraph CLI command should be importable."""
    from sentry_kube.cli.depgraph import depgraph

    assert depgraph is not None


def test_depgraph_outputs_json():
    """depgraph should output a valid JSON dependency graph."""
    from sentry_kube.cli.depgraph import depgraph

    mock_graph = DependencyGraph({"service_a": {"service_b", "service_c"}})

    with patch(
        "sentry_kube.cli.depgraph.build_dependency_graph",
        return_value=mock_graph,
    ):
        runner = CliRunner()
        result = runner.invoke(depgraph, [])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "dependencies" in data
        assert "reverse_dependencies" in data
        assert data["dependencies"]["service_a"] == [
            "service_b",
            "service_c",
        ]
        assert data["reverse_dependencies"]["service_b"] == [
            "service_a",
        ]


def test_depgraph_empty_graph():
    """depgraph should handle empty dependency graph."""
    from sentry_kube.cli.depgraph import depgraph

    mock_graph = DependencyGraph({})

    with patch(
        "sentry_kube.cli.depgraph.build_dependency_graph",
        return_value=mock_graph,
    ):
        runner = CliRunner()
        result = runner.invoke(depgraph, [])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == {
            "dependencies": {},
            "reverse_dependencies": {},
        }


def test_depgraph_stage_option():
    """depgraph should pass the --stage option through."""
    from sentry_kube.cli.depgraph import depgraph

    mock_graph = DependencyGraph({})

    with patch(
        "sentry_kube.cli.depgraph.build_dependency_graph",
        return_value=mock_graph,
    ) as mock_build:
        runner = CliRunner()
        result = runner.invoke(depgraph, ["--stage", "production"])

        assert result.exit_code == 0
        mock_build.assert_called_once_with(stage="production")
