"""Runs the Kafka-to-Celery bridge (rides/kafka_bridge.py) as its own long-running process,
alongside `manage.py runserver`, a Celery worker, and Celery beat — see README.md/claude.md for
the full local-dev process list this milestone introduces.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from rides.kafka_bridge import consume_forever


class Command(BaseCommand):
    help = "Reads ride-lifecycle events off Kafka and enqueues a Celery task per message."

    def handle(self, *args: Any, **options: Any) -> None:
        consume_forever()
