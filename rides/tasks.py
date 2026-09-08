"""Celery tasks: the async/background-work side of the ride lifecycle (M4 dispatch, M5 delivery).

Ports KafkaConsumer.kt's four @KafkaListener methods in full now (M4 shipped the non-SSE half of
each; M5 fills in the Channels-group-send delivery Kotlin/FastAPI do via SseEmitter/asyncio.Queue)
and StaleRideRetryJob.kt's scheduled loop. These are enqueued by rides/kafka_bridge.py (a
lightweight Kafka-to-Celery bridge, run via `manage.py consume_kafka`) rather than consumed
directly here — a Celery task doesn't read Kafka itself, it's *given* a ride id to act on.

Every test in this project calls these as plain functions (`tasks.handle_ride_requested(ride_id)`)
rather than through `.delay()`/a real worker — a `@shared_task`-decorated function stays directly
callable, so this needs no eager-mode setting at all, only tests/test_dispatch.py's one true
end-to-end test (proving the actual Kafka -> bridge -> Celery -> worker wiring) goes through a
real broker and a real `celery_worker` fixture.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from celery import shared_task
from rides.dispatch import fanout_to_nearby_drivers, get_driver_location, notify_offer_cancelled_and_clear
from rides.domain import RideStatus
from rides.geo import eta_minutes, haversine_km
from rides.kafka_producer import publish_ride_requested
from rides.streaming.groups import ride_location_group, send_close, send_event

logger = logging.getLogger(__name__)

RETRY_CUTOFF = timedelta(minutes=2)


@shared_task(name="rides.handle_ride_requested")
def handle_ride_requested(ride_id: str) -> None:
    from rides.state import ride_repository

    ride = ride_repository.find_by_id(ride_id)
    if ride is None or ride.status != RideStatus.REQUESTED:
        return
    logger.info("Ride requested: id=%s rider=%s status=%s", ride.id, ride.rider_id, ride.status)
    fanout_to_nearby_drivers(ride)


@shared_task(name="rides.handle_ride_accepted")
def handle_ride_accepted(ride_id: str) -> None:
    from rides.state import ride_repository

    ride = ride_repository.find_by_id(ride_id)
    if ride is None:
        return
    logger.info("Ride accepted: id=%s driver=%s rider=%s", ride.id, ride.driver_id, ride.rider_id)

    if ride.driver_id is not None:
        position = get_driver_location(ride.driver_id)
        if position is not None:
            lat, lng = position
            eta = eta_minutes(haversine_km(lat, lng, ride.pickup_lat, ride.pickup_lng))
            send_event(ride_location_group(ride.id), "driver.eta_to_pickup", json.dumps({"etaMinutes": eta}))

    notify_offer_cancelled_and_clear(ride_id, ride.driver_id)


@shared_task(name="rides.handle_ride_completed")
def handle_ride_completed(ride_id: str) -> None:
    from rides.state import ride_repository

    ride = ride_repository.find_by_id(ride_id)
    if ride is None or ride.status != RideStatus.COMPLETED:
        return
    logger.info(
        "Ride completed: id=%s fare=%s driver=%s rider=%s", ride.id, ride.fare, ride.driver_id, ride.rider_id
    )
    send_close(ride_location_group(ride_id))
    # Payment processing was never implemented in the Kotlin original either (just a TODO
    # comment there).


@shared_task(name="rides.handle_ride_cancelled")
def handle_ride_cancelled(ride_id: str) -> None:
    from rides.state import ride_repository

    ride = ride_repository.find_by_id(ride_id)
    if ride is None:
        return
    logger.info("Ride cancelled: id=%s rider=%s", ride.id, ride.rider_id)
    send_close(ride_location_group(ride_id))


@shared_task(name="rides.retry_stale_rides")
def retry_stale_rides() -> None:
    """Celery Beat runs this every 60s (see config/settings.py's CELERY_BEAT_SCHEDULE) —
    replaces StaleRideRetryJob.kt's @Scheduled loop and the FastAPI sibling's
    asyncio.sleep-loop task with Celery's own periodic-task mechanism."""
    from rides.state import ride_repository

    cutoff = datetime.now(UTC) - RETRY_CUTOFF
    stale = ride_repository.find_by_status_and_requested_at_before(RideStatus.REQUESTED, cutoff)
    if not stale:
        return

    logger.info("Retrying %d stale ride(s)", len(stale))
    for ride in stale:
        logger.info("Re-dispatching stale ride %s, requested at %s", ride.id, ride.requested_at)
        publish_ride_requested(ride.id)
