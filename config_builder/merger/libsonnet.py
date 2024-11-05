from pathlib import Path
from typing import MutableSequence

from config_builder.merger import FileMerger

HEADER = """// This is an auto generated file. Do not update by hand
"""

LINE_FORMAT = "  %(file_name)s: import '../%(file_name)s.libsonnet',\n"


class LibsonnetMerger(FileMerger):
    """
    Merges libsonnet files into a single file that imports all the
    individual files.
    """

    def __init__(self) -> None:
        self.__content: MutableSequence[Path] = []

    def add_file(self, file: Path) -> None:
        if file.suffix == ".libsonnet":
            self.__content.append(file)

    def serialize_content(self) -> str:
        content = [
            LINE_FORMAT % {"file_name": file.stem} for file in sorted(self.__content)
        ]
        return HEADER + "\n{\n" + "".join(content) + "}\n"
