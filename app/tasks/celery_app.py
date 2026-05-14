from __future__ import annotations

import asyncio
from typing import Any, Coroutine

from celery import Celery
from celery.signals import worker_process_init

from app.config import settings

celery_app = Celery(
    "wacdeckstudio",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.generation_tasks",
        "app.tasks.export_tasks",
        "app.tasks.extraction_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run an async coroutine from a synchronous Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@worker_process_init.connect
def init_worker(**kwargs):
    """Initialize DB and Gemini when a Celery worker process starts."""
    from app.core.database import init_db
    from app.ai.gemini_client import init_gemini

    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    loop.close()
    init_gemini()
