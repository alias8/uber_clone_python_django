"""Ported from uber_clone's AuthController.kt."""

from __future__ import annotations

from typing import cast

import bcrypt
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, Throttled, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from rides.auth.authentication import AuthContext
from rides.auth.cookies import issue_token_cookie
from rides.domain import Role, User
from rides.serializers import (
    AuthResponseSerializer,
    LoginRequestSerializer,
    MeResponseSerializer,
    RegisterRequestSerializer,
    SwitchModeRequestSerializer,
)
from rides.services import DomainError
from rides.state import auth_attempt_rate_limiter, user_repository


def _client_ip(request: Request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return str(forwarded.split(",")[0].strip())
    return str(request.META.get("REMOTE_ADDR", "unknown"))


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        if not auth_attempt_rate_limiter.allow(_client_ip(request)):
            raise Throttled(detail="Too many attempts — try again later")

        body = RegisterRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        if user_repository.exists_by_username(body.validated_data["username"]):
            raise DomainError(status.HTTP_409_CONFLICT, "Username already taken")

        user = user_repository.save(
            User(
                username=body.validated_data["username"],
                password_hash=_hash_password(body.validated_data["password"]),
            )
        )
        response = Response(status=status.HTTP_201_CREATED)
        token = issue_token_cookie(response, user.username, Role.RIDER)
        response.data = AuthResponseSerializer({"token": token}).data
        return response


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        if not auth_attempt_rate_limiter.allow(_client_ip(request)):
            raise Throttled(detail="Too many attempts — try again later")

        body = LoginRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        user = user_repository.find_by_username(body.validated_data["username"])
        if user is None or not _verify_password(body.validated_data["password"], user.password_hash):
            raise AuthenticationFailed("Invalid credentials")

        response = Response(status=status.HTTP_200_OK)
        token = issue_token_cookie(response, user.username, user.role)
        response.data = AuthResponseSerializer({"token": token}).data
        return response


class SwitchModeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        body = SwitchModeRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        try:
            requested_mode = Role(body.validated_data["mode"].upper())
        except ValueError:
            raise ValidationError("Invalid mode") from None

        auth = cast(AuthContext, request.user)

        # Switching *into* driver mode requires the permanent DB role to already be DRIVER —
        # switching back to RIDER mode is always allowed, matching the original's behavior of
        # only gating the DRIVER branch.
        if requested_mode == Role.DRIVER:
            user = user_repository.find_by_username(auth.username)
            if user is None:
                raise AuthenticationFailed
            if user.role != Role.DRIVER:
                raise PermissionDenied

        response = Response(status=status.HTTP_200_OK)
        token = issue_token_cookie(response, auth.username, requested_mode)
        response.data = AuthResponseSerializer({"token": token}).data
        return response


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        auth = cast(AuthContext, request.user)
        user = user_repository.find_by_username(auth.username)
        if user is None:
            raise AuthenticationFailed
        return Response(MeResponseSerializer({"user_id": user.id}).data)
