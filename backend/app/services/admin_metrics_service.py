"""Admin reporting orchestration service."""

from __future__ import annotations

from app.repositories.olap import AdminReportRepository


class AdminMetricsService:
    def __init__(self, admin_report_repository: AdminReportRepository) -> None:
        self._admin_report_repository = admin_report_repository

    async def platform_stats(self) -> dict:
        return await self._admin_report_repository.platform_stats()

    async def recent_activity(self, limit: int = 50) -> list[dict]:
        return await self._admin_report_repository.recent_activity(limit)

    async def model_usage(self) -> list[dict]:
        return await self._admin_report_repository.model_usage()

    async def admin_metrics(self) -> dict:
        return await self._admin_report_repository.admin_metrics()

    async def overview(self) -> dict:
        return await self._admin_report_repository.overview_metrics()

    async def rag(self) -> dict:
        return await self._admin_report_repository.rag_metrics()

    async def usage(self) -> dict:
        return await self._admin_report_repository.usage_metrics()

    async def marketing(self) -> dict:
        return await self._admin_report_repository.marketing_metrics()
