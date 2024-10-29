import os
import pytest
from libsentrykube.context import init_cluster_context
from libsentrykube.quickpatch import apply_patch, get_arguments
from libsentrykube.service import (
    get_service_path,
    get_service_value_overrides_file_path,
)

_service = "service1"
_region = "saas"
_patch = "test-patch"
_resource = "test-consumer-prod"


@pytest.fixture(autouse=True)
def reset_configs():
    region = _region
    cluster = "customer"
    init_cluster_context(region, cluster)
    template = get_service_value_overrides_file_path(_service, "us", "_default", False)
    config = get_service_value_overrides_file_path(_service, "us", "default", False)
    with open(template, "r") as file:
        content = file.read()
        with open(config, "w") as file:
            file.write(content)

    template = (
        get_service_path(_service) / "quickpatches" / f"{_patch}-template.yaml.j2"
    )
    config = get_service_path(_service) / "quickpatches" / f"{_patch}.yaml.j2"
    with open(template, "r") as file:
        content = file.read()
        with open(config, "w") as file:
            file.write(content)


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
                "replicas1": 2221,
                "replicas2": 2221,
            },
        )


def test_missing_patch_file2():
    with pytest.raises(
        FileNotFoundError, match=f"Patch file {_patch}.yaml.j2 not found"
    ):
        os.remove(get_service_path(_service) / "quickpatches" / f"{_patch}.yaml.j2")
        get_arguments("service2", _patch)


def test_missing_arguments():
    with pytest.raises(ValueError, match="Missing argument: replicas2"):
        apply_patch(
            _service,
            _region,
            _resource,
            _patch,
            {
                "replicas1": 2221,
            },
        )
    with pytest.raises(ValueError, match="Missing argument: replicas1"):
        apply_patch(
            _service,
            _region,
            _resource,
            _patch,
            {
                "replicas2": 2221,
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
                "replicas1": 2221,
                "replicas2": 2221,
            },
        )


def test_invalid_resource():
    with pytest.raises(
        ValueError, match="Resource test-consumer-invalid not allowed to be patched"
    ):
        apply_patch(
            _service,
            _region,
            "test-consumer-invalid",
            _patch,
            {
                "replicas1": 2221,
                "replicas2": 2221,
            },
        )
