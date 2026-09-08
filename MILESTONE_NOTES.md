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

## What's left

- **Milestone 3** — Redis: driver geo-index (`GEOSEARCH`), availability set, surge cache,
  Redis-backed rate limiting.
- **Milestone 4** — Celery: the Kafka-consuming dispatch pipeline
  (`ride-requested`/`accepted`/`completed`/`cancelled`) + the stale-ride retry job.
- **Milestone 5** — Django Channels: the SSE-equivalent endpoints (`/rides/{id}/location`,
  `/driver/offers`).

Milestones 6 (Docker/CI against real infra) and 7 (the AWS deployment doc port) are cancelled by
James, not deferred — don't build them.
