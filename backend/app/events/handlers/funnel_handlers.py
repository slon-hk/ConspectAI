"""Marketing funnel event handlers."""

from __future__ import annotations

<<<<<<< HEAD
from app.domain.analytics.events import FUNNEL_STEP_EVENT_TYPE
from app.events.base import BaseEvent
from app.repositories.olap import FunnelMetricRepository


class FunnelStepEventHandler:
    def __init__(self, repository: FunnelMetricRepository) -> None:
        self._repository = repository
=======
from app.events.base import BaseEvent
from app.repositories.olap import FunnelMetricRepository

FUNNEL_STEP_EVENT_TYPE = "funnel.step.recorded"


class FunnelStepEventHandler:
    def __init__(self, repository: FunnelMetricRepository | None = None) -> None:
        self._repository = repository or FunnelMetricRepository()
>>>>>>> 65d9c6e (fix bag)

    async def __call__(self, event: BaseEvent) -> None:
        payload = event.payload
        await self._repository.record_event(
            user_id=event.user_id,
            event_name=str(payload.get("event_name", "")),
            source=payload.get("source"),
            campaign=payload.get("campaign"),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )
