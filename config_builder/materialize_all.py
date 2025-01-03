from __future__ import annotations

import argparse
import sys
from pathlib import Path
from sys import stderr
from typing import Sequence

from config_builder.combined_generator import (
    Outcome,
    generate_all_files,
    validate_all_files,
)
from config_builder.json_schema_validator import ValidationException
from config_builder.materializer import (
    JsonnetException,
    iterate_jsonnet_configs,
    materialize_file,
)

GREEN = "\033[92m"
RED = "\033[31m"
RESET = "\033[0m"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="""
            Materializes all the jsonnet files in the shared config directories.
            It also generates the combined libsonnet files.
        """
    )
    parser.add_argument(
        "-c",
        "--combine-sources",
        action="store_true",
        help="""
            Runs the individual source files combination upfront.
        """,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="""
            Print the full stack trace if a validation error or Jsonnet error is found.
        """,
    )
    parser.add_argument(
        "-o",
        "--output-directory",
        action="store",
        help="""
            The root directory for the materialized files.
            This is relative to the root_dir for the source files provided
            below.
            If not present it defaults to the same as `root_dir`. Files would be
            materialized in place.
        """,
    )
    parser.add_argument(
        "-e",
        "--exclude-dirs",
        nargs="+",
        default=[],
        help="""
            A list of directories to exclude. Each directory is specified by
            its name. Multiple directories can be specified.
        """,
    )
    parser.add_argument(
        "--root-dir",
        type=str,
        action="store",
        help="""
            The root directory of the shared configs.
        """,
    )
    parser.add_argument(
        "-p",
        "--ext-packages",
        action="append",
        default=[],
        help="""
            Name of external package to add to jsonnet import paths.
        """,
    )

    args = parser.parse_args(argv)
    if args.combine_sources:
        print("Validating yaml file schemas", file=stderr)
        try:
            validate_all_files(Path(args.root_dir))
        except ValidationException as e:
            print(
                f"{RED}Schema validation failed in {e.file}, please refer to the schema file {e.schema}{RESET}"
            )
            print(e.__cause__)
            if args.verbose:
                raise
            sys.exit(-1)

        print("Combining individual source files", file=stderr)
        combined_files = generate_all_files(Path(args.root_dir))
        for outcome, file_name in combined_files:
            if outcome != Outcome.UNCHANGED:
                print(f"[{GREEN}UPDATED{RESET}] {file_name}")
    else:
        print("Skipping individual files combination", file=stderr)

    print("Materializing jsonnet files", file=stderr)
    materialized_root = Path(args.output_directory) if args.output_directory else None
    for file in iterate_jsonnet_configs(Path(args.root_dir), args.exclude_dirs):
        try:
            materialize_file(
                Path(args.root_dir), file, materialized_root, args.ext_packages
            )
        except JsonnetException as e:
            print(f"{RED}Jsonnet Error occurred while materializing {file}{RESET}")
            print(f"{e.__cause__}")
            if args.verbose:
                raise
            sys.exit(-2)
        print(f"[{GREEN}GENERATED{RESET}] {file}")

    sys.exit(0)


if __name__ == "__main__":
    main()
