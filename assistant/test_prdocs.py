import os
import tempfile
from json import dumps
from pathlib import Path
from typing import Generator

import pytest

from assistant.prdocs import InstructionsMessage


@pytest.fixture
def valid_structure() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        full_path1 = Path(temp_dir) / "shared_config/kafka/generated_files"
        full_path2 = Path(temp_dir) / "shared_config/kafka/other_files"
        full_path3 = Path(temp_dir) / "shared_config/kafka/more_files"
        os.makedirs(full_path1)
        os.makedirs(full_path2)
        os.makedirs(full_path3)

        with open(full_path1 / "topic1.yaml", "w") as file:
            file.write("something")

        with open(full_path1 / "topic2.yaml", "w") as file:
            file.write("something")

        with open(full_path1 / "deploy_instructions.md", "w") as file:
            file.write("Kafka content")

        with open(full_path2 / "topic3.yaml", "w") as file:
            file.write("something")

        with open(full_path2 / "deploy_instructions.json", "w") as file:
            file.write(dumps({"ref": str(full_path1 / "deploy_instructions.md")}))

        yield temp_dir


def test_empty(valid_structure) -> None:
    message = InstructionsMessage(Path(valid_structure))
    message.add_path(
        Path(valid_structure) / "shared_config/kafka/more_files/something.yaml"
    )

    assert message.produce_message() is None


def test_full_message(valid_structure) -> None:
    message = InstructionsMessage(Path(valid_structure))
    message.add_path(
        Path(valid_structure) / "shared_config/kafka/generated_files/topic1.yaml"
    )
    message.add_path(
        Path(valid_structure) / "shared_config/kafka/generated_files/topic2.yaml"
    )

    two_files_comment = f"""# Ops Assistant

The ops repo is a large monorepo and there are multiple processes to deploy its content depending on what is changed.


Kafka content

<details>
<summary>Files changed</summary>

```
{Path(valid_structure).resolve() / "shared_config/kafka/generated_files/topic1.yaml"}{Path(valid_structure).resolve() / "shared_config/kafka/generated_files/topic2.yaml"}
```
</details>

"""
    assert message.produce_message() == two_files_comment

    message.add_path(
        Path(valid_structure) / "shared_config/kafka/other_files/topic3.yaml"
    )

    three_files_comment = f"""# Ops Assistant

The ops repo is a large monorepo and there are multiple processes to deploy its content depending on what is changed.


Kafka content

<details>
<summary>Files changed</summary>

```
{Path(valid_structure).resolve() / "shared_config/kafka/generated_files/topic1.yaml"}{Path(valid_structure).resolve() / "shared_config/kafka/generated_files/topic2.yaml"}{Path(valid_structure).resolve() / "shared_config/kafka/other_files/topic3.yaml"}
```
</details>

"""
    assert message.produce_message() == three_files_comment
