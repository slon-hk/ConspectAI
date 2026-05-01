"""No-op cache implementation used until Redis is configured."""

from __future__ import annotations

from typing import Any


class NullCache:
    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None
