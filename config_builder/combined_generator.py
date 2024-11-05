import os
from enum import Enum
from pathlib import Path
from shutil import rmtree
from typing import Generator, Sequence, Tuple

from config_builder.loaders import YamlFileLoader
from config_builder.merger import FileMerger
from config_builder.merger.libsonnet import LibsonnetMerger
from config_builder.merger.yamljson import YamlMerger

from config_builder.json_schema_validator import JsonSchemaValidator

DEFAULT_LIBSONNET_OUTPUT_FILE_NAME = "_generated.libsonnet"
DEFAULT_JSON_OUTPUT_FILE_NAME = "_generated.json"
CONFIG_GENERATOR_SETTINGS = "_config_generator.json"
GENERATED_DIR = "generated"


class Outcome(Enum):
    NEW = "new"
    UNCHANGED = "unchanged"
    UPDATED = "updated"


def combined_file_name(root_directory: Path, output_file_name: str) -> Path:
    return root_directory / GENERATED_DIR / output_file_name


def combine_files(
    merger: FileMerger,
    root_directory: Path,
) -> str:
    """
    Combine all the files in `root_directory` using the FileMerger
    provided.
    """
    assert root_directory.is_dir(), "Root directory must be a directory"

    input_files = [x for x in Path(root_directory).iterdir() if x.is_file()]
    for f in input_files:
        merger.add_file(f)

    return merger.serialize_content()


def validate_schema(validator: JsonSchemaValidator, root_directory: Path) -> None:
    input_files = [x for x in Path(root_directory).iterdir() if x.is_file()]
    for f in input_files:
        if str(f).endswith(("yaml", "yml")):
            validator.validate_yaml(f)


def combine_and_write(
    merger: FileMerger,
    root_directory: Path,
    output_file_name: str,
) -> Tuple[Outcome, Path]:
    """
    Generates a unified file from the all the yaml/json/libsonnet files
    contained in a directory.

    A FileMerger is provided that is able to merge files of different types.
    There are two implementations: one for yaml/json and the other for
    libsonnet files.

    The output is written in a predefined location `root_dir/generated` if
    the generated file is in a ny way different from the pre-existing
    combined import file in such directory.

    It return whether the file was unchanged, new or updated.
    """
    generated_file = combine_files(merger, root_directory)
    if not (root_directory / GENERATED_DIR).exists():
        os.makedirs(root_directory / GENERATED_DIR)

    generated_file_path = combined_file_name(root_directory, output_file_name)
    if not generated_file_path.exists():
        outcome = Outcome.NEW
    else:
        with open(generated_file_path) as f:
            existing_content = f.read()
            outcome = (
                Outcome.UNCHANGED
                if existing_content == generated_file
                else Outcome.UPDATED
            )

    if outcome != Outcome.UNCHANGED:
        with open(generated_file_path, "w") as f:
            f.write(generated_file)

    return (outcome, generated_file_path)


def iterate_config_directories(root_dir: Path) -> Generator[Path, None, None]:
    """
    Finds all the subdirectories of `root_dir` that are suitable for jsonnet
    import file generation.

    Such directories contain:
    - a `_config_generator.json` file to mark it
    - a `generated` subdirectory where the generated file will be (if it doesn't exsit, it'll get created in the upcoming combine_and_write step)
    """
    for item in root_dir.rglob("*"):
        if item.is_dir() and (item / CONFIG_GENERATOR_SETTINGS).exists():
            yield item


def iterate_roots_and_regions(
    root_dir: Path,
) -> Generator[Tuple[Path, bool | None], None, None]:
    """
    Extends `iterate_config_directories` by managing properly region
    subdirectories.

    If a root_directory (a directory with containing a CONFIG_GENERATOR_SETTINGS
    file) contains a `regional_overrides` subdirectory, the root_directory
    is also returned, as well as each dir inside 'regional_overrides'.
    """

    for config_root in iterate_config_directories(root_dir):
        yield (config_root, False)
        regions_path = Path(config_root / "regional_overrides")
        if regions_path.exists() and regions_path.is_dir():
            for region_root in regions_path.iterdir():
                if region_root.is_dir():
                    yield (region_root, True)


def validate_all_files(root_directory: Path) -> None:
    validator = JsonSchemaValidator()
    for config_root, _ in iterate_roots_and_regions(root_directory):
        input_files = [
            x
            for x in Path(config_root).iterdir()
            if x.is_file() and str(x).endswith(("yaml", "yml"))
        ]
        for f in input_files:
            validator.validate_yaml(f)


def generate_all_files(root_directory: Path) -> Sequence[Tuple[Outcome, Path]]:
    """
    Scan all the directories under `root_directory` to find directories
    that has to be treated as root configs, thus requiring us to combine
    multiple files into one to be imported by a jsonnet script.
    """
    ret = []

    for config_root, _ in iterate_roots_and_regions(root_directory):
        outcome, output_file_name = combine_and_write(
            LibsonnetMerger(), config_root, DEFAULT_LIBSONNET_OUTPUT_FILE_NAME
        )
        ret.append((outcome, output_file_name))

        content_loader = YamlFileLoader(config_root)
        outcome, output_file_name = combine_and_write(
            YamlMerger(CONFIG_GENERATOR_SETTINGS, content_loader),
            config_root,
            DEFAULT_JSON_OUTPUT_FILE_NAME,
        )
        ret.append((outcome, output_file_name))
    return ret


def clean_all(root_directory: Path) -> None:
    """
    Deletes all the generated files.
    """
    for config_root, _ in iterate_roots_and_regions(root_directory):
        if (config_root / GENERATED_DIR).exists():
            assert (config_root / GENERATED_DIR).is_dir()
            rmtree(config_root / GENERATED_DIR)
