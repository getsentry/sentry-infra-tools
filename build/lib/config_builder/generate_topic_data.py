import os
import json
from sys import stderr
from sentry_kafka_schemas import get_topic, list_topics
from pathlib import Path


def main() -> None:
    root = Path(os.path.abspath(__file__)).parent.parent.parent.parent
    output = f"{root}/shared_config/kafka/topics/generated/_generated_raw_topic_data.json"

    print("Generating raw topic data", file=stderr)
    file_path = Path(output)

    topic_data = {
        name: {"topic_creation_config": get_topic(name)["topic_creation_config"]}
        for name in list_topics()
    }

    os.makedirs(file_path.parent, exist_ok=True)
    with open(file_path, "w") as f:
        f.write(json.dumps(topic_data, indent=2) + "\n")


if __name__ == "__main__":
    main()
