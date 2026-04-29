"""Message OLTP repository."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class MessageRepository(BaseRepository):
    async def list_by_chat(
        self,
        chat_id: str,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> list[dict]:
        async with self.connection(conn) as db_conn:
            rows = await db_conn.fetch(
                "SELECT * FROM messages WHERE chat_id=$1 ORDER BY created_at ASC",
                chat_id,
            )
            messages = [dict(r) for r in rows]
            for message in messages:
                file_rows = await db_conn.fetch(
                    """SELECT f.sha256, f.mime_type, f.compressed, f.original_size,
                              mf.original_filename
                       FROM message_files mf
                       JOIN files f ON f.sha256 = mf.sha256
                       WHERE mf.message_id=$1
                       ORDER BY mf.display_order""",
                    message["id"],
                )
                message["files"] = [dict(r) for r in file_rows]
            return messages

    async def create(
        self,
        chat_id: str,
        role: str,
        content: str,
        *,
        tokens: int = 0,
        model: str = "",
        cost_usd: float = 0,
        file_metas: list[dict] | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """INSERT INTO messages (chat_id, role, content, tokens_used, model, cost_usd)
                   VALUES ($1,$2,$3,$4,$5,$6) RETURNING *""",
                chat_id, role, content, tokens, model, cost_usd,
            )
            message = dict(row)
            if file_metas:
                for index, file_meta in enumerate(file_metas):
                    await db_conn.execute(
                        """INSERT INTO message_files (message_id, sha256, original_filename, display_order)
                           VALUES ($1,$2,$3,$4)""",
                        message["id"], file_meta["sha256"], file_meta["original_filename"], index,
                    )
            message["files"] = file_metas or []
            return message

