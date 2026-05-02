"""Async Redis implementation of CacheClient."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis


class RedisCache:
    def __init__(self, url: str) -> None:
        import redis.asyncio as _aioredis
        self._client: "_aioredis.Redis" = _aioredis.from_url(url, decode_responses=False)

    async def get(self, key: str) -> bytes | None:
        return await self._client.get(key)

    async def set(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> None:
        if ttl_seconds is not None:
            await self._client.setex(key, ttl_seconds, value)
        else:
            await self._client.set(key, value)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def scan_delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching pattern using SCAN (never KEYS *)."""
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = await self._client.scan(cursor, match=pattern, count=100)
            if keys:
                await self._client.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        return deleted

    async def close(self) -> None:
        await self._client.aclose()
