"""Redis-backed fixed-window rate limiter, ported from uber_clone's Bucket4j-based
RateLimiterService.kt.

Fixed-window counter (`INCR` + `EXPIRE` on the first hit of a window), not a true token bucket —
same simplification the FastAPI sibling made against Bucket4j via the `limits` package, just
hand-rolled here rather than pulling in a library, since a single-command-pair `INCR`/`EXPIRE` is
all a fixed window needs. Key format matches `RateLimiterService.kt` exactly
(`rate_limit:ride_request:{key}` / `rate_limit:auth:{key}`), passed in via `key_prefix` so this
class stays generic across both limiters `state.py` constructs.

`INCR` is atomic in Redis, so — unlike M1's in-memory version — no local lock is needed even
though this Django app is otherwise synchronous and single-threaded per worker; concurrent
requests across multiple workers/processes now share one real counter, which the in-memory
version never could.
"""

from __future__ import annotations

from typing import cast

from rides.redis_client import get_client


class RateLimiter:
    def __init__(self, capacity: int, window_seconds: int, key_prefix: str) -> None:
        self._capacity = capacity
        self._window_seconds = window_seconds
        self._key_prefix = key_prefix

    def allow(self, key: str) -> bool:
        redis_key = f"{self._key_prefix}{key}"
        client = get_client()
        # redis-py's `incr()` stub returns a union that includes Awaitable[Any] (its sync/async
        # client classes share overload signatures) even though this project only ever uses the
        # synchronous client — same kind of targeted cast pricing.py's SurgeCache already needed
        # for `get()`, see claude.md.
        count = cast("int", client.incr(redis_key))
        if count == 1:
            client.expire(redis_key, self._window_seconds)
        return bool(count <= self._capacity)
