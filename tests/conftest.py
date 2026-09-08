from collections.abc import Iterator

import pytest
from rest_framework.test import APIClient

from rides import state


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    """All state lives in process-wide singletons for M1 — reset them between tests. No view in
    this milestone touches the ORM/sqlite database at all (see conftest — no `django_db` marker
    needed anywhere), so this is the only per-test reset required."""
    state.user_repository.clear()
    state.driver_repository.clear()
    state.ride_repository.clear()
    state.rating_repository.clear()
    state.pricing_service.clear_cache()
    state.ride_request_rate_limiter.clear()
    state.auth_attempt_rate_limiter.clear()
    yield


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
