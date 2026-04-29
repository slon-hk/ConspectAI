"""Analytics event repository."""

from __future__ import annotations

import json

import asyncpg

from app.repositories.base import BaseRepository


class AnalyticsEventRepository(BaseRepository):
    async def append_event(
        self,
        event: str,
        user_id: int | None,
        props: dict,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                "INSERT INTO events (user_id, event, props) VALUES ($1, $2, $3::jsonb)",
                user_id,
                event,
                json.dumps(props or {}),
            )

    async def daily_active_users(
        self,
        days: int = 30,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> list[dict]:
        async with self.connection(conn) as db_conn:
            rows = await db_conn.fetch("""
                SELECT date_trunc('day', created_at)::date AS day,
                       COUNT(DISTINCT user_id)              AS users
                FROM events
                WHERE created_at > now() - ($1 || ' days')::interval
                  AND user_id IS NOT NULL
                GROUP BY day ORDER BY day
            """, str(days))
            return [{"day": r["day"].isoformat(), "users": r["users"]} for r in rows]

    async def signups_by_day(
        self,
        days: int = 30,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> list[dict]:
        async with self.connection(conn) as db_conn:
            rows = await db_conn.fetch("""
                SELECT date_trunc('day', created_at)::date AS day,
                       COUNT(*)                             AS signups
                FROM users
                WHERE created_at > now() - ($1 || ' days')::interval
                GROUP BY day ORDER BY day
            """, str(days))
            return [{"day": r["day"].isoformat(), "signups": r["signups"]} for r in rows]

    async def messages_by_day(
        self,
        days: int = 30,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> list[dict]:
        async with self.connection(conn) as db_conn:
            rows = await db_conn.fetch("""
                SELECT date_trunc('day', created_at)::date AS day,
                       COUNT(*)                             AS messages,
                       COALESCE(SUM(tokens_used), 0)        AS tokens,
                       COALESCE(SUM(cost_usd), 0)::float    AS cost_usd
                FROM messages
                WHERE created_at > now() - ($1 || ' days')::interval
                  AND role = 'assistant'
                GROUP BY day ORDER BY day
            """, str(days))
            return [{
                "day": r["day"].isoformat(),
                "messages": r["messages"],
                "tokens": r["tokens"],
                "cost_usd": float(r["cost_usd"]),
            } for r in rows]

    async def top_events(
        self,
        days: int = 7,
        limit: int = 12,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> list[dict]:
        async with self.connection(conn) as db_conn:
            rows = await db_conn.fetch("""
                SELECT event,
                       COUNT(*) AS count,
                       COUNT(DISTINCT user_id) AS unique_users
                FROM events
                WHERE created_at > now() - ($1 || ' days')::interval
                GROUP BY event ORDER BY count DESC LIMIT $2
            """, str(days), limit)
            return [dict(r) for r in rows]

    async def funnel(
        self,
        days: int = 30,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow("""
                WITH win AS (
                  SELECT id FROM users
                  WHERE created_at > now() - ($1 || ' days')::interval
                )
                SELECT
                  (SELECT COUNT(*) FROM win)                                       AS signups,
                  (SELECT COUNT(DISTINCT c.user_id) FROM chats c JOIN win ON win.id = c.user_id)         AS created_chat,
                  (SELECT COUNT(DISTINCT c.user_id)
                     FROM chats c JOIN messages m ON m.chat_id = c.id JOIN win ON win.id = c.user_id
                     WHERE m.role='user')                                          AS sent_message,
                  (SELECT COUNT(*) FROM (
                      SELECT c.user_id FROM chats c JOIN messages m ON m.chat_id = c.id
                      JOIN win ON win.id = c.user_id
                      WHERE m.role='user'
                      GROUP BY c.user_id HAVING COUNT(*) >= 5
                  ) t)                                                             AS engaged
            """, str(days))
            return dict(row)

    async def feature_adoption(
        self,
        days: int = 30,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow("""
                SELECT
                  (SELECT COUNT(DISTINCT user_id) FROM events
                     WHERE event='file_uploaded' AND created_at > now() - ($1||' days')::interval) AS file_upload,
                  (SELECT COUNT(DISTINCT user_id) FROM events
                     WHERE event='mindmap_opened' AND created_at > now() - ($1||' days')::interval) AS mindmap,
                  (SELECT COUNT(DISTINCT user_id) FROM events
                     WHERE event LIKE 'export_%' AND created_at > now() - ($1||' days')::interval) AS export,
                  (SELECT COUNT(DISTINCT user_id) FROM events
                     WHERE event='template_switched' AND created_at > now() - ($1||' days')::interval) AS templates,
                  (SELECT COUNT(DISTINCT user_id) FROM events
                     WHERE event='buy_modal_opened' AND created_at > now() - ($1||' days')::interval) AS buy_modal,
                  (SELECT COUNT(DISTINCT user_id) FROM events
                     WHERE event='tokens_depleted' AND created_at > now() - ($1||' days')::interval) AS tokens_out
            """, str(days))
            return dict(row)

    async def cleanup_old_events(
        self,
        retain_days: int = 90,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> str:
        async with self.connection(conn) as db_conn:
            return await db_conn.execute(
                "DELETE FROM events WHERE created_at < now() - ($1 || ' days')::interval",
                str(retain_days),
            )

