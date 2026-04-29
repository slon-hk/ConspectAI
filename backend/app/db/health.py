"""Database health checks."""

from __future__ import annotations

from .pool import Database, database


async def check_database_health(db: Database = database) -> bool:
    """Return True when Postgres responds to a minimal query."""
    try:
        async with db.acquire() as conn:
            return await conn.fetchval("SELECT 1") == 1
    except Exception:
        return False

