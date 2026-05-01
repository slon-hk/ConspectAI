"""Analytics event handlers."""

from __future__ import annotations

<<<<<<< HEAD
from app.domain.analytics.events import ANALYTICS_EVENT_TYPE
from app.events.base import BaseEvent
from app.repositories.olap import AnalyticsEventRepository


class AnalyticsEventHandler:
    def __init__(self, repository: AnalyticsEventRepository) -> None:
        self._repository = repository
=======
from app.events.base import BaseEvent
from app.repositories.olap import AnalyticsEventRepository

ANALYTICS_EVENT_TYPE = "analytics.event"


class AnalyticsEventHandler:
    def __init__(self, repository: AnalyticsEventRepository | None = None) -> None:
        self._repository = repository or AnalyticsEventRepository()
>>>>>>> 65d9c6e (fix bag)

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
<<<<<<< HEAD
=======

>>>>>>> 65d9c6e (fix bag)
