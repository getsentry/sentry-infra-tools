import json
import yaml
import os
import tempfile
from pathlib import Path
from typing import Generator, List

import pytest

from config_builder.materializer import iterate_jsonnet_configs, materialize_file

JSONNET_FILE = """{
    test_key: 123,
    test_key2: 234,
}
"""

DICT_RESULT = {
    "test_key": 123,
    "test_key2": 234,
}


@pytest.fixture
def config_struct() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_config = Path(temp_dir)
        feature1 = base_config / "feature1"
        os.makedirs(feature1 / "combined")
        with open(feature1 / "combined" / "file1.jsonnet", "w") as f:
            f.write(JSONNET_FILE)
        with open(feature1 / "combined" / "file2.jsonnet", "w") as f:
            f.write(JSONNET_FILE)
        with open(feature1 / "combined" / "file3.yaml.jsonnet", "w") as f:
            f.write(JSONNET_FILE)

        feature2 = base_config / "feature2"
        os.makedirs(feature2 / "combined")
        with open(feature2 / "combined" / "file1.jsonnet", "w") as f:
            f.write(JSONNET_FILE)
        with open(feature2 / "combined" / "another_file.something", "w") as f:
            f.write("")
        os.makedirs(feature1 / ".terragrunt-cache")
        with open(feature1 / ".terragrunt-cache" / "file3.jsonnet", "w") as f:
            f.write("")
        yield temp_dir


def test_iteration(config_struct: str) -> None:
    root_dir = Path(config_struct)
    ret = list(iterate_jsonnet_configs(root_dir, [".terragrunt-cache"]))

    assert sorted(ret) == [
        root_dir / Path("feature1/combined/file1.jsonnet"),
        root_dir / Path("feature1/combined/file2.jsonnet"),
        root_dir / Path("feature1/combined/file3.yaml.jsonnet"),
        root_dir / Path("feature2/combined/file1.jsonnet"),
    ]


@pytest.mark.parametrize(
    "materialized_root, expected_path, is_yaml",
    [
        pytest.param(
            Path("some_subdir/_materialized_configs/"),
            Path("some_subdir/_materialized_configs/feature1/combined/file1.json"),
            False,
            id="Put the materialized file in an arbitrary directory",
        ),
        pytest.param(
            None,
            Path("feature1/combined/file1.json"),
            False,
            id="Put the materialized file in an arbitrary directory",
        ),
        pytest.param(
            None,
            Path("feature1/combined/file3.yaml"),
            True,
            id="Yaml file",
        ),
    ],
)
def test_materialize_file(
    config_struct: str,
    materialized_root: Path | None,
    expected_path: Path,
    is_yaml: bool,
) -> None:
    jsonnet_file_path = (
        f"{expected_path}.jsonnet" if is_yaml else "feature1/combined/file1.jsonnet"
    )

    materialize_file(
        Path(config_struct),
        Path(config_struct) / jsonnet_file_path,
        materialized_root,
    )

    expected_file = Path(config_struct) / expected_path
    assert expected_file.exists()
    with open(expected_file) as f:
        content = f.read()
        if is_yaml:
            assert yaml.safe_load(content) == DICT_RESULT
            with pytest.raises(json.decoder.JSONDecodeError):
                json.loads(content)
        else:
            assert json.loads(content) == DICT_RESULT


@pytest.mark.parametrize(
    "materialized_root, expected_path, is_yaml, ext_packages",
    [
        pytest.param(
            Path("some_subdir/_materialized_configs/"),
            Path("some_subdir/_materialized_configs/feature1/combined/file1.json"),
            False,
            ["yaml"],
            id="Put the materialized file in an arbitrary directory",
        ),
        pytest.param(
            None,
            Path("feature1/combined/file1.json"),
            False,
            ["os"],
            id="Put the materialized file in an arbitrary directory",
        ),
        pytest.param(
            None,
            Path("feature1/combined/file3.yaml"),
            True,
            [],
            id="Yaml file",
        ),
    ],
)
def test_materialize_file_ext_pkgs(
    config_struct: str,
    materialized_root: Path | None,
    expected_path: Path,
    is_yaml: bool,
    ext_packages: List[str],
) -> None:
    jsonnet_file_path = (
        f"{expected_path}.jsonnet" if is_yaml else "feature1/combined/file1.jsonnet"
    )

    materialize_file(
        Path(config_struct),
        Path(config_struct) / jsonnet_file_path,
        materialized_root,
        ext_packages=ext_packages,
    )

    expected_file = Path(config_struct) / expected_path
    assert expected_file.exists()
    with open(expected_file) as f:
        content = f.read()
        if is_yaml:
            assert yaml.safe_load(content) == DICT_RESULT
            with pytest.raises(json.decoder.JSONDecodeError):
                json.loads(content)
        else:
            assert json.loads(content) == DICT_RESULT


def test_materialize_file_missing_ext_pkgs(
    config_struct: str,
) -> None:
    expected_path = Path("feature1/combined/file3.yaml")
    jsonnet_file_path = f"{expected_path}.jsonnet"

    with pytest.raises(ModuleNotFoundError):
        materialize_file(
            Path(config_struct),
            Path(config_struct) / jsonnet_file_path,
            None,
            ext_packages=["definitely-missing-pkg-12345"],
        )

        expected_file = Path(config_struct) / expected_path
        assert expected_file.exists()
        with open(expected_file) as f:
            content = f.read()
            assert yaml.safe_load(content) == DICT_RESULT
            with pytest.raises(json.decoder.JSONDecodeError):
                json.loads(content)
