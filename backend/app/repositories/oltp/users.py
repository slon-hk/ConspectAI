"""User OLTP repository."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def create(
        self,
        username: str,
        email: str,
        password_hash: str,
        default_plan_key: str,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """INSERT INTO users (username, email, password_hash, subscription_id)
                   VALUES (
                        $1, $2, $3,
                        (SELECT id FROM subscriptions WHERE plan_key=$4 LIMIT 1)
                   )
                   RETURNING *""",
                username, email, password_hash, default_plan_key,
            )
            return dict(row)

    async def get_by_email(
        self,
        email: str,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow("SELECT * FROM users WHERE email=$1", email)
            return dict(row) if row else None

    async def get_by_id(
        self,
        uid: int,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)
            return dict(row) if row else None

    async def get_by_username(
        self,
        username: str,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow("SELECT * FROM users WHERE username=$1", username)
            return dict(row) if row else None

