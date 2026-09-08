"""Django settings for the uber_clone-django project.

Postgres-backed as of M2 — see rides/models.py for the ORM models and rides/repositories.py for
the translation to/from the plain-dataclass domain types in rides/domain.py.
"""

from __future__ import annotations

import os

# Placeholder, dev-only — matches the sibling FastAPI project's own "change-me" default. Real
# deployments would pull this from the environment; there are none for this repo (see README).
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-change-me-in-production")

DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    # "daphne" first: Channels' documented pattern for making `manage.py runserver` itself serve
    # ASGI_APPLICATION (below) via Daphne instead of Django's default WSGI dev server — this repo
    # has no django.contrib.staticfiles installed, so there's no ordering conflict with that app's
    # own runserver override to worry about.
    "daphne",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "channels",
    "rides",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "uber_clone_django"),
        "USER": os.environ.get("POSTGRES_USER", "postgres"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rides.auth.authentication.JWTCookieAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "UNAUTHENTICATED_USER": None,
}

# --- Domain config (ported from ride_service/config.py's pydantic-settings Settings class) ---

JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-to-a-long-random-secret-in-production")
JWT_EXPIRATION_MS = 2_592_000_000  # 30 days, matches uber_clone's application.properties
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

RIDE_REQUEST_LIMIT_PER_MINUTE = 5
AUTH_ATTEMPTS_LIMIT_PER_15_MIN = 10

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

# --- Celery (M4): the async/background-work side of this project, see config/celery.py and
# rides/tasks.py. Redis (already required from M3) doubles as the broker and result backend —
# no reason to introduce a second broker technology just for task queuing.
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
# The stale-ride retry job (StaleRideRetryJob.kt / the FastAPI sibling's asyncio-sleep loop) is
# a Celery Beat periodic task here instead — same 60s interval.
CELERY_BEAT_SCHEDULE = {
    "retry-stale-rides": {
        "task": "rides.retry_stale_rides",
        "schedule": 60.0,
    },
}

# --- Channels (M5): real-time delivery for the driver-offers and ride-location streams, see
# rides/streaming/. Backed by the same Redis this project already requires (M3) — a
# `channels_redis.core.RedisChannelLayer` group_send is genuinely cross-process (unlike
# `rides/redis_client.py`'s raw pub/sub, which needed rides/dispatch.py's M4 in-process listener
# on the *consuming* side), so M4's Celery worker process can call it directly to notify a driver
# whose SSE connection lives in the ASGI (Daphne) process, with no separate bridge process
# needed — see README's Streaming section for the fuller "why".
#
# tests/conftest.py's `_redis_setup` fixture repoints `REDIS_URL` at a throwaway container *and*
# mutates this dict's "hosts" list in place (not a fresh dict) before anything constructs a
# channel layer for the first time — same "mutate in place, don't replace the dict" lesson M2's
# DATABASES fixture already documented, applied here on the same kind of already-possibly-cached
# settings object.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}
