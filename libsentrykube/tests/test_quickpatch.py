import os
from typing import Generator
from jsonschema import ValidationError
import pytest
from libsentrykube.context import init_cluster_context
from libsentrykube.quickpatch import apply_patch, get_arguments, patch_json
from libsentrykube.service import (
    get_tools_managed_service_value_overrides,
    get_service_path,
)
import yaml
import shutil
from pathlib import Path


def load_yaml(file_path):
    with open(file_path, "r") as file:
        return yaml.safe_load(file)


SERVICE = "my_service"
REGION = "customer1"
TEST_PATCH = "test-patch"
TEST_RESOURCE = "test-consumer-prod"
CLUSTER = "default"
TEST_NUM_REPLICAS = 10


# Before each test, use a temporary directory
@pytest.fixture(autouse=True)
def reset_configs(initialized_config_structure) -> Generator[str, None, None]:
    # Convert temp_dir string to Path object
    init_cluster_context("customer1", "cluster1")
    tmp_path = Path(initialized_config_structure)

    # Create necessary directory structure in temp directory
    service_dir = tmp_path / "k8s" / "services" / SERVICE
    quickpatches_dir = service_dir / "quickpatches"
    quickpatches_dir.mkdir()
    values_dir = service_dir / "region_overrides" / REGION

    # Copy your template files to the temp directory
    template_dir = Path(__file__).parent / "test_data"
    # Copy all files from values directory
    values_source = template_dir / "values"
    for file in values_source.iterdir():
        shutil.copy(file, values_dir / file.name)

    # Copy all files from quickpatches directory
    quickpatches_source = template_dir / "quickpatches"
    for file in quickpatches_source.iterdir():
        shutil.copy(file, quickpatches_dir / file.name)

    yield initialized_config_structure  # This allows the test to run with the temporary directory


def test_get_arguments1():
    args = get_arguments(SERVICE, TEST_PATCH)
    assert args == ["replicas1", "replicas2"]


def test_get_arguments_missing_service():
    with pytest.raises(FileNotFoundError):
        get_arguments("service2", TEST_PATCH)


def test_get_arguments_missing_schema():
    with pytest.raises(ValueError):
        get_arguments(SERVICE, "test-patch-missing-schema")


def test_missing_patch_path1():
    with pytest.raises(FileNotFoundError):
        get_arguments(SERVICE, "test-patch-definitely-does-not-exist")


def test_missing_patch_path2():
    with pytest.raises(FileNotFoundError):
        get_arguments("service2", TEST_PATCH)


def test_missing_patch_file1():
    with pytest.raises(
        FileNotFoundError, match=f"Patch file {TEST_PATCH}.yaml not found"
    ):
        os.remove(get_service_path(SERVICE) / "quickpatches" / f"{TEST_PATCH}.yaml")
        apply_patch(
            SERVICE,
            REGION,
            TEST_RESOURCE,
            TEST_PATCH,
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
        )


def test_missing_patches():
    with pytest.raises(
        ValueError,
        match="Patches not found in patch file test-patch-missing-patches.yaml",
    ):
        apply_patch(
            SERVICE,
            REGION,
            TEST_RESOURCE,
            "test-patch-missing-patches",
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
        )


def test_missing_resource_mappings():
    with pytest.raises(
        ValueError,
        match="Resource mappings not found in patch file test-patch-missing-mappings.yaml",
    ):
        apply_patch(
            SERVICE,
            REGION,
            TEST_RESOURCE,
            "test-patch-missing-mappings",
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
        )


def test_invalid_resource():
    with pytest.raises(
        ValueError, match="Resource test-consumer-invalid is not allowed to be patched"
    ):
        apply_patch(
            SERVICE,
            REGION,
            "test-consumer-invalid",
            TEST_PATCH,
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
        )


@pytest.mark.parametrize(
    "patch, cluster, expected, args",
    [
        pytest.param(
            TEST_PATCH,
            CLUSTER,
            {
                "consumers": {
                    "consumer": {
                        "replicas": TEST_NUM_REPLICAS,
                    },
                },
            },
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
            id="Normal patch test",
        ),
        pytest.param(
            "test-patch2",
            CLUSTER,
            {
                "consumers": {
                    "consumer": {
                        "replicas": TEST_NUM_REPLICAS,
                    },
                },
            },
            {
                "replicas-1": TEST_NUM_REPLICAS,
                "replicas_2": TEST_NUM_REPLICAS,
            },
            id="Normal patch test2",
        ),
        pytest.param(
            "test-patch",
            "missing-file",
            {
                "consumers": {
                    "consumer": {
                        "replicas": TEST_NUM_REPLICAS,
                    },
                },
            },
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
            id="Test with missing .managed.yaml file (it should auto-generate)",
        ),
        pytest.param(
            "test-patch",
            "empty",
            {
                "consumers": {
                    "consumer": {
                        "replicas": TEST_NUM_REPLICAS,
                    },
                },
            },
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
            id="Test with empty .managed.yaml file (it should auto-generate)",
        ),
        pytest.param(
            "test-patch",
            "default2",
            {
                "consumers": {
                    "consumer": {
                        "replicas": TEST_NUM_REPLICAS,
                    },
                    "existing-consumer": {
                        "replicas": 4,
                    },
                },
            },
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
            id="Test with missing resources in yaml file (it should auto-generate)",
        ),
        pytest.param(
            "test-patch-override",
            "default2",
            {
                "consumers": {
                    "consumer": {
                        "replicas": TEST_NUM_REPLICAS,
                    },
                },
            },
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
            id="Test override of existing resource.",
        ),  # Expects to override all resources at the same path level with the value
    ],
)
def test_correct_patch(patch, cluster, expected, args):
    apply_patch(
        SERVICE,
        REGION,
        TEST_RESOURCE,
        patch,
        args,
        cluster,
    )
    actual = get_tools_managed_service_value_overrides(SERVICE, REGION, cluster, False)
    assert expected == actual


def test_missing_schema():
    with pytest.raises(
        ValueError,
        match="Schema not found in patch file test-patch-missing-schema.yaml",
    ):
        apply_patch(
            SERVICE,
            REGION,
            TEST_RESOURCE,
            "test-patch-missing-schema",
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
        )


@pytest.mark.parametrize(
    "patch",
    [
        pytest.param(
            "invalid-schema1",
        ),
        pytest.param(
            "invalid-schema2",
        ),
        pytest.param(
            "invalid-schema3",
        ),
    ],
)
def test_invalid_schema(patch):
    with pytest.raises(ValueError):
        apply_patch(
            SERVICE,
            REGION,
            TEST_RESOURCE,
            patch,
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
        )


def test_missing_resource_file():
    with pytest.raises(FileNotFoundError):
        apply_patch(
            SERVICE,
            "invalid-region",  # Non-existent region
            TEST_RESOURCE,
            TEST_PATCH,
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
            },
        )


@pytest.mark.parametrize(
    "expected_message, arguments",
    [
        pytest.param(
            "Invalid arguments: 'replicas2' is a required property",
            {
                "replicas1": TEST_NUM_REPLICAS,
            },
            id="missing-replicas1",
        ),
        pytest.param(
            "Invalid arguments: 'replicas1' is a required property",
            {
                "replicas2": TEST_NUM_REPLICAS,
            },
            id="missing-replicas2",
        ),
        pytest.param(
            "Invalid arguments: 'invalid' is not of type 'integer'",
            {
                "replicas1": "invalid",  # Should be integer
                "replicas2": TEST_NUM_REPLICAS,
            },
            id="invalid-replicas1",
        ),
        pytest.param(
            r"Invalid arguments: Additional properties are not allowed \('extra_arg' was unexpected\)",
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
                "extra_arg": "should be ignored",
            },
            id="extra-args",
        ),
    ],
)
def test_validations(expected_message, arguments):
    with pytest.raises(ValidationError, match=expected_message):
        apply_patch(
            SERVICE,
            REGION,
            TEST_RESOURCE,
            TEST_PATCH,
            arguments,
        )


@pytest.mark.parametrize(
    "patch, resource, expected",
    [
        pytest.param(
            [
                {
                    "path": "a/b/c",
                    "value": TEST_NUM_REPLICAS,
                }
            ],
            {},
            {"a": {"b": {"c": TEST_NUM_REPLICAS}}},
            id="Whole path is not present, should create",
        ),
        pytest.param(
            [
                {
                    "path": "a/b/c",
                    "value": TEST_NUM_REPLICAS,
                }
            ],
            {"a": {}},
            {"a": {"b": {"c": TEST_NUM_REPLICAS}}},
            id="Part of path is not present, should create",
        ),
        pytest.param(
            [
                {
                    "path": "a/b/c",
                    "value": TEST_NUM_REPLICAS,
                }
            ],
            {"a": {"b": {"c": 0}}},
            {"a": {"b": {"c": TEST_NUM_REPLICAS}}},
            id="Update existing value",
        ),
        pytest.param(
            [
                {
                    "path": "a/b/c",
                    "value": {"d": TEST_NUM_REPLICAS},
                }
            ],
            {"a": {"b": {"c": 0}}},
            {"a": {"b": {"c": {"d": TEST_NUM_REPLICAS}}}},
            id="Apply a dict, should override existing value",
        ),
        pytest.param(
            [
                {
                    "path": "a/b/d",
                    "value": {"e": TEST_NUM_REPLICAS},
                }
            ],
            {"a": {"b": {"c": 0}}},
            {"a": {"b": {"c": 0, "d": {"e": TEST_NUM_REPLICAS}}}},
            id="Apply a dict with an existing dict",
        ),
        pytest.param(
            [
                {
                    "path": "a/b/c/////////",
                    "value": TEST_NUM_REPLICAS,
                }
            ],
            {},
            {"a": {"b": {"c": TEST_NUM_REPLICAS}}},
            id="Weird path",
        ),
        pytest.param(
            [
                {
                    "path": "a/b/c",
                    "value": {"d": TEST_NUM_REPLICAS},
                }
            ],
            {"a": {"b": {"c": 0}}},
            {"a": {"b": {"c": {"d": TEST_NUM_REPLICAS}}}},
            id="Should replace existing value",
        ),
    ],
)
def test_patch_json(patch, resource, expected):
    actual = patch_json(patch, resource)
    assert expected == actual


def test_patch_json_invalid_path():
    with pytest.raises(ValueError):
        patch_json(
            [
                {"value": TEST_NUM_REPLICAS},
            ],
            {},
        )  # missing path value
    with pytest.raises(ValueError):
        patch_json(
            [
                {
                    "path": "/",
                    "value": TEST_NUM_REPLICAS,
                }
            ],
            {},
        )  # invalid path value
    with pytest.raises(
        ValueError,
        match="Cannot traverse path 'a' as it points to a non-dictionary value",
    ):
        patch_json(
            [
                {
                    "path": "a/b",
                    "value": TEST_NUM_REPLICAS,
                }
            ],
            {"a": []},
        )  # not a dict
    with pytest.raises(
        ValueError,
        match="resource must be a dict",
    ):
        patch_json(
            [
                {
                    "path": "a",
                    "value": TEST_NUM_REPLICAS,
                }
            ],
            [],
        )  # not a dict
