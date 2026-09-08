# Milestone notes

Working notes from building this project with Claude Code — what shipped, what broke along the
way, and what got fixed. `README.md` is the source of truth for current architecture; this file
is the session-by-session history behind it; `claude.md` carries the standing conventions this
history produced.

## Milestone 1 — Django/DRF skeleton, JWT auth, ride state machine, in-process pricing

- Django project (`config/`) + one app (`rides/`), Django REST Framework for the API layer.
  In-memory repositories (`repositories.py`) backing plain dataclasses (`domain.py`) — no
  Postgres/Redis/Celery/Channels yet, matching how both sibling repos' own milestone 1 worked
  before their infra milestones landed.
- Ported the domain logic faithfully from `uber_clone`'s Kotlin source, cross-checked against
  `uber_clone-python`'s own port of the same logic where a design choice wasn't obvious from the
  Kotlin alone (e.g. the accept-ride lock, the rating-score 400-not-422 choice): JWT-in-cookie
  dual-role auth, the full ride state machine, in-process fare/surge pricing, naive in-memory
  driver dispatch, in-memory rate limiting, ratings.
- DRF-idiomatic choices made deliberately, not just copied from FastAPI's shape — see README's
  "Ported faithfully vs. reimplemented" for the full list: `Serializer` (not `ModelSerializer`)
  subclasses standing in for Pydantic schemas; `APIView`s for the non-resource-shaped auth/driver
  endpoints; one real `ViewSet` + `DefaultRouter` for the resource-shaped `rides` endpoints
  (`create`/`retrieve` plus five `@action` state-machine transitions); a `require_role()`
  permission-class factory standing in for FastAPI's `Depends(require_role(...))`.
- **Real bug, caught by the test suite, not by inspection**: 5 of the ported "401 for missing/
  invalid auth" tests initially failed with 403 instead. Root cause: DRF's
  `APIView.handle_exception()` has a specific rule — `NotAuthenticated`/`AuthenticationFailed`
  gets silently downgraded from 401 to 403 whenever the authentication class's
  `authenticate_header()` returns a falsy value, and `BaseAuthentication`'s default
  implementation returns `None`. This is intentional DRF behavior for session-style auth (a 403
  with no `WWW-Authenticate` challenge is arguably more correct there), but it doesn't match
  `uber_clone`/`uber_clone-python`, both of which always return 401 for missing/invalid auth and
  never use a 403 for that case. Fixed with a one-method override —
  `JWTCookieAuthentication.authenticate_header()` returning `"Bearer"` — which tells DRF's
  exception handling "yes, challenge with 401." Cost about ten minutes to trace from "five
  seemingly unrelated 403s" to this one specific DRF internal rule; worth knowing about for any
  future custom DRF authentication class, not just this one.
- `mypy --strict` clean with one accepted, documented exception:
  `djangorestframework-stubs` doesn't overload `Serializer.__init__` on `many=True`, so the three
  call sites that serialize a list (`RideViewSet.history`, `DriverRidesView.get`,
  `DriverNearbyView.get`) carry a targeted `# type: ignore[arg-type]` rather than a
  project-wide type-safety downgrade. See `claude.md`'s "The one accepted mypy-strict exception"
  for the reasoning against alternatives (parametrizing every response serializer as
  `Serializer[Any]` would have thrown away real checking on the far more common single-instance
  call sites).
- Manually verified end-to-end against a real running dev server (`python manage.py runserver`):
  `GET /health`, `POST /auth/register` (issues an httponly cookie), and `POST /rides` (returns a
  real estimated fare) all confirmed via `curl` with real cookie-jar round-tripping, not just the
  test suite.
- 55 tests passing, `ruff check .` and `mypy` clean.

## Milestone 2 — Postgres

- Real Django ORM models (`rides/models.py`: `UserRow`/`DriverRow`/`RideRow`/`RatingRow`)
  replacing the in-memory dataclass repos from M1. `domain.py` survived as-is — it keeps the
  plain dataclasses `services.py` operates on, unchanged; `repositories.py` is the only module
  translating between the two, mirroring the FastAPI sibling's `models.py`/`db/tables.py` split
  (see `claude.md`).
- Three migrations (`rides/migrations/0001_baseline_schema.py`/`0002_ride_indexes.py`/
  `0003_rating_count.py`), hand-written rather than a single `makemigrations` run through to the
  final schema, so the migration history mirrors `uber_clone`'s `V1`/`V2`/`V3` Flyway migrations
  one-to-one: 0001 creates all four tables plus the `ratings` unique constraint (matching V1
  exactly, including that constraint living in the baseline migration, not a later one); 0002
  adds just the four `rides` indexes (matching V2); 0003 adds `rating_count` to `users`/`drivers`
  plus a `RunPython` backfill from existing `ratings` rows (matching V3's `UPDATE` statements —
  a no-op on this project's always-fresh database, but faithful to what the Kotlin migration
  actually does against one with existing data). `makemigrations --check --dry-run` confirms
  these three, applied in order, produce exactly the schema `models.py` describes — no drift.
  Verified the resulting Postgres schema column-for-column against `V1`-`V3` directly with
  `psql \d`; the only differences are ones Django adds automatically for every `CharField`
  primary key/unique column (`_like` btree indexes for pattern-matching), not anything this port
  chose.
- Kept driver lat/lng out of Postgres entirely, matching `uber_clone`'s `Driver` JPA entity
  (no such column) and the FastAPI sibling's own M2 — `DriverRepository` keeps an in-memory
  `_locations` overlay dict, merged onto rows read from Postgres, until M3 replaces it with a
  real Redis geo-index.
- One deliberate faithfulness choice against the FastAPI sibling rather than in agreement with
  it: `DriverRow.user_id` has no `ForeignKey("users.id")` here, because the literal
  `V1__baseline_schema.sql` doesn't declare that constraint either (just a bare primary key) —
  the FastAPI sibling's SQLAlchemy table added one anyway. Matched the actual migration SQL over
  matching the sibling's ORM.
- Tests moved from M1's pure in-memory stores to a real throwaway Postgres via `testcontainers`
  for the whole session (`tests/conftest.py`'s `django_db_setup`), same idea as the FastAPI
  sibling's own `_postgres` fixture. Per-test isolation is simpler here than the sibling's manual
  `TRUNCATE`: depending on pytest-django's built-in `db` fixture wraps each test in its own
  transaction and rolls it back automatically.
- **Real bug hit while wiring up `django_db_setup`**: repointing Django at the testcontainers
  Postgres by replacing `settings.DATABASES["default"]` with a brand-new dict silently didn't
  work — every test kept trying to connect to the `postgres`/`localhost` fallback baked into
  `config/settings.py`, even though the fixture had clearly "set" the new URL first. Root cause:
  by the time that fixture body runs, pytest-django's own `django_db_blocker` setup has already
  forced Django's `ConnectionHandler` to create and cache a connection wrapper for the `default`
  alias from the *original* dict object, and that wrapper keeps its own reference to it —
  replacing the dict swaps what `settings.DATABASES["default"]` points to, but the already-cached
  wrapper never looks there again. Fixed by mutating the existing dict **in place**
  (`django_settings.DATABASES["default"].update({...})`) instead of replacing it, since every
  existing reference (cached wrapper included) points at that same dict object. Took a short
  detour with a standalone `python -c` repro against a real Django shell to confirm the caching
  behavior before landing on the fix.
- Two straightforward mypy-strict fixes, both one-liners: a migration's `dependencies = []`
  needed to drop an explicit `list[tuple[str, str]]` type annotation (django-stubs types
  `Migration.dependencies` as a class variable; re-annotating it in a subclass reads as
  overriding a class variable with an instance one); the testcontainers `PORT` value needed
  `str(...)` around it since `DATABASES["default"]` is typed `dict[str, str]`.
- Manually verified the full ride lifecycle against a real local Postgres (register → driver
  register → go online → request ride → accept → start → complete → rate) via `curl`, then
  killed and restarted the dev server and confirmed via `psql` that the completed ride and the
  driver's updated `avg_rating`/`rating_count` both survived — proof this is no longer in-memory.
- 55 tests passing (unchanged from M1 — this milestone was a backing-store swap, not new
  endpoints), `ruff check .` and `mypy` clean.

## Milestone 3 — Redis

- Driver geo-index (`drivers:locations`, real `GEOSEARCH`) and availability set
  (`drivers:available`) replace the M2 in-memory `_locations` overlay dict — `dispatch.py`'s
  `find_nearby_available_drivers()` does the reads directly against Redis (no more `list[Driver]`
  argument — it queries Redis itself now); `repositories.py`'s new
  `DriverRepository.set_location()`/`clear_location()` do the writes. `Driver.lat`/`Driver.lng`
  are gone from `domain.py` entirely — location was never anything but an overlay for this
  dataclass, and now it isn't even that.
- `DriverRepository.save()` keeps `is_available` a genuine dual write, same as `DriverService.kt`:
  a Postgres column update plus a `drivers:available` Redis `SADD`/`SREM` — kept strictly separate
  from location, on purpose, per the M2 note above about the FastAPI sibling's real bug (bundling
  the two would wipe a driver's position out of Redis on every plain availability toggle, since
  `mark_available_by_id`/`mark_unavailable_by_id` always re-fetch the driver first with no location
  info).
- Surge cache (`pricing.py`'s `SurgeCache`) moved from an in-memory dict to real Redis
  (`SET key value EX 30`/`GET key`, same `surge:{lat}:{lng}` grid key format as before). Rate
  limiting (`rate_limit.py`) moved from an in-memory fixed-window dict to a Redis-backed one
  (`INCR`+`EXPIRE` on the first hit of a window), hand-rolled rather than pulling in a library —
  unlike the FastAPI sibling's move to the `limits` package, a bare `INCR`/`EXPIRE` pair is all a
  fixed window needs, and Redis's `INCR` is atomic on its own, so (unlike M1's in-memory version)
  no local lock was needed either. Key format matches `RateLimiterService.kt` exactly
  (`rate_limit:ride_request:{userId}` / `rate_limit:auth:{ip}`).
- `redis_client.py` is a single process-wide singleton, not one client per running event loop like
  the FastAPI sibling's own module. That per-loop caching solves a real problem there — an async
  Redis client is bound to whichever asyncio event loop created it, and `TestClient` gives each
  test its own loop — but this Django app is synchronous end to end (a deliberate M2 choice), so
  there's no event loop for a client to bind to. Confirmed this wasn't needed rather than assuming
  it: ran the full suite and watched for any "attached to a different loop"-shaped failure before
  concluding the simpler singleton was safe here.
- Removed the now-dead `DriverRepository.all()`/`.clear()`, `count_nearby_available_drivers()`
  (already unused before this milestone), `SurgeCache.clear()`, `PricingService.clear_cache()`,
  and `RateLimiter.clear()` — same dead-code removals the FastAPI sibling made at its own M3, for
  the same reason: once Redis owns this state, a single `FLUSHDB` in the test fixture replaces all
  of the individual `.clear()` calls at once.
- `tests/conftest.py` gained a `_redis_setup` session fixture (a throwaway Redis via
  testcontainers, mirroring `django_db_setup`'s Postgres one) and `_reset_state` now does one
  `FLUSHDB` instead of three separate `.clear()` calls. No bug here to speak of: unlike
  `DATABASES["default"]`, `settings.REDIS_URL` is a plain value `redis_client.get_client()` re-reads
  on every call and compares against its own cached copy, so a plain reassignment (not the
  in-place-mutation workaround M2 needed for the Postgres dict) was enough.
- All 55 tests passed on the first full run against real Postgres + Redis testcontainers — no new
  bug surfaced during automated testing this milestone (M1 and M2 each hit one). Manually verified
  the parts a test suite can't easily prove instead, against a real local Postgres/Redis and the
  dev server: a far-away driver (NYC vs. LA) and an offline-but-registered driver were both
  correctly excluded from a 5km nearby search; the 6th ride-request within a minute got a real 429
  (confirmed via `redis-cli TTL` that the window's `EXPIRE` was actually set); killed and restarted
  the dev server process entirely and confirmed via a fresh `/driver/nearby` call that the online
  driver's location and availability both survived — proof this state is genuinely in Redis, not
  process memory.
- `ruff check .` and `mypy --strict` clean, one straightforward fix needed: redis-py's stubs type
  `GET`'s return as `bytes | str | None` regardless of the client's `decode_responses=True`
  constructor argument, so `SurgeCache.get()` needed a documented `cast("str | None", ...)` — this
  is a different, narrower gap than the existing `many=True` mypy exception, not an extension of it.

## Milestone 4 — Celery-driven Kafka dispatch

The one milestone James deliberately chose to diverge from the FastAPI sibling's architecture on:
Celery + a dedicated Kafka-to-Celery bridge process, not an asyncio consumer loop. See README's
"Celery" section for the full pipeline diagram and reasoning.

- `kafka_producer.py`: a plain synchronous `kafka.KafkaProducer` (kafka-python-ng), cached as a
  process-wide singleton by bootstrap-servers string — same reasoning as `redis_client.py`, no
  event loop for a client to get bound to, so none of the FastAPI sibling's per-loop caching is
  needed. Wired into `services.py`'s four ride-state transitions, replacing the `# ... deferred`
  comments M1-M3 left in place.
- `dispatch.py` gained `fanout_to_nearby_drivers()` (Redis `SADD`+`EXPIRE` on `dispatched:{rideId}`,
  then `PUBLISH` a ride-offer payload per nearby driver — same wire format as the Kotlin/FastAPI
  versions) and `clear_dispatch()` (just deletes the dispatched-drivers key — see below for why
  it doesn't also notify other drivers the way the reference implementations' handler does).
- `tasks.py`: five Celery tasks — `handle_ride_requested`/`_accepted`/`_completed`/`_cancelled`
  (ported from `KafkaConsumer.kt`'s four `@KafkaListener`s, minus their SSE-emitting lines) and
  `retry_stale_rides` (a Celery Beat periodic task on the sibling's 60s/2-minute timing, replacing
  its `asyncio.sleep`-loop task).
- `kafka_bridge.py` + `management/commands/consume_kafka.py`: the actual Kafka consumer. Reads
  the four topics with a blocking `kafka.KafkaConsumer` and calls `.delay(ride_id)` per message —
  no domain logic of its own, by design (see claude.md).
- **Deliberate scope trim vs. the reference implementations**: `clear_dispatch()` only clears the
  `dispatched:{rideId}` key. `KafkaConsumer.kt`'s ride-accepted handler (and the FastAPI port of
  it) also directly notifies every other dispatched driver their offer is cancelled — but it does
  that by calling straight into the same process's in-memory SSE registry, never touching Redis.
  This project's Celery worker and its future Channels layer (M5) are separate processes, so
  there's no in-process registry to call here, and inventing a new Redis pub/sub wire format for
  "offer cancelled" right now would mean guessing at what M5 actually needs before M5 exists.
  Deferred that one notification to M5 rather than building it twice.
- **A real, multi-round dependency-resolution fight, not a code bug**: adding `celery[redis]` and
  `kafka-python-ng` triggered a chain of version conflicts once actually resolved by pip (not
  just guessed at):
  1. `celery[redis]` (via `kombu`) caps `redis-py` at `<6.0`/`<6.5` depending on the celery
     version — celery `<5.6.3` requires `<6.0`, `>=5.6.3` relaxed it to `<6.5`. Pinned
     `celery[redis]>=5.6.3` specifically for that relaxed cap.
  2. `testcontainers`'s own `redis` extra requires `redis-py>=7` — directly incompatible with
     celery's `<6.5` cap, an unresolvable conflict as long as both extras are requested. Fixed by
     dropping testcontainers' `redis` extra entirely (`testcontainers[postgres,kafka]`, not
     `[postgres,redis,kafka]`) — `RedisContainer` doesn't actually need that extra's specific
     `redis-py` pin, just *some* `redis` package importable, which this project already installs.
  3. Along the way, an intermediate resolution silently downgraded `testcontainers` from 4.15.0 to
     4.13.3 to satisfy an earlier (wrong) version combination — which broke `tests/conftest.py`
     immediately, since `testcontainers.community.{postgres,redis,kafka}` (the module path M2/M3
     already used) doesn't exist in 4.13.x at all, only at the top level. Pinned
     `testcontainers[...]>=4.15` explicitly once the redis conflict above was fixed, rather than
     leaving the version unconstrained and hoping the resolver picks right.
  4. The redis-py downgrade (8.1.0 → 6.4.0, forced by celery's cap) also surfaced a new
     mypy-strict regression in `rate_limit.py`'s `RateLimiter.allow()` (`incr()`'s stub started
     resolving to an `Awaitable`-inclusive union) — same class of gap as `pricing.py`'s existing
     `get()` cast, fixed the same way.

  None of this was a code defect — every individual constraint was correct in isolation, they
  just didn't have a shared solution until the redis version ranges were narrowed by hand. Full
  reasoning is in `pyproject.toml`'s comments next to each pin.
- **Real bug, caught while writing the stale-retry test, not by inspection**: a
  `kafka.KafkaConsumer` with `auto_offset_reset="latest"` only actually joins its consumer group
  and fixes its starting offset on the *first poll*, not at construction time. The stale-retry
  test built its probe consumer, then called `tasks.retry_stale_rides()` (which publishes before
  the probe has polled even once) — the probe's "latest" position ended up set to a point *after*
  the message it was supposed to catch, so it just timed out. Fixed with an explicit
  `probe.poll(timeout_ms=1000)` before triggering the publish, forcing group-join/position-fixing
  to happen first. aiokafka's `await consumer.start()` in the FastAPI sibling doesn't return until
  an equivalent readiness point, which is why that sibling's own version of this test doesn't need
  the extra step.
- **`celery.contrib.pytest` isn't auto-registered as a `pytest11` entry point** in this celery
  version — `tests/conftest.py` opts in explicitly (`pytest_plugins = ("celery.contrib.pytest",)`)
  and overrides its default `celery_app`/`celery_config` fixtures to point at this project's real
  Celery app rather than a throwaway one with no tasks registered. That override then hit a
  second, smaller issue: `celery_worker`'s startup check asserts `celery.ping` is registered, but
  that task is a `@shared_task` defined in `celery.contrib.testing.tasks` (not a real celery
  builtin) that only attaches to an app finalized *after* the module is imported — the plugin's
  own default test app imports it implicitly, a plain `Celery()` app doesn't. Fixed by importing
  `celery.contrib.testing.tasks` before calling `.finalize()` in the `celery_app` fixture.
- **`_reset_state` switched from pytest-django's `db` fixture to `transactional_db`.** The one
  true end-to-end test needs a real Celery worker thread — its own DB connection — to see a ride
  the test just created; a rollback-based transaction (what `db` gives you) is invisible across
  connections, so the worker would never find it. `transactional_db` commits for real and
  truncates between tests instead — slightly slower, but the only way that test's guarantee (the
  actual wiring works, not just the function bodies) is real rather than accidental.
- Tests: `tests/test_dispatch.py`, 8 new — fan-out writes the dispatched key/TTL and publishes the
  right payload; fan-out with no nearby drivers writes nothing; `handle_ride_requested` dispatches
  when still `REQUESTED` and no-ops otherwise; `handle_ride_accepted` clears the dispatched key;
  stale-ride retry republishes an old ride and leaves recent ones alone; and the one true
  end-to-end test (real Kafka broker, the actual `kafka_bridge.consume_forever()` loop in a
  background thread, and a real Celery worker via the `celery_worker` fixture — not just the task
  functions called directly like every other test here). 63 tests passing total (55 + 8),
  `ruff check .` and `mypy --strict` clean.
- Manually verified the entire live pipeline with all four processes running against real local
  Postgres/Redis/Kafka (`runserver`, `celery worker`, `celery beat`, `manage.py consume_kafka`):
  requesting a ride produced a real `dispatched:{rideId}` Redis key with the correct driver via
  the actual Kafka → bridge → Celery round-trip (confirmed in the worker's own log, not just
  Redis state); accepting cleared that key the same way; a full lifecycle
  (register → driver online → request → accept → start → complete → rate) worked end to end over
  real HTTP; and Celery Beat's own scheduler log confirmed it fired `retry-stale-rides` on its
  60-second interval, not just that the schedule was configured.

## Milestone 5 — Django Channels

- `rides/streaming/groups.py`: `send_event()`/`send_close()` wrap `channel_layer.group_send()`
  (via `async_to_sync`) — the delivery mechanism for both streams. This *replaces* M4's
  placeholder Redis pub/sub channel (`ride_offers:{driverId}`, written by `dispatch.py`, read by
  nothing) rather than bridging it: `channels_redis`'s channel layer is itself Redis-backed and
  genuinely cross-process, so a Celery worker can call `group_send()` directly and reach a
  driver's SSE connection held open in the separate Daphne process, with no bridge process
  needed — unlike `RideOfferListener.kt`/the FastAPI sibling's `ride_offer_listener.py`, both of
  which need one because `SseEmitter`/`asyncio.Queue` have no cross-process delivery of their own.
- `rides/dispatch.py`: `fanout_to_nearby_drivers()` now calls `send_event(..., "ride.offer", ...)`
  instead of `client.publish(...)`; new `get_driver_location()` (ported from
  `DriverService.kt::getDriverLocation`, cross-checked against the FastAPI sibling's own port) and
  `notify_offer_cancelled_and_clear()` (replaces M4's `clear_dispatch` — now reads the
  `dispatched:{rideId}` set *before* deleting it, notifying every other dispatched driver via
  `offer.cancelled`, same order as the ported Kotlin/FastAPI handlers).
- `rides/tasks.py::handle_ride_accepted` now sends `driver.eta_to_pickup` to the ride's location
  group (via `get_driver_location` + the same Haversine/ETA formulas `services.py` already used)
  and calls `notify_offer_cancelled_and_clear`. `handle_ride_completed`/`handle_ride_cancelled`
  send `stream.close` to the ride's location group, closing the rider's stream.
- `rides/services.py::DriverService` gained a `ride_repository` constructor dependency (for
  `update_location`'s active-ride lookup) — `state.py` updated to pass it. `update_location` sends
  `driver.location` to the ride's location group when the driver has an active
  (`MATCHED`/`IN_PROGRESS`) ride; `go_offline` sends `stream.close` to the driver's own offers
  group. Both run in-process (a Celery task doesn't call these — they're synchronous DRF view
  code), so they call `send_event`/`send_close` directly rather than through a task.
- New `rides/repositories.py::RideRepository.find_first_by_driver_id_and_status_in` (ported from
  the FastAPI sibling's repository method of the same name) backs that lookup.
- `rides/streaming/auth.py`: re-does `auth/authentication.py`'s token extraction
  (Authorization-bearer-or-`auth_token`-cookie) against a raw ASGI `scope["headers"]` list instead
  of a DRF `Request` — Channels consumers get neither `.headers` nor `.COOKIES`.
  `StreamGuardError(status, detail)` is the Channels-consumer equivalent of `services.DomainError`.
- `rides/streaming/consumers.py`: `DriverOffersConsumer`/`RideLocationConsumer`, both genuine
  SSE-over-HTTP (not WebSocket — see README's "Streaming" section for the full reasoning on both
  that choice and the group_send-over-pub/sub-bridge choice above). **The first design tried here
  was wrong** and is worth recording: defining `ride_offer()`/`stream_close()`/etc. as per-type
  handler methods, relying on `AsyncConsumer`'s own outer dispatch loop to invoke them (the
  pattern a WebSocket consumer uses) — this deadlocks/races for `AsyncHttpConsumer` specifically,
  because that outer loop calls `dispatch()` once for the `http.request` message and blocks
  *inside* that single call for as long as `handle()` runs, while a second, framework-owned task
  keeps concurrently polling the *same* channel-layer queue this module needs to read from itself,
  occasionally stealing a message before a matching handler method could receive it (crashing the
  connection with "No handler for message type ..."). Caught by reading Channels' own
  `consumer.py`/`utils.py` source directly (`await_many_dispatch`'s `while True` loop `await
  dispatch(result)`s sequentially, one task at a time) before writing a single test, not by
  reproducing the race in a test — the source made the mechanism unambiguous once actually read
  rather than assumed. Fixed by having `_SseStreamConsumer` override `__call__` entirely (no
  `self.dispatch()`, no `http_request`/`http_disconnect`) and having `handle()` run its own
  `asyncio.wait(..., FIRST_COMPLETED)` loop directly against the ASGI `receive` callable and
  `self.channel_layer.receive(self.channel_name)`, exclusively. `AsyncHttpConsumer` stays the base
  class purely for its `send_headers`/`send_body` helpers.
- `config/asgi.py`: a real `ProtocolTypeRouter`/`URLRouter` — the two SSE paths route to the new
  consumers, everything else falls through to a catch-all route to the plain Django ASGI app
  (`get_asgi_application()`), so ordinary DRF views are completely unaffected. `"daphne"` added
  first in `INSTALLED_APPS` (Channels' documented pattern) so `manage.py runserver` itself serves
  ASGI instead of Django's default WSGI dev server.
- `CHANNEL_LAYERS` (settings.py) uses `channels_redis.core.RedisChannelLayer` against the same
  Redis this project already requires from M3 — no new infrastructure dependency, just a new
  consumer of the same Redis.
- Tests: `tests/test_streaming.py`, 6 new, driving the real consumer ASGI apps directly via
  `channels.testing.ApplicationCommunicator` (not through `URLRouter` — `url_route` is supplied by
  hand in each scope). Two guard-rejection tests per stream (missing auth, wrong role/ride status/
  non-participant) plus one true live-delivery test per stream: connect, receive a **real**
  group-sent event round-tripped through the actual Redis-backed channel layer, then either a
  real client disconnect (`http.disconnect`) or a real server-initiated `stream.close` ending the
  response on its own. This is a genuine improvement over both other repos here, not just a
  language-appropriate equivalent: the Kotlin original has zero tests for this at all (its own
  README says so), and the FastAPI sibling's `TestClient` can't read a live SSE stream in this
  environment at all (its httpx transport buffers the whole response before returning), so its
  `test_sse.py` could only test the registry and each call site in isolation. `test_dispatch.py`
  also gained 2 tests (eta-to-pickup delivery, offer-cancelled delivery to the non-accepted
  driver) and its existing fan-out test switched from asserting a raw Redis pub/sub message to
  asserting a real channel-layer group-sent event. 70 tests passing total (63 + 6 in
  `test_streaming.py` + 1 net new in `test_dispatch.py`), `ruff check .` and `mypy --strict` clean.
- **New accepted mypy-strict exceptions** (see `claude.md`): `channels.*`/`channels_redis.*`/
  `daphne.*` added to the existing `ignore_missing_imports` override (same treatment as `kafka.*`)
  — plus two *additional* targeted ignores that override alone doesn't clear, because mypy strict
  won't let you subclass or decorate with something typed as `Any`:
  `_SseStreamConsumer(AsyncHttpConsumer)` needed `# type: ignore[misc]` on the class line, and each
  `@database_sync_to_async`-decorated test helper needed `# type: ignore[untyped-decorator]`.
  `config/asgi.py`'s catch-all `re_path(r"", django_asgi_app)` needed `# type: ignore[arg-type]`
  too — django-stubs' `re_path()` only knows about WSGI-style views, not this documented
  Channels pattern of routing to a raw ASGI callable.
- **Real dependency snag, not a code bug**: `cbor2` (a `channels_redis` dependency) ships no
  prebuilt wheel yet for this project's Python (3.14, very new) and its source build needs a Rust
  toolchain not present here. Resolved by installing an older pure-Python `cbor2` release (5.9.0)
  directly before installing `channels_redis`, rather than reaching for a Rust toolchain just to
  build a serializer this project doesn't even need to pick — `channels_redis` falls back to it
  only if `msgpack` (already a transitive dependency here) isn't picked instead at runtime either
  way.
- Manually verified against a real running `daphne` process plus a real Postgres and Redis
  (throwaway Docker containers stood up for this pass, separate from the testcontainers-managed
  ones `pytest` uses): a `curl -N` on `/driver/offers` received a real `ride_offer` SSE event
  triggered from a completely separate process (`manage.py shell`, calling
  `dispatch.fanout_to_nearby_drivers` directly) — genuine cross-process proof, the whole point of
  switching to `group_send`; a `curl -N` on `/rides/{id}/location` received a real
  `driver_location` event after a real `POST /driver/location`, then the connection closed on its
  own the instant the ride was marked completed (timed at 0s against a 10s `curl -m` ceiling, not
  just "eventually stopped"); `POST /driver/mode/off` closed an already-open `/driver/offers`
  connection immediately, same way; unauthenticated requests to both endpoints got a plain 401
  over real HTTP, not a hang. **One honest scope gap in this particular manual pass**: Kafka
  wasn't in the loop — standing up a throwaway single-node Kafka broker by hand (outside
  testcontainers) didn't work cleanly in the time available (the `apache/kafka` image needs KRaft
  env vars this quick attempt didn't configure), so `fanout_to_nearby_drivers`/
  `notify_offer_cancelled_and_clear` were called directly rather than through a real
  `POST /rides` → Kafka → Celery round-trip. That round-trip itself is unchanged from M4 and
  already has its own real-broker coverage (M4's own manual verification, plus this milestone's
  automated `test_ride_request_is_dispatched_end_to_end_through_kafka_and_celery`, which didn't
  need re-verifying by hand) — what this pass specifically needed to prove, and did, is that a
  Celery-worker-triggered event actually reaches a live SSE connection in a different process.

## What's left

Nothing planned. This project's milestone roadmap ends at M5 — **milestones 6 (Docker/CI against
real infra) and 7 (the AWS deployment doc port) were explicitly cancelled by James**, not
deferred. Don't build them without him asking again.
