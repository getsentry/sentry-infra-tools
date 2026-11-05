import os
import tempfile
from json import dumps, loads
from pathlib import Path
from typing import Callable, Generator

import pytest

from config_builder.combined_generator import (
    CONFIG_GENERATOR_SETTINGS,
    Outcome,
    clean_all,
    combine_and_write,
    combine_files,
    generate_all_files,
    iterate_config_directories,
    iterate_roots_and_regions,
    regional_dir_name,
    validate_all_files,
)
from config_builder.json_schema_validator import ValidationException
from config_builder.merger import FileMerger
from config_builder.merger.libsonnet import LibsonnetMerger
from config_builder.merger.yamljson import YamlMerger
from config_builder.loaders import YamlFileLoader

EXPECTED_LIBSONNET_CONTENT = """// This is an auto generated file. Do not update by hand

{
  file1: import '../file1.libsonnet',
  file2: import '../file2.libsonnet',
}
"""

EXPECTED_JSON_CONTENT = {
    "yaml_file": {
        "keyy1": 1,
        "keyy2": 2,
    },
    "json_file": {
        "keyj1": 1,
        "keyj2": 2,
    },
}

YAML_VALUE = """keyy1: 1
keyy2: 2
"""

JSON_VALUE = """{
  "keyj1": 1,
  "keyj2": 2,
}
"""


@pytest.fixture
def valid_structure() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        feature1 = Path(temp_dir) / "feature1"
        os.makedirs(feature1 / "generated")
        with open(feature1 / CONFIG_GENERATOR_SETTINGS, "w") as f:
            f.write("{}")
        with open(feature1 / "yaml_file.yaml", "w") as f:
            f.write(YAML_VALUE)
        with open(feature1 / "json_file.json", "w") as f:
            f.write(JSON_VALUE)
        open(feature1 / "file1.libsonnet", "w").close()
        open(feature1 / "file2.libsonnet", "w").close()

        feature2 = Path(temp_dir) / "feature2"
        os.makedirs(feature2)
        with open(feature2 / CONFIG_GENERATOR_SETTINGS, "w") as f:
            f.write("{}")

        feature2_us = feature2 / "regional_overrides" / "us"
        os.makedirs(feature2_us / "generated")
        open(feature2_us / "file1.libsonnet", "w").close()
        open(feature2_us / "file2.libsonnet", "w").close()

        feature2_s4s = feature2 / "regional_overrides" / "s4s"
        os.makedirs(feature2_s4s)
        open(feature2_s4s / "file1.libsonnet", "w").close()
        open(feature2_s4s / "file2.libsonnet", "w").close()

        # Add a file that is not a region to ensure the iteration
        # works. This can be the .DS_Store file.
        open(feature2 / "regional_overrides" / "a_nonsensical_file", "w").close()

        os.makedirs(Path(temp_dir) / "unrelated_directory")

        yield temp_dir


def test_generated_libsonnet_file_content(valid_structure: str) -> None:
    generated_file = combine_files(
        LibsonnetMerger(),
        Path(valid_structure) / "feature1",
    )
    assert EXPECTED_LIBSONNET_CONTENT == generated_file


def test_generated_json_content(valid_structure: str) -> None:
    generated_file = combine_files(
        YamlMerger(
            CONFIG_GENERATOR_SETTINGS,
            YamlFileLoader(Path(valid_structure) / "feature1"),
        ),
        Path(valid_structure) / "feature1",
    )
    assert EXPECTED_JSON_CONTENT == loads(generated_file)


def test_iteration(valid_structure: str) -> None:
    valid_directories = sorted(iterate_config_directories(Path(valid_structure)))
    assert valid_directories == [
        Path(valid_structure) / "feature1",
        Path(valid_structure) / "feature2",
    ]

    valid_directories_2 = set(iterate_roots_and_regions(Path(valid_structure)))
    assert valid_directories_2 == {
        (Path(valid_structure) / "feature1", False),
        (Path(valid_structure) / "feature2", False),
        (Path(valid_structure) / "feature2" / "regional_overrides" / "s4s", True),
        (Path(valid_structure) / "feature2" / "regional_overrides" / "us", True),
    }


@pytest.mark.parametrize(
    "merger, file_name",
    [
        pytest.param(
            LibsonnetMerger,
            "_generated.libsonnet",
            id="Combining libsonnet files",
        ),
        pytest.param(
            YamlMerger,
            "_generated.json",
            id="Combining yaml files",
        ),
    ],
)
def test_combine_all(
    valid_structure: str,
    merger: Callable[[], FileMerger],
    file_name: str,
) -> None:
    def build_merger() -> FileMerger:
        if merger == LibsonnetMerger:
            return LibsonnetMerger()
        else:
            return YamlMerger(
                CONFIG_GENERATOR_SETTINGS,
                YamlFileLoader(Path(valid_structure) / "feature1"),
            )

    generated_file = Path(valid_structure) / "feature1" / "generated" / file_name
    assert not generated_file.exists(), "The file already exists"

    ret, _ = combine_and_write(
        build_merger(),
        Path(valid_structure) / "feature1",
        generated_file.name,
    )
    assert ret == Outcome.NEW
    assert generated_file.exists(), "The file does not exist"

    ret, _ = combine_and_write(
        build_merger(),
        Path(valid_structure) / "feature1",
        generated_file.name,
    )
    assert ret == Outcome.UNCHANGED

    open(Path(valid_structure) / "feature1" / "file3.libsonnet", "w").close()
    open(Path(valid_structure) / "feature1" / "file3.json", "w").close()
    ret, _ = combine_and_write(
        build_merger(),
        Path(valid_structure) / "feature1",
        generated_file.name,
    )
    assert ret == Outcome.UPDATED


def test_cleanup(valid_structure: str) -> None:
    path_structure = Path(valid_structure)
    feature1 = path_structure / "feature1" / "generated"
    open(feature1 / "generated1", "w").close()
    open(feature1 / "generated2", "w").close()

    feature2_s4s = (
        path_structure / "feature2" / "regional_overrides" / "s4s" / "generated"
    )
    os.makedirs(feature2_s4s)
    open(feature2_s4s / "generated1", "w").close()

    clean_all(path_structure)

    assert not feature1.exists()
    assert not (feature1 / "generated1").exists()
    assert not (feature1 / "generated2").exists()
    assert not feature2_s4s.exists()
    assert not (feature2_s4s / "generated1").exists()


@pytest.fixture
def custom_regional_dir(tmp_path: Path) -> Path:
    """
    `dir1` renames its regional dir to `custom_regions`, `dir2` keeps the
    default `regional_overrides`.
    """
    root = tmp_path / "shared_config"
    dir1 = root / "dir1"
    os.makedirs(dir1 / "custom_regions" / "region1")
    # A leftover default dir must be ignored once the root renamed it.
    os.makedirs(dir1 / "regional_overrides" / "stale")
    (dir1 / CONFIG_GENERATOR_SETTINGS).write_text(
        dumps({"regional_dir": "custom_regions"})
    )

    dir2 = root / "dir2"
    os.makedirs(dir2 / "regional_overrides" / "region2")
    # A JSON object spread over lines must parse as the default.
    (dir2 / CONFIG_GENERATOR_SETTINGS).write_text("{\n}")
    return root


def test_iteration_custom_regional_dir(custom_regional_dir: Path) -> None:
    dir1 = custom_regional_dir / "dir1"
    dir2 = custom_regional_dir / "dir2"
    assert set(iterate_roots_and_regions(custom_regional_dir)) == {
        (dir1, False),
        (dir1 / "custom_regions" / "region1", True),
        (dir2, False),
        (dir2 / "regional_overrides" / "region2", True),
    }


@pytest.mark.parametrize(
    "settings, expected",
    [
        ("", "regional_overrides"),
        ("{}", "regional_overrides"),
        ('{"regional_dir": "custom_regions"}', "custom_regions"),
        ('{"regional_dir": ""}', None),
        ('{"regional_dir": 1}', None),
        ("[]", None),
        ("not json", None),
    ],
)
def test_regional_dir_name(tmp_path: Path, settings: str, expected: str | None) -> None:
    (tmp_path / CONFIG_GENERATOR_SETTINGS).write_text(settings)
    if expected is None:
        with pytest.raises(ValueError):
            regional_dir_name(tmp_path)
    else:
        assert regional_dir_name(tmp_path) == expected


def test_generate_custom_regional_dir(custom_regional_dir: Path) -> None:
    region1 = custom_regional_dir / "dir1" / "custom_regions" / "region1"
    (region1 / "new-item.yaml").write_text("enabled: true\n")

    generate_all_files(custom_regional_dir)

    generated = loads((region1 / "generated" / "_generated.json").read_text())
    assert generated == {"new-item": {"enabled": True}}


def test_validate_custom_regional_dir(
    custom_regional_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared_config = custom_regional_dir
    os.makedirs(shared_config / "schemas")
    (shared_config / "schemas" / "item.schema.json").write_text(
        dumps({"type": "object", "properties": {"count": {"type": "integer"}}})
    )
    legend = shared_config / "legend.json"
    legend.write_text(dumps({"dir1/custom_regions/*/*.yaml": "item.schema.json"}))
    item = shared_config / "dir1" / "custom_regions" / "region1" / "item.yaml"
    # JsonSchemaValidator resolves globs and schemas relative to ./shared_config
    monkeypatch.chdir(shared_config.parent)

    item.write_text("count: 1\n")
    validate_all_files(Path("shared_config"), legend)

    item.write_text("count: not-an-int\n")
    with pytest.raises(ValidationException):
        validate_all_files(Path("shared_config"), legend)
