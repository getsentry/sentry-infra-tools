import os
import tempfile
from json import dumps, loads
from pathlib import Path
from typing import Generator
from config_builder.loaders import YamlFileLoader
import pytest

from config_builder.merger.yamljson import YamlMerger

CONFIG_GENERATOR_SETTINGS = "_config_generator.json"


@pytest.fixture
def files_structure() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "base_dir"
        os.makedirs(base_dir)
        with open(base_dir / "file1.yaml", "w") as f:
            f.write(dumps({"base": 1, "params": {"test": 1}}))
        with open(base_dir / "file2.json", "w") as f:
            f.write(dumps({"base": 2, "params": {"test": 2}}))
        with open(base_dir / "file3.json", "w") as f:
            f.write(dumps({"base": 3, "params": {"test": 3}}))

        feature1 = Path(temp_dir) / "feature1"
        os.makedirs(feature1)
        with open(feature1 / CONFIG_GENERATOR_SETTINGS, "w") as f:
            f.write(dumps({"override_from": "../base_dir"}))
        with open(feature1 / "file1.yaml", "w") as f:
            f.write(dumps({"params": {"test2": 1}}))
        with open(feature1 / "file2.json", "w") as f:
            f.write(dumps({"params": {"test2": 2}}))

        region_us = Path(temp_dir) / "feature1" / "regional_overrides" / "us"
        os.makedirs(region_us)
        with open(region_us / "file1.yaml", "w") as f:
            f.write(dumps({"params": {"test3": 1}}))
        with open(region_us / "file4.json", "w") as f:
            f.write(dumps({"params": {"test3": 2}}))
        with open(region_us / "unrelated.txt", "w") as f:
            f.write("unrelated")

        yield temp_dir


def test_merger(files_structure: str) -> None:
    path = Path(files_structure)
    merger = YamlMerger(
        CONFIG_GENERATOR_SETTINGS,
        YamlFileLoader(path / "feature1" / "regional_overrides" / "us"),
    )
    merger.add_file(path / "feature1" / "regional_overrides" / "us" / "file1.yaml")
    merger.add_file(path / "feature1" / "regional_overrides" / "us" / "file4.json")
    merger.add_file(path / "feature1" / "regional_overrides" / "us" / "unrelated.txt")

    ret = merger.serialize_content()
    assert loads(ret) == {
        "file1": {
            "params": {
                "test3": 1,
            },
        },
        "file4": {
            "params": {
                "test3": 2,
            },
        },
    }
