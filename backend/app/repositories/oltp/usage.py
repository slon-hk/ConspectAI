"""Usage and quota OLTP repository."""

from __future__ import annotations

from typing import Any

import asyncpg

from app.repositories.base import BaseRepository


def _week_start_expr() -> str:
    return "(date_trunc('week', now() AT TIME ZONE 'UTC'))::date"


def _month_start_expr() -> str:
    return "(date_trunc('month', now() AT TIME ZONE 'UTC'))::date"


def _empty_usage_snapshot() -> dict[str, Any]:
    return {
        "plan_key": "free",
        "subscription_name": "Free",
        "price_rub": 0,
        "daily_limit": 0,
        "weekly_limit": 0,
        "monthly_limit": 0,
        "daily_used": 0,
        "weekly_used": 0,
        "monthly_used": 0,
        "daily_remaining": 0,
        "weekly_remaining": 0,
        "monthly_remaining": 0,
    }


def _usage_snapshot_from_row(row: asyncpg.Record | None) -> dict[str, Any]:
    if not row:
        return _empty_usage_snapshot()
    return {
        "plan_key": row["plan_key"],
        "subscription_name": row["display_name"],
        "price_rub": int(row["price_rub"]),
        "daily_limit": int(row["daily_limit"]),
        "weekly_limit": int(row["weekly_limit"]),
        "monthly_limit": int(row["monthly_limit"]),
        "daily_used": int(row["daily_used_now"]),
        "weekly_used": int(row["weekly_used_now"]),
        "monthly_used": int(row["monthly_used_now"]),
        "daily_remaining": max(int(row["daily_limit"]) - int(row["daily_used_now"]), 0),
        "weekly_remaining": max(int(row["weekly_limit"]) - int(row["weekly_used_now"]), 0),
        "monthly_remaining": max(int(row["monthly_limit"]) - int(row["monthly_used_now"]), 0),
    }


class UsageRepository(BaseRepository):
    async def reserve_quota_units(
        self,
        user_id: int,
        endpoint: str,
        units: int,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any]:
        async with self.connection(conn) as db_conn:
            async with db_conn.transaction():
                plan = await db_conn.fetchrow(
                    """
                    SELECT s.daily_limit, s.weekly_limit, s.monthly_limit
                    FROM users u
                    JOIN subscriptions s ON s.id = u.subscription_id
                    WHERE u.id = $1
                    FOR UPDATE
                    """,
                    user_id,
                )
                if not plan:
                    return {"allowed": False, "reason": "user_or_plan_not_found"}

                row = await db_conn.fetchrow(
                    f"""
                    INSERT INTO user_usage (
                        user_id, day_start, week_start, month_start,
                        daily_used, weekly_used, monthly_used
                    )
                    SELECT $1, CURRENT_DATE, {_week_start_expr()}, {_month_start_expr()}, $5::int, $5::int, $5::int
                    WHERE $5::int <= $2 AND $5::int <= $3 AND $5::int <= $4
                    ON CONFLICT (user_id) DO UPDATE
                    SET
                        day_start = CASE WHEN user_usage.day_start <> CURRENT_DATE THEN CURRENT_DATE ELSE user_usage.day_start END,
                        week_start = CASE WHEN user_usage.week_start <> {_week_start_expr()} THEN {_week_start_expr()} ELSE user_usage.week_start END,
                        month_start = CASE WHEN user_usage.month_start <> {_month_start_expr()} THEN {_month_start_expr()} ELSE user_usage.month_start END,
                        daily_used = CASE
                            WHEN user_usage.day_start <> CURRENT_DATE THEN $5::int
                            WHEN user_usage.daily_used + $5::int <= $2 THEN user_usage.daily_used + $5::int
                            ELSE user_usage.daily_used
                        END,
                        weekly_used = CASE
                            WHEN user_usage.week_start <> {_week_start_expr()} THEN $5::int
                            WHEN user_usage.weekly_used + $5::int <= $3 THEN user_usage.weekly_used + $5::int
                            ELSE user_usage.weekly_used
                        END,
                        monthly_used = CASE
                            WHEN user_usage.month_start <> {_month_start_expr()} THEN $5::int
                            WHEN user_usage.monthly_used + $5::int <= $4 THEN user_usage.monthly_used + $5::int
                            ELSE user_usage.monthly_used
                        END,
                        updated_at = now()
                    WHERE
                        (CASE WHEN user_usage.day_start <> CURRENT_DATE THEN 0 ELSE user_usage.daily_used END) + $5::int <= $2
                        AND (CASE WHEN user_usage.week_start <> {_week_start_expr()} THEN 0 ELSE user_usage.weekly_used END) + $5::int <= $3
                        AND (CASE WHEN user_usage.month_start <> {_month_start_expr()} THEN 0 ELSE user_usage.monthly_used END) + $5::int <= $4
                    RETURNING user_id, day_start, week_start, month_start, daily_used, weekly_used, monthly_used
                    """,
                    user_id, plan["daily_limit"], plan["weekly_limit"], plan["monthly_limit"], units,
                )
                if not row:
                    await db_conn.execute(
                        "INSERT INTO request_logs (user_id, endpoint, status, error_text) VALUES ($1, $2, 'blocked', 'quota_exceeded')",
                        user_id, endpoint,
                    )
                    usage = await self.get_usage_snapshot(user_id, conn=db_conn)
                    return {"allowed": False, "reason": "quota_exceeded", "remaining": usage}

                log_row = await db_conn.fetchrow(
                    """
                    INSERT INTO request_logs (
                        user_id, endpoint, status, consumed_units,
                        period_day_start, period_week_start, period_month_start
                    )
                    VALUES ($1, $2, 'pending', $3, $4, $5, $6)
                    RETURNING id
                    """,
                    user_id, endpoint, units, row["day_start"], row["week_start"], row["month_start"],
                )
                usage = {
                    "daily_remaining": max(plan["daily_limit"] - row["daily_used"], 0),
                    "weekly_remaining": max(plan["weekly_limit"] - row["weekly_used"], 0),
                    "monthly_remaining": max(plan["monthly_limit"] - row["monthly_used"], 0),
                }
                return {"allowed": True, "request_log_id": int(log_row["id"]), "remaining": usage}

    async def get_usage_snapshot(
        self,
        user_id: int,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, Any]:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                f"""
                SELECT
                    s.plan_key, s.display_name,
                    s.price_rub,
                    s.daily_limit, s.weekly_limit, s.monthly_limit,
                    COALESCE(CASE WHEN uu.day_start = CURRENT_DATE THEN uu.daily_used ELSE 0 END, 0) AS daily_used_now,
                    COALESCE(CASE WHEN uu.week_start = {_week_start_expr()} THEN uu.weekly_used ELSE 0 END, 0) AS weekly_used_now,
                    COALESCE(CASE WHEN uu.month_start = {_month_start_expr()} THEN uu.monthly_used ELSE 0 END, 0) AS monthly_used_now
                FROM users u
                JOIN subscriptions s ON s.id = u.subscription_id
                LEFT JOIN user_usage uu ON uu.user_id = u.id
                WHERE u.id = $1
                """,
                user_id,
            )
            return _usage_snapshot_from_row(row)

    async def finalize_request_usage(
        self,
        request_log_id: int,
        *,
        model_name: str,
        cache_hit: bool,
        input_tokens: int,
        output_tokens: int,
        context_tokens: int,
        total_tokens: int,
        estimated_no_rag: int,
        actual_with_rag: int,
        savings_pct: float,
        cost_units: float,
        status: str = "completed",
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            async with db_conn.transaction():
                await db_conn.execute(
                    """
                    UPDATE request_logs
                    SET
                        model_name = $2,
                        status = $3,
                        cache_hit = $4,
                        input_tokens = $5,
                        output_tokens = $6,
                        context_tokens = $7,
                        total_tokens = $8,
                        estimated_no_rag = $9,
                        actual_with_rag = $10,
                        savings_pct = $11,
                        cost_units = $12,
                        completed_at = now()
                    WHERE id = $1
                    """,
                    request_log_id, model_name, status, cache_hit, input_tokens, output_tokens,
                    context_tokens, total_tokens, estimated_no_rag, actual_with_rag, savings_pct, cost_units,
                )
                await db_conn.execute(
                    """
                    INSERT INTO efficiency_metrics (
                        request_log_id, user_id, estimated_no_rag, actual_with_rag, saved_tokens, savings_pct
                    )
                    SELECT
                        rl.id, rl.user_id, rl.estimated_no_rag, rl.actual_with_rag,
                        GREATEST(rl.estimated_no_rag - rl.actual_with_rag, 0),
                        rl.savings_pct
                    FROM request_logs rl
                    WHERE rl.id = $1
                    ON CONFLICT (request_log_id) DO UPDATE SET
                        estimated_no_rag = EXCLUDED.estimated_no_rag,
                        actual_with_rag = EXCLUDED.actual_with_rag,
                        saved_tokens = EXCLUDED.saved_tokens,
                        savings_pct = EXCLUDED.savings_pct
                    """,
                    request_log_id,
                )

    async def fail_and_refund_request(
        self,
        request_log_id: int,
        default_units: int,
        error_text: str = "",
        *,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            async with db_conn.transaction():
                row = await db_conn.fetchrow(
                    """
                    SELECT user_id, period_day_start, period_week_start, period_month_start, consumed_units
                    FROM request_logs
                    WHERE id = $1
                    """,
                    request_log_id,
                )
                if not row:
                    return
                units = max(1, int(row["consumed_units"] or default_units))
                await db_conn.execute(
                    """
                    UPDATE user_usage
                    SET
                        daily_used = CASE WHEN day_start = $2 THEN GREATEST(daily_used - $5, 0) ELSE daily_used END,
                        weekly_used = CASE WHEN week_start = $3 THEN GREATEST(weekly_used - $5, 0) ELSE weekly_used END,
                        monthly_used = CASE WHEN month_start = $4 THEN GREATEST(monthly_used - $5, 0) ELSE monthly_used END,
                        updated_at = now()
                    WHERE user_id = $1
                    """,
                    row["user_id"], row["period_day_start"], row["period_week_start"], row["period_month_start"], units,
                )
                await db_conn.execute(
                    "UPDATE request_logs SET status='failed', error_text=$2, completed_at=now() WHERE id=$1",
                    request_log_id, (error_text or "")[:400],
                )

