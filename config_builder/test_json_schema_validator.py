import json
import os
import tempfile
from pathlib import Path
from typing import Generator, Mapping
import pytest
import yaml

from .json_schema_validator import JsonSchemaValidator, ValidationException

# the schema files need to be fake temp files with test schemas
SCHEMAS = {
    "kafka/consumer_groups/regional_overrides/*/*.yaml": "consumer_groups/regional_override_consumer_group.schema.json",
    "kafka/consumer_groups/*.yaml": "consumer_groups/default_consumer_group.schema.json",
    "kafka/consumer_types/*.yaml": "consumer_groups/consumer_type.schema.json",
}

CONSUMER_TYPE_SCHEMA = {
    "$id": "consumer_type.schema.json",
    "$schema": "http://json-schema.org/draft-07/schema",
    "type": "object",
    "properties": {
        "notes": {
            "type": ["string", "null"],
            "description": "An intermediate variable to hold multiple lines of text. This is read by the field `troubleshooting_notes` in consumer group files.",
        },
        "service": {"$ref": "../common/common1.schema.json#/$defs/service"},
    },
    "required": ["service"],
    "additionalProperties": False,
}

REGIONAL_CONSUMER_GROUP_SCHEMA = {
    "$id": "regional_override_consumer_group.schema.json",
    "$schema": "http://json-schema.org/draft-07/schema",
    "type": "object",
    "properties": {
        "another_ref": {"$ref": "../common/common2.schema.json#/$defs/another_ref"},
    },
    "required": ["another_ref"],
    "additionalProperties": False,
}

DEFAULT_CONSUMER_GROUP_SCHEMA = {
    "$id": "default_consumer_group.schema.json",
    "$schema": "http://json-schema.org/draft-07/schema",
    "type": "object",
    "properties": {
        "service": {"$ref": "../common/common1.schema.json#/$defs/service"},
    },
    "required": ["service"],
    "additionalProperties": False,
}

COMMON1 = {
    "$id": "common/common1.schema.json",
    "$schema": "http://json-schema.org/draft-07/schema",
    "$defs": {
        "service": {
            "type": "string",
            "description": "The name of the software service that owns this consumer group. All Datadog monitors for this consumer group will use that service's PagerDuty escalation path. Please use the same file name from /service_registry/, without the file extension.",
        }
    },
}

COMMON2 = {
    "$id": "common/common2.schema.json",
    "$schema": "http://json-schema.org/draft-07/schema",
    "$defs": {
        "another_ref": {
            "type": "string",
            "description": "something",
        }
    },
}

REGIONAL = {"another_ref": "something"}
DEFAULT = {"service": "snuba"}
UNRELATED = {"unrelated": 1}

FAILURE_CASE_WRONG_TYPE_IN_REF = {"service": 1}
FAILURE_CASE_EXTRA_KEY = {"service": "snuba", "extra_key": "extra"}
FAILURE_CASE_MISSING_REQUIRED: Mapping[str, str] = {"anything": "anything"}


@pytest.fixture
def valid_structure() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        # make all the schema files
        os.makedirs(Path(temp_dir) / "schemas" / "consumer_groups")
        consumer_type_schema = (
            Path(temp_dir) / "schemas" / "consumer_groups" / "consumer_type.schema.json"
        )
        consumer_type_schema.write_text(json.dumps(CONSUMER_TYPE_SCHEMA, indent=4))
        regional_schema_schema = (
            Path(temp_dir)
            / "schemas"
            / "consumer_groups"
            / "regional_override_consumer_group.schema.json"
        )
        regional_schema_schema.write_text(
            json.dumps(REGIONAL_CONSUMER_GROUP_SCHEMA, indent=4)
        )

        default_schema = (
            Path(temp_dir)
            / "schemas"
            / "consumer_groups"
            / "default_consumer_group.schema.json"
        )
        default_schema.write_text(json.dumps(DEFAULT_CONSUMER_GROUP_SCHEMA, indent=4))

        os.makedirs(Path(temp_dir) / "schemas" / "common")
        common1 = Path(temp_dir) / "schemas" / "common" / "common1.schema.json"
        common1.write_text(json.dumps(COMMON1, indent=4))
        common2 = Path(temp_dir) / "schemas" / "common" / "common2.schema.json"
        common2.write_text(json.dumps(COMMON2, indent=4))

        # make valid yaml files
        os.makedirs(
            Path(temp_dir)
            / "kafka"
            / "consumer_groups"
            / "regional_overrides"
            / "disney"
        )
        regional_override_snuba = (
            Path(temp_dir)
            / "kafka"
            / "consumer_groups"
            / "regional_overrides"
            / "disney"
            / "snuba.yaml"
        )
        regional_override_snuba.write_text(yaml.dump(REGIONAL, indent=4))
        default_snuba = Path(temp_dir) / "kafka" / "consumer_groups" / "snuba.yaml"
        default_snuba.write_text(yaml.dump(DEFAULT, indent=4))

        # make invalid yaml files
        os.makedirs(Path(temp_dir) / "kafka" / "consumer_types")
        consumer_type_fail1 = (
            Path(temp_dir) / "kafka" / "consumer_types" / "consumer_type_fail1.yaml"
        )
        consumer_type_fail1.write_text(
            yaml.dump(FAILURE_CASE_WRONG_TYPE_IN_REF, indent=4)
        )

        consumer_type_fail2 = (
            Path(temp_dir) / "kafka" / "consumer_types" / "consumer_type_fail2.yaml"
        )
        consumer_type_fail2.write_text(yaml.dump(FAILURE_CASE_EXTRA_KEY, indent=4))

        consumer_type_fail3 = (
            Path(temp_dir) / "kafka" / "consumer_types" / "consumer_type_fail3.yaml"
        )
        consumer_type_fail3.write_text(
            yaml.dump(FAILURE_CASE_MISSING_REQUIRED, indent=4)
        )

        # make yaml file that should NOT be validated
        os.makedirs(Path(temp_dir) / "kafka" / "unrelated_dir")
        unrelated1 = Path(temp_dir) / "kafka" / "unrelated_dir" / "consumer_type.yaml"
        unrelated1.write_text(yaml.dump(UNRELATED, indent=4))

        os.makedirs(
            Path(temp_dir)
            / "kafka"
            / "consumer_groups"
            / "regional_override"
            / "disney"
            / "unrelated_dir"
        )
        unrelated2 = (
            Path(temp_dir)
            / "kafka"
            / "consumer_groups"
            / "regional_override"
            / "disney"
            / "unrelated_dir"
            / "consumer_type.yaml"
        )
        unrelated2.write_text(yaml.dump(UNRELATED, indent=4))
        yield temp_dir


@pytest.mark.parametrize(
    "file, return_code",
    [
        pytest.param(
            Path("kafka")
            / "consumer_groups"
            / "regional_overrides"
            / "disney"
            / "snuba.yaml",
            0,
            id="Validate valid yaml file (regional override)",
        ),
        pytest.param(
            Path("kafka") / "consumer_groups" / "snuba.yaml",
            0,
            id="Validate valid yaml file (default consumer group)",
        ),
        pytest.param(
            Path("kafka") / "unrelated_dir" / "consumer_type.yaml",
            None,
            id="Unrelated directory should not be validated",
        ),
        pytest.param(
            Path("kafka")
            / "consumer_groups"
            / "regional_override"
            / "disney"
            / "unrelated_dir"
            / "consumer_type.yaml",
            None,
            id="Unrelated subdirectory should not be validated",
        ),
    ],
)
def test_json_schema_validator(
    valid_structure: str, file: Path, return_code: int | None
) -> None:
    validator = JsonSchemaValidator(Path(valid_structure), SCHEMAS)
    assert validator.validate_yaml(Path(valid_structure) / file) == return_code


@pytest.mark.parametrize(
    "file",
    [
        pytest.param(
            Path("kafka") / "consumer_types" / "consumer_type_fail1.yaml",
            id="Invalid yaml file should fail correctly (wrong type in ref)",
        ),
        pytest.param(
            Path("kafka") / "consumer_types" / "consumer_type_fail2.yaml",
            id="Invalid yaml file should fail correctly (extra key)",
        ),
        pytest.param(
            Path("kafka") / "consumer_types" / "consumer_type_fail3.yaml",
            id="Invalid yaml file should fail correctly (missing required)",
        ),
    ],
)
def test_json_schema_invalid(valid_structure: str, file: Path) -> None:
    with pytest.raises(ValidationException):
        validator = JsonSchemaValidator(Path(valid_structure), SCHEMAS)
        validator.validate_yaml(Path(valid_structure) / file)
