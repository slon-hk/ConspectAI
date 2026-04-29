"""File metadata OLTP repository."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class FileRepository(BaseRepository):
    async def register(
        self,
        sha256: str,
        mime: str,
        compressed: bool,
        original_size: int,
        stored_size: int,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """INSERT INTO files (sha256, mime_type, compressed, original_size, stored_size, ref_count)
                   VALUES ($1,$2,$3,$4,$5,1)
                   ON CONFLICT (sha256) DO UPDATE
                     SET ref_count = files.ref_count + 1
                   RETURNING *""",
                sha256, mime, compressed, original_size, stored_size,
            )
            return dict(row)

    async def release(
        self,
        sha256: str,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> int:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                "UPDATE files SET ref_count=ref_count-1 WHERE sha256=$1 RETURNING ref_count",
                sha256,
            )
            return row["ref_count"] if row else 0

    async def get(
        self,
        sha256: str,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow("SELECT * FROM files WHERE sha256=$1", sha256)
            return dict(row) if row else None

