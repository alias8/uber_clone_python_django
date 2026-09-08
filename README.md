# uber_clone-django

The Django/DRF sibling of [`uber_clone`](../uber_clone) (Kotlin/Spring) and
[`uber_clone-python`](../uber_clone-python) (FastAPI) — the same ride-hailing domain (rider
requests, driver dispatch, surge pricing, ratings), a third time, in idiomatic Django this time.

## Why this exists

A survey of real senior full-stack postings in the Boston market turned up Django about as often
as FastAPI when a role asked for Python at all (`job_interview_prep/linkedin_job_search/
python_requirements_survey.md`, in a sibling private repo) — and `uber_clone-python` only
demonstrates FastAPI. This repo exists to close that specifically: same domain, same production
bar (`mypy --strict`, tested, CI-checked), but built the way a Django shop actually builds it —
Django REST Framework for the API layer, and (from the milestone that adds background/async work)
Celery for the Kafka-equivalent dispatch pipeline and Django Channels for the SSE-equivalent
streams, rather than trying to force this project into the other two repos' asyncio-native shape.
That's a deliberate trade: it makes the three repos slightly less line-for-line comparable from
that milestone onward, in exchange for actually being representative of what "Django experience"
means in an interview.

## A deliberate simplification: no gRPC

Same call the FastAPI sibling made, for the same reason: `uber_clone` splits fare/surge quoting
into a standalone gRPC `pricing-service`, specifically to practice a synchronous, must-answer-now
service boundary. This port keeps it as a plain in-process module (`pricing.py`) instead — a scope
decision, not a missing piece. The formulas and constants are ported exactly, so a known
pickup/dropoff pair and a known pending-rides/available-drivers ratio produce the same numbers as
`PricingGrpcService.kt`.

## Project layout

```
config/                 Django project package — settings.py, urls.py, asgi.py, wsgi.py
rides/                  The one Django app this domain lives in
  domain.py               Domain dataclasses: User, Driver, Ride, Rating, Role, RideStatus —
                           deliberately not named models.py, see the module docstring
  repositories.py          In-memory repos — swapped for the Django ORM in M2
  geo.py                   Haversine distance + ETA (ported from GeoUtils.kt)
  pricing.py               Fare + surge formulas, in-process (see above)
  dispatch.py              Nearby-driver radius search — in-memory now, Redis GEOSEARCH later
  rate_limit.py            In-memory fixed-window limiter — Redis-backed later
  services.py              DriverService / RideService / RatingService — the domain logic
  state.py                 Process-wide singletons wiring repos + services together
  auth/
    jwt.py                   JWT encode/decode (ported from JwtUtil.kt)
    cookies.py                HttpOnly cookie issuance (ported from JwtCookieService.kt)
    authentication.py          DRF authentication class + require_role() permission factory
                                (ported from JwtFilter.kt / the FastAPI sibling's dependencies.py)
  serializers.py            DRF Serializers — request validation and response shaping
  views/
    auth.py, driver.py, rides.py   APIViews (auth, driver) and one ViewSet (rides) — see
                                    rides.py's module docstring for why rides gets a ViewSet and
                                    driver doesn't
  urls.py                    Route table — DefaultRouter(trailing_slash=False) for rides, plain
                              path() entries for everything else, to keep route shapes identical
                              to the other two repos (`/rides`, not `/rides/`)
tests/                    pytest (via pytest-django) — unit tests against the in-memory stores;
                          no test needs a `django_db` marker at this milestone, see conftest.py
```

## Ported faithfully vs. reimplemented

Kept exact (same constants, same formulas, ported test cases prove numeric parity): Haversine +
30 km/h ETA; the surge formula (`clamp(pending/available, 1.0, 3.0)`, ~1km grid cache, 30s TTL);
the fare formula (`$2.00 + $1.50/km`, HALF_UP rounding to 2dp); rate-limit thresholds (5
ride-requests/min/rider, 10 auth-attempts/15min/IP); the ride state machine and every guard
condition; the JWT dual-role design (DB `role` = permanent capability, JWT `role` claim = active
mode, reissued on `/auth/switch-mode`); every route shape (`/rides`, `/driver/mode/on`, etc.).

Genuinely reimplemented, where idiomatic Django/DRF differs enough to be worth naming:

- **Pydantic schemas → DRF Serializers.** `serializers.py`'s `Serializer` (not `ModelSerializer`
  — there's no ORM model behind these yet) subclasses read attributes off a domain dataclass
  instance the same way they'd read off a Django model, via `getattr`.
- **FastAPI routers → a mix of `APIView`s and one `ViewSet`.** Auth and driver endpoints aren't
  naturally resource-shaped (no `{id}` in the path — they act on "the current user"), so they're
  plain `APIView` subclasses, one per endpoint, registered with explicit `path()` entries — the
  closest DRF equivalent to FastAPI's flat router functions. Rides *is* a resource with an `{id}`,
  so it gets a real `ViewSet` (`create`/`retrieve` plus five `@action`-decorated state-machine
  transitions) registered through a `DefaultRouter` — the more idiomatic DRF shape when a router
  fits, and a fair test of whether that shape holds up against a domain this stateful.
- **FastAPI `Depends(require_role(...))` → a permission-class factory.** DRF `permission_classes`
  are types, evaluated once for the whole request before the view body runs, not per-parameter
  closures — `require_role(Role.DRIVER)` in `auth/authentication.py` returns a fresh
  `BasePermission` subclass parameterized on that role. Where an action needs a role beyond what
  the class-wide `permission_classes` already covers (e.g. only `accept`/`start`/`complete` on
  `RideViewSet` need `DRIVER`, not every action), the view calls the permission explicitly inside
  the action method — see `RideViewSet.check_permissions_for()`.
- **`bcrypt` password hashing, hand-rolled, not `djangorestframework-simplejwt`.** The dual-role
  cookie design (DB role vs. active-mode JWT claim) is specific enough that reaching for
  simplejwt's refresh-token flow would fight the port rather than help it — same call the FastAPI
  sibling made against a token library in its own ecosystem.
- **pydantic-settings → plain Django settings.** `JWT_SECRET`, rate-limit thresholds, etc. live as
  ordinary values in `config/settings.py` rather than a separate settings object, since that's
  where Django idiom puts them.
- A **real DRF gotcha, caught by the test suite, not by inspection**: DRF's
  `APIView.handle_exception()` silently downgrades `AuthenticationFailed`/`NotAuthenticated` from
  401 to 403 whenever the authentication class's `authenticate_header()` returns a falsy value —
  `BaseAuthentication`'s default. Every "401 for missing/invalid auth" test ported from the other
  two repos (both always 401, never 403 for this) failed with 403 until `JWTCookieAuthentication`
  got an explicit `authenticate_header()` override. See `auth/authentication.py` and
  `MILESTONE_NOTES.md` for the full story.

## Running it

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python manage.py migrate   # Django's own auth/contenttypes tables — nothing in this app uses
                            # the ORM yet, see domain.py, but Django's stock apps still want this
python manage.py runserver
```

## Checks

```
ruff check .      # lint
mypy              # strict type check
pytest -q         # unit tests, all against in-memory stores at this milestone
```

All three run in CI on every push/PR (`.github/workflows/ci.yml`).

## Status

This is milestone 1: Django/DRF skeleton, JWT-cookie auth with the dual-role design, the full
ride state machine, in-process fare/surge quoting, and driver registration/dispatch — all backed
by in-memory repositories, all covered by tests, `mypy --strict` and `pytest` clean. Not yet
built:

- **Milestone 2** — the Django ORM against Postgres, replacing the in-memory repos
- **Milestone 3** — Redis: driver geo-index (`GEOSEARCH`), availability set, surge cache,
  Redis-backed rate limiting
- **Milestone 4** — Celery: the Kafka-consuming dispatch pipeline
  (`ride-requested`/`accepted`/`completed`/`cancelled`) + the stale-ride retry job, as Celery
  tasks/beat schedule rather than asyncio background tasks
- **Milestone 5** — Django Channels: the SSE-equivalent endpoints
  (`/rides/{id}/location`, `/driver/offers`) as WebSocket or SSE-over-ASGI consumers
- **Milestone 6** — Docker + docker-compose + GitHub Actions CI running against real infra
- **Milestone 7 (stretch)** — port the multi-region AWS deployment doc
