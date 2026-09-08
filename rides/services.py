"""Ride/driver/rating domain logic. Ported from RideService.kt, DriverService.kt and
RatingService.kt (and cross-checked against the FastAPI sibling's services.py, which ported the
same logic first). Fare calculation is in-process (rides.pricing) rather than a gRPC call — see
pricing.py's docstring. Kafka event publishing (ride-requested/accepted/completed/cancelled) is
wired in as of M4 (rides/kafka_producer.py) — see rides/tasks.py for what consumes those events.
`DriverService.update_location`/`go_offline` are the two streaming events produced in-process
(this code runs inside the Daphne/ASGI process itself, not a Celery worker) rather than via
rides/tasks.py — see rides/streaming/groups.py for the Channels group_send helpers used here.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from rest_framework.exceptions import APIException

from rides.dispatch import NearbyDriver, find_nearby_available_drivers
from rides.domain import Driver, Rating, Ride, RideStatus
from rides.geo import eta_minutes, haversine_km
from rides.kafka_producer import (
    publish_ride_accepted,
    publish_ride_cancelled,
    publish_ride_completed,
    publish_ride_requested,
)
from rides.pricing import SURGE_SEARCH_RADIUS_KM, PricingService
from rides.repositories import (
    ACTIVE_RIDE_STATUSES,
    DriverRepository,
    RatingRepository,
    RideRepository,
    UserRepository,
)
from rides.streaming.groups import driver_offers_group, ride_location_group, send_close, send_event


class DomainError(APIException):
    """A domain-rule violation, mapped to a specific HTTP status by the view layer.

    DRF's APIException always carries an HTTP status of its own (default 500), but the status
    here is chosen per-error to match the ported Kotlin/FastAPI behavior (404/403/409/400) —
    services raise this directly with the right `status_code`/`detail`, and views let it
    propagate (DRF's exception handler renders it), rather than each view re-deriving the status
    from a domain-specific exception type.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(detail)


_ACTIVE_RIDE_STATUSES = (RideStatus.MATCHED, RideStatus.IN_PROGRESS)


class DriverService:
    def __init__(self, driver_repository: DriverRepository, ride_repository: RideRepository) -> None:
        self._driver_repository = driver_repository
        self._ride_repository = ride_repository

    def register_driver(self, user_id: str, vehicle_type: str, license_plate: str) -> Driver:
        if self._driver_repository.exists_by_id(user_id):
            raise DomainError(409, "Driver profile already exists")
        driver = Driver(user_id=user_id, vehicle_type=vehicle_type, license_plate=license_plate)
        return self._driver_repository.save(driver)

    def get_profile(self, user_id: str) -> Driver:
        driver = self._driver_repository.find_by_id(user_id)
        if driver is None:
            raise DomainError(404, "No driver profile found")
        return driver

    def go_online(self, user_id: str, lat: float, lng: float) -> Driver:
        self.get_profile(user_id)
        self._driver_repository.set_location(user_id, lat, lng)
        return self.mark_available_by_id(user_id)

    def go_offline(self, user_id: str) -> Driver:
        self.get_profile(user_id)
        self._driver_repository.clear_location(user_id)
        saved = self.mark_unavailable_by_id(user_id)
        send_close(driver_offers_group(user_id))
        return saved

    def mark_available_by_id(self, user_id: str) -> Driver:
        driver = self._driver_repository.find_by_id(user_id)
        if driver is None:
            raise DomainError(404, "No driver profile found")
        return self._driver_repository.save(replace(driver, is_available=True))

    def mark_unavailable_by_id(self, user_id: str) -> Driver:
        driver = self._driver_repository.find_by_id(user_id)
        if driver is None:
            raise DomainError(404, "No driver profile found")
        return self._driver_repository.save(replace(driver, is_available=False))

    def update_location(self, user_id: str, lat: float, lng: float) -> None:
        self.get_profile(user_id)
        self._driver_repository.set_location(user_id, lat, lng)
        ride = self._ride_repository.find_first_by_driver_id_and_status_in(user_id, _ACTIVE_RIDE_STATUSES)
        if ride is not None:
            eta = eta_minutes(haversine_km(lat, lng, ride.dropoff_lat, ride.dropoff_lng))
            payload = json.dumps({"lat": lat, "lng": lng, "etaMinutes": eta})
            send_event(ride_location_group(ride.id), "driver.location", payload)

    def find_nearby(self, lat: float, lng: float, radius_km: float) -> list[NearbyDriver]:
        return find_nearby_available_drivers(lat, lng, radius_km)


class RideService:
    def __init__(
        self,
        ride_repository: RideRepository,
        driver_repository: DriverRepository,
        driver_service: DriverService,
        pricing_service: PricingService,
    ) -> None:
        self._ride_repository = ride_repository
        self._driver_repository = driver_repository
        self._driver_service = driver_service
        self._pricing_service = pricing_service
        # Guards the accept-ride check-then-write sequence. uber_clone relies on JPA optimistic
        # locking (a `version` column + ObjectOptimisticLockingFailureException) to make two
        # concurrent accepts of the same ride safe; with a single in-memory process, a lock
        # around the same sequence gives the same guarantee.
        self._accept_lock = threading.Lock()

    def request_ride(
        self, rider_id: str, pickup_lat: float, pickup_lng: float, dropoff_lat: float, dropoff_lng: float
    ) -> Ride:
        if self._ride_repository.exists_by_rider_id_and_status_in(rider_id, ACTIVE_RIDE_STATUSES):
            raise DomainError(409, "Rider already has an active ride")

        ride = Ride(
            rider_id=rider_id,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            dropoff_lat=dropoff_lat,
            dropoff_lng=dropoff_lng,
        )
        estimated_fare = self._calculate_fare(ride)
        saved = self._ride_repository.save(replace(ride, estimated_fare=estimated_fare))
        publish_ride_requested(saved.id)
        return saved

    def get_ride(self, ride_id: str) -> Ride:
        ride = self._ride_repository.find_by_id(ride_id)
        if ride is None:
            raise DomainError(404, "Ride not found")
        return ride

    def accept_ride(self, ride_id: str, driver_id: str) -> Ride:
        with self._accept_lock:
            ride = self.get_ride(ride_id)
            if ride.status != RideStatus.REQUESTED:
                raise DomainError(409, "Ride is not available for acceptance")
            driver = self._driver_repository.find_by_id(driver_id)
            if driver is None:
                raise DomainError(403, "No driver profile found — register as a driver first")
            if not driver.is_available:
                raise DomainError(409, "Driver is not currently available")

            self._driver_service.mark_unavailable_by_id(driver_id)
            saved = self._ride_repository.save(
                replace(ride, driver_id=driver_id, status=RideStatus.MATCHED, version=ride.version + 1)
            )
            publish_ride_accepted(saved.id)
            return saved

    def start_ride(self, ride_id: str, driver_id: str) -> Ride:
        ride = self.get_ride(ride_id)
        if ride.status != RideStatus.MATCHED:
            raise DomainError(409, "Ride is not in MATCHED state")
        if ride.driver_id != driver_id:
            raise DomainError(403, "Not the assigned driver for this ride")
        return self._ride_repository.save(
            replace(ride, status=RideStatus.IN_PROGRESS, version=ride.version + 1)
        )

    def complete_ride(self, ride_id: str, driver_id: str) -> Ride:
        ride = self.get_ride(ride_id)
        if ride.status != RideStatus.IN_PROGRESS:
            raise DomainError(409, "Ride is not IN_PROGRESS")
        if ride.driver_id != driver_id:
            raise DomainError(403, "Not the assigned driver for this ride")

        saved = self._ride_repository.save(
            replace(
                ride,
                status=RideStatus.COMPLETED,
                fare=ride.estimated_fare,
                completed_at=datetime.now(UTC),
                version=ride.version + 1,
            )
        )
        self._driver_service.mark_available_by_id(driver_id)
        publish_ride_completed(saved.id)
        return saved

    def cancel_ride(self, ride_id: str, user_id: str) -> Ride:
        ride = self.get_ride(ride_id)
        if ride.status in (RideStatus.IN_PROGRESS, RideStatus.COMPLETED):
            raise DomainError(409, f"Cannot cancel a ride with status {ride.status.value}")
        if ride.rider_id != user_id and ride.driver_id != user_id:
            raise DomainError(403, "Not a participant in this ride")

        if ride.driver_id is not None:
            self._driver_service.mark_available_by_id(ride.driver_id)

        saved = self._ride_repository.save(
            replace(ride, status=RideStatus.CANCELLED, version=ride.version + 1)
        )
        publish_ride_cancelled(saved.id)
        return saved

    # Calls the in-process pricing module rather than a separate gRPC pricing-service — the
    # deliberate simplification this port makes relative to uber_clone (see pricing.py).
    def _calculate_fare(self, ride: Ride) -> Decimal:
        pending_rides = self._ride_repository.count_pending_near(ride.pickup_lat, ride.pickup_lng)
        available_drivers = len(
            find_nearby_available_drivers(ride.pickup_lat, ride.pickup_lng, SURGE_SEARCH_RADIUS_KM)
        )
        fare, _surge = self._pricing_service.get_fare_quote(
            ride.pickup_lat,
            ride.pickup_lng,
            ride.dropoff_lat,
            ride.dropoff_lng,
            pending_rides,
            available_drivers,
        )
        return fare


def _incremental_avg(old_avg: float | None, old_count: int, new_score: int) -> float:
    avg = old_avg if old_avg is not None else 0.0
    return (avg * old_count + new_score) / (old_count + 1)


def _round_to_2(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class RatingService:
    def __init__(
        self,
        rating_repository: RatingRepository,
        ride_repository: RideRepository,
        user_repository: UserRepository,
        driver_repository: DriverRepository,
    ) -> None:
        self._rating_repository = rating_repository
        self._ride_repository = ride_repository
        self._user_repository = user_repository
        self._driver_repository = driver_repository

    def rate(self, ride_id: str, from_user_id: str, score: int, comment: str | None) -> None:
        if not (1 <= score <= 5):
            raise DomainError(400, "Score must be between 1 and 5")

        ride = self._ride_repository.find_by_id(ride_id)
        if ride is None:
            raise DomainError(404, "Ride not found")
        if ride.status != RideStatus.COMPLETED:
            raise DomainError(409, "Can only rate a completed ride")

        if from_user_id == ride.rider_id:
            if ride.driver_id is None:
                raise DomainError(409, "Ride has no assigned driver")
            to_user_id = ride.driver_id
        elif from_user_id == ride.driver_id:
            to_user_id = ride.rider_id
        else:
            raise DomainError(403, "Not a participant in this ride")

        if self._rating_repository.exists_by_ride_id_and_from_user_id(ride_id, from_user_id):
            raise DomainError(409, "Already rated this ride")

        self._rating_repository.save(
            Rating(
                ride_id=ride_id,
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                score=score,
                comment=comment,
            )
        )

        driver = self._driver_repository.find_by_id(to_user_id)
        if driver is not None:
            new_avg = _round_to_2(_incremental_avg(driver.avg_rating, driver.rating_count, score))
            self._driver_repository.save(
                replace(driver, avg_rating=new_avg, rating_count=driver.rating_count + 1)
            )

        user = self._user_repository.find_by_id(to_user_id)
        if user is not None:
            new_avg = _round_to_2(_incremental_avg(user.avg_rating, user.rating_count, score))
            self._user_repository.save(replace(user, avg_rating=new_avg, rating_count=user.rating_count + 1))
