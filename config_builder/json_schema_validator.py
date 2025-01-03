import json
from pathlib import Path
from typing import Any, Mapping, Optional

import jsonschema
import yaml
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

# TODO: this should not be hardcoded. Find a dynamic way to specify schema files, maybe in _config_generator.json?
SCHEMAS = {
    "kafka/topics/regional_overrides/*/*.yaml": "kafka_topic_overrides.schema.json",
    "kafka/consumer_groups/regional_overrides/*/*.yaml": "consumer_groups/regional_override_consumer_group.schema.json",
    "kafka/consumer_groups/*.yaml": "consumer_groups/default_consumer_group.schema.json",
    "kafka/clusters/*.yaml": "kafka_clusters.schema.json",
    "kafka/consumer_types/*.yaml": "consumer_groups/consumer_type.schema.json",
}

ROOT = Path("shared_config")
SCHEMAS_DIR = "schemas"
REGIONAL_OVERRIDE_DIR = "regional_overrides"


class ValidationException(Exception):
    def __init__(self, file: str, schema: str):
        self.file = file
        self.schema = schema


class JsonSchemaValidator:
    def __init__(
        self,
        root: Optional[Path] = None,
        schemas: Optional[Mapping[str, str]] = None,
        schemas_dir: Optional[str] = None,
    ) -> None:
        self.root = ROOT if root is None else root
        self.schema_root = (
            (self.root / SCHEMAS_DIR)
            if schemas_dir is None
            else (self.root / schemas_dir)
        )
        self.schemas = SCHEMAS if schemas is None else schemas
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
