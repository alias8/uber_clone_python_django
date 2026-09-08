"""Ported from uber_clone's JwtCookieService.kt."""

from django.conf import settings
from rest_framework.response import Response

from rides.auth.jwt import generate
from rides.domain import Role

COOKIE_NAME = "auth_token"


def issue_token_cookie(response: Response, username: str, role: Role) -> str:
    token = generate(username, role)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        path="/",
        max_age=settings.JWT_EXPIRATION_MS // 1000,
    )
    return token
