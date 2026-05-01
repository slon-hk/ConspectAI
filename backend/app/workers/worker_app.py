"""Standalone worker process entrypoint.

This first worker intentionally runs only existing non-request maintenance work.
It creates the same DB pool as the API process and leaves Docker/API startup
unchanged while giving us a clean place to add OLAP batching, outbox dispatching,
and metrics workers in later stages.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from app.db.pool import database
from app.repositories.olap import AnalyticsEventRepository
from app.services.analytics_maintenance_service import AnalyticsMaintenanceService
from app.workers.analytics_worker import start_analytics_cleanup_task


async def run_worker() -> None:
    """Run background workers until the process is cancelled."""
    await database.create_pool()
    analytics_cleanup_task = start_analytics_cleanup_task(
        AnalyticsMaintenanceService(AnalyticsEventRepository(database))
    )
    try:
        await asyncio.Event().wait()
    finally:
        analytics_cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await analytics_cleanup_task
        await database.close_pool()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
