"""Request and aggregate metric write repository."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class RequestMetricRepository(BaseRepository):
    async def log_request_metrics(
        self,
        *,
        request_log_id: int | None,
        user_id: int,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cost_usd: float,
        status: str,
        error_message: str,
        latency_ms: int,
        cache_hit: bool,
        rag_savings_percent: float,
        session_count_inc: int = 1,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            async with db_conn.transaction():
                if request_log_id:
                    await db_conn.execute(
                        """
                        UPDATE request_logs
                        SET
                            model_name = $2,
                            model = $2,
                            input_tokens = $3,
                            output_tokens = $4,
                            total_tokens = $5,
                            cost_usd = $6,
                            cost_units = $6,
                            status = $7,
                            error_text = $8,
                            latency_ms = $9,
                            cache_hit = $10,
                            completed_at = now()
                        WHERE id = $1
                        """,
                        request_log_id, model, input_tokens, output_tokens, total_tokens,
                        cost_usd, status, error_message[:400], latency_ms, cache_hit,
                    )
                else:
                    await db_conn.execute(
                        """
                        INSERT INTO request_logs (
                            user_id, model_name, model, input_tokens, output_tokens, total_tokens,
                            cost_usd, cost_units, status, error_text, latency_ms, cache_hit, completed_at
                        )
                        VALUES ($1,$2,$2,$3,$4,$5,$6,$6,$7,$8,$9,$10,now())
                        """,
                        user_id, model, input_tokens, output_tokens, total_tokens, cost_usd,
                        status, error_message[:400], latency_ms, cache_hit,
                    )

                await db_conn.execute(
                    """
                    INSERT INTO user_activity_daily (
                        user_id, date, requests_count, tokens_used, cost_usd, rag_savings_avg, session_count
                    )
                    VALUES ($1, CURRENT_DATE, 1, $2, $3, $4, $5)
                    ON CONFLICT (user_id, date) DO UPDATE
                    SET
                        requests_count = user_activity_daily.requests_count + 1,
                        tokens_used = user_activity_daily.tokens_used + EXCLUDED.tokens_used,
                        cost_usd = user_activity_daily.cost_usd + EXCLUDED.cost_usd,
                        rag_savings_avg =
                            ((user_activity_daily.rag_savings_avg * user_activity_daily.requests_count) + EXCLUDED.rag_savings_avg)
                            / (user_activity_daily.requests_count + 1),
                        session_count = user_activity_daily.session_count + EXCLUDED.session_count
                    """,
                    user_id, total_tokens, cost_usd, rag_savings_percent, session_count_inc,
                )

                await db_conn.execute(
                    """
                    INSERT INTO system_metrics (
                        date, total_requests, total_cost, avg_latency, cache_hit_rate, rag_savings_avg,
                        _latency_points, _cache_hits, _rag_points
                    )
                    VALUES (
                        CURRENT_DATE,
                        1,
                        $1::numeric,
                        $2::numeric,
                        CASE WHEN $3::boolean THEN 100::numeric ELSE 0::numeric END,
                        $4::numeric,
                        $2::bigint,
                        CASE WHEN $3::boolean THEN 1::bigint ELSE 0::bigint END,
                        $4::numeric
                    )
                    ON CONFLICT (date) DO UPDATE
                    SET
                        total_requests = system_metrics.total_requests + 1,
                        total_cost = system_metrics.total_cost + EXCLUDED.total_cost,
                        _latency_points = system_metrics._latency_points + EXCLUDED._latency_points,
                        _cache_hits = system_metrics._cache_hits + EXCLUDED._cache_hits,
                        _rag_points = system_metrics._rag_points + EXCLUDED._rag_points,
                        avg_latency = (system_metrics._latency_points + EXCLUDED._latency_points)::numeric
                            / (system_metrics.total_requests + 1),
                        cache_hit_rate = ((system_metrics._cache_hits + EXCLUDED._cache_hits)::numeric
                            / (system_metrics.total_requests + 1)) * 100,
                        rag_savings_avg = (system_metrics._rag_points + EXCLUDED._rag_points)
                            / (system_metrics.total_requests + 1)
                    """,
                    cost_usd, latency_ms, cache_hit, rag_savings_percent,
                )

