"""Synchronous Redis client, backing the driver geo-index/availability set (dispatch.py,
repositories.py), the surge cache (pricing.py), and rate limiting (rate_limit.py) — ported from
RedisConfig.kt's connection setup.

Unlike the FastAPI sibling's redis_client.py, this hands back one process-wide singleton rather
than one client per running event loop. That workaround exists there specifically because an
asyncio Redis client is bound to the event loop that created it, and FastAPI's TestClient gives
each test its own loop. This app is synchronous end to end (see repositories.py's docstring) —
there is no event loop for a client to get bound to, so the plain-singleton case that comment
warns FastAPI *away* from is exactly the safe case here.

Cached by URL rather than created once at import time so tests can point `settings.REDIS_URL` at
a throwaway testcontainers instance before first use (see tests/conftest.py) and still get a
fresh client for it.
"""

from __future__ import annotations

import redis
from django.conf import settings

_client: redis.Redis | None = None
_client_url: str | None = None


def get_redis_client() -> redis.Redis:
    global _client, _client_url
    url = settings.REDIS_URL
    if _client is None or _client_url != url:
        _client = redis.Redis.from_url(url, decode_responses=True)
        _client_url = url
    return _client
