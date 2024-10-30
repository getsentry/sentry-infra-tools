import os
from jsonschema import ValidationError
import pytest
from libsentrykube.quickpatch import apply_patch, get_arguments
from libsentrykube.service import (
    get_service_path,
    get_service_value_overrides_file_path,
)
import yaml
import shutil
from pathlib import Path
import tempfile


def load_yaml(file_path):
    with open(file_path, "r") as file:
        return yaml.safe_load(file)


_service = "service1"
_region = "us"
_patch = "test-patch"
_resource = "test-consumer-prod"
num_replicas = 10


# Before each test, use a temporary directory
@pytest.fixture(autouse=True)
def reset_configs(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        # Convert temp_dir string to Path object
        tmp_path = Path(temp_dir)

        # Create necessary directory structure in temp directory
        service_dir = tmp_path / "services" / _service
        service_dir.mkdir(parents=True)
        quickpatches_dir = service_dir / "quickpatches"
        quickpatches_dir.mkdir()
        values_dir = service_dir / "region_overrides" / _region
        values_dir.mkdir(parents=True)

        # Mock the get_service_path to return our temp directory
        def mock_get_service_path(service):
            print(f"Mocking get_service_path for {service}")
            return tmp_path / "services" / service

        # Mock the get_service_value_overrides_file_path
        def mock_get_value_path(
            service: str,
            region_name: str,
            cluster_name: str = "default",
            external: bool = False,
        ):
            print(
                f"Mocking get_service_value_overrides_file_path for {service}, {region_name}, {cluster_name}, {external}"
            )
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
        monkeypatch.setattr(
            "libsentrykube.quickpatch.get_service_value_overrides_file_path",
            mock_get_value_path,
        )
        monkeypatch.setattr(
            "libsentrykube.service.get_service_path", mock_get_service_path
        )
        # Also mock any direct imports in the test file itself
        monkeypatch.setattr(
            "libsentrykube.tests.test_quickpatch.get_service_path",
            mock_get_service_path,
        )  # Add this line
        monkeypatch.setattr(
            "libsentrykube.tests.test_quickpatch.get_service_value_overrides_file_path",
            mock_get_value_path,
        )
        # Copy your template files to the temp directory
        template_dir = Path(__file__).parent / "test_data"
        print(f"Template directory: {template_dir}")
        # Copy all files from values directory
        values_source = template_dir / "values"
        for file in values_source.iterdir():
            shutil.copy(file, values_dir / file.name)

        # Copy all files from quickpatches directory
        quickpatches_source = template_dir / "quickpatches"
        for file in quickpatches_source.iterdir():
            shutil.copy(file, quickpatches_dir / file.name)
        print(f"Temp directory: {temp_dir}")
        print(f"Service directory: {service_dir}")
        print(f"Files in service directory: {os.listdir(service_dir)}")
        print(f"Files in quickpatches directory: {os.listdir(quickpatches_dir)}")
        print(f"Files in values directory: {os.listdir(values_dir)}")

        yield temp_dir  # This allows the test to run with the temporary directory


def test_get_arguments():
    args = get_arguments(_service, _patch)
    assert args == ["replicas1", "replicas2"]


def test_missing_patch_path1():
    with pytest.raises(FileNotFoundError):
        get_arguments(_service, "test-patch2")


def test_missing_patch_path2():
    with pytest.raises(FileNotFoundError):
        get_arguments("service2", _patch)


def test_missing_patch_file1():
    with pytest.raises(
        FileNotFoundError, match=f"Patch file {_patch}.yaml.j2 not found"
    ):
        os.remove(get_service_path(_service) / "quickpatches" / f"{_patch}.yaml.j2")
        apply_patch(
            _service,
            _region,
            _resource,
            _patch,
            {
                "replicas1": num_replicas,
                "replicas2": num_replicas,
            },
        )


def test_missing_patch_file2():
    with pytest.raises(
        FileNotFoundError, match=f"Patch file {_patch}.yaml.j2 not found"
    ):
        os.remove(get_service_path(_service) / "quickpatches" / f"{_patch}.yaml.j2")
        get_arguments("service2", _patch)


def test_missing_arguments():
    with pytest.raises(
        ValidationError, match="Invalid arguments: 'replicas2' is a required property"
    ):
        apply_patch(
            _service,
            _region,
            _resource,
            _patch,
            {
                "replicas1": num_replicas,
            },
        )
    with pytest.raises(
        ValidationError, match="Invalid arguments: 'replicas1' is a required property"
    ):
        apply_patch(
            _service,
            _region,
            _resource,
            _patch,
            {
                "replicas2": num_replicas,
            },
        )


def test_missing_value_file():
    with pytest.raises(
        FileNotFoundError,
        match=f"Resource value file not found for service {_service} in region {_region}",
    ):
        os.remove(
            get_service_value_overrides_file_path(_service, _region, "default", False)
        )
        apply_patch(
            _service,
            _region,
            _resource,
            _patch,
            {
                "replicas1": num_replicas,
                "replicas2": num_replicas,
            },
        )


def test_invalid_resource():
    with pytest.raises(
        ValueError, match="Resource test-consumer-invalid is not allowed to be patched"
    ):
        apply_patch(
            _service,
            _region,
            "test-consumer-invalid",
            _patch,
            {
                "replicas1": num_replicas,
                "replicas2": num_replicas,
            },
        )


def test_correct_patch():
    expected_data = """
                    consumers:
                        consumer:
                            replicas: 10
                    """
    apply_patch(
        _service,
        _region,
        _resource,
        _patch,
        {
            "replicas1": num_replicas,
            "replicas2": num_replicas,
        },
    )
    config = get_service_value_overrides_file_path(_service, "us", "default", False)
    actual = load_yaml(config)
    expected = yaml.safe_load(expected_data)
    assert expected == actual


# TODO: Add more tests covering different patch operations and edge cases
def test_missing_schema():
    with pytest.raises(
        ValueError,
        match="Schema not found in patch file test-patch-missing-schema.yaml.j2",
    ):
        apply_patch(
            _service,
            _region,
            _resource,
            "test-patch-missing-schema",
            {
                "replicas1": num_replicas,
                "replicas2": num_replicas,
            },
        )


def test_invalid_arguments():
    with pytest.raises(ValidationError):
        apply_patch(
            _service,
            _region,
            _resource,
            _patch,
            {
                "replicas1": "invalid",  # Should be integer
                "replicas2": num_replicas,
            },
        )


def test_missing_required_argument():
    with pytest.raises(ValidationError):
        apply_patch(
            _service,
            _region,
            _resource,
            _patch,
            {
                "replicas1": num_replicas,
                # Missing required replicas2
            },
        )


def test_missing_resource_file():
    with pytest.raises(FileNotFoundError, match="Resource value file not found"):
        apply_patch(
            _service,
            "invalid-region",  # Non-existent region
            _resource,
            _patch,
            {
                "replicas1": num_replicas,
                "replicas2": num_replicas,
            },
        )


def test_patch_with_additional_arguments():
    with pytest.raises(ValidationError):
        apply_patch(
            _service,
            _region,
            _resource,
            _patch,
            {
                "replicas1": num_replicas,
                "replicas2": num_replicas,
                "extra_arg": "should be ignored",
            },
        )
