"""Shared repository helpers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from app.db.pool import Database, database


class BaseRepository:
    """Base class for repositories that use the shared asyncpg pool."""

    def __init__(self, db: Database = database) -> None:
        self._db = db

    @asynccontextmanager
    async def connection(
        self,
        conn: asyncpg.Connection | None = None,
    ) -> AsyncIterator[asyncpg.Connection]:
        if conn is not None:
            yield conn
            return

        async with self._db.acquire() as acquired:
            yield acquired

