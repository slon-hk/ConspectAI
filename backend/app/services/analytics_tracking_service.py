"""Application analytics tracking facade."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.domain.analytics.events import ANALYTICS_EVENT_TYPE
from app.events import BaseEvent, EventBus
from app.events.handlers.analytics_handlers import AnalyticsEventHandler
from app.infrastructure.observability import system_metrics
from app.repositories.olap import AnalyticsEventRepository

logger = logging.getLogger(__name__)


class AnalyticsTrackingService:
    def __init__(
        self,
        analytics_repository: AnalyticsEventRepository,
        bus: EventBus,
    ) -> None:
        self._event_bus = bus
        self._event_bus.subscribe(
            ANALYTICS_EVENT_TYPE,
            AnalyticsEventHandler(analytics_repository),
        )

    def track(self, event: str, user_id: int | None = None, **props: Any) -> None:
        try:
            asyncio.get_running_loop().create_task(
                self._event_bus.publish(
                    BaseEvent(
                        event_type=ANALYTICS_EVENT_TYPE,
                        aggregate_id=event,
                        user_id=user_id,
                        payload={"event": event, "props": props or {}},
                    )
                )
            )
        except RuntimeError:
            logger.warning("Dropped analytics event %s because no running loop exists", event)

    def record_http(self, path: str, status: int, latency_ms: float) -> None:
        system_metrics.record_http(path, status, latency_ms)

    def record_gemini(self, model: str, latency_ms: float, *, ok: bool) -> None:
        system_metrics.record_gemini(model, latency_ms, ok=ok)

    def increment_mindmap_runs(self) -> None:
        system_metrics.bg_mindmap_runs += 1

    def increment_mindmap_failures(self) -> None:
        system_metrics.bg_mindmap_failed += 1
