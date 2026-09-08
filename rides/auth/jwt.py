"""Ported from uber_clone's JwtUtil.kt. The `role` claim is the *active mode* for this session
(reissued on /auth/switch-mode), not necessarily the user's permanent DB role — see
auth/authentication.py for how the two interact."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
from django.conf import settings

from rides.domain import Role

ALGORITHM = "HS256"


def generate(username: str, role: Role) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": username,
        "role": role.value,
        "iat": now,
        "exp": now + timedelta(milliseconds=settings.JWT_EXPIRATION_MS),
    }
    return pyjwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def extract_username(token: str) -> str:
    payload = pyjwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
    username = payload["sub"]
    assert isinstance(username, str)
    return username


def extract_role(token: str) -> Role | None:
    try:
        payload = pyjwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
        return Role(payload["role"])
    except (pyjwt.InvalidTokenError, ValueError, KeyError):
        return None


def is_valid(token: str) -> bool:
    try:
        extract_username(token)
        return True
    except pyjwt.InvalidTokenError:
        return False
