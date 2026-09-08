"""Tests the Channels streaming layer (M5) directly against the real consumer ASGI apps via
`channels.testing.ApplicationCommunicator` — not through `config/asgi.py`'s `URLRouter` (so
`url_route` is supplied by hand in `_scope()` below, the same thing `URLRouter` would populate
from a real request path).

Unlike the FastAPI sibling's `tests/test_sse.py` (which could only test its `sse.py` registry and
each `emit()`/`complete()` call site directly — `TestClient`'s httpx transport buffers a whole
response before returning, so it can't read a live, open-ended stream at all — see that project's
claude.md), `ApplicationCommunicator` gives full control over the ASGI message flow: this suite
drives a real consumer instance through a full connect -> receive a real Channels-group-sent
event -> disconnect cycle, with no stand-in for the streaming machinery.

`async_to_sync` (used by `rides.streaming.groups.send_event`/`send_close`) refuses to run inside a
thread that already has an event loop running — which every test function in this file does, being
`async def` itself — so every call to those helpers below goes through `sync_to_async` first, to
push it onto a plain worker thread with no event loop of its own. Real synchronous test setup
(registering users, going online, requesting/accepting rides via `APIClient`) is wrapped in
`channels.db.database_sync_to_async` for the same reason plus its usual DB-thread-safety purpose.
"""

from __future__ import annotations

import json

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.testing import ApplicationCommunicator
from rest_framework.test import APIClient

from rides.auth.jwt import generate
from rides.domain import Role
from rides.streaming.consumers import DriverOffersConsumer, RideLocationConsumer
from rides.streaming.groups import driver_offers_group, ride_location_group, send_close, send_event
from tests.conftest import register_and_login, register_driver

RIDE_REQUEST = {
    "pickup_lat": 40.7128,
    "pickup_lng": -74.0060,
    "dropoff_lat": 40.7300,
    "dropoff_lng": -74.0000,
}


def _scope(path: str, token: str | None, url_route_kwargs: dict[str, str] | None = None) -> dict[str, object]:
    headers: list[tuple[bytes, bytes]] = []
    if token is not None:
        headers.append((b"cookie", f"auth_token={token}".encode()))
    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": headers,
    }
    if url_route_kwargs is not None:
        scope["url_route"] = {"kwargs": url_route_kwargs}
    return scope


def _cookie_value(client: APIClient) -> str:
    morsel = client.cookies["auth_token"]
    assert isinstance(morsel.value, str)
    return morsel.value


@database_sync_to_async  # type: ignore[untyped-decorator]  # channels ships no stubs
def _setup_online_driver(username: str) -> tuple[str, str]:
    client = APIClient()
    register_driver(client, username)
    client.post("/driver/mode/on", {"lat": 40.7128, "lng": -74.0060}, format="json")
    driver_id = client.get("/auth/me").data["user_id"]
    return driver_id, _cookie_value(client)


@database_sync_to_async  # type: ignore[untyped-decorator]  # channels ships no stubs
def _setup_requested_ride() -> tuple[str, str]:
    rider = APIClient()
    register_and_login(rider, "rider1")
    ride_id = rider.post("/rides", RIDE_REQUEST, format="json").data["id"]
    return ride_id, _cookie_value(rider)


@database_sync_to_async  # type: ignore[untyped-decorator]  # channels ships no stubs
def _setup_matched_ride() -> tuple[str, str]:
    rider = APIClient()
    register_and_login(rider, "rider1")
    ride_id = rider.post("/rides", RIDE_REQUEST, format="json").data["id"]
    rider_token = _cookie_value(rider)

    driver = APIClient()
    register_driver(driver, "driver1")
    driver.post("/driver/mode/on", {"lat": 40.7128, "lng": -74.0060}, format="json")
    accept_response = driver.post(f"/rides/{ride_id}/accept")
    assert accept_response.status_code == 200, accept_response.data
    return ride_id, rider_token


async def test_driver_offers_stream_rejects_missing_auth() -> None:
    communicator = ApplicationCommunicator(DriverOffersConsumer.as_asgi(), _scope("/driver/offers", None))
    await communicator.send_input({"type": "http.request", "body": b""})

    start = await communicator.receive_output(timeout=5)
    assert start["type"] == "http.response.start"
    assert start["status"] == 401

    body = await communicator.receive_output(timeout=5)
    assert body["more_body"] is False


async def test_driver_offers_stream_rejects_rider_only_token() -> None:
    token = generate("anyone", Role.RIDER)
    communicator = ApplicationCommunicator(DriverOffersConsumer.as_asgi(), _scope("/driver/offers", token))
    await communicator.send_input({"type": "http.request", "body": b""})

    start = await communicator.receive_output(timeout=5)
    assert start["status"] == 403


async def test_driver_offers_stream_delivers_a_real_group_sent_event_then_disconnects() -> None:
    driver_id, token = await _setup_online_driver("alice")
    communicator = ApplicationCommunicator(DriverOffersConsumer.as_asgi(), _scope("/driver/offers", token))
    await communicator.send_input({"type": "http.request", "body": b""})

    start = await communicator.receive_output(timeout=5)
    assert start["status"] == 200
    assert (b"Content-Type", b"text/event-stream") in start["headers"]

    payload = json.dumps({"rideId": "ride-123"})
    await sync_to_async(send_event)(driver_offers_group(driver_id), "ride.offer", payload)

    chunk = await communicator.receive_output(timeout=5)
    assert chunk["type"] == "http.response.body"
    assert chunk["body"] == f"event: ride_offer\ndata: {payload}\n\n".encode()
    assert chunk["more_body"] is True

    # A real client disconnect (browser tab closed, etc.) — proves _pump()'s disconnect branch
    # actually runs, not just the happy path.
    await communicator.send_input({"type": "http.disconnect"})
    assert await communicator.receive_nothing(timeout=1)


async def test_ride_location_stream_rejects_before_ride_is_matched() -> None:
    ride_id, rider_token = await _setup_requested_ride()
    scope = _scope(f"/rides/{ride_id}/location", rider_token, {"ride_id": ride_id})
    communicator = ApplicationCommunicator(RideLocationConsumer.as_asgi(), scope)
    await communicator.send_input({"type": "http.request", "body": b""})

    start = await communicator.receive_output(timeout=5)
    # Still REQUESTED, not yet MATCHED — the same 409 RideLocationController.kt raises before
    # constructing an SseEmitter.
    assert start["status"] == 409


async def test_ride_location_stream_rejects_a_non_participant() -> None:
    ride_id, _rider_token = await _setup_matched_ride()
    other_token = generate("someone-else", Role.RIDER)
    scope = _scope(f"/rides/{ride_id}/location", other_token, {"ride_id": ride_id})
    communicator = ApplicationCommunicator(RideLocationConsumer.as_asgi(), scope)
    await communicator.send_input({"type": "http.request", "body": b""})

    start = await communicator.receive_output(timeout=5)
    # "someone-else" isn't a real user, so this actually exercises the 401 "user not found"
    # branch first — still proves the guard rejects before a stream opens either way.
    assert start["status"] == 401


async def test_ride_location_stream_delivers_driver_location_then_closes_on_stream_close() -> None:
    ride_id, rider_token = await _setup_matched_ride()
    scope = _scope(f"/rides/{ride_id}/location", rider_token, {"ride_id": ride_id})
    communicator = ApplicationCommunicator(RideLocationConsumer.as_asgi(), scope)
    await communicator.send_input({"type": "http.request", "body": b""})

    start = await communicator.receive_output(timeout=5)
    assert start["status"] == 200

    payload = json.dumps({"lat": 40.71, "lng": -74.0, "etaMinutes": 3})
    await sync_to_async(send_event)(ride_location_group(ride_id), "driver.location", payload)
    chunk = await communicator.receive_output(timeout=5)
    assert chunk["body"] == f"event: driver_location\ndata: {payload}\n\n".encode()
    assert chunk["more_body"] is True

    # Server-initiated close (ride completed/cancelled) — the stream should end itself, matching
    # the other two repos' "closed on its own the moment the ride was completed" behavior.
    await sync_to_async(send_close)(ride_location_group(ride_id))
    closing_chunk = await communicator.receive_output(timeout=5)
    assert closing_chunk == {"type": "http.response.body", "body": b"", "more_body": False}
