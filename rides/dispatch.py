"""Nearby-driver matching.

Backed by a real Redis GEOSEARCH index (`drivers:locations`) filtered to the `drivers:available`
set, matching `DriverService.kt::findNearby` exactly — same two key names, same
geo-search-then-filter-by-availability shape. Kotlin still calls the deprecated `GEORADIUS`
command; this uses its `GEOSEARCH` successor instead, the same choice the FastAPI sibling made.

Both keys are written by repositories.py's `DriverRepository.save()`/`set_location()`/
`clear_location()` — this module only reads them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from rides.redis_client import get_client

DRIVER_GEO_KEY = "drivers:locations"
DRIVER_AVAILABLE_SET = "drivers:available"

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
