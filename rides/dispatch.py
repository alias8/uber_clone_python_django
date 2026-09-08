"""Nearby-driver matching.

In uber_clone this is backed by a Redis GEOSEARCH index (see DriverService.kt) so it works
across instances. Until M3 wires Redis in, drivers/locations live in the in-memory
DriverRepository and this module does the same radius search over that in-process.
"""

from __future__ import annotations

from dataclasses import dataclass

from rides.domain import Driver
from rides.geo import haversine_km

DEFAULT_SEARCH_RADIUS_KM = 5.0
MAX_RESULTS = 20


@dataclass
class NearbyDriver:
    driver_id: str
    distance_km: float


def find_nearby_available_drivers(
    lat: float, lng: float, drivers: list[Driver], radius_km: float = DEFAULT_SEARCH_RADIUS_KM
) -> list[NearbyDriver]:
    in_range: list[NearbyDriver] = []
    for driver in drivers:
        if not driver.is_available or driver.lat is None or driver.lng is None:
            continue
        distance_km = haversine_km(lat, lng, driver.lat, driver.lng)
        if distance_km <= radius_km:
            in_range.append(NearbyDriver(driver_id=driver.user_id, distance_km=distance_km))
    in_range.sort(key=lambda c: c.distance_km)
    return in_range[:MAX_RESULTS]


def count_nearby_available_drivers(lat: float, lng: float, drivers: list[Driver], radius_km: float) -> int:
    return len(find_nearby_available_drivers(lat, lng, drivers, radius_km))
