"""In-process event bus.

This is the first delivery mechanism for non-critical side effects. The public
interface is intentionally small so it can be backed by an outbox, Redis
Streams, NATS, Kafka, or another broker later.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Protocol

from .base import BaseEvent

logger = logging.getLogger(__name__)

EventHandler = Callable[[BaseEvent], Awaitable[None]]


class EventBus(Protocol):
    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        ...

    async def publish(self, event: BaseEvent) -> None:
        ...

    async def publish_many(self, events: list[BaseEvent]) -> None:
        ...


class InProcessEventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        handlers = self._handlers[event_type]
        if handler not in handlers:
            handlers.append(handler)

    async def publish(self, event: BaseEvent) -> None:
        for handler in list(self._handlers.get(event.event_type, [])):
            try:
                await handler(event)
            except Exception:
                logger.exception("Event handler failed for %s", event.event_type)

    async def publish_many(self, events: list[BaseEvent]) -> None:
        for event in events:
            await self.publish(event)

