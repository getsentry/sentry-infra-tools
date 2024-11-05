from libsentrykube.datadog import check_monitor
from libsentrykube.datadog import MissingOverallStateException
from unittest.mock import patch
from unittest.mock import MagicMock
import pytest
import json

COMMON_RESPONSE = {
    "id": 1,
    "org_id": 1,
    "type": "query alert",
    "name": "test name",
    "message": "test message",
    "tags": [
        "tag1:val1",
    ],
    "query": "test query",
    "options": {
        "include_tags": True,
        "new_host_delay": 300,
        "notify_no_data": False,
        "require_full_window": False,
        "thresholds": {"critical": 0.5},
        "notify_audit": False,
        "silenced": {},
    },
    "multi": True,
    "created_at": 1715193044000,
    "created": "2024-05-08T18:30:44.520475+00:00",
    "modified": "2024-05-08T18:30:44.520475+00:00",
    "deleted": None,
    "restricted_roles": None,
    "priority": None,
    "overall_state_modified": "2024-06-11T08:00:59+00:00",
    "creator": {
        "name": "Datadog-Terraform",
        "email": "test-email@sentry.io",
        "handle": "00000000-000-0000-0000-000000000000",
        "id": 1,
    },
}


@patch("libsentrykube.datadog.DATADOG_API_KEY", "TEST_DD_API_KEY")
@patch("urllib.request.urlopen")
def test_check_monitor_ok(mock) -> None:
    RESPONSE = COMMON_RESPONSE.copy()
    RESPONSE["overall_state"] = "OK"

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(RESPONSE)
    mock.return_value = mock_response

    assert check_monitor(1, "TEST_DD_APP_KEY")


@patch("libsentrykube.datadog.DATADOG_API_KEY", "TEST_DD_API_KEY")
@patch("urllib.request.urlopen")
def test_check_monitor_missing_overall_state(mock) -> None:
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(COMMON_RESPONSE)
    mock.return_value = mock_response

    with pytest.raises(MissingOverallStateException):
        check_monitor(1, "TEST_DD_APP_KEY")


@patch("libsentrykube.datadog.DATADOG_API_KEY", "TEST_DD_API_KEY")
@patch("urllib.request.urlopen")
def test_check_monitor_bad_http_response(mock) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock.return_value = mock_response

    with pytest.raises(TypeError):
        check_monitor(1, "TEST_DD_APP_KEY")
