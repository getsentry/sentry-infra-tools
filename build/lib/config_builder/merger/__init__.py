from abc import ABC
from abc import abstractmethod
from pathlib import Path


class FileMerger(ABC):
    """
    A File Merger is provided files one at a time, typically
    iterating over all the files in a directory, it knows
    how to combine them into a file in a specific format (json,
    jsonnet, yaml, etc.).

    Example:
    ```
    file1.json
    {
       a: 1
    }
    file2.json
    {
       a: 2
    }
    ```

    If we add the two files above to the merger and call
    `serialize_content` we get:
    ```
    {
       file1: {
          a: 1
       },
       file2: {
          a: 2
       }
    }
    ```
    """

    @abstractmethod
    def add_file(self, file: Path) -> None:
        """
        Adds a file to the merged output.
        """
        raise NotImplementedError

    @abstractmethod
    def serialize_content(self) -> str:
        """
        Generates the combined file and returns it serialized
        as a string.
        """
        raise NotImplementedError
