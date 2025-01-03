import importlib
import json
import os
from pathlib import Path
from typing import Generator, Union

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


def pkg_import_callback(
    module: Path, ext_packages: list[str]
) -> Union[str, bytes, None]:
    if module.is_file():
        content = module.read_text()
    elif module.exists():
        raise RuntimeError("Attempted to import a directory")
    else:  # cache the import-path miss
        content = None
        module = module.resolve()
        rel_path = str(module).strip("/").split("/")
        ext_package = rel_path[0]
        if ext_package in ext_packages:
            package = importlib.resources.files(ext_package)
            # Join all path components after the package name
            resource_path = "/".join(rel_path[1:])
            # Use joinpath with the full resource path
            with package.joinpath(resource_path).open("r") as f:
                content = f.read()
            return content
    return content


def materialize_file(
    root_dir: Path,
    jsonnet_file: Path,
    materialized_root: Path | None,
    ext_packages: list[str] = [],
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

    for ext_package in ext_packages:
        try:
            importlib.import_module(ext_package)
        except ImportError:
            raise ModuleNotFoundError(f"Package '{ext_package}' not found")

    def _import_callback(module: Path) -> Union[str, bytes, None]:
        return pkg_import_callback(module, ext_packages)

    try:
        content = jsonnet(
            jsonnet_file.name,
            base_dir=jsonnet_file.parent.absolute(),
            import_callback=_import_callback,
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
