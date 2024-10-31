import os
from jsonschema import ValidationError
import pytest
from libsentrykube.quickpatch import apply_patch, get_arguments
from libsentrykube.service import (
    get_managed_service_value_overrides,
    get_service_path,
)
import yaml
import shutil
from pathlib import Path
import tempfile


def load_yaml(file_path):
    with open(file_path, "r") as file:
        return yaml.safe_load(file)


SERVICE = "service1"
REGION = "us"
TEST_PATCH = "test-patch"
TEST_RESOURCE = "test-consumer-prod"
TEST_NUM_REPLICAS = 10


# Before each test, use a temporary directory
@pytest.fixture(autouse=True)
def reset_configs(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        # Convert temp_dir string to Path object
        tmp_path = Path(temp_dir)

        # Create necessary directory structure in temp directory
        service_dir = tmp_path / "services" / SERVICE
        service_dir.mkdir(parents=True)
        quickpatches_dir = service_dir / "quickpatches"
        quickpatches_dir.mkdir()
        values_dir = service_dir / "region_overrides" / REGION
        values_dir.mkdir(parents=True)

        # Mock the get_service_path to return our temp directory
        def mock_get_service_path(service):
            return tmp_path / "services" / service

        # Mock the get_service_value_overrides_file_path
        def mock_get_value_path(
            service: str,
            region_name: str,
            cluster_name: str = "default",
            external: bool = False,
        ):
            if region_name == "saas":
                region_name = "us"
            return (
                tmp_path
                / "services"
                / service
                / "region_overrides"
                / region_name
                / f"{cluster_name}.yaml"
            )

        # Apply the mocks
        monkeypatch.setattr(
            "libsentrykube.quickpatch.get_service_path", mock_get_service_path
        )
        # monkeypatch.setattr(
        #     "libsentrykube.quickpatch.get_service_value_overrides_file_path",
        #     mock_get_value_path,
        # )
        monkeypatch.setattr(
            "libsentrykube.service.get_service_path", mock_get_service_path
        )
        # Also mock any direct imports in the test file itself
        monkeypatch.setattr(
            "libsentrykube.tests.test_quickpatch.get_service_path",
            mock_get_service_path,
        )  # Add this line
        # monkeypatch.setattr(
        #     "libsentrykube.tests.test_quickpatch.get_service_value_overrides_file_path",
        #     mock_get_value_path,
        # )
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

        yield temp_dir  # This allows the test to run with the temporary directory


def test_get_arguments():
    args = get_arguments(SERVICE, TEST_PATCH)
    assert args == ["replicas1", "replicas2"]


def test_missing_patch_path1():
    with pytest.raises(FileNotFoundError):
        get_arguments(SERVICE, "test-patch2")


def test_missing_patch_path2():
    with pytest.raises(FileNotFoundError):
        get_arguments("service2", TEST_PATCH)


def test_missing_patch_file1():
    with pytest.raises(
        FileNotFoundError, match=f"Patch file {TEST_PATCH}.yaml.j2 not found"
    ):
        os.remove(get_service_path(SERVICE) / "quickpatches" / f"{TEST_PATCH}.yaml.j2")
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


def test_missing_patch_file2():
    with pytest.raises(
        FileNotFoundError, match=f"Patch file {TEST_PATCH}.yaml.j2 not found"
    ):
        os.remove(get_service_path(SERVICE) / "quickpatches" / f"{TEST_PATCH}.yaml.j2")
        get_arguments("service2", TEST_PATCH)


def test_missing_arguments():
    with pytest.raises(
        ValidationError, match="Invalid arguments: 'replicas2' is a required property"
    ):
        apply_patch(
            SERVICE,
            REGION,
            TEST_RESOURCE,
            TEST_PATCH,
            {
                "replicas1": TEST_NUM_REPLICAS,
            },
        )
    with pytest.raises(
        ValidationError, match="Invalid arguments: 'replicas1' is a required property"
    ):
        apply_patch(
            SERVICE,
            REGION,
            TEST_RESOURCE,
            TEST_PATCH,
            {
                "replicas2": TEST_NUM_REPLICAS,
            },
        )


# def test_missing_value_file():
#     with pytest.raises(
#         FileNotFoundError,
#         match=f"Resource value file not found for service {SERVICE} in region {REGION}",
#     ):
#         os.remove(
#             get_service_value_overrides_file_path(SERVICE, REGION, "default", False)
#         )
#         apply_patch(
#             SERVICE,
#             REGION,
#             TEST_RESOURCE,
#             TEST_PATCH,
#             {
#                 "replicas1": TEST_NUM_REPLICAS,
#                 "replicas2": TEST_NUM_REPLICAS,
#             },
#         )


def test_invalidTEST_RESOURCE():
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


def test_correct_patch():
    expected_data = """
                    consumers:
                        consumer:
                            replicas: 10
                    """
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
    actual = get_managed_service_value_overrides(SERVICE, "us", "default", False)
    expected = yaml.safe_load(expected_data)
    assert expected == actual


# TODO: Add more tests covering different patch operations and edge cases
def test_missing_schema():
    with pytest.raises(
        ValueError,
        match="Schema not found in patch file test-patch-missing-schema.yaml.j2",
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


def test_invalid_arguments():
    with pytest.raises(ValidationError):
        apply_patch(
            SERVICE,
            REGION,
            TEST_RESOURCE,
            TEST_PATCH,
            {
                "replicas1": "invalid",  # Should be integer
                "replicas2": TEST_NUM_REPLICAS,
            },
        )


def test_missing_required_argument():
    with pytest.raises(ValidationError):
        apply_patch(
            SERVICE,
            REGION,
            TEST_RESOURCE,
            TEST_PATCH,
            {
                "replicas1": TEST_NUM_REPLICAS,
                # Missing required replicas2
            },
        )


def test_missing_resource_file():
    with pytest.raises(FileNotFoundError, match="Resource value file not found"):
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


def test_patch_with_additional_arguments():
    with pytest.raises(ValidationError):
        apply_patch(
            SERVICE,
            REGION,
            TEST_RESOURCE,
            TEST_PATCH,
            {
                "replicas1": TEST_NUM_REPLICAS,
                "replicas2": TEST_NUM_REPLICAS,
                "extra_arg": "should be ignored",
            },
        )
