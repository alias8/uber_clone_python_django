"""Ported from uber_clone's RideController.kt.

A DRF `ViewSet` (not `ModelViewSet` — no Django ORM model behind Ride yet, see domain.py) maps
naturally onto this resource: `create`/`retrieve` cover POST /rides and GET /rides/{id}, and the
rest of the state-machine transitions are `@action` methods on the ride's own detail route
(`/rides/{id}/accept`, `/start`, `/complete`, `/cancel`, `/rate`) — the same shape FastAPI's flat
router functions had, just grouped under one router-registered class instead of five decorated
functions. `history` is a `list`-shaped action (no pk) kept at GET /rides/history to match the
original's routing rather than DRF's usual `GET /rides/` for "list everything" (there's no
"list all rides" endpoint in the original — only "my rides").
"""

from __future__ import annotations

from typing import cast

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from rides.auth.authentication import AuthContext, require_role, resolve_current_user
from rides.domain import Role
from rides.serializers import RatingRequestSerializer, RideRequestSerializer, RideResponseSerializer
from rides.services import DomainError
from rides.state import rating_service, ride_repository, ride_request_rate_limiter, ride_service


class RideViewSet(ViewSet):
    permission_classes = [IsAuthenticated]

    def create(self, request: Request) -> Response:
        self.check_permissions_for(request, require_role(Role.RIDER))
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)

        # Prevents rider from requesting, then cancelling rapidly.
        if not ride_request_rate_limiter.allow(user.id):
            raise DomainError(status.HTTP_429_TOO_MANY_REQUESTS, "Too many ride requests — try again shortly")

        body = RideRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        ride = ride_service.request_ride(
            user.id,
            body.validated_data["pickup_lat"],
            body.validated_data["pickup_lng"],
            body.validated_data["dropoff_lat"],
            body.validated_data["dropoff_lng"],
        )
        return Response(RideResponseSerializer(ride).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="history")
    def history(self, request: Request) -> Response:
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)
        rides = ride_repository.find_by_rider_id_ordered(user.id)
        # djangorestframework-stubs doesn't overload Serializer.__init__ on many=True, so it
        # always types `instance` as the single-item type — see claude.md's conventions.
        return Response(RideResponseSerializer(rides, many=True).data)  # type: ignore[arg-type]

    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        assert pk is not None
        ride = ride_service.get_ride(pk)
        return Response(RideResponseSerializer(ride).data)

    @action(detail=True, methods=["post"])
    def accept(self, request: Request, pk: str | None = None) -> Response:
        assert pk is not None
        self.check_permissions_for(request, require_role(Role.DRIVER))
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)
        ride = ride_service.accept_ride(pk, user.id)
        return Response(RideResponseSerializer(ride).data)

    @action(detail=True, methods=["post"])
    def start(self, request: Request, pk: str | None = None) -> Response:
        assert pk is not None
        self.check_permissions_for(request, require_role(Role.DRIVER))
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)
        ride = ride_service.start_ride(pk, user.id)
        return Response(RideResponseSerializer(ride).data)

    @action(detail=True, methods=["post"])
    def complete(self, request: Request, pk: str | None = None) -> Response:
        assert pk is not None
        self.check_permissions_for(request, require_role(Role.DRIVER))
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)
        ride = ride_service.complete_ride(pk, user.id)
        return Response(RideResponseSerializer(ride).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk: str | None = None) -> Response:
        assert pk is not None
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)
        ride = ride_service.cancel_ride(pk, user.id)
        return Response(RideResponseSerializer(ride).data)

    @action(detail=True, methods=["post"])
    def rate(self, request: Request, pk: str | None = None) -> Response:
        assert pk is not None
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)

        body = RatingRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        rating_service.rate(pk, user.id, body.validated_data["score"], body.validated_data.get("comment"))
        return Response(status=status.HTTP_204_NO_CONTENT)

    def check_permissions_for(self, request: Request, permission_class: type[BasePermission]) -> None:
        """Applies an extra, action-specific permission (e.g. "must be in RIDER mode") on top of
        the class-wide `permission_classes` — DRF only auto-runs `permission_classes` once per
        request, so an action that needs a role beyond plain authentication checks it explicitly,
        the same way the FastAPI sibling layered `Depends(require_role(...))` on top of
        `Depends(get_current_auth)` per-router-function rather than per-router."""
        permission = permission_class()
        if not permission.has_permission(request, self):
            raise PermissionDenied(getattr(permission, "message", None))
