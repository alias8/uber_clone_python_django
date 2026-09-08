from collections.abc import Iterator

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from pytest_django.plugin import DjangoDbBlocker
from rest_framework.test import APIClient
from testcontainers.community.kafka import KafkaContainer
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from rides.redis_client import get_client

# celery ships celery.contrib.pytest (celery_app/celery_config/celery_worker fixtures) but
# doesn't register it as a pytest11 entry point in this version — it has to be opted into
# explicitly, unlike testcontainers/pytest-django's plugins above, which auto-register.
pytest_plugins = ("celery.contrib.pytest",)


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
    is enough to take effect before anything calls `get_client()` for the first time.

    Also repoints `settings.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"]` (M5) at the same
    container — `channels.layers.get_channel_layer()` lazily builds and caches its backend from
    `settings.CHANNEL_LAYERS` on first access (nothing has accessed it yet this early in the
    session), so this only needs to happen before that first access, same ordering guarantee
    `django_db_setup` relies on for DATABASES."""
    with RedisContainer("redis:7-alpine") as redis_container:
        url = f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}/0"
        django_settings.REDIS_URL = url
        # CHANNEL_LAYERS isn't a setting django-stubs models precisely (it's Channels', not
        # Django's own), so mypy can't narrow the nested dict's value type here.
        django_settings.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"] = [url]  # type: ignore[index]
        yield


@pytest.fixture(scope="session", autouse=True)
def _kafka_setup() -> Iterator[None]:
    """Points `settings.KAFKA_BOOTSTRAP_SERVERS` at a throwaway Kafka broker for the whole test
    session — same idea as `_redis_setup` above. `kafka_producer.get_producer()` re-reads
    `settings.KAFKA_BOOTSTRAP_SERVERS` on every call the same way `redis_client.get_client()`
    re-reads `settings.REDIS_URL`, so a plain reassignment here is enough.

    Uses the confluentinc/cp-kafka image's default startup wait; that's taken ~30-40s locally
    for the FastAPI sibling's own Kafka container (see its conftest.py), budget for that on a
    cold run — much slower than the Postgres/Redis containers above."""
    kafka = KafkaContainer()
    kafka.start()
    try:
        django_settings.KAFKA_BOOTSTRAP_SERVERS = kafka.get_bootstrap_server()
        yield
    finally:
        kafka.stop()


@pytest.fixture(scope="session")
def celery_config(_redis_setup: None) -> dict[str, str]:
    """Celery's own pytest plugin (bundled with the `celery` package, no extra dependency needed)
    reads this fixture to build the `celery_app`/`celery_worker` fixtures used by the one true
    end-to-end test in test_dispatch.py. Depends on `_redis_setup` explicitly so the broker URL
    below is the real testcontainers Redis, not the localhost default."""
    return {"broker_url": django_settings.REDIS_URL, "result_backend": django_settings.REDIS_URL}


@pytest.fixture(scope="session")
def celery_app(celery_config: dict[str, str]) -> object:
    """Overrides celery's default test-app fixture to return *this project's* real Celery app
    (config.celery.app) — the throwaway app the plugin builds otherwise has none of
    rides/tasks.py's tasks registered on it. config/celery.py's `config_from_object` call ran at
    process start against the default (localhost) Redis URL, before `_redis_setup`/`celery_config`
    pointed `settings.REDIS_URL` at the real test container — so the broker/result-backend are
    re-applied here, after that fixture has run."""
    # celery.contrib.testing.worker's start_worker() asserts `celery.ping` is registered before
    # starting a real worker thread (celery_worker fixture, below). That task isn't a real
    # celery builtin — it's a @shared_task defined in celery.contrib.testing.tasks, which
    # celery.contrib.pytest's own default TestApp imports implicitly; a plain Celery() app (this
    # one) only picks it up if that module is imported *before* the app finalizes, since
    # @shared_task registers itself via an on-finalize hook rather than retroactively.
    import celery.contrib.testing.tasks  # noqa: F401

    from config.celery import app as real_app

    real_app.conf.update(**celery_config)
    real_app.finalize()
    return real_app


@pytest.fixture(autouse=True)
def _reset_state(transactional_db: None) -> None:
    """`transactional_db` (not the plain `db` fixture M2/M3 used) commits each test's rows for
    real and truncates between tests, rather than wrapping the test in a transaction that's
    rolled back afterward. That rollback-based isolation is invisible across connections — a
    real Celery worker thread (see test_dispatch.py's one true end-to-end test) opens its own DB
    connection to pick up a task, and would never see a ride only visible inside the test's own
    uncommitted transaction. Slightly slower than plain `db` (a real TRUNCATE beats an
    in-memory rollback), acceptable for this test count.

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
