"""Port of uber_clone's V2__ride_indexes.sql — adds the four `rides` indexes, nothing else."""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("rides", "0001_baseline_schema")]

    operations = [
        migrations.AddIndex(
            model_name="riderow",
            index=models.Index(fields=["rider_id", "requested_at"], name="idx_rides_rider_requested"),
        ),
        migrations.AddIndex(
            model_name="riderow",
            index=models.Index(fields=["driver_id", "requested_at"], name="idx_rides_driver_requested"),
        ),
        migrations.AddIndex(
            model_name="riderow",
            index=models.Index(fields=["driver_id", "status"], name="idx_rides_driver_status"),
        ),
        migrations.AddIndex(
            model_name="riderow",
            index=models.Index(
                fields=["status", "pickup_lat", "pickup_lng"], name="idx_rides_status_location"
            ),
        ),
    ]
