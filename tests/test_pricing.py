"""Numeric parity with PricingGrpcService.kt's fare/surge formulas."""

from decimal import Decimal

from rides.pricing import (
    PricingService,
    calculate_base_fare,
    calculate_fare,
    compute_surge_multiplier,
    surge_grid_key,
)

NYC = (40.7128, -74.0060)
LA = (34.0522, -118.2437)


def test_base_fare_is_two_dollars_plus_a_dollar_fifty_per_km() -> None:
    # 10km trip: 2.00 + 1.50 * 10 = 17.00
    assert calculate_base_fare(10.0) == Decimal("17.00")


def test_calculate_fare_applies_surge_and_rounds_half_up_to_2dp() -> None:
    # 10km * surge 1.5 => base 17.00 * 1.5 = 25.50
    assert calculate_fare(10.0, Decimal("1.5")) == Decimal("25.50")


def test_calculate_fare_rounds_half_up() -> None:
    # base for 1km = 2.00 + 1.50 = 3.50; surge 1.111... would round, use a case that lands on .xx5
    fare = calculate_fare(1.0, Decimal("1.015"))
    # 3.50 * 1.015 = 3.5525 -> rounds to 3.55 (HALF_UP)
    assert fare == Decimal("3.55")


def test_surge_multiplier_is_one_when_supply_meets_or_exceeds_demand() -> None:
    assert compute_surge_multiplier(pending_rides=2, available_drivers=10) == Decimal("1.00")
    assert compute_surge_multiplier(pending_rides=0, available_drivers=5) == Decimal("1.00")


def test_surge_multiplier_scales_with_pending_to_available_ratio() -> None:
    # 6 pending / 4 available = 1.5
    assert compute_surge_multiplier(pending_rides=6, available_drivers=4) == Decimal("1.50")


def test_surge_multiplier_is_capped_at_three() -> None:
    assert compute_surge_multiplier(pending_rides=100, available_drivers=1) == Decimal("3.0")


def test_surge_multiplier_is_max_when_no_drivers_available() -> None:
    assert compute_surge_multiplier(pending_rides=5, available_drivers=0) == Decimal("3.0")


def test_surge_grid_key_rounds_to_1km_resolution() -> None:
    assert surge_grid_key(40.71284, -74.00601) == "surge:40.71:-74.01"


def test_pricing_service_caches_surge_within_the_grid_cell() -> None:
    service = PricingService()
    first = service.get_surge_multiplier(*NYC, pending_rides=6, available_drivers=4)
    # Change the inputs — a cached hit should still return the first result, not recompute.
    second = service.get_surge_multiplier(*NYC, pending_rides=100, available_drivers=1)
    assert first == second == Decimal("1.50")


def test_pricing_service_get_fare_quote_combines_distance_and_surge() -> None:
    service = PricingService()
    fare, surge = service.get_fare_quote(*NYC, *NYC, pending_rides=6, available_drivers=4)
    # Same pickup/dropoff => 0km distance => base fare is just $2.00.
    assert surge == Decimal("1.50")
    assert fare == Decimal("3.00")
