"""Bridges a blocking Kafka consumer into Celery tasks — the split this project uses instead of
the FastAPI sibling's single `asyncio` consumer loop (see README.md's "Celery" section for the
full reasoning). This module's only job is reading Kafka and enqueuing one Celery task per
message; the actual ride-lifecycle logic lives in rides/tasks.py, where Celery's own retry
handling, concurrency, and worker pool apply to it for free.

Run as its own process via `manage.py consume_kafka` — it does not require Celery to be
importable in-process beyond `.delay()`, since that just publishes to the broker.
"""

from __future__ import annotations

import logging

from django.conf import settings
from kafka import KafkaConsumer

from rides import tasks
from rides.kafka_producer import (
    RIDE_ACCEPTED_TOPIC,
    RIDE_CANCELLED_TOPIC,
    RIDE_COMPLETED_TOPIC,
    RIDE_REQUESTED_TOPIC,
)

logger = logging.getLogger(__name__)

CONSUMER_GROUP_ID = "feed-fanout-group"

_TASKS = {
    RIDE_REQUESTED_TOPIC: tasks.handle_ride_requested,
    RIDE_ACCEPTED_TOPIC: tasks.handle_ride_accepted,
    RIDE_COMPLETED_TOPIC: tasks.handle_ride_completed,
    RIDE_CANCELLED_TOPIC: tasks.handle_ride_cancelled,
}


def build_consumer(bootstrap_servers: str | None = None, **extra_config: object) -> KafkaConsumer:
    return KafkaConsumer(
        RIDE_REQUESTED_TOPIC,
        RIDE_ACCEPTED_TOPIC,
        RIDE_COMPLETED_TOPIC,
        RIDE_CANCELLED_TOPIC,
        bootstrap_servers=bootstrap_servers or settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id=CONSUMER_GROUP_ID,
        auto_offset_reset="earliest",
        **extra_config,
    )


def consume_forever(consumer: KafkaConsumer | None = None) -> None:
    """Iterates the consumer once (a plain `for` loop over it, which by default blocks
    forever waiting for the next message — the process-level `manage.py consume_kafka` never
    expects this to return). A consumer built with a finite `consumer_timeout_ms` instead raises
    StopIteration once idle, ending the loop early — the one true end-to-end test in
    tests/test_dispatch.py relies on exactly that to poll a stoppable background thread."""
    consumer = consumer if consumer is not None else build_consumer()
    for message in consumer:
        ride_id = message.value.decode("utf-8")
        task = _TASKS.get(message.topic)
        if task is None:
            continue
        try:
            task.delay(ride_id)
        except Exception:
            logger.exception("Failed enqueuing %s for ride %s", message.topic, ride_id)
