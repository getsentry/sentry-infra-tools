import json
from pathlib import Path
from typing import Any, Mapping, Optional

import jsonschema
import yaml
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

# TODO: Delete this and only use `JsonSchemaValidator.__load_schema_legend()` to map files to schemas
DEFAULT_SCHEMAS = {
    "kafka/topics/regional_overrides/*/*.yaml": "kafka_topic_overrides.schema.json",
    "kafka/consumer_groups/regional_overrides/*/*.yaml": "consumer_groups/regional_override_consumer_group.schema.json",
    "kafka/consumer_groups/*.yaml": "consumer_groups/default_consumer_group.schema.json",
    "kafka/clusters/*.yaml": "kafka_clusters.schema.json",
    "kafka/consumer_types/*.yaml": "consumer_groups/consumer_type.schema.json",
}

DEFAULT_ROOT = Path("shared_config")
DEFAULT_SCHEMAS_DIR = "schemas"


class ValidationException(Exception):
    def __init__(self, file: str, schema: str):
        self.file = file
        self.schema = schema


class JsonSchemaValidator:
    def __init__(
        self,
        root: Optional[Path] = None,
        schema_legend: Optional[Path] = None,
        schemas_dir: Optional[str] = None,
    ) -> None:
        """
        Used to validate the JSON schema of a file.
        Works by mapping the file's path to a schema file via a legend.

        Args:
            root: Optional root dir to look in for schemas. Defaults to `DEFAULT_ROOT`.
            schema_legend: Optional legend file containing a JSON mapping of filepaths to schema files.
                           Filepath keys are globs (matched with `Path.match`), relative to `root`.
                           Defaults to `DEFAULT_SCHEMAS`.
            schemas_dir: Optional dir containing schema files. Defaults to `DEFAULT_SCHEMAS_DIR`.
        """

        self.root = DEFAULT_ROOT if root is None else root
        self.schema_root = (
            (self.root / DEFAULT_SCHEMAS_DIR)
            if schemas_dir is None
            else (self.root / schemas_dir)
        )
        self.schemas = (
            DEFAULT_SCHEMAS
            if schema_legend is None
            else self.__load_schema_legend(schema_legend)
        )
        self.registry = self.__build_registry()

    def __build_registry(self) -> Registry[Mapping[str, Resource[Mapping[str, Any]]]]:
        """
        The jsonschema's concept of registry is for $ref keyword to
        resolve file paths and find the referenced object
        """

        def retrieve_ref(ref: str) -> Resource[Mapping[str, Any]]:
            ref_file = self.schema_root / Path(ref)
            contents = json.loads(ref_file.read_text())
            return Resource.from_contents(contents)

        # library bug, typing of this argument is wrong
        registry: Registry = Registry(retrieve=retrieve_ref)  # type: ignore
        return registry

    def __get_schema(self, file: Path) -> Path | None:
        """
        Finds which schema to use for a yaml file
        """
        for glob, schema in self.schemas.items():
            if file.match(str(self.root / glob)):
                return self.schema_root / schema
        return None

    def __load_schema_legend(self, file: Path) -> dict[str, str]:
        """
        Takes a path to a file which maps filepath globs to schema files.
        Overrides the hardcoded `DEFAULT_SCHEMAS` mapping.
        """
        with open(file) as f:
            schema_mapping: dict[str, str] = json.load(f)
        return schema_mapping

    def validate_yaml(self, file: Path) -> int | None:
        schema = self.__get_schema(file)
        if schema:
            file_content = yaml.safe_load(file.read_text())
            try:
                jsonschema.validate(
                    schema=json.load(open(schema)),
                    instance=file_content,
                    registry=self.registry,
                )
                return 0
            except ValidationError as e:
                raise ValidationException(str(file), str(schema)) from e
        return None
