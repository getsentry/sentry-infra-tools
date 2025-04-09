from libsentrykube.iap import _is_external_connection_allowed
from unittest import mock


@mock.patch("subprocess.check_output")
def test_external_connection_allowed(mock_subprocess):
    mock_subprocess.return_value = b"True\n"
    result = _is_external_connection_allowed(
        "test-project", "us-central1", "test-cluster"
    )

    assert result is True
