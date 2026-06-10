from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from libsentrykube.reversemap import ResourceReference
from sentry_kube.render_helm_manifests import render_helm_manifests


def _index_for(resources):
    index = MagicMock()
    index.get_resources_for_path.return_value = resources
    return index


def _invoke(runner, touched_file, *args):
    return runner.invoke(render_helm_manifests, ["--fast", *args, str(touched_file)])


@patch("sentry_kube.render_helm_manifests.init_cluster_context")
@patch("sentry_kube.render_helm_manifests.materialize_manifests")
@patch("sentry_kube.render_helm_manifests.build_helm_index")
def test_unchanged(mock_index, mock_materialize, mock_init_ctx, tmp_path):
    touched = tmp_path / "values.yaml"
    touched.write_text("")
    mock_index.return_value = _index_for(
        {ResourceReference("cust1", "cluster-a", "svc-a")}
    )
    mock_materialize.return_value = False

    result = _invoke(CliRunner(), touched)

    assert result.exit_code == 0
    assert "Service unchanged: cust1 : cluster-a : svc-a" in result.output


@patch("sentry_kube.render_helm_manifests.init_cluster_context")
@patch("sentry_kube.render_helm_manifests.materialize_manifests")
@patch("sentry_kube.render_helm_manifests.build_helm_index")
def test_changes_made(mock_index, mock_materialize, mock_init_ctx, tmp_path):
    touched = tmp_path / "values.yaml"
    touched.write_text("")
    mock_index.return_value = _index_for(
        {ResourceReference("cust1", "cluster-a", "svc-a")}
    )
    mock_materialize.return_value = True

    result = _invoke(CliRunner(), touched)

    assert result.exit_code != 0
    assert "Service materialized: cust1 : cluster-a : svc-a" in result.output
    assert "I made changes to the materialized manifests" in result.output


@patch("sentry_kube.render_helm_manifests.init_cluster_context")
@patch("sentry_kube.render_helm_manifests.materialize_manifests")
@patch("sentry_kube.render_helm_manifests.build_helm_index")
def test_failure_is_loud(mock_index, mock_materialize, mock_init_ctx, tmp_path):
    touched = tmp_path / "values.yaml"
    touched.write_text("")
    mock_index.return_value = _index_for(
        {ResourceReference("cust1", "cluster-a", "svc-bad")}
    )
    mock_materialize.side_effect = RuntimeError("chart fetch failed")

    result = _invoke(CliRunner(), touched)

    assert result.exit_code == 1
    assert "Service FAILED: cust1 : cluster-a : svc-bad" in result.stderr
    assert "1 service(s) failed to render" in result.stderr


@patch("sentry_kube.render_helm_manifests.init_cluster_context")
@patch("sentry_kube.render_helm_manifests.materialize_manifests")
@patch("sentry_kube.render_helm_manifests.build_helm_index")
def test_kube_version_and_api_versions_forwarded(
    mock_index, mock_materialize, mock_init_ctx, tmp_path
):
    touched = tmp_path / "values.yaml"
    touched.write_text("")
    mock_index.return_value = _index_for(
        {ResourceReference("cust1", "cluster-a", "svc-a")}
    )
    mock_materialize.return_value = False

    result = _invoke(
        CliRunner(),
        touched,
        "--kube-version",
        "1.30.0",
        "--api-versions",
        "monitoring.coreos.com/v1",
    )

    assert result.exit_code == 0
    mock_materialize.assert_called_once_with(
        region_name="cust1",
        service_name="svc-a",
        cluster_name="cluster-a",
        kube_version="1.30.0",
        api_versions=("monitoring.coreos.com/v1",),
    )
