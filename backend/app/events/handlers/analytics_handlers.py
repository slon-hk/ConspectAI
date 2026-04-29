"""Analytics event handlers."""

from __future__ import annotations

from app.events.base import BaseEvent
from app.repositories.olap import AnalyticsEventRepository

ANALYTICS_EVENT_TYPE = "analytics.event"


class AnalyticsEventHandler:
    def __init__(self, repository: AnalyticsEventRepository | None = None) -> None:
        self._repository = repository or AnalyticsEventRepository()

    async def __call__(self, event: BaseEvent) -> None:
        analytics_event = str(event.payload.get("event", ""))
        if not analytics_event:
            return
        props = event.payload.get("props") or {}
        await self._repository.append_event(
            analytics_event,
            event.user_id,
            props if isinstance(props, dict) else {},
        )

