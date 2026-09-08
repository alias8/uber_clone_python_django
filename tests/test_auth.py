"""Ported from AuthController.kt's behavior — register/login/switch-mode/me."""

from rest_framework.test import APIClient

from rides import state


def test_register_issues_httponly_cookie_and_creates_rider(client: APIClient) -> None:
    response = client.post("/auth/register", {"username": "alice", "password": "hunter2"}, format="json")
    assert response.status_code == 201
    assert "auth_token" in response.cookies
    assert response.json()["token"]
    user = state.user_repository.find_by_username("alice")
    assert user is not None
    assert user.role.value == "RIDER"


def test_register_rejects_duplicate_username(client: APIClient) -> None:
    client.post("/auth/register", {"username": "alice", "password": "hunter2"}, format="json")
    response = client.post("/auth/register", {"username": "alice", "password": "other"}, format="json")
    assert response.status_code == 409


def test_login_succeeds_with_correct_credentials(client: APIClient) -> None:
    client.post("/auth/register", {"username": "alice", "password": "hunter2"}, format="json")
    response = client.post("/auth/login", {"username": "alice", "password": "hunter2"}, format="json")
    assert response.status_code == 200
    assert response.json()["token"]


def test_login_rejects_wrong_password(client: APIClient) -> None:
    client.post("/auth/register", {"username": "alice", "password": "hunter2"}, format="json")
    response = client.post("/auth/login", {"username": "alice", "password": "wrong"}, format="json")
    assert response.status_code == 401


def test_login_rejects_unknown_username(client: APIClient) -> None:
    response = client.post("/auth/login", {"username": "nobody", "password": "x"}, format="json")
    assert response.status_code == 401


def test_auth_attempts_are_rate_limited(client: APIClient) -> None:
    client.post("/auth/register", {"username": "alice", "password": "hunter2"}, format="json")
    responses = [
        client.post("/auth/login", {"username": "alice", "password": "wrong"}, format="json")
        for _ in range(15)
    ]
    assert any(r.status_code == 429 for r in responses)


def test_me_requires_authentication(client: APIClient) -> None:
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_returns_current_user_id(client: APIClient) -> None:
    client.post("/auth/register", {"username": "alice", "password": "hunter2"}, format="json")
    response = client.get("/auth/me")
    assert response.status_code == 200
    user = state.user_repository.find_by_username("alice")
    assert user is not None
    assert response.json()["user_id"] == user.id


def test_switch_mode_to_driver_requires_permanent_driver_role(client: APIClient) -> None:
    client.post("/auth/register", {"username": "alice", "password": "hunter2"}, format="json")
    response = client.post("/auth/switch-mode", {"mode": "driver"}, format="json")
    assert response.status_code == 403


def test_switch_mode_to_driver_succeeds_after_driver_registration(client: APIClient) -> None:
    client.post("/auth/register", {"username": "alice", "password": "hunter2"}, format="json")
    client.post("/driver/register", {"vehicle_type": "sedan", "license_plate": "ABC123"}, format="json")
    # /driver/register already reissues a DRIVER-mode cookie, but switch-mode should also work.
    response = client.post("/auth/switch-mode", {"mode": "rider"}, format="json")
    assert response.status_code == 200
    back_to_driver = client.post("/auth/switch-mode", {"mode": "driver"}, format="json")
    assert back_to_driver.status_code == 200


def test_switch_mode_rejects_unknown_mode(client: APIClient) -> None:
    client.post("/auth/register", {"username": "alice", "password": "hunter2"}, format="json")
    response = client.post("/auth/switch-mode", {"mode": "admin"}, format="json")
    assert response.status_code == 400
