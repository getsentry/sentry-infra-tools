from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Mapping, cast

from yaml import safe_load


class ContentLoader(ABC):
    """
    Abstractions that allow the config materializers to load content
    from different sources, being them directories or external
    packages.

    Instances of this class can be reused.
    """

    @abstractmethod
    def load_dict(self, file_name: str) -> Mapping[str, Any] | None:
        """
        Loads a dictionary from a a source. The file name can be an actual
        file on disk or something logical representing a file.
        `file_name` should contain the extension.

        If a non existing file is asked for, this returns None. It does not
        fail.
        """
        raise NotImplementedError


class YamlFileLoader(ContentLoader):
    """
    Loader that loads simple yaml file from a directory
    """

    def __init__(self, directory: Path) -> None:
        assert directory.exists() and directory.is_dir(), (
            f"The provided path {directory} does not exists or it is not a directory"
        )
        self.__directory = directory

    def load_dict(self, file_name: str) -> Mapping[str, Any] | None:
        if (self.__directory / file_name).exists():
            with open(self.__directory / file_name) as content:
                return cast(Mapping[str, Any], safe_load(content))

        return None
