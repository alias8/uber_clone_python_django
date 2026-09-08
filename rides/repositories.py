"""In-memory persistence. Swapped for the Django ORM against Postgres in M2 — the method names
here (save/find_by_id/exists_by_*) are chosen to make that swap mechanical, same convention the
FastAPI sibling used for its SQLAlchemy swap."""

from __future__ import annotations

import threading

from rides.domain import Driver, Rating, Ride, RideStatus, User

ACTIVE_RIDE_STATUSES = (RideStatus.REQUESTED, RideStatus.MATCHED, RideStatus.IN_PROGRESS)


class UserRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, User] = {}
        self._lock = threading.Lock()

    def save(self, user: User) -> User:
        with self._lock:
            self._by_id[user.id] = user
        return user

    def find_by_id(self, user_id: str) -> User | None:
        return self._by_id.get(user_id)

    def find_by_username(self, username: str) -> User | None:
        return next((u for u in self._by_id.values() if u.username == username), None)

    def exists_by_username(self, username: str) -> bool:
        return self.find_by_username(username) is not None

    def clear(self) -> None:
        self._by_id.clear()


class DriverRepository:
    def __init__(self) -> None:
        self._by_user_id: dict[str, Driver] = {}
        self._lock = threading.Lock()

    def save(self, driver: Driver) -> Driver:
        with self._lock:
            self._by_user_id[driver.user_id] = driver
        return driver

    def find_by_id(self, user_id: str) -> Driver | None:
        return self._by_user_id.get(user_id)

    def exists_by_id(self, user_id: str) -> bool:
        return user_id in self._by_user_id

    def all(self) -> list[Driver]:
        return list(self._by_user_id.values())

    def clear(self) -> None:
        self._by_user_id.clear()


class RideRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Ride] = {}
        self._lock = threading.Lock()

    def save(self, ride: Ride) -> Ride:
        with self._lock:
            self._by_id[ride.id] = ride
        return ride

    def find_by_id(self, ride_id: str) -> Ride | None:
        return self._by_id.get(ride_id)

    def exists_by_rider_id_and_status_in(self, rider_id: str, statuses: tuple[RideStatus, ...]) -> bool:
        return any(r.rider_id == rider_id and r.status in statuses for r in self._by_id.values())

    def find_by_rider_id_ordered(self, rider_id: str) -> list[Ride]:
        rides = [r for r in self._by_id.values() if r.rider_id == rider_id]
        return sorted(rides, key=lambda r: r.requested_at, reverse=True)

    def find_by_driver_id_ordered(self, driver_id: str) -> list[Ride]:
        rides = [r for r in self._by_id.values() if r.driver_id == driver_id]
        return sorted(rides, key=lambda r: r.requested_at, reverse=True)

    def count_pending_near(self, lat: float, lng: float, delta: float = 0.01) -> int:
        return sum(
            1
            for r in self._by_id.values()
            if r.status == RideStatus.REQUESTED
            and (lat - delta) <= r.pickup_lat <= (lat + delta)
            and (lng - delta) <= r.pickup_lng <= (lng + delta)
        )

    def clear(self) -> None:
        self._by_id.clear()


class RatingRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Rating] = {}
        self._lock = threading.Lock()

    def save(self, rating: Rating) -> Rating:
        with self._lock:
            self._by_id[rating.id] = rating
        return rating

    def exists_by_ride_id_and_from_user_id(self, ride_id: str, from_user_id: str) -> bool:
        return any(r.ride_id == ride_id and r.from_user_id == from_user_id for r in self._by_id.values())

    def clear(self) -> None:
        self._by_id.clear()
