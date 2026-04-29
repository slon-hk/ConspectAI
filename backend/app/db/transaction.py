"""Transaction boundary helper for future repositories and services."""

from __future__ import annotations

from types import TracebackType
from typing import Any

import asyncpg

from .pool import Database, database


class TransactionManager:
    """Open a short-lived connection-scoped asyncpg transaction."""

    def __init__(self, db: Database = database) -> None:
        self._db = db
        self._acquire_context: Any | None = None
        self._transaction: Any | None = None
        self.connection: asyncpg.Connection | None = None

    async def __aenter__(self) -> asyncpg.Connection:
        self._acquire_context = self._db.pool().acquire()
        self.connection = await self._acquire_context.__aenter__()
        self._transaction = self.connection.transaction()
        await self._transaction.__aenter__()
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        try:
            if self._transaction is not None:
                return await self._transaction.__aexit__(exc_type, exc, traceback)
            return None
        finally:
            if self._acquire_context is not None:
                await self._acquire_context.__aexit__(exc_type, exc, traceback)
            self.connection = None
            self._transaction = None
            self._acquire_context = None

