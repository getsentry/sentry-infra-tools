from unittest.mock import MagicMock, patch

import pytest

import logging

from libsentrykube.gcloud import build_api, get_channel_versions, get_release_channel


@pytest.fixture
def mock_container_resource():
    with (
        patch("libsentrykube.gcloud.googleapiclient.discovery.build") as mock_build,
        patch("libsentrykube.gcloud.google.auth.default", return_value=("creds", None)),
    ):
        yield mock_build


def test_get_channel_versions_success(mock_container_resource):
    # Mocking the container resource and its method 'execute'
    mock_execute = MagicMock(
        return_value={
            "channels": [{"channel": "stable", "validVersions": ["1.2.3", "1.2.4"]}]
        }
    )
    mock_container_resource.return_value.projects.return_value.zones.return_value.getServerconfig.return_value.execute = mock_execute

    project = "test_project"
    zone = "test_zone"
    channel = "stable"

    versions = get_channel_versions(project, zone, channel)

    assert versions == ["1.2.3", "1.2.4"]


def test_get_channel_versions_bad_response(mock_container_resource):
    # Mocking the container resource and its method 'execute'
    mock_execute = MagicMock(return_value={})
    mock_container_resource.return_value.projects.return_value.zones.return_value.getServerconfig.return_value.execute = mock_execute

    project = "test_project"
    zone = "test_zone"
    channel = "stable"

    with pytest.raises(Exception) as e:
        get_channel_versions(project, zone, channel)

    assert str(e.value) == "Bad channel API response"


def test_get_release_channel_returns_channel_config(mock_container_resource):
    channel = {"channel": "stable", "defaultVersion": "1.2.3", "validVersions": []}
    mock_execute = MagicMock(return_value={"channels": [{"channel": "rapid"}, channel]})
    mock_container_resource.return_value.projects.return_value.zones.return_value.getServerconfig.return_value.execute = mock_execute

    assert get_release_channel("test_project", "test_zone", "stable") == channel


def test_build_api_passes_credentials_without_cache(mock_container_resource):
    with patch("libsentrykube.gcloud.google.auth.default") as default:
        default.return_value = ("creds", None)
        client = build_api("compute", "v1")

    assert client is mock_container_resource.return_value
    mock_container_resource.assert_called_once_with(
        "compute", "v1", credentials="creds", cache_discovery=False
    )


def test_build_api_silences_missing_project_warning(mock_container_resource, caplog):
    def default():
        logging.getLogger("google.auth._default").warning("No project ID")
        return "creds", None

    with patch("libsentrykube.gcloud.google.auth.default", side_effect=default):
        build_api("compute", "v1")

    assert "No project ID" not in caplog.text
