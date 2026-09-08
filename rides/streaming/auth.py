"""ASGI-scope auth helpers for the Channels streaming layer (rides/streaming/consumers.py).

`rides.auth.authentication.JWTCookieAuthentication` extracts a token from a DRF `Request`
object's `.headers`/`.COOKIES`. Channels' raw ASGI HTTP scope has neither — it hands consumers
`scope["headers"]: list[tuple[bytes, bytes]]` — so this module re-does that same extraction
(Authorization bearer header, falling back to the `auth_token` cookie) directly against the raw
scope, mirroring `rides.auth.authentication._extract_token`'s behavior exactly rather than trying
to wrap a DRF Request around an ASGI scope just to reuse it.
"""

from __future__ import annotations

from http.cookies import SimpleCookie

from rides.auth.jwt import extract_role, extract_username, is_valid
from rides.domain import Role

Headers = list[tuple[bytes, bytes]]


class StreamGuardError(Exception):
    """Raised by any guard check in this module or in a consumer's `_authorize()` — the status
    is whatever HTTP status the failure should map to (401/403/404/409), mirroring the specific
    `ResponseStatusException`/`HTTPException`/`DomainError` each ported guard raises elsewhere in
    this codebase. Consumers catch this in `handle()` and send it as a plain (non-streaming)
    response, before `send_headers` ever opens the event-stream content type — same "guard before
    the stream is created" ordering as `DriverOfferController.kt`/`RideLocationController.kt` and
    the FastAPI sibling's routers.
    """

    def __init__(self, status: int, detail: str = "") -> None:
        self.status = status
        super().__init__(detail)


def _decode_headers(headers: Headers) -> dict[str, str]:
    return {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in headers}


def extract_token(headers: Headers) -> str | None:
    decoded = _decode_headers(headers)
    auth_header = decoded.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ")
    cookie_header = decoded.get("cookie")
    if cookie_header:
        cookie: SimpleCookie = SimpleCookie()  # SimpleCookie isn't generic despite the name
        cookie.load(cookie_header)
        morsel = cookie.get("auth_token")
        if morsel is not None:
            return morsel.value
    return None


def resolve_active_role(headers: Headers) -> tuple[str, frozenset[Role]]:
    """Returns (username, authorities), the same shape as `AuthContext` in
    rides/auth/authentication.py, without needing a DRF Request. Every authenticated user always
    holds RIDER authority; DRIVER authority is granted additionally only while the token's
    active-mode `role` claim is DRIVER — see that module's docstring for why.

    Raises StreamGuardError(401) on a missing or invalid token, matching
    JWTCookieAuthentication.authenticate() + IsAuthenticated's combined 401 behavior."""
    token = extract_token(headers)
    if token is None or not is_valid(token):
        raise StreamGuardError(401, "Not authenticated")
    username = extract_username(token)
    active_role = extract_role(token) or Role.RIDER
    authorities = {Role.RIDER}
    if active_role == Role.DRIVER:
        authorities.add(Role.DRIVER)
    return username, frozenset(authorities)
