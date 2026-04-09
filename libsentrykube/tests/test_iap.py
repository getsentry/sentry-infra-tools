from unittest import mock
import json
import pytest
import click
from libsentrykube.iap import ensure_iap_tunnel, _get_cluster_credentials

dummy_kube_config = json.dumps(
    {
        "clusters": [
            {
                "name": "gke_test-proj_test-region_test-cluster",
                "cluster": {
                    "certificate-authority-data": "secure-data",
                    "server": "https://abc123.gke.goog",
                },
            }
        ]
    }
)

dummy_kube_config_non_dns = json.dumps(
    {
        "clusters": [
            {
                "name": "gke_test-proj_test-region_test-cluster",
                "cluster": {
                    "certificate-authority-data": "secure-data",
                    "server": "https://1.2.3.4",
                },
            }
        ]
    }
)

dummy_kube_config_non_gke = json.dumps(
    {
        "clusters": [
            {
                "name": "kind-local-cluster",
                "cluster": {
                    "certificate-authority-data": "secure-data",
                    "server": "https://127.0.0.1:6443",
                },
            }
        ]
    }
)


@mock.patch("builtins.open", new_callable=mock.mock_open, read_data=dummy_kube_config)
@mock.patch("os.path.isfile", return_value=True)
@mock.patch("os.path.isdir", return_value=True)
@mock.patch("libsentrykube.iap.KUBE_CONFIG_PATH", "/tmp/kubeconfig")
def test_ensure_iap_tunnel(mock_isdir, mock_isfile, mock_open) -> None:
    mock_ctx = mock.Mock()
    mock_ctx.obj.context_name = "gke_test-proj_test-region_test-cluster"
    result = ensure_iap_tunnel(mock_ctx)

    assert result == "/tmp/kubeconfig"


def test_get_cluster_credentials_invalid_context_format() -> None:
    # Non-GKE format: should return silently without raising
    _get_cluster_credentials("invalid-context")


def test_get_cluster_credentials_non_gke_context() -> None:
    # Non-GKE prefix: should return silently without raising
    _get_cluster_credentials("eks_project_region_cluster")


@mock.patch("libsentrykube.iap.subprocess.run")
@mock.patch("libsentrykube.iap.click.echo")
def test_get_cluster_credentials_gcloud_failure(mock_echo, mock_run) -> None:
    mock_run.return_value = mock.Mock(returncode=1, stderr="auth error", stdout="")
    with pytest.raises(click.ClickException) as exc_info:
        _get_cluster_credentials("gke_test-proj_test-region_test-cluster")
    assert "Failed to get cluster credentials" in str(exc_info.value)
    assert "auth error" in str(exc_info.value)


@mock.patch("libsentrykube.iap.KUBE_CONFIG_PATH", "/tmp/test-kubeconfig")
@mock.patch("libsentrykube.iap.subprocess.run")
@mock.patch("libsentrykube.iap.click.echo")
def test_get_cluster_credentials_success(mock_echo, mock_run) -> None:
    mock_run.return_value = mock.Mock(returncode=0, stderr="", stdout="")
    _get_cluster_credentials("gke_test-proj_test-region_test-cluster")
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "gcloud"
    assert "test-cluster" in cmd
    assert "--dns-endpoint" in cmd
    # Verify KUBECONFIG is passed to subprocess
    env = mock_run.call_args[1]["env"]
    assert env["KUBECONFIG"] == "/tmp/test-kubeconfig"


@mock.patch("builtins.open", new_callable=mock.mock_open, read_data="")
@mock.patch("os.path.isfile", return_value=True)
@mock.patch("os.path.isdir", return_value=True)
@mock.patch("libsentrykube.iap.KUBE_CONFIG_PATH", "/tmp/kubeconfig")
@mock.patch("libsentrykube.iap._get_cluster_credentials")
def test_ensure_iap_tunnel_empty_kubeconfig(
    mock_get_creds, mock_isdir, mock_isfile, mock_open
) -> None:
    """Empty kubeconfig file should trigger credential fetch."""
    mock_ctx = mock.Mock()
    mock_ctx.obj.context_name = "gke_test-proj_test-region_test-cluster"

    # After credential fetch, still empty - should raise
    with pytest.raises(click.ClickException) as exc_info:
        ensure_iap_tunnel(mock_ctx)
    assert "not found in kubeconfig and could not be fetched automatically" in str(
        exc_info.value
    )
    mock_get_creds.assert_called_once()


@mock.patch("builtins.open", new_callable=mock.mock_open, read_data="{}")
@mock.patch("os.path.isfile", return_value=True)
@mock.patch("os.path.isdir", return_value=True)
@mock.patch("libsentrykube.iap.KUBE_CONFIG_PATH", "/tmp/kubeconfig")
@mock.patch("libsentrykube.iap._get_cluster_credentials")
def test_ensure_iap_tunnel_no_clusters_key(
    mock_get_creds, mock_isdir, mock_isfile, mock_open
) -> None:
    """Kubeconfig without clusters key should trigger credential fetch."""
    mock_ctx = mock.Mock()
    mock_ctx.obj.context_name = "gke_test-proj_test-region_test-cluster"

    with pytest.raises(click.ClickException) as exc_info:
        ensure_iap_tunnel(mock_ctx)
    assert "not found in kubeconfig and could not be fetched automatically" in str(
        exc_info.value
    )
    mock_get_creds.assert_called_once()


@mock.patch("builtins.open", new_callable=mock.mock_open, read_data="clusters: null")
@mock.patch("os.path.isfile", return_value=True)
@mock.patch("os.path.isdir", return_value=True)
@mock.patch("libsentrykube.iap.KUBE_CONFIG_PATH", "/tmp/kubeconfig")
@mock.patch("libsentrykube.iap._get_cluster_credentials")
def test_ensure_iap_tunnel_null_clusters_value(
    mock_get_creds, mock_isdir, mock_isfile, mock_open
) -> None:
    """Kubeconfig with null clusters value should trigger credential fetch."""
    mock_ctx = mock.Mock()
    mock_ctx.obj.context_name = "gke_test-proj_test-region_test-cluster"

    with pytest.raises(click.ClickException) as exc_info:
        ensure_iap_tunnel(mock_ctx)
    assert "not found in kubeconfig and could not be fetched automatically" in str(
        exc_info.value
    )
    mock_get_creds.assert_called_once()


@mock.patch(
    "builtins.open", new_callable=mock.mock_open, read_data=dummy_kube_config_non_dns
)
@mock.patch("os.path.isfile", return_value=True)
@mock.patch("os.path.isdir", return_value=True)
@mock.patch("libsentrykube.iap.KUBE_CONFIG_PATH", "/tmp/kubeconfig")
@mock.patch("libsentrykube.iap._get_cluster_credentials")
def test_ensure_iap_tunnel_non_dns_endpoint_triggers_refetch(
    mock_get_creds, mock_isdir, mock_isfile, mock_open
) -> None:
    """Server not ending in gke.goog should trigger credential re-fetch."""
    mock_ctx = mock.Mock()
    mock_ctx.obj.context_name = "gke_test-proj_test-region_test-cluster"

    with pytest.raises(click.ClickException) as exc_info:
        ensure_iap_tunnel(mock_ctx)
    assert "Failed to configure DNS endpoint" in str(exc_info.value)
    mock_get_creds.assert_called_once()


@mock.patch(
    "builtins.open", new_callable=mock.mock_open, read_data=dummy_kube_config_non_gke
)
@mock.patch("os.path.isfile", return_value=True)
@mock.patch("os.path.isdir", return_value=True)
@mock.patch("libsentrykube.iap.KUBE_CONFIG_PATH", "/tmp/kubeconfig")
def test_ensure_iap_tunnel_non_gke_context_in_kubeconfig(
    mock_isdir, mock_isfile, mock_open
) -> None:
    """Non-GKE context already in kubeconfig should succeed without credential fetch."""
    mock_ctx = mock.Mock()
    mock_ctx.obj.context_name = "kind-local-cluster"
    result = ensure_iap_tunnel(mock_ctx)
    assert result == "/tmp/kubeconfig"
