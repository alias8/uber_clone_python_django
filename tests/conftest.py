from collections.abc import Iterator

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from pytest_django.plugin import DjangoDbBlocker
from rest_framework.test import APIClient
from testcontainers.community.postgres import PostgresContainer

from rides import state


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


@pytest.fixture(autouse=True)
def _reset_state(db: None) -> None:
    """Depending on pytest-django's `db` fixture gives every test its own transaction against
    the real Postgres container, rolled back afterward — that's what resets DB rows, the same
    guarantee the FastAPI sibling gets from a manual per-test TRUNCATE, just automatic here.
    Process-local state (the driver location overlay, the pricing cache, both rate limiters)
    isn't a database row, so it still needs clearing directly — `driver_repository.clear()` also
    issues a (redundant-with-the-rollback, but harmless) DriverRow delete alongside that."""
    state.driver_repository.clear()
    state.pricing_service.clear_cache()
    state.ride_request_rate_limiter.clear()
    state.auth_attempt_rate_limiter.clear()


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
