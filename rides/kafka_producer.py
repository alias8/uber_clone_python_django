"""Ported from KafkaEventProducer.kt. Sends the bare ride id as the message value (no key, no
JSON envelope) on each of the four ride-lifecycle topics — the same wire format
`KafkaTemplate<String, String>.send(topic, rideId)` produces, and the same format the FastAPI
sibling's aiokafka producer uses.

A plain synchronous `kafka.KafkaProducer` (kafka-python-ng), cached as a process-wide singleton
by bootstrap-servers string — same reasoning as redis_client.py: this app has no event loop for
a client to get bound to, so there's no need for the FastAPI sibling's one-producer-per-loop
workaround. Cached by address rather than created once at import time so tests can point
`settings.KAFKA_BOOTSTRAP_SERVERS` at a throwaway testcontainers broker before first use.
"""

from __future__ import annotations

from django.conf import settings
from kafka import KafkaProducer

RIDE_REQUESTED_TOPIC = "ride-requested"
RIDE_ACCEPTED_TOPIC = "ride-accepted"
RIDE_COMPLETED_TOPIC = "ride-completed"
RIDE_CANCELLED_TOPIC = "ride-cancelled"

_producer: KafkaProducer | None = None
_producer_servers: str | None = None


def get_producer() -> KafkaProducer:
    global _producer, _producer_servers
    servers = settings.KAFKA_BOOTSTRAP_SERVERS
    if _producer is None or _producer_servers != servers:
        _producer = KafkaProducer(bootstrap_servers=servers)
        _producer_servers = servers
    return _producer


def _publish(topic: str, ride_id: str) -> None:
    producer = get_producer()
    producer.send(topic, ride_id.encode("utf-8"))
    producer.flush()


def publish_ride_requested(ride_id: str) -> None:
    _publish(RIDE_REQUESTED_TOPIC, ride_id)


def publish_ride_accepted(ride_id: str) -> None:
    _publish(RIDE_ACCEPTED_TOPIC, ride_id)


def publish_ride_completed(ride_id: str) -> None:
    _publish(RIDE_COMPLETED_TOPIC, ride_id)


def publish_ride_cancelled(ride_id: str) -> None:
    _publish(RIDE_CANCELLED_TOPIC, ride_id)
