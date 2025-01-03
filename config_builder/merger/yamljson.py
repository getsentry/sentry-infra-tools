from json import dumps
from pathlib import Path
from typing import Any, MutableMapping

from config_builder.loaders import ContentLoader
from config_builder.merger import FileMerger


class YamlMerger(FileMerger):
    """
    Merges a list of yaml or json files in a dictionary returned in json
    format (which is also valid yaml).

    The output dictionary has the file names as keys and the content as its
    value.
    """

    def __init__(self, config_file_name: str, loader: ContentLoader) -> None:
        self.__config_file_name = config_file_name
        self.__content: MutableMapping[str, Any] = {}
        self.__loader = loader

    def add_file(self, file: Path) -> None:
        """
        Adds a file to this merger. It also apply all the needed overrides.
        """

        if (
            file.suffix not in {".json", ".yaml", ".yml"}
            or file.name == self.__config_file_name
        ):
            return

        file_name = file.name
        content = self.__loader.load_dict(file_name)
        self.__content[file.stem] = content or {}

    def serialize_content(self) -> str:
        return dumps(self.__content, indent=2, sort_keys=True)
