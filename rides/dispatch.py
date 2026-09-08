"""Nearby-driver matching and offer fan-out.

find_nearby_available_drivers is backed by a real Redis GEOSEARCH index (`drivers:locations`)
filtered to the `drivers:available` set, matching `DriverService.kt::findNearby` exactly — same
two key names, same geo-search-then-filter-by-availability shape. Kotlin still calls the
deprecated `GEORADIUS` command; this uses its `GEOSEARCH` successor instead, the same choice the
FastAPI sibling made. Both keys are written by repositories.py's `DriverRepository.save()`/
`set_location()`/`clear_location()` — this module only reads them.

fanout_to_nearby_drivers, notify_offer_cancelled, and get_driver_location port
`DispatchService.kt`'s fan-out and `KafkaConsumer.kt`'s ride-accepted handler (both halves now —
M5 fills in the SSE-equivalent delivery M4 deferred), called from `rides/tasks.py`'s Celery tasks
rather than an asyncio consumer loop. Delivery itself goes through `rides.streaming.groups`
(Channels group_send, M5) rather than the raw Redis pub/sub channel this module used as a
placeholder through M4 — see that module's docstring for why. The `dispatched:{rideId}` Redis set
is still real Redis bookkeeping (who was offered this ride), not a delivery mechanism, so it's
unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from rides.domain import Ride
from rides.geo import eta_minutes
from rides.redis_client import get_client
from rides.streaming.groups import driver_offers_group, send_event

DRIVER_GEO_KEY = "drivers:locations"
DRIVER_AVAILABLE_SET = "drivers:available"
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
    and sends a `ride.offer` event to each nearby available driver's own Channels group."""
    nearby = find_nearby_available_drivers(ride.pickup_lat, ride.pickup_lng, DEFAULT_SEARCH_RADIUS_KM)
    if not nearby:
        return

    client = get_client()
    dispatched_key = f"{DISPATCHED_KEY_PREFIX}{ride.id}"
    client.sadd(dispatched_key, *(driver.driver_id for driver in nearby))
    client.expire(dispatched_key, DISPATCHED_TTL_SECONDS)

    for driver in nearby:
        # camelCase keys: this is a wire format for the browser client (an SSE `data:` payload
        # via rides/streaming/consumers.py), not internal Python state — matching Kotlin's JSON
        # payload exactly.
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
        send_event(driver_offers_group(driver.driver_id), "ride.offer", payload)


def get_driver_location(driver_id: str) -> tuple[float, float] | None:
    """Ported from DriverService.kt::getDriverLocation (cross-checked against the FastAPI
    sibling's own port of it). Returns (lat, lng) — GEOPOS itself returns (lon, lat), Redis's own
    convention; this flips it to match the rest of this codebase."""
    positions = cast("list[tuple[float, float] | None]", get_client().geopos(DRIVER_GEO_KEY, driver_id))
    position = positions[0]
    if position is None:
        return None
    lng, lat = position
    return lat, lng


def notify_offer_cancelled_and_clear(ride_id: str, accepted_driver_id: str | None) -> None:
    """Ports the notification half of KafkaConsumer.kt's ride-accepted handler: every driver who
    was offered this ride but didn't accept it gets an `offer.cancelled` event on their own
    Channels group, then the dispatched-drivers bookkeeping set is cleared. Unlike M4's
    `clear_dispatch` (which only did the second half — Channels group delivery didn't exist yet),
    this reads the set *before* deleting it, same order as the ported Kotlin/FastAPI handlers."""
    client = get_client()
    dispatched_key = f"{DISPATCHED_KEY_PREFIX}{ride_id}"
    dispatched_drivers = cast("set[str]", client.smembers(dispatched_key))
    payload = json.dumps({"rideId": ride_id})
    for driver_id in dispatched_drivers:
        if driver_id != accepted_driver_id:
            send_event(driver_offers_group(driver_id), "offer.cancelled", payload)
    client.delete(dispatched_key)
