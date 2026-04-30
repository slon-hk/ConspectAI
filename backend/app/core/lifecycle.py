"""Application lifespan wiring."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.pool import Database


def create_lifespan(
    *,
    database: Database,
    start_analytics_cleanup_task: Callable[[], asyncio.Task[None]],
):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await database.create_pool()
        cleanup_task = start_analytics_cleanup_task()
        try:
            yield
        finally:
            cleanup_task.cancel()
            await database.close_pool()

    return lifespan
