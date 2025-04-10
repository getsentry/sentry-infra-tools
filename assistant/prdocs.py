import argparse
import sys
from dataclasses import dataclass
from json import loads
from pathlib import Path
from typing import MutableMapping, MutableSet, Sequence

COMMENT_TEMPLATE = """# Ops Assistant

The ops repo is a large monorepo and there are multiple processes to deploy its content depending on what is changed.

{content}
"""

INSTRUCTION_TEMPLATE = """
{content}

<details>
<summary>Files changed</summary>

```
{files}
```
</details>
"""

INSTRUCTIONS_FILE = "deploy_instructions.md"
INSTRUCTIONS_CONF_FILE = "deploy_instructions.json"


@dataclass
class Instruction:
    paths: MutableSet[Path]
    text: str


class InstructionsMessage:
    """
    Accumulates instructions to add to a comment in a PR.

    Given a list of paths (touched by the PR) this scans all its
    parent directories looking for instructions on how to deploy
    the change contained in the file.

    It then merges all instructions into one message.
    """

    def __init__(self, root: Path) -> None:
        # The root directory where the repo is.
        self.__root = root.resolve()
        self.__instructions: MutableMapping[Path, Instruction] = {}

    def __fetch_instruction(self, path: Path, updated_file: Path) -> None:
        """
        Looks for a `deploy_instructions.md` file to add to the comment.

        Many directories should share the same instructions (think about
        the k8s services deployable with GoCD). For that there is the
        `deploy_instructions.json` file that contains a reference to a
        markdown instructions file.

        This file will also be used to provide more sophisticated
        directives on how to compose the message.
        """
        config_path = path / INSTRUCTIONS_CONF_FILE
        instructions_path = path / INSTRUCTIONS_FILE

        if config_path.exists() and config_path.is_file():
            conf = loads(config_path.read_text())
            assert isinstance(conf, MutableMapping), (
                f"Invalid content of {INSTRUCTIONS_CONF_FILE}"
            )
            ref_path = conf.get("ref")
            if ref_path:
                instructions_path = path / ref_path

        if instructions_path.exists() and instructions_path.is_file():
            content = instructions_path.read_text()

            instruction_obj = self.__instructions.get(instructions_path.resolve())
            if instruction_obj is None:
                instruction_obj = Instruction({updated_file}, content)
                self.__instructions[instructions_path] = instruction_obj
            else:
                instruction_obj.paths.add(updated_file)

    def add_path(self, path: Path) -> None:
        path = path.resolve()
        if self.__root not in path.parents:
            return

        updated_file = path
        if not path.is_dir():
            path = path.parent

        while path != self.__root:
            self.__fetch_instruction(path, updated_file)
            path = path.parent

    def produce_message(self) -> str | None:
        if not self.__instructions:
            return None

        instructions_content = ""
        for instruction in self.__instructions.values():
            files = "".join(sorted(str(p) for p in instruction.paths))
            instructions_content += INSTRUCTION_TEMPLATE.format(
                **{"files": files, "content": instruction.text}
            )

        return COMMENT_TEMPLATE.format(**{"content": instructions_content})


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="""
            Collects all the relevant documentation to deploy the changes
            in a pr given the list of involved files. It, then, produces a single
            message to attach to the PR itself.
        """
    )
    parser.add_argument(
        "-r",
        "--root",
        action="store",
        help="""
            The root directory of all changes
        """,
    )

    args = parser.parse_args()

    root = args.root
    assert root is not None and Path(root).exists() and Path(root).is_dir(), (
        "The root path does not exists or is not a directory."
    )
    message = InstructionsMessage(Path(root))

    for path in sys.stdin:
        print(f"Processing {path}", file=sys.stderr)
        message.add_path(Path(path))

    response = message.produce_message()
    if response is not None:
        print(response)


if __name__ == "__main__":
    main()
