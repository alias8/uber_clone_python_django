"""ASGI entrypoint. Regular DRF views stay on Django's own (synchronous) view stack, reached via
`django_asgi_app` as the catch-all route — only the two SSE paths route to the Channels consumers
in rides/streaming/consumers.py. `django.setup()` must run (via `get_asgi_application()`) before
importing anything that imports Django models/settings, which is why the consumer import is
below it rather than at the top of the file."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.urls import re_path  # noqa: E402

from rides.streaming.consumers import DriverOffersConsumer, RideLocationConsumer  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": URLRouter(
            [
                re_path(r"^driver/offers$", DriverOffersConsumer.as_asgi()),
                re_path(r"^rides/(?P<ride_id>[^/]+)/location$", RideLocationConsumer.as_asgi()),
                # Channels' own documented pattern for a catch-all to the plain Django ASGI app —
                # django-stubs' re_path() signature only knows about WSGI-style Django views, not
                # this use with a raw ASGI callable.
                re_path(r"", django_asgi_app),  # type: ignore[arg-type]
            ]
        ),
    }
)
