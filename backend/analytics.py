"""
Analytics layer:
  • track(event, user_id, **props)   — fire-and-forget event logging to Postgres
  • SysMetrics                       — lightweight in-memory counters/timers
                                       (HTTP requests, Gemini calls, latencies)
  • aggregate_*()                    — read-side queries for the admin dashboard
  • cleanup_old_events()             — periodic GC for the events table
"""

import asyncio
import time
from collections import defaultdict, deque
from typing import Optional

from app.events import BaseEvent, event_bus
from app.events.handlers.analytics_handlers import ANALYTICS_EVENT_TYPE, AnalyticsEventHandler
from app.repositories.olap import AnalyticsEventRepository

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


# ── In-memory system metrics ─────────────────────────────────────────────────
class SysMetrics:
    """
    Lightweight per-process metrics for the dashboard.
    Reset on every restart (that's fine — they're for live monitoring).
    """
    def __init__(self):
        self.started_at = time.time()
        # HTTP: counters per (route, status_class)
        self.http_calls   = defaultdict(int)        # key: (path, status_class)
        self.http_errors  = 0
        # Latency samples for percentiles: rolling window of last N
        self.http_latencies = deque(maxlen=2000)    # ms floats
        # Gemini API
        self.gemini_calls       = defaultdict(int)  # by model
        self.gemini_errors      = defaultdict(int)
        self.gemini_latencies   = deque(maxlen=500)
        # Background tasks
        self.bg_mindmap_runs    = 0
        self.bg_mindmap_failed  = 0

    def record_http(self, path: str, status: int, latency_ms: float):
        # Group dynamic paths so we don't explode the counter dict
        key_path = self._normalise_path(path)
        cls = f"{status // 100}xx"
        self.http_calls[(key_path, cls)] += 1
        if status >= 500:
            self.http_errors += 1
        self.http_latencies.append(latency_ms)

    def record_gemini(self, model: str, latency_ms: float, ok: bool):
        if ok:
            self.gemini_calls[model] += 1
            self.gemini_latencies.append(latency_ms)
        else:
            self.gemini_errors[model] += 1

    @staticmethod
    def _normalise_path(p: str) -> str:
        """Replace UUIDs and ints in URL paths so /api/chats/<uuid>/messages
        gets grouped instead of producing a unique counter per chat."""
        import re
        p = re.sub(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "/{uuid}", p, flags=re.I)
        p = re.sub(r"/\d+(?=/|$)", "/{id}", p)
        return p

    def snapshot(self) -> dict:
        """Build a JSON-serialisable summary of current metrics."""
        lat = sorted(self.http_latencies) if self.http_latencies else [0]
        gem_lat = sorted(self.gemini_latencies) if self.gemini_latencies else [0]
        def pct(arr, p):
            if not arr: return 0
            return round(arr[min(len(arr) - 1, int(len(arr) * p))], 1)

        # Top 12 routes by request count
        top_routes = sorted(
            ((path, cls, count) for (path, cls), count in self.http_calls.items()),
            key=lambda x: -x[2],
        )[:12]

        return {
            "uptime_seconds":     int(time.time() - self.started_at),
            "http_total":         sum(self.http_calls.values()),
            "http_errors":        self.http_errors,
            "http_p50_ms":        pct(lat, 0.50),
            "http_p95_ms":        pct(lat, 0.95),
            "http_p99_ms":        pct(lat, 0.99),
            "top_routes":         [{"path": p, "status": s, "count": c} for p, s, c in top_routes],
            "gemini_calls":       dict(self.gemini_calls),
            "gemini_errors":      dict(self.gemini_errors),
            "gemini_p50_ms":      pct(gem_lat, 0.50),
            "gemini_p95_ms":      pct(gem_lat, 0.95),
            "bg_mindmap_runs":    self.bg_mindmap_runs,
            "bg_mindmap_failed":  self.bg_mindmap_failed,
        }


metrics = SysMetrics()


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
    try:
        result = await _events.cleanup_old_events(retain_days)
        print(f"[analytics] cleanup_old_events: {result}")
    except Exception as e:
        print(f"[analytics] cleanup failed: {e}")


async def cleanup_loop(interval_hours: int = 24):
    """Background loop run from main app lifespan."""
    while True:
        await asyncio.sleep(interval_hours * 3600)
        await cleanup_old_events()
