"""Quota orchestration service."""

from __future__ import annotations

from typing import Any

from app.repositories.oltp.usage import UsageRepository


class QuotaService:
    def __init__(self, usage_repository: UsageRepository, default_units: int) -> None:
        self._usage_repository = usage_repository
        self._default_units = default_units

    async def check_and_consume_limit(
        self,
        user_id: int,
        endpoint: str,
        units: int | None = None,
    ) -> dict[str, Any]:
        normalized_units = max(1, int(units or self._default_units))
        return await self._usage_repository.reserve_quota_units(
            user_id,
            endpoint,
            normalized_units,
        )

