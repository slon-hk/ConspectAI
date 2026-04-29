"""Admin reporting repository.

These queries still use the primary Postgres database, but the repository
boundary makes the OLAP/reporting path explicit and ready to move later.
"""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class AdminReportRepository(BaseRepository):
    async def platform_stats(
        self,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow("""
                SELECT
                  (SELECT COUNT(*) FROM users)                              AS user_count,
                  (SELECT COUNT(*) FROM users WHERE is_blocked)             AS blocked_count,
                  (SELECT COUNT(*) FROM chats)                              AS chat_count,
                  (SELECT COUNT(*) FROM messages)                           AS message_count,
                  (SELECT COUNT(*) FROM messages WHERE role = 'assistant')  AS reply_count,
                  (SELECT COALESCE(SUM(tokens_used), 0) FROM messages)       AS total_tokens,
                  (SELECT COALESCE(SUM(cost_usd), 0)    FROM messages)      AS total_cost,
                  (SELECT COUNT(*) FROM users WHERE created_at > now() - INTERVAL '24 hours') AS new_users_24h,
                  (SELECT COUNT(*) FROM messages WHERE created_at > now() - INTERVAL '24 hours') AS messages_24h,
                  (SELECT pg_size_pretty(COALESCE(SUM(stored_size), 0)) FROM files) AS storage_size,
                  (SELECT COUNT(*) FROM files)                              AS file_count
            """)
            stats = dict(row)
            plan_rows = await db_conn.fetch("""
                SELECT s.plan_key, COUNT(u.id) AS users
                FROM subscriptions s
                LEFT JOIN users u ON u.subscription_id = s.id
                WHERE s.is_active
                GROUP BY s.plan_key, s.sort_order
                ORDER BY s.sort_order, s.plan_key
            """)
            plan_counts = {r["plan_key"]: int(r["users"]) for r in plan_rows}
            stats["plan_counts"] = plan_counts
            for key, count in plan_counts.items():
                stats[f"{key}_count"] = count
            return stats

    async def recent_activity(
        self,
        limit: int = 50,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> list[dict]:
        async with self.connection(conn) as db_conn:
            rows = await db_conn.fetch("""
                SELECT m.id, m.role, m.content, m.tokens_used, m.model, m.cost_usd, m.created_at,
                       c.id AS chat_id, c.title AS chat_title,
                       u.id AS user_id, u.username, u.email
                FROM messages m
                JOIN chats c ON c.id = m.chat_id
                JOIN users u ON u.id = c.user_id
                ORDER BY m.created_at DESC
                LIMIT $1
            """, limit)
            return [dict(r) for r in rows]

    async def model_usage(
        self,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> list[dict]:
        async with self.connection(conn) as db_conn:
            rows = await db_conn.fetch("""
                SELECT model,
                       COUNT(*)                       AS calls,
                       COALESCE(SUM(tokens_used), 0)  AS tokens,
                       COALESCE(SUM(cost_usd), 0)     AS cost
                FROM messages
                WHERE role = 'assistant' AND model <> ''
                GROUP BY model
                ORDER BY cost DESC
            """)
            return [dict(r) for r in rows]

    async def admin_metrics(
        self,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM request_logs WHERE status = 'completed') AS completed_requests,
                    (SELECT COUNT(*) FROM request_logs WHERE status = 'blocked') AS blocked_requests,
                    (SELECT COALESCE(SUM(cost_units), 0) FROM request_logs WHERE status = 'completed') AS total_cost_units,
                    (SELECT COALESCE(SUM(total_tokens), 0) FROM request_logs WHERE status = 'completed') AS total_tokens,
                    (SELECT COALESCE(AVG(savings_pct), 0) FROM efficiency_metrics) AS avg_savings_pct,
                    (SELECT COALESCE(SUM(saved_tokens), 0) FROM efficiency_metrics) AS saved_tokens_total
                """
            )
            model_rows = await db_conn.fetch(
                """
                SELECT model_name, COUNT(*) AS requests, COALESCE(SUM(cost_units), 0) AS cost_units
                FROM request_logs
                WHERE status = 'completed' AND model_name IS NOT NULL AND model_name <> ''
                GROUP BY model_name
                ORDER BY cost_units DESC
                """
            )
            out = dict(row)
            out["model_usage"] = [dict(r) for r in model_rows]
            return out

    async def overview_metrics(
        self,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            return dict(await db_conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM users) AS total_users,
                    (SELECT COUNT(DISTINCT user_id) FROM request_logs WHERE created_at > now() - INTERVAL '24 hours') AS active_users_24h,
                    (SELECT COUNT(DISTINCT user_id) FROM request_logs WHERE created_at > now() - INTERVAL '7 days') AS active_users_7d,
                    (SELECT COUNT(*) FROM request_logs) AS total_requests,
                    (SELECT COALESCE(SUM(cost_usd), 0) FROM request_logs) AS total_cost,
                    (SELECT COALESCE(AVG(latency_ms), 0) FROM request_logs WHERE status IN ('success', 'completed')) AS avg_latency,
                    (SELECT COALESCE(AVG(CASE WHEN cache_hit THEN 100 ELSE 0 END), 0) FROM request_logs) AS cache_hit_rate,
                    (SELECT COALESCE(AVG(savings_percent), 0) FROM rag_metrics) AS avg_rag_savings
                """
            ))

    async def rag_metrics(
        self,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            summary = await db_conn.fetchrow(
                """
                SELECT
                    COALESCE(AVG(chunks_used), 0) AS avg_chunks_used,
                    COALESCE(AVG(context_tokens), 0) AS avg_context_size,
                    COALESCE(AVG(savings_percent), 0) AS avg_savings_percent,
                    COALESCE(AVG(CASE WHEN cache_hit THEN 100 ELSE 0 END), 0) AS cache_hit_percent
                FROM rag_metrics
                """
            )
            slow = await db_conn.fetch(
                """
                SELECT query, latency_ms, chunks_used, created_at
                FROM rag_metrics
                ORDER BY latency_ms DESC
                LIMIT 20
                """
            )
        out = dict(summary)
        out["slowest_queries"] = [dict(r) for r in slow]
        return out

    async def usage_metrics(
        self,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            daily = await db_conn.fetch(
                """
                SELECT date, SUM(requests_count) AS requests, SUM(cost_usd) AS cost
                FROM user_activity_daily
                WHERE date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY date
                ORDER BY date
                """
            )
            top_users = await db_conn.fetch(
                """
                SELECT u.id, u.email, SUM(uad.requests_count) AS requests, SUM(uad.cost_usd) AS cost
                FROM user_activity_daily uad
                JOIN users u ON u.id = uad.user_id
                WHERE uad.date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY u.id, u.email
                ORDER BY requests DESC
                LIMIT 20
                """
            )
            top_models = await db_conn.fetch(
                """
                SELECT COALESCE(model, model_name, '') AS model, COUNT(*) AS requests, SUM(cost_usd) AS cost
                FROM request_logs
                WHERE created_at >= now() - INTERVAL '30 days'
                GROUP BY COALESCE(model, model_name, '')
                ORDER BY requests DESC
                LIMIT 20
                """
            )
        return {
            "requests_per_day": [dict(r) for r in daily],
            "top_users": [dict(r) for r in top_users],
            "top_models": [dict(r) for r in top_models],
        }

    async def marketing_metrics(
        self,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            funnel = await db_conn.fetchrow(
                """
                WITH
                  visit AS (SELECT COUNT(*)::bigint AS n FROM funnel_events WHERE event_name='visit'),
                  signup AS (SELECT COUNT(*)::bigint AS n FROM funnel_events WHERE event_name='signup'),
                  first_request AS (SELECT COUNT(DISTINCT user_id)::bigint AS n FROM request_logs WHERE status IN ('success','completed')),
                  paid AS (
                    SELECT COUNT(DISTINCT user_id)::bigint AS n
                    FROM funnel_events
                    WHERE event_name='paid'
                  )
                SELECT visit.n AS visit, signup.n AS signup, first_request.n AS first_request, paid.n AS paid
                FROM visit, signup, first_request, paid
                """
            )
            sources = await db_conn.fetch(
                """
                SELECT COALESCE(source, 'unknown') AS source, COUNT(*) AS events
                FROM funnel_events
                WHERE created_at >= now() - INTERVAL '30 days'
                GROUP BY COALESCE(source, 'unknown')
                ORDER BY events DESC
                """
            )
            campaigns = await db_conn.fetch(
                """
                SELECT COALESCE(campaign, 'none') AS campaign, COUNT(*) AS events
                FROM funnel_events
                WHERE created_at >= now() - INTERVAL '30 days'
                GROUP BY COALESCE(campaign, 'none')
                ORDER BY events DESC
                LIMIT 30
                """
            )
        return {
            "funnel": dict(funnel),
            "traffic_sources": [dict(r) for r in sources],
            "campaign_performance": [dict(r) for r in campaigns],
        }

