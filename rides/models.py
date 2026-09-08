"""Django ORM models (M2). Column-for-column port of uber_clone's
src/main/resources/db/migration/V1__baseline_schema.sql, V2__ride_indexes.sql and
V3__rating_count.sql — table/column names match the Kotlin schema exactly (via explicit
`db_table`s, since Django's own app_label-prefixed default would diverge) so this stays directly
comparable to the Kotlin schema and to the FastAPI sibling's SQLAlchemy `db/tables.py`.

These are named `*Row`, matching the FastAPI sibling's `UserRow`/`DriverRow`/`RideRow`/
`RatingRow` naming, and deliberately kept separate from the plain-dataclass domain types in
`domain.py` — `repositories.py` is the seam that translates between the two, same split as the
FastAPI sibling's `models.py` (domain) vs `db/tables.py` (ORM) and the Kotlin JPA entities vs
`model/*.kt`. Service code never touches these directly.

Unlike the FastAPI sibling's SQLAlchemy table (which added a `ForeignKey("users.id")` on
`DriverRow.user_id`), this port matches the literal V1 SQL exactly: no FK constraint is declared
there (`user_id VARCHAR(255) PRIMARY KEY` only), so none is added here either.
"""

from __future__ import annotations

from django.db import models


class UserRow(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    username = models.CharField(max_length=255, unique=True)
    password_hash = models.CharField(max_length=255)
    role = models.CharField(max_length=50)
    avg_rating = models.FloatField(null=True)
    rating_count = models.IntegerField(default=0)  # V3

    class Meta:
        db_table = "users"


class DriverRow(models.Model):
    # Same id as the User this profile belongs to — one Driver profile per User account. No lat/
    # lng columns: that's a Redis GEOSEARCH index in the real system (wired up in M3), same as
    # uber_clone's Driver JPA entity and the FastAPI sibling's DriverRow at this milestone.
    user_id = models.CharField(max_length=255, primary_key=True)
    vehicle_type = models.CharField(max_length=255)
    license_plate = models.CharField(max_length=255)
    is_available = models.BooleanField(default=False)
    avg_rating = models.FloatField(null=True)
    rating_count = models.IntegerField(default=0)  # V3

    class Meta:
        db_table = "drivers"


class RideRow(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    rider_id = models.CharField(max_length=255)
    driver_id = models.CharField(max_length=255, null=True)
    pickup_lat = models.FloatField()
    pickup_lng = models.FloatField()
    dropoff_lat = models.FloatField()
    dropoff_lng = models.FloatField()
    status = models.CharField(max_length=50)
    estimated_fare = models.DecimalField(max_digits=19, decimal_places=2, null=True)
    fare = models.DecimalField(max_digits=19, decimal_places=2, null=True)
    requested_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True)
    # Carried through as a plain counter, not Django's own optimistic-locking support (there
    # isn't any built in) — RideService._accept_lock (a threading.Lock) is what makes concurrent
    # accepts safe here, same deliberate deviation from JPA's @Version already documented in
    # services.py and the FastAPI sibling's db/tables.py.
    version = models.BigIntegerField(default=0)

    class Meta:
        db_table = "rides"
        indexes = [
            models.Index(fields=["rider_id", "requested_at"], name="idx_rides_rider_requested"),
            models.Index(fields=["driver_id", "requested_at"], name="idx_rides_driver_requested"),
            models.Index(fields=["driver_id", "status"], name="idx_rides_driver_status"),
            models.Index(fields=["status", "pickup_lat", "pickup_lng"], name="idx_rides_status_location"),
        ]


class RatingRow(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    ride_id = models.CharField(max_length=255)
    from_user_id = models.CharField(max_length=255)
    to_user_id = models.CharField(max_length=255)
    score = models.IntegerField()
    comment = models.TextField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "ratings"
        constraints = [
            models.UniqueConstraint(fields=["ride_id", "from_user_id"], name="uq_ratings_ride_from"),
        ]
