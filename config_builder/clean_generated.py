from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from config_builder.combined_generator import clean_all as clean_generated


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="""
        Clean up all generated and materialized files.
        """
    )
    parser.add_argument(
        "root_dir",
        type=str,
        action="store",
        help="""
            The root directory of the shared configs.
        """,
    )

    args = parser.parse_args(argv)
    materialized_dir = Path(args.root_dir)
    clean_generated(materialized_dir)


if __name__ == "__main__":
    main()
