"""Celery application, the async/background-work side of this project (M4 onward).

James chose Celery over mirroring the FastAPI sibling's asyncio-native consumer loop
specifically because Celery + a broker is how Django projects idiomatically handle background
work in production — see README.md's "Celery" section for the full architecture writeup. This
module is the standard Celery-with-Django bootstrap: `config/__init__.py` imports `app` from
here so it's created at process start, and `app.autodiscover_tasks()` finds `rides/tasks.py`.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("uber_clone_django")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
