from copy import deepcopy
import yaml
from pathlib import Path
import click
from typing import Any, TypeVar, Self
from dataclasses import dataclass, asdict, field
from sentry_kafka_schemas import list_topics


def is_yaml_file(f: Path) -> bool:
    """
    Helper function for checking if a path is a yaml file.
    """
    return f.is_file() and (f.name.endswith(".yaml") or f.name.endswith(".yml"))


def get_yaml_files(dir: Path) -> list[Path]:
    """
    Helper function for getting all yaml files in a dir.
    """
    return [f for f in dir.iterdir() if is_yaml_file(f)]


def load_defaults(dir: Path) -> dict[str, Any]:
    """
    Helper function for loading default values from a conf dir.
    """
    default_path = dir / "defaults" / "defaults.yaml"
    try:
        with open(default_path, "r") as f:
            defaults: dict[str, Any] = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    return defaults


T = TypeVar("T")


def resolve_default(
    defaults: dict[str, Any],
    overrides: dict[str, Any],
    key: str,
    empty_default: T,
) -> T:
    """
    Helper function which returns:
    - The value of `key` from `overrides`, if it exists
    - Otherwise, the value of `key` from `defaults`, if it exists
    - Otherwise, the provided `empty_default`
    """

    # deepcopy so entries don't share (and yaml doesn't alias) the defaults' dicts
    value: T = deepcopy(overrides.get(key, defaults.get(key, empty_default)))
    return value


@dataclass
class ClusterEntry:
    """A cluster entry in the config blob."""

    config: dict[str, str]
    env_config: dict[str, str] = field(default_factory=dict)
    producer_config: dict[str, str] = field(default_factory=dict)


@dataclass
class PhysicalTopicEntry:
    """A physical topic in the config blob."""

    cluster: str
    topic_name_override: str | None = None


@dataclass
class LogicalTopicEntry:
    """A logical topic in the config blob."""

    name: str
    physical_topics: list[PhysicalTopicEntry]
    producer_config: dict[str, str] = field(default_factory=dict)
    producer_config_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    producer_physical_topic_override: list[PhysicalTopicEntry] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any] | None:
        """
        Helper function for converting this object into a dict.
        We don't include the logical topic name in the config blob, so we use this
        rather than `asdict()`.

        Returns `None` if this logical topic doesn't have any physical topic backends configured.
        """
        if (
            len(self.physical_topics) == 0
            and len(self.producer_physical_topic_override) == 0
        ):
            return None

        return {
            "producer_config": self.producer_config,
            "producer_config_overrides": self.producer_config_overrides,
            "physical_topics": [asdict(pt) for pt in self.physical_topics],
            "producer_physical_topic_override": [
                asdict(pt) for pt in self.producer_physical_topic_override
            ],
        }

    def load_physical_topics(self, physical_topic_dir: Path, region: str) -> Self:
        """
        Loads physical topic data for this logical topic from the given path.
        Since physical topics are different per region, the `physical_topic_dir` must have a subfolder
        for the given `region` which will be searched for physical topic(s) which use this logical topic.
        """
        search_dir = physical_topic_dir / region
        if not search_dir.is_dir():
            raise ValueError(
                f"Could not find a directory named {region} in path {physical_topic_dir}."
            )
        topic_files = get_yaml_files(search_dir)
        defaults = load_defaults(search_dir)
        # FIXME(benm): Is there a way that we can load all physical topics for all logical
        # topics in a region without reading every file everytime?
        for topic in topic_files:
            with open(topic, "r") as f:
                # Physical topic files can be empty
                data = yaml.safe_load(f) or {}
            # `topic_name_override` is only used if the physical topic name differs from the logical topic
            topic_name_override: str | None = (
                topic.stem if topic.stem != self.name else None
            )
            # If there's no explicit logical topic, assume its the physical topic name
            logical_topic_name = data.get("logical_topic", None) or topic.stem
            if logical_topic_name != self.name:
                continue
            cluster_name = resolve_default(defaults, data, "cluster", "")
            entry = PhysicalTopicEntry(
                cluster=cluster_name, topic_name_override=topic_name_override
            )
            if data.get("producer_logical_topic", False):
                self.producer_physical_topic_override.append(entry)
            # Disabled physical topics are not appended to the physical topic backend
            elif not resolve_default(defaults, data, "disabled", False):
                self.physical_topics.append(entry)
        return self


class ConfigBlob:
    """
    Kafka cluster and topic configuration.

    Args:
        cluster_dir: Dir containing cluster config files.
        logical_topic_dir: Dir containing logical topic config files.
        physical_topic_dir: Dir containing physical topic config files.
        region: Production region to use when finding cluster & physical topic config files.
    """

    def __init__(
        self,
        cluster_dir: Path,
        logical_topic_dir: Path,
        physical_topic_dir: Path,
        region: str,
    ) -> None:
        self._topic_schemas = set(list_topics())
        self.clusters = self._load_clusters(cluster_dir, region)
        self.topics = self._load_topics(logical_topic_dir, physical_topic_dir, region)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns the `ConfigBlob` in dict form.
        """
        # FIXME(bmckerry): Delete after slicing is supported by Arroyo reloading clients.
        self._split_sliced_topics()

        dict_clusters = {
            name: asdict(cluster) for name, cluster in self.clusters.items()
        }
        dict_topics = {}
        for name, topic in self.topics.items():
            t_dict = topic.to_dict()
            # Only include logical topics which have a physical topic backend in this region
            if t_dict is not None:
                dict_topics[name] = t_dict
        return {"clusters": dict_clusters, "topics": dict_topics}

    def _load_clusters(self, cluster_dir: Path, region: str) -> dict[str, ClusterEntry]:
        """
        Loads the cluster configs for the given region.
        """
        cluster_file = cluster_dir / f"{region}.yaml"
        if not cluster_file.is_file():
            raise ValueError(f"Could not find a cluster file named {cluster_file}.")
        defaults = load_defaults(cluster_dir)
        with open(cluster_file, "r") as f:
            data = yaml.safe_load(f)
        return {
            cluster: ClusterEntry(
                config={"bootstrap.servers": ",".join(conf["brokers"])},
                env_config=resolve_default(defaults, conf, "env_config", {}),
                producer_config=resolve_default(defaults, conf, "producer_config", {}),
            )
            for cluster, conf in data.items()
        }

    def _load_topics(
        self,
        logical_topic_dir: Path,
        physical_topic_dir: Path,
        region: str,
    ) -> dict[str, LogicalTopicEntry]:
        """
        Loads logical topic data from the given dir into this `ConfigBlob`'s `topics`
        field.
        """
        defaults = load_defaults(logical_topic_dir)
        logical_topics = get_yaml_files(logical_topic_dir)
        all_topics = {}
        for topic in logical_topics:
            topic_name = topic.stem
            with open(topic, "r") as f:
                data = yaml.safe_load(f)
            # Every topic's schema must be registered in sentry-kafka-schemas
            topic_schema = data.get("schema", None) or topic_name
            if topic_schema not in self._topic_schemas:
                raise ValueError(
                    f"{topic}: schema {topic_schema} is not registered in sentry-kafka-schemas"
                )
            all_topics[topic_name] = LogicalTopicEntry(
                name=topic_name,
                physical_topics=[],
                producer_config=resolve_default(
                    defaults, data, "producer_config", empty_default={}
                ),
                producer_config_overrides=resolve_default(
                    defaults, data, "producer_config_overrides", empty_default={}
                ),
            ).load_physical_topics(physical_topic_dir, region)
        return all_topics

    def _split_sliced_topics(self) -> None:
        """
        TODO: Delete this shim once Arroyo reloading clients support slicing.

        This checks the ConfigBlob for all logical topics with multiple physical topics,
        and creates a separate logical topic for each physical topic.
        Currently needed because Arroyo reloading clients don't support having multiple physical
        topics in a logical topic, but we want to start implementing these clients in our services.
        """
        topics_to_split = {
            name: topic
            for name, topic in self.topics.items()
            # Ignore `producer_physical_topic_overrides` since we aren't doing cutovers yet
            if len(topic.physical_topics) > 1
        }
        # Delete logical topics from config
        for name in topics_to_split:
            del self.topics[name]
        # Add split logical topics to config
        for name, topic in topics_to_split.items():
            for pt in topic.physical_topics:
                pt_name = pt.topic_name_override or name
                if pt_name in self.topics:
                    raise ValueError(
                        f"Splitting logical topic {name}: {pt_name} is already a logical topic"
                    )
                # We need to make a deepcopy per entry because PyYAML writes
                # shared dicts as YAML anchors
                self.topics[pt_name] = LogicalTopicEntry(
                    name=pt_name,
                    physical_topics=[PhysicalTopicEntry(cluster=pt.cluster)],
                    producer_config=deepcopy(topic.producer_config),
                    producer_config_overrides=deepcopy(topic.producer_config_overrides),
                    producer_physical_topic_override=deepcopy(
                        topic.producer_physical_topic_override
                    ),
                )


@click.command()
@click.option(
    "--cluster-dir",
    "-c",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Dir containing cluster config files.",
)
@click.option(
    "--logical-topic-dir",
    "-l",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Dir containing logical topic config files.",
)
@click.option(
    "--physical-topic-dir",
    "-p",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Dir containing physical topic config files for each region.",
)
@click.option(
    "--output-dir",
    "-o",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Dir to write the output materialized config file to.",
)
def materialize_config_blob(
    cluster_dir: Path,
    logical_topic_dir: Path,
    physical_topic_dir: Path,
    output_dir: Path,
) -> None:
    # We derive our prod regions from the cluster config files we have
    regions = [f.stem for f in get_yaml_files(cluster_dir)]

    for region in regions:
        blob = ConfigBlob(
            cluster_dir,
            logical_topic_dir,
            physical_topic_dir,
            region,
        )
        regional_output = output_dir / region
        regional_output.mkdir(exist_ok=True)
        with open(regional_output / "_arroyo_config_generated.yaml", "w") as f:
            f.write("# This is a generated file. Please do not edit directly.\n")
            yaml.safe_dump(blob.to_dict(), f)
