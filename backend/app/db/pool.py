"""Central asyncpg pool infrastructure."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg


DEFAULT_DATABASE_URL = "postgresql://orion:orion@localhost:5432/orion"


class Database:
    """Owns the asyncpg pool lifecycle for the application."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        min_size: int = 2,
        max_size: int = 20,
        command_timeout: int = 30,
        connect_retries: int = 20,
        retry_delay_seconds: float = 1.5,
    ) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
        self.min_size = min_size
        self.max_size = max_size
        self.command_timeout = command_timeout
        self.connect_retries = connect_retries
        self.retry_delay_seconds = retry_delay_seconds
        self._pool: asyncpg.Pool | None = None

    async def create_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool

        last_err: Exception | None = None
        for _ in range(self.connect_retries):
            try:
                self._pool = await asyncpg.create_pool(
                    self.database_url,
                    min_size=self.min_size,
                    max_size=self.max_size,
                    command_timeout=self.command_timeout,
                )
                return self._pool
            except (OSError, asyncpg.PostgresError) as exc:
                last_err = exc
                await asyncio.sleep(self.retry_delay_seconds)

        raise RuntimeError(
            f"Could not connect to Postgres after {self.connect_retries} tries: {last_err}"
        )

    async def close_pool(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None

    def pool(self) -> asyncpg.Pool:
        assert self._pool is not None, "DB pool not initialized"
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool().acquire() as conn:
            yield conn


database = Database()

