"""Marketing funnel event orchestration service."""

from __future__ import annotations

import asyncio
import logging

from app.domain.analytics.events import FUNNEL_STEP_EVENT_TYPE
from app.events import BaseEvent, EventBus
from app.events.handlers.funnel_handlers import FunnelStepEventHandler
from app.repositories.olap import FunnelMetricRepository

logger = logging.getLogger(__name__)


class FunnelService:
    def __init__(
        self,
        funnel_repository: FunnelMetricRepository,
        bus: EventBus,
    ) -> None:
        self._event_bus = bus
        self._event_bus.subscribe(FUNNEL_STEP_EVENT_TYPE, FunnelStepEventHandler(funnel_repository))

    def _publish_background(self, event: BaseEvent) -> None:
        try:
            asyncio.get_running_loop().create_task(self._event_bus.publish(event))
        except RuntimeError:
            logger.warning("Dropped %s event because no running loop exists", event.event_type)

    async def record_visit(self, *, path: str) -> None:
        self._publish_background(
            BaseEvent(
                event_type=FUNNEL_STEP_EVENT_TYPE,
                aggregate_id=path,
                payload={
                    "event_name": "visit",
                    "metadata": {"path": path},
                },
            )
        )

    async def record_signup(self, *, user_id: int, channel: str) -> None:
        self._publish_background(
            BaseEvent(
                event_type=FUNNEL_STEP_EVENT_TYPE,
                aggregate_id=str(user_id),
                user_id=user_id,
                payload={
                    "event_name": "signup",
                    "metadata": {"channel": channel},
                },
            )
        )
