"""Channel-layer group naming and a tiny group_send helper, shared between the streaming
consumers (rides/streaming/consumers.py) and whichever process produces an event: M4's Celery
tasks (rides/tasks.py, rides/dispatch.py, running in a worker process) and the synchronous
request-handling code in rides/services.py (running in the Daphne/ASGI process itself, for the
one event — `driver_location` — that's produced in-process).

This replaces the raw Redis pub/sub channel M4 used as a placeholder (`ride_offers:{driverId}`,
written by dispatch.py, read by nothing) with `channels_redis`'s own group primitive. Both are
Redis-backed, so this isn't "adding infrastructure" — it's routing the same cross-process signal
through Channels' own delivery mechanism instead of a hand-rolled one, since a bridge process that
re-reads that raw pub/sub channel just to hand messages to Channels would be redundant: the
Celery worker can call `group_send` directly and reach a driver's ASGI-process connection with no
middleman. See README's Streaming section for the fuller comparison against the FastAPI sibling's
`ride_offer_listener.py` bridge (needed there because asyncio's in-process `sse.py` registry has
no cross-process delivery mechanism of its own).
"""

from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def driver_offers_group(driver_id: str) -> str:
    return f"driver_offers_{driver_id}"


def ride_location_group(ride_id: str) -> str:
    return f"ride_location_{ride_id}"


def send_event(group: str, message_type: str, payload: str) -> None:
    """Sends {"type": message_type, "payload": payload} to `group`. `message_type` uses
    Channels' dot-to-underscore convention for its own dispatch mechanism (e.g. "ride.offer") —
    this project's consumers don't use that dispatch path (see consumers.py's module docstring
    for why), but the convention is kept anyway for readability/consistency with how Channels
    documentation names these.

    Callable from sync code (Celery tasks, Django views) via `async_to_sync` — Channels'
    documented pattern for driving an async channel layer from a synchronous caller. A no-op if
    no channel layer is configured (there always is one in this project; the None-check just
    matches `get_channel_layer()`'s own possibly-None return type).
    """
    channel_layer = get_channel_layer()
    if channel_layer is not None:
        async_to_sync(channel_layer.group_send)(group, {"type": message_type, "payload": payload})


def send_close(group: str) -> None:
    """Signals the consumer holding `group` open to end its stream — the Channels-native
    equivalent of `EmitterRegistry.kt`'s `emitters.remove(key)?.complete()` / the FastAPI
    sibling's `sse.complete()`."""
    channel_layer = get_channel_layer()
    if channel_layer is not None:
        async_to_sync(channel_layer.group_send)(group, {"type": "stream.close"})
