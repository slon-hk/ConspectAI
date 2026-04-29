"""Usage accounting service."""

from __future__ import annotations

from typing import Any

import asyncpg

from app.repositories.oltp.usage import UsageRepository


class UsageService:
    def __init__(self, usage_repository: UsageRepository, default_units: int) -> None:
        self._usage_repository = usage_repository
        self._default_units = default_units

    async def get_usage_snapshot(
        self,
        user_id: int,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any]:
        return await self._usage_repository.get_usage_snapshot(user_id, conn=conn)

    async def finalize_request_usage(
        self,
        request_log_id: int,
        **kwargs: Any,
    ) -> None:
        await self._usage_repository.finalize_request_usage(request_log_id, **kwargs)

    async def fail_and_refund_request(
        self,
        request_log_id: int,
        error_text: str = "",
    ) -> None:
        await self._usage_repository.fail_and_refund_request(
            request_log_id,
            self._default_units,
            error_text,
        )

