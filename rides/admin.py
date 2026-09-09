"""Registers rides/models.py's ORM rows with django.contrib.admin — added purely as a live demo
of Django's built-in admin CRUD UI (auto-generated from these model definitions, no custom views
needed). Not otherwise part of the ported ride-hailing domain; see rides/models.py for what these
rows represent.
"""

from __future__ import annotations

from django.contrib import admin

from rides.models import DriverRow, RatingRow, RideRow, UserRow

# django-stubs types ModelAdmin as generic (ModelAdmin[_ModelT]), but the real runtime class isn't
# subscriptable unless django_stubs_ext.monkeypatch() has run (this project doesn't call it) —
# subscripting it here would blow up django.contrib.admin's autodiscover at import time. Bare
# `admin.ModelAdmin` is the correct runtime base; each class below carries a documented
# `type: ignore[type-arg]` for mypy --strict rather than adding that monkeypatch call project-wide
# for four admin classes, same targeted-ignore approach as claude.md's other stub gaps.


@admin.register(UserRow)
class UserRowAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("id", "username", "role", "avg_rating", "rating_count")
    search_fields = ("id", "username")
    list_filter = ("role",)


@admin.register(DriverRow)
class DriverRowAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("user_id", "vehicle_type", "license_plate", "is_available", "avg_rating", "rating_count")
    search_fields = ("user_id", "license_plate")
    list_filter = ("is_available", "vehicle_type")


@admin.register(RideRow)
class RideRowAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("id", "rider_id", "driver_id", "status", "fare", "requested_at", "completed_at")
    search_fields = ("id", "rider_id", "driver_id")
    list_filter = ("status",)


@admin.register(RatingRow)
class RatingRowAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("id", "ride_id", "from_user_id", "to_user_id", "score", "created_at")
    search_fields = ("id", "ride_id", "from_user_id", "to_user_id")
    list_filter = ("score",)
