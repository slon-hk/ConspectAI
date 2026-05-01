"""Durable event outbox contracts.

The current runtime uses the in-process event bus. These protocols define the
next migration step: write critical domain events into an OLTP outbox in the
same transaction, then dispatch them from a worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.events.base import BaseEvent


@dataclass(frozen=True)
class OutboxRecord:
    event: BaseEvent
    created_at: datetime
    attempts: int = 0
    last_error: str | None = None


class OutboxRepository(Protocol):
    async def append(self, event: BaseEvent) -> None:
        ...

    async def fetch_pending(self, *, limit: int) -> list[OutboxRecord]:
        ...

    async def mark_processed(self, event_id: str) -> None:
        ...

    async def mark_failed(self, event_id: str, error: str) -> None:
        ...
