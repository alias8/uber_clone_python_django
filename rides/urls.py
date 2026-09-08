from django.urls import path
from rest_framework.routers import DefaultRouter

from rides.views import driver as driver_views
from rides.views.auth import LoginView, MeView, RegisterView, SwitchModeView
from rides.views.health import health
from rides.views.rides import RideViewSet

# trailing_slash=False keeps route shapes identical to the Kotlin/FastAPI siblings
# (`/rides`, not `/rides/`) for a clean side-by-side comparison across all three repos.
router = DefaultRouter(trailing_slash=False)
router.register("rides", RideViewSet, basename="ride")

urlpatterns = [
    path("health", health),
    path("auth/register", RegisterView.as_view()),
    path("auth/login", LoginView.as_view()),
    path("auth/switch-mode", SwitchModeView.as_view()),
    path("auth/me", MeView.as_view()),
    path("driver/register", driver_views.DriverRegisterView.as_view()),
    path("driver/profile", driver_views.DriverProfileView.as_view()),
    path("driver/mode/on", driver_views.DriverModeOnView.as_view()),
    path("driver/mode/off", driver_views.DriverModeOffView.as_view()),
    path("driver/location", driver_views.DriverLocationView.as_view()),
    path("driver/rides", driver_views.DriverRidesView.as_view()),
    path("driver/nearby", driver_views.DriverNearbyView.as_view()),
    *router.urls,
]
