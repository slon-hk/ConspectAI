"""HTTP live-metrics middleware."""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.requests import Request

from app.services.analytics_tracking_service import AnalyticsTrackingService


def register_http_metrics_middleware(
    app: FastAPI,
    analytics_tracking_service: AnalyticsTrackingService,
) -> None:
    @app.middleware("http")
    async def http_metrics_middleware(request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception:
            status = 500
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            analytics_tracking_service.record_http(request.url.path, status, elapsed_ms)
