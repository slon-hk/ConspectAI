"""Analytics maintenance orchestration service."""

from __future__ import annotations

import asyncio
import logging

from app.repositories.olap import AnalyticsEventRepository

logger = logging.getLogger(__name__)


class AnalyticsMaintenanceService:
    def __init__(self, analytics_repository: AnalyticsEventRepository) -> None:
        self._analytics_repository = analytics_repository

    async def cleanup_old_events(self, retain_days: int = 90) -> None:
        try:
            result = await self._analytics_repository.cleanup_old_events(retain_days)
            logger.info("analytics cleanup_old_events: %s", result)
        except Exception:
            logger.exception("analytics cleanup failed")

    async def cleanup_loop(self, interval_hours: int = 24, retain_days: int = 90) -> None:
        while True:
            await asyncio.sleep(interval_hours * 3600)
            await self.cleanup_old_events(retain_days)
