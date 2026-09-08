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
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
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
