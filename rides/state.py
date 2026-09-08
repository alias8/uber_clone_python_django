"""Process-wide singletons for M1's in-memory backing stores.

Stands in for Spring's singleton-bean wiring (and the FastAPI sibling's own state.py, same
idea). From M2 onward these get replaced by proper Django ORM-backed managers/querysets — this
module is the seam where that swap happens.
"""

from django.conf import settings

from rides.pricing import PricingService
from rides.rate_limit import RateLimiter
from rides.repositories import DriverRepository, RatingRepository, RideRepository, UserRepository
from rides.services import DriverService, RatingService, RideService

user_repository = UserRepository()
driver_repository = DriverRepository()
ride_repository = RideRepository()
rating_repository = RatingRepository()
pricing_service = PricingService()

driver_service = DriverService(driver_repository)
ride_service = RideService(ride_repository, driver_repository, driver_service, pricing_service)
rating_service = RatingService(rating_repository, ride_repository, user_repository, driver_repository)

ride_request_rate_limiter = RateLimiter(capacity=settings.RIDE_REQUEST_LIMIT_PER_MINUTE, window_seconds=60)
auth_attempt_rate_limiter = RateLimiter(
    capacity=settings.AUTH_ATTEMPTS_LIMIT_PER_15_MIN, window_seconds=15 * 60
)
