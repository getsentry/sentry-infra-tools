import json
import os
from pathlib import Path
import site
from typing import Generator

import yaml
from sentry_jsonnet import jsonnet


class JsonnetException(Exception):
    pass


def iterate_jsonnet_configs(
    root_dir: Path, exclude_dirs: list[str] = []
) -> Generator[Path, None, None]:
    """
    Iterate over all the files that can be materialized in the root
    directory
    """

    files = [
        f
        for f in root_dir.rglob("*")
        if not any(excluded in f.parts for excluded in exclude_dirs)
    ]

    for file in files:
        if not file.is_dir() and file.suffix == ".jsonnet":
            yield file


def materialize_file(
    root_dir: Path, jsonnet_file: Path, materialized_root: Path | None
) -> None:
    """
    Materialize a single jsonnet file
    Generate a json file in the same subdirectory as the jsonnet file
    """
    if materialized_root is not None and not (root_dir / materialized_root).exists():
        os.makedirs(root_dir / materialized_root)

    relative_path = jsonnet_file.absolute().relative_to(root_dir.absolute())
    materialized_root = materialized_root or Path("")
    materialized_path = root_dir / materialized_root / relative_path
    os.makedirs(materialized_path.parent, exist_ok=True)

    def import_callback(base_dirs, rel_path):
        # Add the site-packages path to the search path
        search_paths = site.getsitepackages() + base_dirs
        return jsonnet.JsonnetImportCallback(search_paths)

    try:
        content = jsonnet(
            jsonnet_file.name,
            base_dir=jsonnet_file.parent.absolute(),
            import_callback=import_callback,
        )
    except RuntimeError as e:
        raise JsonnetException() from e

    materialize_yaml = jsonnet_file.stem.endswith(".yaml")
    if materialize_yaml:
        with open(materialized_path.parent / (materialized_path.stem), "w") as f:
            f.write("# This is a generated file. Please do not edit directly.\n")
            # Include a note in the geneated files to read the README.md for
            # the group of configs we're materializing.
            if (root_dir / "README.md").exists():
                f.write(f"# See {root_dir}/README.md for more details.\n")
            f.write(yaml.dump(content))
    else:
        filename = (
            materialized_path.stem
            if materialized_path.stem.endswith(".json")
            else materialized_path.stem + ".json"
        )

        with open(materialized_path.parent / filename, "w") as f:
            f.write(json.dumps(content, indent=2) + "\n")
