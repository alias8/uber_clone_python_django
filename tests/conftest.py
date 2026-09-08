from collections.abc import Iterator

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from pytest_django.plugin import DjangoDbBlocker
from rest_framework.test import APIClient
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from rides.redis_client import get_client


@pytest.fixture(scope="session")
def django_db_setup(django_db_blocker: DjangoDbBlocker | None) -> Iterator[None]:
    """Spins up a throwaway Postgres container for the whole test session and points Django's
    `default` database at it, then runs migrations against it — same idea as the FastAPI
    sibling's own `_postgres` fixture (a real Postgres via testcontainers, not sqlite), adapted
    to pytest-django's `django_db_setup` override point. Docker must be running.

    Mutates `django.conf.settings` directly rather than depending on pytest-django's own
    `settings` fixture — that fixture is function-scoped (it exists to auto-restore per-test
    overrides), and this fixture is session-scoped, so requesting it here is a fixture-scope
    mismatch pytest rejects outright.

    Updates the existing `DATABASES["default"]` dict **in place** (`.update()`) rather than
    replacing it with a new dict object. By the time this fixture body runs, pytest-django's own
    `django_db_blocker` setup has already forced Django's `ConnectionHandler` to create (and
    cache) a connection wrapper for the `default` alias from the original dict object — that
    wrapper keeps its own reference to that exact dict as `settings_dict`. Replacing the
    `"default"` key with a brand-new dict would leave the already-cached wrapper pointed at the
    stale one; updating it in place is visible to every reference to it, cached or not."""
    assert django_db_blocker is not None
    with PostgresContainer("postgres:16-alpine") as pg:
        django_settings.DATABASES["default"].update(
            {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": pg.dbname,
                "USER": pg.username,
                "PASSWORD": pg.password,
                "HOST": pg.get_container_host_ip(),
                "PORT": str(pg.get_exposed_port(5432)),
            }
        )
        with django_db_blocker.unblock():
            call_command("migrate", "--run-syncdb", verbosity=0)
        yield


@pytest.fixture(scope="session", autouse=True)
def _redis_setup() -> Iterator[None]:
    """Points `settings.REDIS_URL` at a throwaway Redis container for the whole test session —
    same idea as `django_db_setup` above, but simpler: `redis_client.get_client()` re-reads
    `settings.REDIS_URL` on every call and only rebuilds its cached client when the value
    changes, so a plain reassignment here (not an in-place mutation, unlike the DATABASES dict)
    is enough to take effect before anything calls `get_client()` for the first time."""
    with RedisContainer("redis:7-alpine") as redis_container:
        django_settings.REDIS_URL = (
            f"redis://{redis_container.get_container_host_ip()}:"
            f"{redis_container.get_exposed_port(6379)}/0"
        )
        yield


@pytest.fixture(autouse=True)
def _reset_state(db: None) -> None:
    """Depending on pytest-django's `db` fixture gives every test its own transaction against
    the real Postgres container, rolled back afterward — that's what resets DB rows, the same
    guarantee the FastAPI sibling gets from a manual per-test TRUNCATE, just automatic here.

    Everything else this app keeps process-local state in (the driver geo-index/availability
    set, the surge cache, both rate limiters) now lives in the same real Redis container, so one
    `FLUSHDB` clears all of it at once — same approach the FastAPI sibling's own conftest.py
    uses, and the reason none of those classes has a `.clear()` method anymore (see
    MILESTONE_NOTES.md's M3 entry)."""
    get_client().flushdb()


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def register_and_login(client: APIClient, username: str, password: str = "hunter2") -> APIClient:
    """Registers a new rider; the client's cookie jar is left authenticated as them."""
    response = client.post("/auth/register", {"username": username, "password": password}, format="json")
    assert response.status_code == 201, response.data
    return client


def register_driver(
    client: APIClient, username: str, vehicle_type: str = "sedan", license_plate: str = "ABC123"
) -> APIClient:
    register_and_login(client, username)
    response = client.post(
        "/driver/register", {"vehicle_type": vehicle_type, "license_plate": license_plate}, format="json"
    )
    assert response.status_code == 201, response.data
    return client
