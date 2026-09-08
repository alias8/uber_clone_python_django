"""Ported from uber_clone's JwtFilter.kt + the `@PreAuthorize("hasRole(...)")` guards on each
controller (cross-checked against the FastAPI sibling's auth/dependencies.py, which ported the
same filter first). Every authenticated user always holds RIDER authority; DRIVER authority is
granted additionally only while the token's active-mode `role` claim is DRIVER — so a user in
driver mode can still call rider endpoints, matching the Kotlin filter's behavior exactly.

DRF idiom for this: an `authentication_classes` entry that resolves the token into
`request.user` (here, an `AuthContext`, not a Django `auth.User` — this app has no ORM user
model in M1, see domain.py), plus `permission_classes` entries that inspect it. FastAPI's
`Depends(require_role(...))` becomes a permission class *factory* here, since DRF permission
classes are types, not per-call closures — `require_role(Role.DRIVER)` returns a fresh
`BasePermission` subclass parameterized on that role.
"""

from __future__ import annotations

from dataclasses import dataclass

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from rides.auth.jwt import extract_role, extract_username, is_valid
from rides.domain import Role, User
from rides.state import user_repository


@dataclass
class AuthContext:
    username: str
    authorities: frozenset[Role]

    # DRF's IsAuthenticated permission checks `request.user.is_authenticated` — Django's own
    # auth.User has this as a property; we provide the same surface on our stand-in.
    @property
    def is_authenticated(self) -> bool:
        return True


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if header and header.startswith("Bearer "):
        return header.removeprefix("Bearer ")
    token = request.COOKIES.get("auth_token")
    return token


class JWTCookieAuthentication(BaseAuthentication):
    def authenticate_header(self, request: Request) -> str:
        # DRF's APIView.handle_exception() silently downgrades NotAuthenticated/
        # AuthenticationFailed from 401 to 403 whenever get_authenticate_header() returns a
        # falsy value — BaseAuthentication's default implementation returns None, meant for
        # session-style auth where a 403 (not a WWW-Authenticate challenge) is the right
        # response to a missing credential. That default silently broke every "401 for missing/
        # invalid auth" test ported from uber_clone/the FastAPI sibling (both always 401, never
        # 403) until this override was added — caught by the test suite, not by inspection.
        return "Bearer"

    def authenticate(self, request: Request) -> tuple[AuthContext, str] | None:
        token = _extract_token(request)
        if token is None:
            # No credentials attempted — DRF treats this as anonymous, and a downstream
            # IsAuthenticated permission (not this class) is what turns it into a 401.
            return None
        if not is_valid(token):
            raise AuthenticationFailed("Not authenticated")

        username = extract_username(token)
        active_role = extract_role(token) or Role.RIDER
        authorities = {Role.RIDER}
        if active_role == Role.DRIVER:
            authorities.add(Role.DRIVER)
        return AuthContext(username=username, authorities=frozenset(authorities)), token


def require_role(role: Role) -> type[BasePermission]:
    class _RequireRole(BasePermission):
        message = f"Requires active {role.value} mode"

        def has_permission(self, request: Request, view: object) -> bool:
            auth = request.user
            if not isinstance(auth, AuthContext):
                return False
            return role in auth.authorities

    return _RequireRole


def resolve_current_user(auth: AuthContext) -> User:
    user = user_repository.find_by_username(auth.username)
    if user is None:
        raise AuthenticationFailed("Not authenticated")
    return user
