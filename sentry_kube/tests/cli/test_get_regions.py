from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from sentry_kube.cli.get_regions import get_regions


@pytest.fixture
def mock_config():
    with patch("sentry_kube.cli.get_regions.Config") as mock:
        config = mock.return_value
        config.silo_regions = {
            "region1": MagicMock(k8s_config="config1"),
            "region2": MagicMock(k8s_config="config2"),
        }
        yield mock


@pytest.fixture
def mock_list_clusters():
    with patch("sentry_kube.cli.get_regions.list_clusters_for_customer") as mock:
        yield mock


def test_get_regions_all(mock_config):
    """Test getting all regions without service filter"""
    runner = CliRunner()
    result = runner.invoke(get_regions)

    assert result.exit_code == 0
    assert set(result.output.strip().split()) == {"region1", "region2"}


def test_get_regions_with_service(mock_config, mock_list_clusters):
    """Test getting regions filtered by service"""
    mock_list_clusters.side_effect = lambda config: [
        (
            MagicMock(service_names=["service1"])
            if config == "config1"
            else MagicMock(service_names=["service2"])
        )
    ]

    runner = CliRunner()
    result = runner.invoke(get_regions, ["--service", "service1"])

    assert result.exit_code == 0
    assert result.output.strip() == "region1"


def test_get_regions_service_not_found(mock_config, mock_list_clusters):
    """Test when service is not found in any region"""
    mock_list_clusters.return_value = [MagicMock(service_names=["other_service"])]

    runner = CliRunner()
    result = runner.invoke(get_regions, ["--service", "nonexistent"])

    assert result.exit_code == 0
    assert result.output.strip() == ""
