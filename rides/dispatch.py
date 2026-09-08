"""Nearby-driver matching and offer fan-out.

find_nearby_available_drivers is backed by a real Redis GEOSEARCH index (`drivers:locations`)
filtered to the `drivers:available` set, matching `DriverService.kt::findNearby` exactly — same
two key names, same geo-search-then-filter-by-availability shape. Kotlin still calls the
deprecated `GEORADIUS` command; this uses its `GEOSEARCH` successor instead, the same choice the
FastAPI sibling made. Both keys are written by repositories.py's `DriverRepository.save()`/
`set_location()`/`clear_location()` — this module only reads them.

fanout_to_nearby_drivers and clear_dispatch port `DispatchService.kt`'s fan-out and the
non-SSE half of `KafkaConsumer.kt`'s ride-accepted handler, called from `rides/tasks.py`'s Celery
tasks (M4) rather than an asyncio consumer loop. Actually delivering the published offer to a
driver (Django Channels reading `ride_offers:{driverId}`) is M5's job — this module only writes
to Redis, it doesn't care who's listening yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from rides.domain import Ride
from rides.geo import eta_minutes
from rides.redis_client import get_client

DRIVER_GEO_KEY = "drivers:locations"
DRIVER_AVAILABLE_SET = "drivers:available"
RIDE_OFFER_CHANNEL_PREFIX = "ride_offers:"
DISPATCHED_KEY_PREFIX = "dispatched:"
DISPATCHED_TTL_SECONDS = 5 * 60

DEFAULT_SEARCH_RADIUS_KM = 5.0
MAX_RESULTS = 20


@dataclass
class NearbyDriver:
    driver_id: str
    distance_km: float


def find_nearby_available_drivers(
    lat: float, lng: float, radius_km: float = DEFAULT_SEARCH_RADIUS_KM
) -> list[NearbyDriver]:
    client = get_client()
    # withdist=True guarantees each result is a (member, distance) pair, not a bare member name.
    results = cast(
        "list[tuple[str, float]]",
        client.geosearch(
            DRIVER_GEO_KEY,
            longitude=lng,
            latitude=lat,
            unit="km",
            radius=radius_km,
            sort="ASC",
            count=MAX_RESULTS,
            withdist=True,
        ),
    )
    if not results:
        return []

    # results is [(driver_id, distance_km), ...]; available is [1, 0, 1, ...] from smismember,
    # same order/length as results.
    driver_ids = [driver_id for driver_id, _distance_km in results]
    available = cast("list[int]", client.smismember(DRIVER_AVAILABLE_SET, driver_ids))
    return [
        NearbyDriver(driver_id=driver_id, distance_km=float(distance_km))
        for (driver_id, distance_km), is_available in zip(results, available, strict=True)
        if is_available
    ]


def fanout_to_nearby_drivers(ride: Ride) -> None:
    """Ports DispatchService.kt::fanoutToNearbyDrivers, called from rides/tasks.py's
    handle_ride_requested. Records who was offered the ride (`dispatched:{rideId}`, 5-minute TTL)
    and publishes a ride-offer payload to each nearby available driver's own pub/sub channel."""
    nearby = find_nearby_available_drivers(ride.pickup_lat, ride.pickup_lng, DEFAULT_SEARCH_RADIUS_KM)
    if not nearby:
        return

    client = get_client()
    dispatched_key = f"{DISPATCHED_KEY_PREFIX}{ride.id}"
    client.sadd(dispatched_key, *(driver.driver_id for driver in nearby))
    client.expire(dispatched_key, DISPATCHED_TTL_SECONDS)

    for driver in nearby:
        # camelCase keys: this is a wire format for whatever eventually subscribes to it (M5's
        # Channels layer), not internal Python state — matching Kotlin's JSON payload exactly.
        payload = json.dumps(
            {
                "rideId": ride.id,
                "pickupLat": ride.pickup_lat,
                "pickupLng": ride.pickup_lng,
                "dropoffLat": ride.dropoff_lat,
                "dropoffLng": ride.dropoff_lng,
                "estimatedFare": float(ride.estimated_fare) if ride.estimated_fare is not None else None,
                "etaMinutes": eta_minutes(driver.distance_km),
            }
        )
        client.publish(f"{RIDE_OFFER_CHANNEL_PREFIX}{driver.driver_id}", payload)


def clear_dispatch(ride_id: str) -> None:
    """Ports the state-cleanup half of KafkaConsumer.kt's ride-accepted handler: clears the
    dispatched-drivers key now that the ride has a driver. In the Kotlin/FastAPI single-process
    design, the *same* handler also directly emits `offer_cancelled`/`driver_eta_to_pickup` SSE
    events to the other dispatched drivers and the rider — pure in-process delivery, no Redis
    involved. This project's Celery worker (this module) and its Channels layer (M5) are
    separate processes, so there's no in-process registry to call here; notifying other drivers
    their offer is gone is deferred to M5, which will own how cross-process delivery works
    rather than this task guessing at a wire format for it now."""
    get_client().delete(f"{DISPATCHED_KEY_PREFIX}{ride_id}")
