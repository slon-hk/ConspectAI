"""Chat OLTP repository."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class ChatRepository(BaseRepository):
    _UPDATE_FIELDS = {"template", "model", "title", "updated_at", "course_id"}

    async def list_for_user(
        self,
        uid: int,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> list[dict]:
        async with self.connection(conn) as db_conn:
            rows = await db_conn.fetch(
                "SELECT * FROM chats WHERE user_id=$1 ORDER BY updated_at DESC",
                uid,
            )
            return [dict(r) for r in rows]

    async def create(
        self,
        uid: int,
        template: str,
        model: str,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """INSERT INTO chats (user_id, title, template, model)
                   VALUES ($1, 'Новый чат', $2, $3) RETURNING *""",
                uid, template, model,
            )
            return dict(row)

    async def update_settings(
        self,
        chat_id: str,
        uid: int,
        *,
        conn: asyncpg.Connection | None = None,
        **kwargs,
    ) -> None:
        sets: list[str] = []
        vals: list = [chat_id, uid]
        for key, value in kwargs.items():
            if key in self._UPDATE_FIELDS:
                sets.append(f"{key}=${len(vals) + 1}")
                vals.append(value)
        if not sets:
            return

        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                f"UPDATE chats SET {','.join(sets)} WHERE id=$1 AND user_id=$2",
                *vals,
            )

    async def delete(
        self,
        chat_id: str,
        uid: int,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                "DELETE FROM chats WHERE id=$1 AND user_id=$2",
                chat_id,
                uid,
            )

    async def get(
        self,
        chat_id: str,
        uid: int,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                "SELECT * FROM chats WHERE id=$1 AND user_id=$2",
                chat_id,
                uid,
            )
            return dict(row) if row else None

