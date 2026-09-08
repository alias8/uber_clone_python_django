"""Django Channels consumers for the two real-time streams the Kotlin/FastAPI siblings expose as
SSE: a driver's ride-offer stream (`GET /driver/offers`) and a rider's ride-location stream
(`GET /rides/{id}/location`). See README's Streaming section for why these stay genuine
SSE-over-HTTP rather than becoming WebSocket consumers, and rides/streaming/groups.py's docstring
for why group delivery replaces the Redis pub/sub bridge the FastAPI sibling needed.

Why this doesn't just subclass `channels.generic.http.AsyncHttpConsumer` and define per-event-type
handler methods the way a WebSocket consumer would (that was the first design tried here, and it's
worth recording why it was wrong): `AsyncConsumer.__call__` (the base every Channels consumer
shares) feeds an *outer* dispatch loop from two concurrently-polled sources when a channel layer
is configured — the ASGI `receive` callable, and `self.channel_layer.receive(self.channel_name)`
— and calls `await self.dispatch(message)` for whichever completes, name-mapping "ride.offer" to
a `ride_offer()` handler method, exactly like a WebSocket consumer's group-message handlers. That
works for WebSocket consumers because `connect()`/`receive()`/each `<type>` handler all return
quickly, letting the loop cycle fast between messages from either source. It does NOT work for
`AsyncHttpConsumer`: `http_request()` calls `await self.handle(body)` *once*, and the *entire*
outer loop is blocked inside that one `dispatch()` call for as long as `handle()` runs — so a
long-lived `handle()` (needed to keep an SSE response open) would starve the framework's own
automatically-polled channel-layer task from ever completing, and worse, that outer task is still
concurrently racing to pop from the *same* per-channel queue this module needs to read from
itself, so messages would occasionally be silently stolen by a dispatch call with no matching
`<type>` handler defined, crashing the connection.

The fix: `_SseStreamConsumer` overrides `__call__` to skip that outer dispatch loop entirely
(no `self.dispatch()`, no `http_request`/`http_disconnect` framework handlers) and instead has
`handle()` run its own loop, directly and exclusively multiplexing the ASGI `receive` callable
(to notice a client disconnect) against `self.channel_layer.receive(self.channel_name)` (to
notice a group-sent event or a server-initiated close) via `asyncio.wait(..., FIRST_COMPLETED)`.
`AsyncHttpConsumer` is still the base class purely for its `send_headers`/`send_body` helpers.

Guard order matches the other two repos exactly: role/ownership/status checks run — and can
reject with a plain (non-streaming) HTTP response — *before* `send_headers()` ever opens the
event-stream response, mirroring `DriverOfferController.kt`/`RideLocationController.kt`'s
`@PreAuthorize` + explicit checks firing before an `SseEmitter`/`StreamingResponse` is created.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from channels.db import database_sync_to_async
from channels.generic.http import AsyncHttpConsumer
from channels.layers import get_channel_layer

from rides.domain import RideStatus, Role
from rides.state import ride_repository, user_repository
from rides.streaming.auth import Headers, StreamGuardError, resolve_active_role
from rides.streaming.groups import driver_offers_group, ride_location_group

_SSE_HEADERS = [
    (b"Content-Type", b"text/event-stream"),
    (b"Cache-Control", b"no-cache"),
]


def _format_sse(event: str, payload: str) -> bytes:
    return f"event: {event}\ndata: {payload}\n\n".encode()


class _SseStreamConsumer(AsyncHttpConsumer):  # type: ignore[misc]  # channels ships no stubs — see claude.md
    """Shared plumbing — see module docstring for why `__call__` is overridden. Subclasses
    implement `_authorize()` (sets `self.group` or raises `StreamGuardError`) and only need to
    provide that; the connect/pump/disconnect/close mechanics below are identical for both
    streams."""

    group: str

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.scope = scope
        self.channel_layer = get_channel_layer(self.channel_layer_alias)
        assert self.channel_layer is not None, "CHANNEL_LAYERS must be configured"
        self.channel_name = await self.channel_layer.new_channel()
        self.base_send = send
        await self.handle(receive)

    async def handle(self, receive: Any) -> None:
        try:
            await self._authorize()
        except StreamGuardError as exc:
            await self.send_headers(status=exc.status)
            await self.send_body(b"")
            return

        await self.send_headers(status=200, headers=_SSE_HEADERS)
        await self.channel_layer.group_add(self.group, self.channel_name)
        try:
            await self._pump(receive)
        finally:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def _pump(self, receive: Any) -> None:
        """Concurrently waits on the client's ASGI receive channel (to notice a disconnect) and
        this consumer's channel-layer queue (to notice a group-sent event or a server-initiated
        `stream.close`), for as long as the stream should stay open."""
        recv_task = asyncio.ensure_future(receive())
        chan_task = asyncio.ensure_future(self.channel_layer.receive(self.channel_name))
        try:
            while True:
                done, _pending = await asyncio.wait(
                    [recv_task, chan_task], return_when=asyncio.FIRST_COMPLETED
                )
                if recv_task in done:
                    message = recv_task.result()
                    if message["type"] == "http.disconnect":
                        return
                    recv_task = asyncio.ensure_future(receive())
                if chan_task in done:
                    message = chan_task.result()
                    if message["type"] == "stream.close":
                        await self.send_body(b"", more_body=False)
                        return
                    event_name = message["type"].replace(".", "_")
                    await self.send_body(_format_sse(event_name, message["payload"]), more_body=True)
                    chan_task = asyncio.ensure_future(self.channel_layer.receive(self.channel_name))
        finally:
            for task in (recv_task, chan_task):
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    async def _authorize(self) -> None:
        raise NotImplementedError


class DriverOffersConsumer(_SseStreamConsumer):
    """Ported from DriverOfferController.kt / the FastAPI sibling's `stream_offers`."""

    async def _authorize(self) -> None:
        headers: Headers = self.scope["headers"]
        username, authorities = resolve_active_role(headers)
        if Role.DRIVER not in authorities:
            raise StreamGuardError(403, "Requires active DRIVER mode")
        user = await database_sync_to_async(user_repository.find_by_username)(username)
        if user is None:
            raise StreamGuardError(401, "Not authenticated")
        self.group = driver_offers_group(user.id)


class RideLocationConsumer(_SseStreamConsumer):
    """Ported from RideLocationController.kt / the FastAPI sibling's `stream_driver_location`."""

    _TRACKABLE_STATUSES = (RideStatus.MATCHED, RideStatus.IN_PROGRESS)

    async def _authorize(self) -> None:
        ride_id: str = self.scope["url_route"]["kwargs"]["ride_id"]
        headers: Headers = self.scope["headers"]
        username, authorities = resolve_active_role(headers)
        if Role.RIDER not in authorities:
            # Every authenticated user always holds RIDER authority (see resolve_active_role) —
            # unreachable today, kept for symmetry with DriverOffersConsumer's role check.
            raise StreamGuardError(403, "Requires active RIDER mode")
        user = await database_sync_to_async(user_repository.find_by_username)(username)
        if user is None:
            raise StreamGuardError(401, "Not authenticated")
        ride = await database_sync_to_async(ride_repository.find_by_id)(ride_id)
        if ride is None:
            raise StreamGuardError(404, "Ride not found")
        if ride.rider_id != user.id:
            raise StreamGuardError(403, "Not a participant in this ride")
        if ride.status not in self._TRACKABLE_STATUSES:
            raise StreamGuardError(409, "No active driver to track for this ride")
        self.group = ride_location_group(ride_id)
