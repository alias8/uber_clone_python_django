"""Ported from DispatchService.kt (fan-out) and StaleRideRetryJob.kt (retry), plus the parts of
KafkaConsumer.kt's ride-requested/ride-accepted handlers that have real (non-SSE) behavior — see
rides/dispatch.py and rides/tasks.py's module docstrings for what's deliberately deferred to M5.

Every test here calls a task as a plain function (e.g. `tasks.handle_ride_requested(ride_id)`)
except the last one, which is the one true end-to-end test proving the actual
Kafka -> kafka_bridge -> Celery -> worker wiring works, via a real broker and a real
`celery_worker` fixture (see tests/conftest.py's celery_app/celery_config overrides)."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import redis
from django.conf import settings
from kafka import KafkaConsumer as RawKafkaConsumer
from rest_framework.test import APIClient

from rides import kafka_bridge, kafka_producer, tasks
from rides.dispatch import (
    DISPATCHED_KEY_PREFIX,
    DISPATCHED_TTL_SECONDS,
    RIDE_OFFER_CHANNEL_PREFIX,
    fanout_to_nearby_drivers,
)
from rides.domain import Ride
from rides.redis_client import get_client
from rides.state import ride_repository
from rides.tasks import RETRY_CUTOFF
from tests.conftest import register_and_login, register_driver

RIDE_REQUEST = {
    "pickup_lat": 40.7128,
    "pickup_lng": -74.0060,
    "dropoff_lat": 40.7300,
    "dropoff_lng": -74.0000,
}


def test_fanout_writes_dispatched_set_with_ttl_and_publishes_offer(client: APIClient) -> None:
    register_driver(client, "alice")
    client.post("/driver/mode/on", {"lat": 40.7128, "lng": -74.0060}, format="json")
    alice_id = client.get("/auth/me").data["user_id"]

    redis_client = get_client()
    # redis-py's pubsub()/PubSub.subscribe() are entirely untyped (no stub signature at all,
    # not just an imprecise one) — same stub-quality gap already documented in claude.md for
    # get()/incr() elsewhere in this project, just severe enough here to need `type: ignore`
    # rather than a plain `cast()`.
    pubsub = cast("redis.client.PubSub", redis_client.pubsub())  # type: ignore[no-untyped-call]
    pubsub.subscribe(f"{RIDE_OFFER_CHANNEL_PREFIX}{alice_id}")  # type: ignore[no-untyped-call]
    # subscribe() sends SUBSCRIBE without reading the response (redis-py deliberately leaves it
    # for get_message(), so it doesn't risk swallowing a real message) — drain that confirmation
    # now so the next get_message() call waits for the actual published offer.
    confirmation = pubsub.get_message(timeout=2)
    assert confirmation is not None and confirmation["type"] == "subscribe"

    ride = Ride(
        rider_id="rider-x",
        pickup_lat=RIDE_REQUEST["pickup_lat"],
        pickup_lng=RIDE_REQUEST["pickup_lng"],
        dropoff_lat=RIDE_REQUEST["dropoff_lat"],
        dropoff_lng=RIDE_REQUEST["dropoff_lng"],
        estimated_fare=Decimal("9.99"),
    )
    fanout_to_nearby_drivers(ride)

    dispatched_key = f"{DISPATCHED_KEY_PREFIX}{ride.id}"
    assert redis_client.smembers(dispatched_key) == {alice_id}
    ttl = cast("int", redis_client.ttl(dispatched_key))
    assert 0 < ttl <= DISPATCHED_TTL_SECONDS

    message = pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    assert message is not None
    payload = json.loads(message["data"])
    assert payload == {
        "rideId": ride.id,
        "pickupLat": ride.pickup_lat,
        "pickupLng": ride.pickup_lng,
        "dropoffLat": ride.dropoff_lat,
        "dropoffLng": ride.dropoff_lng,
        "estimatedFare": 9.99,
        "etaMinutes": payload["etaMinutes"],
    }
    assert payload["etaMinutes"] >= 1
    pubsub.close()


def test_fanout_with_no_nearby_drivers_writes_nothing() -> None:
    ride = Ride(
        rider_id="rider-x",
        pickup_lat=RIDE_REQUEST["pickup_lat"],
        pickup_lng=RIDE_REQUEST["pickup_lng"],
        dropoff_lat=RIDE_REQUEST["dropoff_lat"],
        dropoff_lng=RIDE_REQUEST["dropoff_lng"],
    )
    fanout_to_nearby_drivers(ride)
    assert get_client().exists(f"{DISPATCHED_KEY_PREFIX}{ride.id}") == 0


def test_handle_ride_requested_dispatches_when_still_requested(client: APIClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", RIDE_REQUEST, format="json").data["id"]

    driver = APIClient()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", {"lat": 40.7128, "lng": -74.0060}, format="json")
    driver_id = driver.get("/auth/me").data["user_id"]

    tasks.handle_ride_requested(ride_id)

    assert get_client().smembers(f"{DISPATCHED_KEY_PREFIX}{ride_id}") == {driver_id}


def test_handle_ride_requested_skips_a_ride_that_is_no_longer_requested(client: APIClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", RIDE_REQUEST, format="json").data["id"]
    client.post(f"/rides/{ride_id}/cancel")

    tasks.handle_ride_requested(ride_id)

    assert get_client().exists(f"{DISPATCHED_KEY_PREFIX}{ride_id}") == 0


def test_handle_ride_accepted_clears_the_dispatched_set(client: APIClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", RIDE_REQUEST, format="json").data["id"]

    driver = APIClient()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", {"lat": 40.7128, "lng": -74.0060}, format="json")

    tasks.handle_ride_requested(ride_id)
    dispatched_key = f"{DISPATCHED_KEY_PREFIX}{ride_id}"
    assert get_client().exists(dispatched_key) == 1

    tasks.handle_ride_accepted(ride_id)
    assert get_client().exists(dispatched_key) == 0


def test_retry_stale_rides_republishes_an_old_requested_ride(client: APIClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", RIDE_REQUEST, format="json").data["id"]

    ride = ride_repository.find_by_id(ride_id)
    assert ride is not None
    backdated_at = datetime.now(UTC) - RETRY_CUTOFF - timedelta(seconds=1)
    ride_repository.save(replace(ride, requested_at=backdated_at))

    # A fresh consumer group so this doesn't race any other consumer for the same message.
    probe = RawKafkaConsumer(
        kafka_producer.RIDE_REQUESTED_TOPIC,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="test-stale-retry-probe",
        auto_offset_reset="latest",
        consumer_timeout_ms=10_000,
    )
    # kafka-python only joins the consumer group and fixes its "latest" starting position on
    # the first poll, not at construction — polling once here (before retry_stale_rides()
    # publishes) avoids a race where the probe's position gets set *after* the publish and
    # misses the message entirely.
    probe.poll(timeout_ms=1000)
    try:
        tasks.retry_stale_rides()
        message = next(iter(probe))
        assert message.value.decode("utf-8") == ride_id
    finally:
        probe.close()


def test_retry_stale_rides_leaves_recent_requested_rides_alone(client: APIClient) -> None:
    register_and_login(client, "rider1")
    ride_id = client.post("/rides", RIDE_REQUEST, format="json").data["id"]

    # requested_at defaults to now — well inside RETRY_CUTOFF — so retry_stale_rides() must not
    # touch it.
    tasks.retry_stale_rides()
    ride = ride_repository.find_by_id(ride_id)
    assert ride is not None
    assert ride.status.value == "REQUESTED"


def test_ride_request_is_dispatched_end_to_end_through_kafka_and_celery(
    client: APIClient, celery_app: object, celery_worker: object
) -> None:
    """The one test exercising the real wiring — a real Kafka broker, the actual
    kafka_bridge.consume_forever() loop (run in a background thread here rather than as its own
    `manage.py consume_kafka` process), and a real Celery worker consuming from a real Redis
    broker — not just the task functions called directly like every other test above."""
    register_driver(client, "alice")
    client.post("/driver/mode/on", {"lat": 40.7128, "lng": -74.0060}, format="json")
    alice_id = client.get("/auth/me").data["user_id"]

    # A short consumer_timeout_ms means iterating the consumer raises StopIteration once idle,
    # which is what lets consume_forever()'s for-loop return once `stop` is set below — a plain
    # KafkaConsumer with no timeout blocks forever waiting for the next message.
    consumer = kafka_bridge.build_consumer(consumer_timeout_ms=500)
    stop = threading.Event()

    def _run() -> None:
        while not stop.is_set():
            kafka_bridge.consume_forever(consumer)

    bridge_thread = threading.Thread(target=_run, daemon=True)
    bridge_thread.start()
    try:
        rider = APIClient()
        register_and_login(rider, "rider1")
        ride_id = rider.post("/rides", RIDE_REQUEST, format="json").data["id"]

        dispatched_key = f"{DISPATCHED_KEY_PREFIX}{ride_id}"
        redis_client = get_client()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if redis_client.exists(dispatched_key):
                break
            time.sleep(0.2)
        else:
            raise AssertionError("consumer/worker never dispatched the ride within 15s")

        assert redis_client.smembers(dispatched_key) == {alice_id}
    finally:
        stop.set()
        consumer.close()
