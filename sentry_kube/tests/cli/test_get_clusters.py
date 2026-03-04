import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from sentry_kube.cli.get_clusters import get_clusters


@dataclass
class FakeCluster:
    name: str


@pytest.fixture
def mock_config():
    with patch("sentry_kube.cli.get_clusters.Config") as mock:
        config = mock.return_value
        config.silo_regions = {
            "region1": MagicMock(k8s_config="config1", stage="production"),
            "region2": MagicMock(k8s_config="config2", stage="production"),
        }
        config.get_regions.return_value = ["region1", "region2"]
        yield config


@pytest.fixture
def mock_list_clusters():
    with patch("sentry_kube.cli.get_clusters.list_clusters_for_customer") as mock:
        yield mock


@pytest.fixture
def mock_get_region_config():
    with patch("sentry_kube.cli.get_clusters.get_region_config") as mock:
        yield mock


def test_get_clusters_all_regions(mock_config, mock_list_clusters):
    """Without -C: lists all clusters grouped by region."""
    mock_list_clusters.side_effect = lambda config: {
        "config1": [FakeCluster("cluster-a"), FakeCluster("cluster-b")],
        "config2": [FakeCluster("cluster-c")],
    }[config]

    runner = CliRunner()
    result = runner.invoke(get_clusters, obj={"stage": "production", "customer": None})

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert len(lines) == 2
    assert "region1: cluster-a cluster-b" in lines
    assert "region2: cluster-c" in lines


def test_get_clusters_single_region(
    mock_config, mock_list_clusters, mock_get_region_config
):
    """With -C: lists cluster names for that region only, space-separated."""
    mock_get_region_config.return_value = (
        "region1",
        mock_config.silo_regions["region1"],
    )
    mock_list_clusters.return_value = [
        FakeCluster("cluster-a"),
        FakeCluster("cluster-b"),
    ]

    runner = CliRunner()
    result = runner.invoke(
        get_clusters, obj={"stage": "production", "customer": "region1"}
    )

    assert result.exit_code == 0
    assert result.output.strip() == "cluster-a cluster-b"
    mock_get_region_config.assert_called_once()
    mock_list_clusters.assert_called_once_with("config1")


def test_get_clusters_with_stage_filter(mock_config, mock_list_clusters):
    """--stage filters to only matching regions."""
    mock_config.silo_regions["region2"].stage = "staging"
    mock_config.get_regions.return_value = ["region1"]
    mock_list_clusters.return_value = [FakeCluster("cluster-a")]

    runner = CliRunner()
    result = runner.invoke(
        get_clusters,
        ["--stage", "production"],
        obj={"stage": "production", "customer": None},
    )

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert len(lines) == 1
    assert "region1: cluster-a" in lines


def test_get_clusters_single_region_invalid(mock_config, mock_get_region_config):
    """With -C set to an invalid region, exits with error."""
    mock_get_region_config.side_effect = ValueError("Region 'bogus' not found")

    runner = CliRunner()
    result = runner.invoke(
        get_clusters, obj={"stage": "production", "customer": "bogus"}
    )

    assert result.exit_code != 0


def test_get_clusters_all_regions_json(mock_config, mock_list_clusters):
    """--output json: returns dict of region -> cluster list."""
    mock_list_clusters.side_effect = lambda config: {
        "config1": [FakeCluster("cluster-a"), FakeCluster("cluster-b")],
        "config2": [FakeCluster("cluster-c")],
    }[config]

    runner = CliRunner()
    result = runner.invoke(
        get_clusters, ["-o", "json"], obj={"stage": "production", "customer": None}
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == {
        "region1": ["cluster-a", "cluster-b"],
        "region2": ["cluster-c"],
    }


def test_get_clusters_single_region_json(
    mock_config, mock_list_clusters, mock_get_region_config
):
    """--output json with -C: returns list of cluster names."""
    mock_get_region_config.return_value = (
        "region1",
        mock_config.silo_regions["region1"],
    )
    mock_list_clusters.return_value = [
        FakeCluster("cluster-a"),
        FakeCluster("cluster-b"),
    ]

    runner = CliRunner()
    result = runner.invoke(
        get_clusters,
        ["--output", "json"],
        obj={"stage": "production", "customer": "region1"},
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == ["cluster-a", "cluster-b"]


def test_get_clusters_all_regions_yaml(mock_config, mock_list_clusters):
    """--output yaml: returns dict of region -> cluster list."""
    mock_list_clusters.side_effect = lambda config: {
        "config1": [FakeCluster("cluster-a"), FakeCluster("cluster-b")],
        "config2": [FakeCluster("cluster-c")],
    }[config]

    runner = CliRunner()
    result = runner.invoke(
        get_clusters,
        ["--output", "yaml"],
        obj={"stage": "production", "customer": None},
    )

    assert result.exit_code == 0
    data = yaml.safe_load(result.output)
    assert data == {
        "region1": ["cluster-a", "cluster-b"],
        "region2": ["cluster-c"],
    }
