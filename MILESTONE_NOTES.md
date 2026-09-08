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

## What's left

- **Milestone 2** — the Django ORM against Postgres, replacing the in-memory repos (and deciding
  then whether `domain.py` survives as-is, shrinks to just the enums, or goes away entirely in
  favor of real Django model classes — see `claude.md`).
- **Milestone 3** — Redis: driver geo-index (`GEOSEARCH`), availability set, surge cache,
  Redis-backed rate limiting.
- **Milestone 4** — Celery: the Kafka-consuming dispatch pipeline
  (`ride-requested`/`accepted`/`completed`/`cancelled`) + the stale-ride retry job.
- **Milestone 5** — Django Channels: the SSE-equivalent endpoints (`/rides/{id}/location`,
  `/driver/offers`).
- **Milestone 6** — Docker + docker-compose + GitHub Actions CI running against real infra.
- **Milestone 7 (stretch)** — port the multi-region AWS deployment doc.
