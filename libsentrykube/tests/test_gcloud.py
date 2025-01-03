from unittest.mock import MagicMock, patch

import pytest

from libsentrykube.gcloud import get_channel_versions


@pytest.fixture
def mock_container_resource():
    with patch("libsentrykube.gcloud.googleapiclient.discovery.build") as mock_build:
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
