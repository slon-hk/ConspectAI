"""Admin OLTP repository for user management actions."""

from __future__ import annotations

from typing import Any

import asyncpg

from app.repositories.base import BaseRepository


def _week_start_expr() -> str:
    return "(date_trunc('week', now() AT TIME ZONE 'UTC'))::date"


def _month_start_expr() -> str:
    return "(date_trunc('month', now() AT TIME ZONE 'UTC'))::date"


class AdminUserRepository(BaseRepository):
    _UPDATE_FIELDS = {
        "plan",
        "period",
        "usage_count",
        "usage_reset_at",
        "is_admin",
        "is_blocked",
    }

    async def list_users(
        self,
        search: str = "",
        limit: int = 100,
        offset: int = 0,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> list[dict]:
        week_start = _week_start_expr()
        month_start = _month_start_expr()
        sql = f"""
            SELECT
                u.id, u.username, u.email, u.is_admin, u.is_blocked,
                u.total_spent_usd, u.created_at, u.subscription_id,
                COALESCE(s.plan_key, u.plan, 'free') AS plan_key,
                COALESCE(s.display_name, initcap(COALESCE(u.plan, 'free'))) AS subscription_name,
                COALESCE(s.price_rub, 0) AS price_rub,
                COALESCE(s.daily_limit, 0) AS daily_limit,
                COALESCE(s.weekly_limit, 0) AS weekly_limit,
                COALESCE(s.monthly_limit, 0) AS monthly_limit,
                COALESCE(CASE WHEN uu.day_start = CURRENT_DATE THEN uu.daily_used ELSE 0 END, 0) AS daily_used,
                COALESCE(CASE WHEN uu.week_start = {week_start} THEN uu.weekly_used ELSE 0 END, 0) AS weekly_used,
                COALESCE(CASE WHEN uu.month_start = {month_start} THEN uu.monthly_used ELSE 0 END, 0) AS monthly_used,
                GREATEST(COALESCE(s.daily_limit, 0) - COALESCE(CASE WHEN uu.day_start = CURRENT_DATE THEN uu.daily_used ELSE 0 END, 0), 0) AS daily_remaining,
                GREATEST(COALESCE(s.weekly_limit, 0) - COALESCE(CASE WHEN uu.week_start = {week_start} THEN uu.weekly_used ELSE 0 END, 0), 0) AS weekly_remaining,
                GREATEST(COALESCE(s.monthly_limit, 0) - COALESCE(CASE WHEN uu.month_start = {month_start} THEN uu.monthly_used ELSE 0 END, 0), 0) AS monthly_remaining,
                (SELECT COUNT(*) FROM chats c WHERE c.user_id = u.id) AS chat_count,
                (SELECT COUNT(*) FROM messages m JOIN chats c ON c.id = m.chat_id
                 WHERE c.user_id = u.id) AS message_count
            FROM users u
            LEFT JOIN subscriptions s ON s.id = u.subscription_id
            LEFT JOIN user_usage uu ON uu.user_id = u.id
        """
        params: list[Any] = []
        if search:
            sql += " WHERE u.username ILIKE $1 OR u.email ILIKE $1"
            params.append(f"%{search}%")
        sql += f" ORDER BY u.created_at DESC LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}"
        params += [limit, offset]

        async with self.connection(conn) as db_conn:
            rows = await db_conn.fetch(sql, *params)
            return [dict(r) for r in rows]

    async def count_users(
        self,
        search: str = "",
        *,
        conn: asyncpg.Connection | None = None,
    ) -> int:
        sql = "SELECT COUNT(*) FROM users"
        params: list[Any] = []
        if search:
            sql += " WHERE username ILIKE $1 OR email ILIKE $1"
            params.append(f"%{search}%")

        async with self.connection(conn) as db_conn:
            return await db_conn.fetchval(sql, *params)

    async def set_user_field(
        self,
        uid: int,
        field: str,
        value: Any,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        if field not in self._UPDATE_FIELDS:
            raise ValueError(f"Field {field} not allowed")

        async with self.connection(conn) as db_conn:
            await db_conn.execute(f"UPDATE users SET {field} = $1 WHERE id = $2", value, uid)

    async def set_user_plan(
        self,
        uid: int,
        plan_key: str,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> bool:
        async with self.connection(conn) as db_conn:
            plan = await db_conn.fetchrow(
                "SELECT id, plan_key FROM subscriptions WHERE plan_key=$1 AND is_active",
                plan_key,
            )
            if not plan:
                return False
            result = await db_conn.execute(
                "UPDATE users SET subscription_id=$1, plan=$2 WHERE id=$3",
                plan["id"], plan["plan_key"], uid,
            )
            return result != "UPDATE 0"

    async def delete_user(
        self,
        uid: int,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute("DELETE FROM users WHERE id = $1", uid)

