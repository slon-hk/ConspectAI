"""Marketing funnel event orchestration service."""

from __future__ import annotations

from app.repositories.olap import FunnelMetricRepository


class FunnelService:
    def __init__(self, funnel_repository: FunnelMetricRepository) -> None:
        self._funnel_repository = funnel_repository

    async def record_visit(self, *, path: str) -> None:
        await self._funnel_repository.record_event(
            user_id=None,
            event_name="visit",
            metadata={"path": path},
        )

    async def record_signup(self, *, user_id: int, channel: str) -> None:
        await self._funnel_repository.record_event(
            user_id=user_id,
            event_name="signup",
            metadata={"channel": channel},
        )
