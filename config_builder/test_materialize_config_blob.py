import json
from pathlib import Path
from typing import Any, Generator
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner, Result

from config_builder.materialize_config_blob import (
    ClusterEntry,
    ConfigBlob,
    LogicalTopicEntry,
    PhysicalTopicEntry,
    materialize_config_blob,
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
    assert PhysicalTopicEntry(cluster="c1").to_dict() == {"cluster": "c1"}
    assert PhysicalTopicEntry(cluster="c1", topic_name_override="t").to_dict() == {
        "cluster": "c1",
        "topic_name_override": "t",
    }


def test_logical_topic_to_dict_omits_name() -> None:
    topic = LogicalTopicEntry(
        name="topic-a", physical_topics=[PhysicalTopicEntry(cluster="c1")]
    )
    assert topic.to_dict() == {
        "producer_config": {},
        "producer_config_overrides": {},
        "physical_topics": [{"cluster": "c1"}],
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
        {
            "cluster": "cluster2",
            "logical_topic": "topic-a",
            "producer_logical_topic": True,
        },
    )
    topic_a = LogicalTopicEntry(name="topic-a", physical_topics=[])
    topic_a.load_physical_topics(tmp_path, "region1")
    assert topic_a.physical_topics == []
    assert topic_a.producer_physical_topic_override == [
        PhysicalTopicEntry(cluster="cluster2", topic_name_override="topic-a-new")
    ]

    # A producer cutover for topic-a must not redirect other topics' producers
    topic_b = LogicalTopicEntry(name="topic-b", physical_topics=[])
    topic_b.load_physical_topics(tmp_path, "region1")
    assert topic_b.producer_physical_topic_override == []


def test_load_physical_topics_missing_region(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LogicalTopicEntry(name="t", physical_topics=[]).load_physical_topics(
            tmp_path, "nowhere"
        )


def test_config_blob(config_dirs: Path) -> None:
    blob = make_blob(config_dirs).to_dict()["arroyo_config_blob"]
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
    assert set(blob["topics"]) == {"topic-a", "topic-a-2", "topic-b"}
    assert blob["topics"]["topic-b"] == {
        "producer_config": {"compression.type": "lz4"},
        "producer_config_overrides": {},
        "physical_topics": [{"cluster": "cluster2"}],
        "producer_physical_topic_override": [],
    }


def test_config_blob_missing_region(config_dirs: Path) -> None:
    with pytest.raises(ValueError):
        make_blob(config_dirs, "region2")


def test_config_blob_unregistered_schema(config_dirs: Path) -> None:
    write_yaml(config_dirs / "logical" / "topic-c.yaml", {})
    with pytest.raises(ValueError, match="not registered"):
        make_blob(config_dirs)


def test_split_sliced_topics(config_dirs: Path) -> None:
    blob = make_blob(config_dirs)
    out = blob.to_dict()["arroyo_config_blob"]["topics"]
    # topic-a had two physical topics, so it becomes one logical topic per physical topic
    assert set(out) == {"topic-a", "topic-a-2", "topic-b"}
    for name, cluster in [("topic-a", "cluster1"), ("topic-a-2", "cluster2")]:
        assert out[name]["physical_topics"] == [{"cluster": cluster}]
        assert out[name]["producer_config"] == {"compression.type": "lz4"}
    assert out["topic-a"]["producer_config"] is not out["topic-a-2"]["producer_config"]
    # Calling it again must not change anything
    assert blob.to_dict()["arroyo_config_blob"]["topics"] == out


def test_split_sliced_topics_name_collision(config_dirs: Path) -> None:
    write_yaml(
        config_dirs / "physical" / "region1" / "topic-b-2.yaml",
        {"cluster": "cluster1", "logical_topic": "topic-b"},
    )
    write_yaml(config_dirs / "logical" / "topic-b-2.yaml", {"schema": "schema-b"})
    with pytest.raises(ValueError):
        make_blob(config_dirs).to_dict()


STR_MAP = {"type": "object", "additionalProperties": {"type": "string"}}
OPTIONAL_STR_MAP = {**STR_MAP, "optional": True}
OPTIONAL_OVERRIDES = {
    "type": "object",
    "optional": True,
    "additionalProperties": STR_MAP,
}
PHYSICAL_TOPICS = {
    "type": "array",
    "optional": True,
    "items": {
        "type": "object",
        "properties": {
            "cluster": {"type": "string"},
            "topic_name_override": {"type": "string", "optional": True},
        },
    },
}
TOPIC_FIELDS: dict[str, Any] = {
    "physical_topics": PHYSICAL_TOPICS,
    "producer_physical_topic_override": PHYSICAL_TOPICS,
    "producer_config": OPTIONAL_STR_MAP,
    "producer_config_overrides": OPTIONAL_OVERRIDES,
    "consumer_config": OPTIONAL_STR_MAP,
    "consumer_config_overrides": OPTIONAL_OVERRIDES,
}


def write_schema(root: Path, topic_fields: dict[str, Any]) -> Path:
    """Writes a sentry-options `ops-kafka` schema for the blob, returning the schemas dir."""
    schema = {
        "version": "1.0",
        "type": "object",
        "properties": {
            "config_blob": {
                "type": "object",
                "description": "Arroyo config blob",
                "default": {"clusters": {}, "topics": {}},
                "properties": {
                    "clusters": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "properties": {
                                "config": OPTIONAL_STR_MAP,
                                "env_config": OPTIONAL_STR_MAP,
                                "producer_config": OPTIONAL_STR_MAP,
                            },
                        },
                    },
                    "topics": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "properties": topic_fields,
                        },
                    },
                },
            }
        },
    }
    schemas_dir = root / "schemas"
    (schemas_dir / "ops-kafka").mkdir(parents=True)
    (schemas_dir / "ops-kafka" / "schema.json").write_text(json.dumps(schema))
    return schemas_dir


def run_materialize(root: Path, schemas_dir: Path) -> Result:
    (root / "output").mkdir()
    return CliRunner().invoke(
        materialize_config_blob,
        [
            *("-c", str(root / "clusters")),
            *("-l", str(root / "logical")),
            *("-p", str(root / "physical")),
            *("-o", str(root / "output")),
            *("-s", str(schemas_dir)),
        ],
    )


def test_materialize_validates_and_writes(config_dirs: Path) -> None:
    result = run_materialize(config_dirs, write_schema(config_dirs, TOPIC_FIELDS))
    assert result.exit_code == 0, result.output
    written = yaml.safe_load(
        (
            config_dirs / "output" / "region1" / "_arroyo_config_generated.yaml"
        ).read_text()
    )
    assert written == make_blob(config_dirs).to_dict()


def test_materialize_rejects_blob_failing_schema(config_dirs: Path) -> None:
    # A second region, so we can check a failure in one region writes nothing at all
    write_yaml(
        config_dirs / "clusters" / "region2.yaml", {"cluster1": {"brokers": ["b"]}}
    )
    write_yaml(
        config_dirs / "physical" / "region2" / "topic-b.yaml", {"cluster": "cluster1"}
    )
    # Every topic has producer_config, so a schema without it rejects every blob
    fields = {k: v for k, v in TOPIC_FIELDS.items() if k != "producer_config"}
    result = run_materialize(config_dirs, write_schema(config_dirs, fields))
    assert result.exit_code != 0
    assert "fails the ops-kafka sentry-options schema" in result.output
    assert "producer_config" in result.output
    assert list((config_dirs / "output").iterdir()) == []
