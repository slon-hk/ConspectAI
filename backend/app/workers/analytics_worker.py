"""Analytics maintenance worker helpers.

The FastAPI lifespan still starts this task for backward-compatible local
runtime behavior. The same helper is also used by the standalone worker app so
future OLAP/batch processing can move out of the API process incrementally.
"""

from __future__ import annotations

import asyncio

<<<<<<< HEAD
from app.services.analytics_maintenance_service import AnalyticsMaintenanceService


def start_analytics_cleanup_task(
    service: AnalyticsMaintenanceService,
    *,
    interval_hours: int = 24,
) -> asyncio.Task[None]:
    """Start periodic cleanup of old analytics events on the current loop."""
=======
from app.db.pool import database
from app.repositories.olap import AnalyticsEventRepository
from app.services.analytics_maintenance_service import AnalyticsMaintenanceService


def start_analytics_cleanup_task(interval_hours: int = 24) -> asyncio.Task[None]:
    """Start periodic cleanup of old analytics events on the current loop."""
    service = AnalyticsMaintenanceService(AnalyticsEventRepository(database))
>>>>>>> 65d9c6e (fix bag)
    return asyncio.create_task(service.cleanup_loop(interval_hours=interval_hours))
