"""Legacy admin metrics route aliases."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends

from app.services.admin_metrics_service import AdminMetricsService


def create_admin_metrics_router(
    *,
    require_admin: Callable,
    admin_metrics_service: AdminMetricsService,
) -> APIRouter:
    router = APIRouter(tags=["admin-metrics"])

    @router.get("/admin/metrics")
    async def get_admin_metrics_public(_=Depends(require_admin)):
        return await admin_metrics_service.admin_metrics()

    @router.get("/admin/metrics/overview")
    async def get_admin_metrics_overview_public(_=Depends(require_admin)):
        return await admin_metrics_service.overview()

    @router.get("/admin/metrics/rag")
    async def get_admin_metrics_rag_public(_=Depends(require_admin)):
        return await admin_metrics_service.rag()

    @router.get("/admin/metrics/usage")
    async def get_admin_metrics_usage_public(_=Depends(require_admin)):
        return await admin_metrics_service.usage()

    @router.get("/admin/metrics/marketing")
    async def get_admin_metrics_marketing_public(_=Depends(require_admin)):
        return await admin_metrics_service.marketing()

    return router
