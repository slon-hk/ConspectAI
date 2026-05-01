"""
Analytics layer:
  • track(event, user_id, **props)   — fire-and-forget event logging to Postgres
  • SysMetrics                       — lightweight in-memory counters/timers
                                       (HTTP requests, Gemini calls, latencies)
  • aggregate_*()                    — read-side queries for the admin dashboard
  • cleanup_old_events()             — periodic GC for the events table
"""

import asyncio
from typing import Optional

<<<<<<< HEAD
from app.domain.analytics.events import ANALYTICS_EVENT_TYPE
from app.events import BaseEvent, event_bus
from app.events.handlers.analytics_handlers import AnalyticsEventHandler
=======
from app.events import BaseEvent, event_bus
from app.events.handlers.analytics_handlers import ANALYTICS_EVENT_TYPE, AnalyticsEventHandler
>>>>>>> 65d9c6e (fix bag)
from app.infrastructure.observability import system_metrics
from app.repositories.olap import AnalyticsEventRepository
from app.services.analytics_maintenance_service import AnalyticsMaintenanceService

_events = AnalyticsEventRepository()
event_bus.subscribe(ANALYTICS_EVENT_TYPE, AnalyticsEventHandler(_events))


# ── Event tracking ────────────────────────────────────────────────────────────
async def _record(event: str, user_id: Optional[int], props: dict):
    """Insert a single event. Errors are logged but never raised — analytics
    must never break user-facing flows."""
    try:
        await event_bus.publish(
            BaseEvent(
                event_type=ANALYTICS_EVENT_TYPE,
                aggregate_id=event,
                user_id=user_id,
                payload={"event": event, "props": props or {}},
            )
        )
    except Exception as e:
        print(f"[analytics] failed to record {event}: {e}")


def track(event: str, user_id: Optional[int] = None, **props):
    """Fire-and-forget event tracker. Schedules the DB write on the running
    event loop without awaiting — caller doesn't block."""
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_record(event, user_id, props))
    except RuntimeError:
        # No running loop (e.g. called during shutdown) — drop silently
        pass


metrics = system_metrics


# ── Aggregation queries (read-side) ───────────────────────────────────────────
async def daily_active_users(days: int = 30) -> list[dict]:
    """Distinct users per day for the last `days` days."""
    return await _events.daily_active_users(days)


async def signups_by_day(days: int = 30) -> list[dict]:
    return await _events.signups_by_day(days)


async def messages_by_day(days: int = 30) -> list[dict]:
    """Messages and tokens spent per day, plus USD cost."""
    return await _events.messages_by_day(days)


async def top_events(days: int = 7, limit: int = 12) -> list[dict]:
    return await _events.top_events(days, limit)


async def funnel(days: int = 30) -> dict:
    """Simple acquisition funnel: signup → first chat → first message → 5+ messages."""
    return await _events.funnel(days)


async def feature_adoption(days: int = 30) -> dict:
    """Counts of users who used key product features at least once."""
    return await _events.feature_adoption(days)


# ── Maintenance ───────────────────────────────────────────────────────────────
async def cleanup_old_events(retain_days: int = 90):
    """Delete events older than `retain_days`. Call from a startup task / cron."""
    await AnalyticsMaintenanceService(_events).cleanup_old_events(retain_days)


async def cleanup_loop(interval_hours: int = 24):
    """Background loop run from main app lifespan."""
    await AnalyticsMaintenanceService(_events).cleanup_loop(interval_hours=interval_hours)
