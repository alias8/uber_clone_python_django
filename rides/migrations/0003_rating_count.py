"""Port of uber_clone's V3__rating_count.sql — adds `rating_count` to `users` and `drivers`,
then backfills it from the existing `ratings` rows (a no-op on a fresh database, but faithful to
what the Kotlin migration actually does for a database with pre-existing data).
"""

from __future__ import annotations

from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def backfill_rating_counts(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    UserRow = apps.get_model("rides", "UserRow")
    DriverRow = apps.get_model("rides", "DriverRow")
    RatingRow = apps.get_model("rides", "RatingRow")

    for user in UserRow.objects.all():
        user.rating_count = RatingRow.objects.filter(to_user_id=user.id).count()
        user.save(update_fields=["rating_count"])

    for driver in DriverRow.objects.all():
        driver.rating_count = RatingRow.objects.filter(to_user_id=driver.user_id).count()
        driver.save(update_fields=["rating_count"])


def noop_reverse(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    pass


class Migration(migrations.Migration):
    dependencies = [("rides", "0002_ride_indexes")]

    operations = [
        migrations.AddField(model_name="userrow", name="rating_count", field=models.IntegerField(default=0)),
        migrations.AddField(
            model_name="driverrow", name="rating_count", field=models.IntegerField(default=0)
        ),
        migrations.RunPython(backfill_rating_counts, noop_reverse),
    ]
