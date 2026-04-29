"""Mindmap OLTP repository."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class MindmapRepository(BaseRepository):
    async def get(
        self,
        chat_id: str,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                "SELECT markdown, updated_at FROM mindmaps WHERE chat_id=$1",
                chat_id,
            )
            return dict(row) if row else None

    async def save(
        self,
        chat_id: str,
        markdown: str,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                """INSERT INTO mindmaps (chat_id, markdown, updated_at)
                   VALUES ($1, $2, now())
                   ON CONFLICT (chat_id) DO UPDATE
                     SET markdown = EXCLUDED.markdown,
                         updated_at = now()""",
                chat_id, markdown,
            )

