"""Postgres-backed persistence via the Django ORM (M2). Method names (save/find_by_id/
exists_by_*) are unchanged from M1's in-memory version — that was the point of choosing them
there — and match the FastAPI sibling's own SQLAlchemy repository method names too.

Driver lat/lng are the one exception: they're not columns in uber_clone's schema (that's a
Redis GEOSEARCH index in the real system, wired up here in M3), so they stay in an in-memory
overlay dict here, merged onto rows read from Postgres — same pattern the FastAPI sibling used
at its own M2 (see its repositories.py docstring).
"""

from __future__ import annotations

from rides.domain import Driver, Rating, Ride, RideStatus, Role, User
from rides.models import DriverRow, RatingRow, RideRow, UserRow

ACTIVE_RIDE_STATUSES = (RideStatus.REQUESTED, RideStatus.MATCHED, RideStatus.IN_PROGRESS)


def _user_from_row(row: UserRow) -> User:
    return User(
        id=row.id,
        username=row.username,
        password_hash=row.password_hash,
        role=Role(row.role),
        avg_rating=row.avg_rating,
        rating_count=row.rating_count,
    )


def _ride_from_row(row: RideRow) -> Ride:
    return Ride(
        id=row.id,
        rider_id=row.rider_id,
        driver_id=row.driver_id,
        pickup_lat=row.pickup_lat,
        pickup_lng=row.pickup_lng,
        dropoff_lat=row.dropoff_lat,
        dropoff_lng=row.dropoff_lng,
        status=RideStatus(row.status),
        estimated_fare=row.estimated_fare,
        fare=row.fare,
        requested_at=row.requested_at,
        completed_at=row.completed_at,
        version=row.version,
    )


def _rating_from_row(row: RatingRow) -> Rating:
    return Rating(
        id=row.id,
        ride_id=row.ride_id,
        from_user_id=row.from_user_id,
        to_user_id=row.to_user_id,
        score=row.score,
        comment=row.comment,
        created_at=row.created_at,
    )


class UserRepository:
    def save(self, user: User) -> User:
        row, _created = UserRow.objects.update_or_create(
            id=user.id,
            defaults={
                "username": user.username,
                "password_hash": user.password_hash,
                "role": user.role.value,
                "avg_rating": user.avg_rating,
                "rating_count": user.rating_count,
            },
        )
        return _user_from_row(row)

    def find_by_id(self, user_id: str) -> User | None:
        row = UserRow.objects.filter(id=user_id).first()
        return _user_from_row(row) if row is not None else None

    def find_by_username(self, username: str) -> User | None:
        row = UserRow.objects.filter(username=username).first()
        return _user_from_row(row) if row is not None else None

    def exists_by_username(self, username: str) -> bool:
        return UserRow.objects.filter(username=username).exists()

    def clear(self) -> None:
        UserRow.objects.all().delete()


class DriverRepository:
    def __init__(self) -> None:
        # In-memory stand-in for the Redis geo-index, until M3 — see module docstring.
        self._locations: dict[str, tuple[float, float]] = {}

    def _with_location(self, driver: Driver) -> Driver:
        loc = self._locations.get(driver.user_id)
        if loc is not None:
            driver.lat, driver.lng = loc
        return driver

    @staticmethod
    def _driver_from_row(row: DriverRow) -> Driver:
        return Driver(
            user_id=row.user_id,
            vehicle_type=row.vehicle_type,
            license_plate=row.license_plate,
            is_available=row.is_available,
            avg_rating=row.avg_rating,
            rating_count=row.rating_count,
        )

    def save(self, driver: Driver) -> Driver:
        if driver.lat is not None and driver.lng is not None:
            self._locations[driver.user_id] = (driver.lat, driver.lng)
        else:
            self._locations.pop(driver.user_id, None)

        row, _created = DriverRow.objects.update_or_create(
            user_id=driver.user_id,
            defaults={
                "vehicle_type": driver.vehicle_type,
                "license_plate": driver.license_plate,
                "is_available": driver.is_available,
                "avg_rating": driver.avg_rating,
                "rating_count": driver.rating_count,
            },
        )
        return self._with_location(self._driver_from_row(row))

    def find_by_id(self, user_id: str) -> Driver | None:
        row = DriverRow.objects.filter(user_id=user_id).first()
        return self._with_location(self._driver_from_row(row)) if row is not None else None

    def exists_by_id(self, user_id: str) -> bool:
        return DriverRow.objects.filter(user_id=user_id).exists()

    def all(self) -> list[Driver]:
        return [self._with_location(self._driver_from_row(r)) for r in DriverRow.objects.all()]

    def clear(self) -> None:
        DriverRow.objects.all().delete()
        self._locations.clear()


class RideRepository:
    def save(self, ride: Ride) -> Ride:
        row, _created = RideRow.objects.update_or_create(
            id=ride.id,
            defaults={
                "rider_id": ride.rider_id,
                "driver_id": ride.driver_id,
                "pickup_lat": ride.pickup_lat,
                "pickup_lng": ride.pickup_lng,
                "dropoff_lat": ride.dropoff_lat,
                "dropoff_lng": ride.dropoff_lng,
                "status": ride.status.value,
                "estimated_fare": ride.estimated_fare,
                "fare": ride.fare,
                "requested_at": ride.requested_at,
                "completed_at": ride.completed_at,
                "version": ride.version,
            },
        )
        return _ride_from_row(row)

    def find_by_id(self, ride_id: str) -> Ride | None:
        row = RideRow.objects.filter(id=ride_id).first()
        return _ride_from_row(row) if row is not None else None

    def exists_by_rider_id_and_status_in(self, rider_id: str, statuses: tuple[RideStatus, ...]) -> bool:
        return RideRow.objects.filter(
            rider_id=rider_id, status__in=[s.value for s in statuses]
        ).exists()

    def find_by_rider_id_ordered(self, rider_id: str) -> list[Ride]:
        rows = RideRow.objects.filter(rider_id=rider_id).order_by("-requested_at")
        return [_ride_from_row(r) for r in rows]

    def find_by_driver_id_ordered(self, driver_id: str) -> list[Ride]:
        rows = RideRow.objects.filter(driver_id=driver_id).order_by("-requested_at")
        return [_ride_from_row(r) for r in rows]

    def count_pending_near(self, lat: float, lng: float, delta: float = 0.01) -> int:
        return RideRow.objects.filter(
            status=RideStatus.REQUESTED.value,
            pickup_lat__gte=lat - delta,
            pickup_lat__lte=lat + delta,
            pickup_lng__gte=lng - delta,
            pickup_lng__lte=lng + delta,
        ).count()

    def clear(self) -> None:
        RideRow.objects.all().delete()


class RatingRepository:
    def save(self, rating: Rating) -> Rating:
        row, _created = RatingRow.objects.update_or_create(
            id=rating.id,
            defaults={
                "ride_id": rating.ride_id,
                "from_user_id": rating.from_user_id,
                "to_user_id": rating.to_user_id,
                "score": rating.score,
                "comment": rating.comment,
                "created_at": rating.created_at,
            },
        )
        return _rating_from_row(row)

    def exists_by_ride_id_and_from_user_id(self, ride_id: str, from_user_id: str) -> bool:
        return RatingRow.objects.filter(ride_id=ride_id, from_user_id=from_user_id).exists()

    def clear(self) -> None:
        RatingRow.objects.all().delete()
