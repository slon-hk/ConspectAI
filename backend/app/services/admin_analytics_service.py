"""Admin analytics read-side orchestration service."""

from __future__ import annotations

from app.infrastructure.observability import system_metrics
from app.repositories.olap import AnalyticsEventRepository


class AdminAnalyticsService:
    def __init__(self, analytics_repository: AnalyticsEventRepository) -> None:
        self._analytics_repository = analytics_repository

    async def daily_active_users(self, days: int = 30) -> list[dict]:
        return await self._analytics_repository.daily_active_users(days)

    async def signups_by_day(self, days: int = 30) -> list[dict]:
        return await self._analytics_repository.signups_by_day(days)

    async def messages_by_day(self, days: int = 30) -> list[dict]:
        return await self._analytics_repository.messages_by_day(days)

    async def top_events(self, days: int = 7, limit: int = 12) -> list[dict]:
        return await self._analytics_repository.top_events(days, limit)

    async def funnel(self, days: int = 30) -> dict:
        return await self._analytics_repository.funnel(days)

    async def feature_adoption(self, days: int = 30) -> dict:
        return await self._analytics_repository.feature_adoption(days)

    def system_metrics(self) -> dict:
        return system_metrics.snapshot()
