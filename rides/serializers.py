"""DRF serializers — the idiomatic-Django counterpart of the FastAPI sibling's schemas.py
(Pydantic BaseModels). Request serializers validate input the same way Pydantic request models
did; response serializers are plain `serializers.Serializer` (not `ModelSerializer` — there's no
Django ORM model behind these yet, see domain.py) instantiated directly against a domain
dataclass instance, since DRF's `Serializer.to_representation()` reads attributes with `getattr`
just like it would off a Django model.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from rides.dispatch import NearbyDriver
from rides.domain import Driver, Ride
from rides.geo import eta_minutes, haversine_km

# --- auth ---
#
# Request serializers below are never instantiated with `instance=` (only `data=`, for
# validating incoming JSON), so there's no meaningful instance type to parametrize them with —
# `Serializer[Any]` is the honest annotation, not a cop-out. Response serializers *are*
# instantiated against a real instance (a domain dataclass, or a plain dict for the couple of
# shapes with no dataclass behind them), so those are parametrized with that real type.


class RegisterRequestSerializer(serializers.Serializer[Any]):
    username = serializers.CharField()
    password = serializers.CharField()


class LoginRequestSerializer(serializers.Serializer[Any]):
    username = serializers.CharField()
    password = serializers.CharField()


class SwitchModeRequestSerializer(serializers.Serializer[Any]):
    mode = serializers.CharField()


class AuthResponseSerializer(serializers.Serializer[dict[str, str]]):
    token = serializers.CharField()


class MeResponseSerializer(serializers.Serializer[dict[str, str]]):
    user_id = serializers.CharField()


# --- rides ---


class RideRequestSerializer(serializers.Serializer[Any]):
    pickup_lat = serializers.FloatField()
    pickup_lng = serializers.FloatField()
    dropoff_lat = serializers.FloatField()
    dropoff_lng = serializers.FloatField()


class RideResponseSerializer(serializers.Serializer[Ride]):
    id = serializers.CharField()
    rider_id = serializers.CharField()
    driver_id = serializers.CharField(allow_null=True)
    pickup_lat = serializers.FloatField()
    pickup_lng = serializers.FloatField()
    dropoff_lat = serializers.FloatField()
    dropoff_lng = serializers.FloatField()
    status = serializers.CharField()
    estimated_fare = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    fare = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    requested_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)
    estimated_journey_minutes = serializers.SerializerMethodField()

    def get_estimated_journey_minutes(self, ride: Ride) -> int:
        distance_km = haversine_km(ride.pickup_lat, ride.pickup_lng, ride.dropoff_lat, ride.dropoff_lng)
        return eta_minutes(distance_km)


# --- driver ---


class DriverRegisterRequestSerializer(serializers.Serializer[Any]):
    vehicle_type = serializers.CharField()
    license_plate = serializers.CharField()


class DriverProfileResponseSerializer(serializers.Serializer[Driver]):
    user_id = serializers.CharField()
    vehicle_type = serializers.CharField()
    license_plate = serializers.CharField()
    is_available = serializers.BooleanField()
    avg_rating = serializers.FloatField(allow_null=True)


class DriverLocationRequestSerializer(serializers.Serializer[Any]):
    lat = serializers.FloatField()
    lng = serializers.FloatField()


class NearbyDriverResponseSerializer(serializers.Serializer[NearbyDriver]):
    driver_id = serializers.CharField()
    distance_km = serializers.FloatField()


# --- rating ---


class RatingRequestSerializer(serializers.Serializer[Any]):
    # Range (1-5) is enforced in the service layer with a 400, matching RatingService.kt (and
    # the FastAPI sibling's own schemas.py) — not via a `min_value`/`max_value` validator here,
    # which would 400 through a differently-shaped DRF validation-error body instead of the
    # ported one.
    score = serializers.IntegerField()
    comment = serializers.CharField(required=False, allow_null=True, allow_blank=True)
