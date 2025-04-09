from unittest import mock
import json
from libsentrykube.iap import ensure_iap_tunnel

dummy_kube_config = json.dumps(
    {
        "clusters": [
            {
                "certificate-authority-data": "secure-data",
                "server": "server-url",
                "name": "gke_test-proj_test-region_test-cluster",
            }
        ]
    }
)


@mock.patch("builtins.open", new_callable=mock.mock_open, read_data=dummy_kube_config)
@mock.patch("yaml.dump")
@mock.patch("os.getenv", return_value="/tmp/kubeconfig")
def test_ensure_iap_tunnel(mock_getenv, mock_yaml_dump, mock_open) -> None:
    mock_ctx = mock.Mock()
    mock_ctx.obj.cluster.services_data.__getitem__ = mock.Mock(return_value=8080)
    mock_ctx.obj.context_name = "gke_test-proj_test-region_test-cluster"
    ensure_iap_tunnel(mock_ctx)

    mock_yaml_dump.assert_called_once_with(
        json.loads(dummy_kube_config),
        mock.ANY,
    )
