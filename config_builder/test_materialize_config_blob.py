from dataclasses import asdict
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest
import yaml

from config_builder.materialize_config_blob import (
    ClusterEntry,
    ConfigBlob,
    LogicalTopicEntry,
    PhysicalTopicEntry,
    resolve_default,
)


def write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))


@pytest.fixture
def config_dirs(tmp_path: Path) -> Generator[Path, None, None]:
    write_yaml(
        tmp_path / "clusters" / "defaults" / "defaults.yaml",
        {"producer_config": {"acks": "all"}},
    )
    write_yaml(
        tmp_path / "clusters" / "region1.yaml",
        {
            "cluster1": {"brokers": ["b1:9092", "b2:9092"]},
            "cluster2": {"brokers": ["b3:9092"], "producer_config": {"acks": "1"}},
        },
    )
    write_yaml(
        tmp_path / "logical" / "defaults" / "defaults.yaml",
        {"producer_config": {"compression.type": "lz4"}},
    )
    write_yaml(tmp_path / "logical" / "topic-a.yaml", {})
    write_yaml(tmp_path / "logical" / "topic-b.yaml", {"schema": "schema-b"})
    physical = tmp_path / "physical" / "region1"
    write_yaml(physical / "topic-a.yaml", {"cluster": "cluster1"})
    write_yaml(
        physical / "topic-a-2.yaml", {"cluster": "cluster2", "logical_topic": "topic-a"}
    )
    write_yaml(physical / "topic-b.yaml", {"cluster": "cluster2"})
    with patch(
        "config_builder.materialize_config_blob.list_topics",
        return_value=["topic-a", "schema-b"],
    ):
        yield tmp_path


def make_blob(root: Path, region: str = "region1") -> ConfigBlob:
    return ConfigBlob(root / "clusters", root / "logical", root / "physical", region)


def test_resolve_default() -> None:
    defaults = {"a": {"x": "1"}, "b": {"y": "2"}}
    assert resolve_default(defaults, {"a": {"z": "3"}}, "a", {}) == {"z": "3"}
    assert resolve_default(defaults, {}, "b", {}) == {"y": "2"}
    assert resolve_default(defaults, {}, "c", {}) == {}
    assert resolve_default(defaults, {}, "b", {}) is not defaults["b"]


def test_cluster_entry_defaults_not_shared() -> None:
    a, b = ClusterEntry(config={}), ClusterEntry(config={})
    a.producer_config["acks"] = "all"
    assert b.producer_config == {}


def test_physical_topic_entry() -> None:
    assert asdict(PhysicalTopicEntry(cluster="c1")) == {
        "cluster": "c1",
        "topic_name_override": None,
    }


def test_logical_topic_to_dict_omits_name() -> None:
    topic = LogicalTopicEntry(
        name="topic-a", physical_topics=[PhysicalTopicEntry(cluster="c1")]
    )
    assert topic.to_dict() == {
        "producer_config": {},
        "producer_config_overrides": {},
        "physical_topics": [{"cluster": "c1", "topic_name_override": None}],
        "producer_physical_topic_override": [],
    }


def test_load_physical_topics(config_dirs: Path) -> None:
    topic = LogicalTopicEntry(name="topic-a", physical_topics=[])
    topic.load_physical_topics(config_dirs / "physical", "region1")
    assert sorted(topic.physical_topics, key=lambda t: t.cluster) == [
        PhysicalTopicEntry(cluster="cluster1", topic_name_override=None),
        PhysicalTopicEntry(cluster="cluster2", topic_name_override="topic-a-2"),
    ]


def test_load_physical_topics_producer_override(tmp_path: Path) -> None:
    write_yaml(
        tmp_path / "region1" / "topic-a-new.yaml",
        {"cluster": "cluster2", "producer_logical_topic": True},
    )
    topic = LogicalTopicEntry(name="topic-a", physical_topics=[])
    topic.load_physical_topics(tmp_path, "region1")
    assert topic.physical_topics == []
    assert topic.producer_physical_topic_override == [
        PhysicalTopicEntry(cluster="cluster2", topic_name_override="topic-a-new")
    ]


def test_load_physical_topics_missing_region(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LogicalTopicEntry(name="t", physical_topics=[]).load_physical_topics(
            tmp_path, "nowhere"
        )


def test_config_blob(config_dirs: Path) -> None:
    blob = make_blob(config_dirs).to_dict()
    assert blob["clusters"] == {
        "cluster1": {
            "config": {"bootstrap.servers": "b1:9092,b2:9092"},
            "env_config": {},
            "producer_config": {"acks": "all"},
        },
        "cluster2": {
            "config": {"bootstrap.servers": "b3:9092"},
            "env_config": {},
            "producer_config": {"acks": "1"},
        },
    }
    assert set(blob["topic"]) == {"topic-a", "topic-b"}
    assert blob["topic"]["topic-b"] == {
        "producer_config": {"compression.type": "lz4"},
        "producer_config_overrides": {},
        "physical_topics": [{"cluster": "cluster2", "topic_name_override": None}],
        "producer_physical_topic_override": [],
    }
    assert len(blob["topic"]["topic-a"]["physical_topics"]) == 2


def test_config_blob_missing_region(config_dirs: Path) -> None:
    with pytest.raises(ValueError):
        make_blob(config_dirs, "region2")


def test_config_blob_unregistered_schema(config_dirs: Path) -> None:
    write_yaml(config_dirs / "logical" / "topic-c.yaml", {})
    with pytest.raises(ValueError, match="not registered"):
        make_blob(config_dirs)
