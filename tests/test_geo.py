"""Ported from uber_clone's GeoUtilsTest.kt — same cases, same tolerances."""

from rides.geo import eta_minutes, haversine_km

NYC = (40.7128, -74.0060)
LA = (34.0522, -118.2437)


def test_haversine_km_is_zero_for_the_same_point() -> None:
    assert abs(haversine_km(*NYC, *NYC)) < 0.0001


def test_haversine_km_is_symmetric() -> None:
    a = haversine_km(*NYC, *LA)
    b = haversine_km(*LA, *NYC)
    assert abs(a - b) < 0.0001


def test_haversine_km_matches_known_distance_between_nyc_and_la() -> None:
    distance = haversine_km(*NYC, *LA)
    # Real-world great-circle distance is ~3936 km.
    assert 3900.0 <= distance <= 3970.0, f"expected ~3936 km, got {distance}"


def test_eta_minutes_scales_with_distance_at_average_city_speed() -> None:
    assert eta_minutes(15.0) == 30
    assert eta_minutes(30.0) == 60


def test_eta_minutes_never_returns_less_than_one_minute() -> None:
    assert eta_minutes(0.0) == 1
    assert eta_minutes(0.01) == 1
