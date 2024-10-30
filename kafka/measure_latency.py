from __future__ import annotations

import time
import signal
from math import floor
from typing import Sequence, Any
import sys
import argparse
from confluent_kafka import (
    Consumer,
    TopicPartition,
    ConsumerGroupTopicPartitions,
)
from confluent_kafka.admin import AdminClient


shutdown = False


def bisect_offsets(
    consumer: Consumer,
    start_timestamp: int,
    end_timestamp: int,
    target_offset: int,
    partition: TopicPartition,
) -> int:
    midpoint = start_timestamp + int((end_timestamp - start_timestamp) / 2)

    if midpoint - start_timestamp <= 1:
        return midpoint

    # This gives us the earliest offset for the timestamp provided in milliseconds
    offsets = consumer.offsets_for_times(
        [
            # Timestamp in milliseconds
            TopicPartition(
                partition.topic, partition.partition, offset=midpoint * 1000
            ),
        ]
    )
    # -1 means that the timestamp provided is higher than the timestamp of the
    # latest offset
    if offsets[0].offset > target_offset or offsets[0].offset == -1:
        return bisect_offsets(
            consumer, start_timestamp, midpoint, target_offset, partition
        )
    elif offsets[0].offset < target_offset:
        return bisect_offsets(
            consumer, midpoint, end_timestamp, target_offset, partition
        )
    else:
        return midpoint


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="""
            Measure the latency of a consumer group over a kafka topic
            by bisecting the offsets till it finds the timestamp for the
            latest offset and the offset of the last committed offset.
        """
    )
    parser.add_argument(
        "--bootstrap-server",
        type=str,
        action="store",
        help="""
            Kafka bootstrap server.
        """,
    )
    parser.add_argument(
        "--topic",
        type=str,
        action="store",
        help="""
            Kafka topic
        """,
    )
    parser.add_argument(
        "--consumer-group",
        type=str,
        action="store",
        help="""
            Kafka consumer group
        """,
    )

    args = parser.parse_args(argv)

    consumer = Consumer(
        {
            "bootstrap.servers": args.bootstrap_server,
            "group.id": args.consumer_group,
            # "auto.offset.reset": "earliest",
        }
    )

    admin_client = AdminClient({"bootstrap.servers": args.bootstrap_server})

    def handler(signum: int, frame: Any) -> None:
        global shutdown
        shutdown = True
        consumer.close()
        print("Shutting down")

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    while not shutdown:
        start = time.time()
        next = floor(start) + 1

        # This gives the latest and earliest offset.
        watermark = consumer.get_watermark_offsets(TopicPartition(args.topic, 0))
        # This gives us the committed offset
        group_offsets = admin_client.list_consumer_group_offsets(
            [
                ConsumerGroupTopicPartitions(
                    args.consumer_group, [TopicPartition(args.topic, 0)]
                )
            ]
        )

        for _, future in group_offsets.items():
            group_topic_partition = future.result()
            for topic_partition in group_topic_partition.topic_partitions:
                latest_offset = watermark[1]
                committed_offset = topic_partition.offset

                committed_ts = bisect_offsets(
                    consumer,
                    int(time.time() - (24 * 3600)),
                    int(time.time()),
                    committed_offset,
                    TopicPartition(args.topic, 0),
                )
                latest_ts = bisect_offsets(
                    consumer,
                    int(time.time() - (24 * 3600)),
                    int(time.time()),
                    latest_offset,
                    TopicPartition(args.topic, 0),
                )

                print(
                    f"{start}\tearliest offset\t{watermark[0]}\tlatest offset\t{latest_offset}\t- {latest_ts}\tcommitted offset\t{committed_offset}\t- {committed_ts}\tLatency\t{latest_ts - committed_ts}"
                )

        time.sleep(max(0, next - start))

    sys.exit(0)


if __name__ == "__main__":
    main()
