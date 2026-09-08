"""Ported from uber_clone's DriverController.kt."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from rides.auth.authentication import AuthContext, require_role, resolve_current_user
from rides.auth.cookies import issue_token_cookie
from rides.dispatch import DEFAULT_SEARCH_RADIUS_KM
from rides.domain import Role
from rides.serializers import (
    DriverLocationRequestSerializer,
    DriverProfileResponseSerializer,
    DriverRegisterRequestSerializer,
    NearbyDriverResponseSerializer,
    RideResponseSerializer,
)
from rides.state import driver_service, ride_repository, user_repository


class DriverRegisterView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)

        body = DriverRegisterRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        driver = driver_service.register_driver(
            user.id, body.validated_data["vehicle_type"], body.validated_data["license_plate"]
        )
        user_repository.save(replace(user, role=Role.DRIVER))

        response = Response(status=status.HTTP_201_CREATED)
        issue_token_cookie(response, user.username, Role.DRIVER)
        response.data = DriverProfileResponseSerializer(driver).data
        return response


class DriverProfileView(APIView):
    permission_classes = [IsAuthenticated, require_role(Role.DRIVER)]

    def get(self, request: Request) -> Response:
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)
        driver = driver_service.get_profile(user.id)
        return Response(DriverProfileResponseSerializer(driver).data)


class DriverModeOnView(APIView):
    permission_classes = [IsAuthenticated, require_role(Role.DRIVER)]

    def post(self, request: Request) -> Response:
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)

        body = DriverLocationRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        driver = driver_service.go_online(user.id, body.validated_data["lat"], body.validated_data["lng"])
        return Response(DriverProfileResponseSerializer(driver).data)


class DriverModeOffView(APIView):
    permission_classes = [IsAuthenticated, require_role(Role.DRIVER)]

    def post(self, request: Request) -> Response:
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)
        driver = driver_service.go_offline(user.id)
        return Response(DriverProfileResponseSerializer(driver).data)


class DriverLocationView(APIView):
    permission_classes = [IsAuthenticated, require_role(Role.DRIVER)]

    def post(self, request: Request) -> Response:
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)

        body = DriverLocationRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        driver_service.update_location(user.id, body.validated_data["lat"], body.validated_data["lng"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class DriverRidesView(APIView):
    permission_classes = [IsAuthenticated, require_role(Role.DRIVER)]

    def get(self, request: Request) -> Response:
        auth = cast(AuthContext, request.user)
        user = resolve_current_user(auth)
        rides = ride_repository.find_by_driver_id_ordered(user.id)
        # djangorestframework-stubs doesn't overload Serializer.__init__ on many=True, so it
        # always types `instance` as the single-item type — see claude.md's conventions.
        return Response(RideResponseSerializer(rides, many=True).data)  # type: ignore[arg-type]


class DriverNearbyView(APIView):
    permission_classes = [IsAuthenticated, require_role(Role.DRIVER)]

    def get(self, request: Request) -> Response:
        lat = float(request.query_params["lat"])
        lng = float(request.query_params["lng"])
        radius_km = float(request.query_params.get("radius_km", DEFAULT_SEARCH_RADIUS_KM))
        results = driver_service.find_nearby(lat, lng, radius_km)
        return Response(NearbyDriverResponseSerializer(results, many=True).data)  # type: ignore[arg-type]
