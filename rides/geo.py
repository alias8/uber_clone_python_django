"""Ported from uber_clone's GeoUtils.kt — same formula, same constants."""

import math

AVERAGE_CITY_SPEED_KMH = 30.0
EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(
        d_lng / 2
    ) ** 2
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def eta_minutes(distance_km: float) -> int:
    return max(1, int(distance_km / AVERAGE_CITY_SPEED_KMH * 60))
