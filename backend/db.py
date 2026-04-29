"""Legacy compatibility data access layer.

The pool lifecycle has moved to app.db. SQL functions remain here during the
incremental repository extraction so existing imports keep working.
"""

import asyncpg
import json
from typing import Optional, Any

from app.db.pool import database
from billing_plans import DEFAULT_INTERNAL_TOKENS_PER_REQUEST, DEFAULT_PLAN_KEY, SUBSCRIPTION_PLANS


async def create_pool():
    await database.create_pool()
    await init_schema()


async def close_pool():
    await database.close_pool()


def pool() -> asyncpg.Pool:
    return database.pool()


# ── Schema ─────────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id               SERIAL PRIMARY KEY,
    username         TEXT    NOT NULL UNIQUE,
    email            TEXT    NOT NULL UNIQUE,
    password_hash    TEXT    NOT NULL,
    plan             TEXT    NOT NULL DEFAULT 'free',   -- legacy (kept for compatibility)
    period           TEXT    NOT NULL DEFAULT 'daily',  -- legacy (kept for compatibility)
    usage_count      INTEGER NOT NULL DEFAULT 0,        -- legacy (kept for compatibility)
    usage_reset_at   TIMESTAMPTZ,                       -- legacy (kept for compatibility)
    subscription_id  INTEGER,
    is_admin         BOOLEAN NOT NULL DEFAULT FALSE,
    is_blocked       BOOLEAN NOT NULL DEFAULT FALSE,
    total_spent_usd  NUMERIC(12,6)   DEFAULT 0,
    created_at       TIMESTAMPTZ     DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chats (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT    NOT NULL DEFAULT 'Новый чат',
    template   TEXT    NOT NULL DEFAULT 'deep',
    model      TEXT    NOT NULL DEFAULT 'gemini-2.0-flash',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chats_user_id_idx ON chats(user_id, updated_at DESC);

-- Content-addressed file registry (deduplication)
CREATE TABLE IF NOT EXISTS files (
    sha256        TEXT    PRIMARY KEY,
    mime_type     TEXT    NOT NULL,
    compressed    BOOLEAN NOT NULL DEFAULT FALSE,
    original_size INTEGER NOT NULL,
    stored_size   INTEGER NOT NULL,
    ref_count     INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id     UUID    NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role        TEXT    NOT NULL,
    content     TEXT    NOT NULL DEFAULT '',
    tokens_used INTEGER NOT NULL DEFAULT 0,
    model       TEXT    NOT NULL DEFAULT '',
    cost_usd    NUMERIC(12,6)   DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_chat_id_idx ON messages(chat_id, created_at ASC);

-- Links messages ↔ files (many-to-many)
CREATE TABLE IF NOT EXISTS message_files (
    id                SERIAL PRIMARY KEY,
    message_id        UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    sha256            TEXT NOT NULL REFERENCES files(sha256),
    original_filename TEXT NOT NULL,
    display_order     SMALLINT DEFAULT 0
);
CREATE INDEX IF NOT EXISTS mf_message_idx ON message_files(message_id);

-- Auto-generated topic mindmap per chat
CREATE TABLE IF NOT EXISTS mindmaps (
    chat_id    UUID    PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
    markdown   TEXT    NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Analytics event log (append-only)
CREATE TABLE IF NOT EXISTS events (
    id         BIGSERIAL PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    event      TEXT    NOT NULL,
    props      JSONB   NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_event_time_idx ON events(event, created_at DESC);
CREATE INDEX IF NOT EXISTS events_user_time_idx  ON events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS events_time_idx       ON events(created_at DESC);

-- Subscription plans and internal token budgets
CREATE TABLE IF NOT EXISTS subscriptions (
    id              SERIAL PRIMARY KEY,
    plan_key        TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    price_rub       INTEGER NOT NULL DEFAULT 0,
    daily_limit     INTEGER NOT NULL,
    weekly_limit    INTEGER NOT NULL,
    monthly_limit   INTEGER NOT NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-user rolling counters with anchored period starts (single row per user)
CREATE TABLE IF NOT EXISTS user_usage (
    user_id               INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    day_start             DATE NOT NULL,
    week_start            DATE NOT NULL,
    month_start           DATE NOT NULL,
    daily_used            INTEGER NOT NULL DEFAULT 0,
    weekly_used           INTEGER NOT NULL DEFAULT 0,
    monthly_used          INTEGER NOT NULL DEFAULT 0,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS user_usage_updated_idx ON user_usage(updated_at DESC);

-- Per-request tracking
CREATE TABLE IF NOT EXISTS request_logs (
    id                    BIGSERIAL PRIMARY KEY,
    user_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint              TEXT NOT NULL,
    model_name            TEXT,
    status                TEXT NOT NULL DEFAULT 'pending', -- pending|completed|failed|blocked
    cache_hit             BOOLEAN NOT NULL DEFAULT FALSE,
    period_day_start      DATE,
    period_week_start     DATE,
    period_month_start    DATE,
    consumed_units        INTEGER NOT NULL DEFAULT 0,
    input_tokens          INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    context_tokens        INTEGER NOT NULL DEFAULT 0,
    total_tokens          INTEGER NOT NULL DEFAULT 0,
    estimated_no_rag      INTEGER NOT NULL DEFAULT 0,
    actual_with_rag       INTEGER NOT NULL DEFAULT 0,
    savings_pct           NUMERIC(6,3) NOT NULL DEFAULT 0,
    cost_units            NUMERIC(14,8) NOT NULL DEFAULT 0,
    error_text            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS request_logs_user_time_idx ON request_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS request_logs_model_time_idx ON request_logs(model_name, created_at DESC);
CREATE INDEX IF NOT EXISTS request_logs_status_time_idx ON request_logs(status, created_at DESC);

-- RAG efficiency by request
CREATE TABLE IF NOT EXISTS efficiency_metrics (
    request_log_id        BIGINT PRIMARY KEY REFERENCES request_logs(id) ON DELETE CASCADE,
    user_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    estimated_no_rag      INTEGER NOT NULL,
    actual_with_rag       INTEGER NOT NULL,
    saved_tokens          INTEGER NOT NULL,
    savings_pct           NUMERIC(6,3) NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS efficiency_user_time_idx ON efficiency_metrics(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS efficiency_time_idx ON efficiency_metrics(created_at DESC);

-- RAG analytics (per-query)
CREATE TABLE IF NOT EXISTS rag_metrics (
    id                        BIGSERIAL PRIMARY KEY,
    user_id                   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query                     TEXT NOT NULL,
    chunks_used               INTEGER NOT NULL DEFAULT 0,
    context_tokens            INTEGER NOT NULL DEFAULT 0,
    total_tokens              INTEGER NOT NULL DEFAULT 0,
    estimated_tokens_no_rag   INTEGER NOT NULL DEFAULT 0,
    savings_percent           NUMERIC(6,3) NOT NULL DEFAULT 0,
    latency_ms                INTEGER NOT NULL DEFAULT 0,
    cache_hit                 BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rag_metrics_user_time_idx ON rag_metrics(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS rag_metrics_time_idx ON rag_metrics(created_at DESC);
CREATE INDEX IF NOT EXISTS rag_metrics_cache_idx ON rag_metrics(cache_hit, created_at DESC);

-- Daily per-user pre-aggregations
CREATE TABLE IF NOT EXISTS user_activity_daily (
    user_id                   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date                      DATE NOT NULL,
    requests_count            INTEGER NOT NULL DEFAULT 0,
    tokens_used               BIGINT NOT NULL DEFAULT 0,
    cost_usd                  NUMERIC(14,8) NOT NULL DEFAULT 0,
    rag_savings_avg           NUMERIC(6,3) NOT NULL DEFAULT 0,
    session_count             INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, date)
);
CREATE INDEX IF NOT EXISTS user_activity_date_idx ON user_activity_daily(date DESC);
CREATE INDEX IF NOT EXISTS user_activity_tokens_idx ON user_activity_daily(tokens_used DESC);

-- Product/marketing funnel events
CREATE TABLE IF NOT EXISTS funnel_events (
    id                        BIGSERIAL PRIMARY KEY,
    user_id                   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    event_name                TEXT NOT NULL,
    source                    TEXT,
    campaign                  TEXT,
    metadata                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS funnel_events_time_idx ON funnel_events(created_at DESC);
CREATE INDEX IF NOT EXISTS funnel_events_name_time_idx ON funnel_events(event_name, created_at DESC);
CREATE INDEX IF NOT EXISTS funnel_events_source_idx ON funnel_events(source, created_at DESC);
CREATE INDEX IF NOT EXISTS funnel_events_campaign_idx ON funnel_events(campaign, created_at DESC);
CREATE INDEX IF NOT EXISTS funnel_events_metadata_gin_idx ON funnel_events USING GIN(metadata);

-- Global daily snapshot
CREATE TABLE IF NOT EXISTS system_metrics (
    date                      DATE PRIMARY KEY,
    total_requests            BIGINT NOT NULL DEFAULT 0,
    total_cost                NUMERIC(14,8) NOT NULL DEFAULT 0,
    avg_latency               NUMERIC(10,3) NOT NULL DEFAULT 0,
    cache_hit_rate            NUMERIC(6,3) NOT NULL DEFAULT 0,
    rag_savings_avg           NUMERIC(6,3) NOT NULL DEFAULT 0,
    _latency_points           BIGINT NOT NULL DEFAULT 0,
    _cache_hits               BIGINT NOT NULL DEFAULT 0,
    _rag_points               NUMERIC(16,6) NOT NULL DEFAULT 0
);
"""


async def init_schema():
    """
    Initialize the database schema. Wrapped in a transaction-scoped
    advisory lock so that when multiple uvicorn workers start at once,
    only one runs the DDL and the others wait for it to finish.
    """
    # Import RAG schema here to avoid circular imports at module level
    try:
        from rag import RAG_SCHEMA
    except ImportError:
        RAG_SCHEMA = ""

    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(424242)")
            await conn.execute(SCHEMA)
            if RAG_SCHEMA:
                await conn.execute(RAG_SCHEMA)
            # Migrations for DBs created by earlier versions
            for stmt in (
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_id INTEGER",
                "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS price_rub INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0",
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'users_subscription_fk'
                    ) THEN
                        ALTER TABLE users
                            ADD CONSTRAINT users_subscription_fk
                            FOREIGN KEY (subscription_id) REFERENCES subscriptions(id);
                    END IF;
                END $$;
                """,
                "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS model TEXT",
                "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS latency_ms INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(14,8) NOT NULL DEFAULT 0",
                "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS consumed_units INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    # Use a savepoint per migration so one bad statement
                    # does not abort the whole startup transaction.
                    async with conn.transaction():
                        await conn.execute(stmt)
                except Exception as e:
                    print(f"[migrate] {stmt}: {e}")
            for plan in SUBSCRIPTION_PLANS:
                await conn.execute(
                    """
                    INSERT INTO subscriptions (
                        plan_key, display_name, price_rub,
                        daily_limit, weekly_limit, monthly_limit, sort_order
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (plan_key) DO UPDATE
                    SET
                        display_name = EXCLUDED.display_name,
                        price_rub = EXCLUDED.price_rub,
                        daily_limit = EXCLUDED.daily_limit,
                        weekly_limit = EXCLUDED.weekly_limit,
                        monthly_limit = EXCLUDED.monthly_limit,
                        sort_order = EXCLUDED.sort_order,
                        is_active = TRUE
                    """,
                    plan["plan_key"],
                    plan["display_name"],
                    plan["price_rub"],
                    plan["daily_limit"],
                    plan["weekly_limit"],
                    plan["monthly_limit"],
                    plan["sort_order"],
                )
            await conn.execute(
                """
                UPDATE users u
                SET subscription_id = s.id
                FROM subscriptions s
                WHERE u.subscription_id IS NULL AND s.plan_key = COALESCE(u.plan, $1)
                """,
                DEFAULT_PLAN_KEY,
            )


# ── User queries ───────────────────────────────────────────────────────────────
async def create_user(username: str, email: str, password_hash: str) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO users (username, email, password_hash, subscription_id)
               VALUES (
                    $1, $2, $3,
                    (SELECT id FROM subscriptions WHERE plan_key=$4 LIMIT 1)
               )
               RETURNING *""",
            username, email, password_hash, DEFAULT_PLAN_KEY,
        )
        return dict(row)


async def get_user_by_email(email: str) -> Optional[dict]:
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE email=$1", email)
        return dict(row) if row else None


async def get_user_by_id(uid: int) -> Optional[dict]:
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)
        return dict(row) if row else None


async def get_user_by_username(username: str) -> Optional[dict]:
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE username=$1", username)
        return dict(row) if row else None


# ── Chat queries ───────────────────────────────────────────────────────────────
async def get_chats(uid: int) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM chats WHERE user_id=$1 ORDER BY updated_at DESC", uid
        )
        return [dict(r) for r in rows]


async def create_chat(uid: int, template: str, model: str) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO chats (user_id, title, template, model)
               VALUES ($1, 'Новый чат', $2, $3) RETURNING *""",
            uid, template, model,
        )
        return dict(row)


async def update_chat_settings(chat_id: str, uid: int, **kwargs):
    allowed = {"template", "model", "title", "updated_at", "course_id"}
    sets, vals = [], [chat_id, uid]
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k}=${len(vals)+1}")
            vals.append(v)
    if not sets:
        return
    async with pool().acquire() as conn:
        await conn.execute(
            f"UPDATE chats SET {','.join(sets)} WHERE id=$1 AND user_id=$2", *vals
        )


async def delete_chat(chat_id: str, uid: int):
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM chats WHERE id=$1 AND user_id=$2", chat_id, uid)


async def get_chat(chat_id: str, uid: int) -> Optional[dict]:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM chats WHERE id=$1 AND user_id=$2", chat_id, uid
        )
        return dict(row) if row else None

def check_limits(user):
    limits = {plan["plan_key"]: plan["monthly_limit"] for plan in SUBSCRIPTION_PLANS}
    plan_key = user.get("plan") or DEFAULT_PLAN_KEY
    usage_count = int(user.get("usage_count") or 0)

    if usage_count >= limits.get(plan_key, limits[DEFAULT_PLAN_KEY]):
        return False

    return True


def _week_start_expr() -> str:
    return "(date_trunc('week', now() AT TIME ZONE 'UTC'))::date"


def _month_start_expr() -> str:
    return "(date_trunc('month', now() AT TIME ZONE 'UTC'))::date"


async def check_and_consume_limit(
    user_id: int,
    endpoint: str,
    units: int = DEFAULT_INTERNAL_TOKENS_PER_REQUEST,
) -> dict[str, Any]:
    """
    Atomically consume internal quota units for daily/weekly/monthly quota.
    Returns {allowed, request_log_id, remaining{...}}.
    """
    units = max(1, int(units or DEFAULT_INTERNAL_TOKENS_PER_REQUEST))
    async with pool().acquire() as conn:
        async with conn.transaction():
            plan = await conn.fetchrow(
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

            row = await conn.fetchrow(
                f"""
                INSERT INTO user_usage (
                    user_id, day_start, week_start, month_start,
                    daily_used, weekly_used, monthly_used
                )
                SELECT $1, CURRENT_DATE, {_week_start_expr()}, {_month_start_expr()}, $5, $5, $5
                WHERE $5 <= $2 AND $5 <= $3 AND $5 <= $4
                ON CONFLICT (user_id) DO UPDATE
                SET
                    day_start = CASE WHEN user_usage.day_start <> CURRENT_DATE THEN CURRENT_DATE ELSE user_usage.day_start END,
                    week_start = CASE WHEN user_usage.week_start <> {_week_start_expr()} THEN {_week_start_expr()} ELSE user_usage.week_start END,
                    month_start = CASE WHEN user_usage.month_start <> {_month_start_expr()} THEN {_month_start_expr()} ELSE user_usage.month_start END,
                    daily_used = CASE
                        WHEN user_usage.day_start <> CURRENT_DATE THEN $5
                        WHEN user_usage.daily_used + $5 <= $2 THEN user_usage.daily_used + $5
                        ELSE user_usage.daily_used
                    END,
                    weekly_used = CASE
                        WHEN user_usage.week_start <> {_week_start_expr()} THEN $5
                        WHEN user_usage.weekly_used + $5 <= $3 THEN user_usage.weekly_used + $5
                        ELSE user_usage.weekly_used
                    END,
                    monthly_used = CASE
                        WHEN user_usage.month_start <> {_month_start_expr()} THEN $5
                        WHEN user_usage.monthly_used + $5 <= $4 THEN user_usage.monthly_used + $5
                        ELSE user_usage.monthly_used
                    END,
                    updated_at = now()
                WHERE
                    (CASE WHEN user_usage.day_start <> CURRENT_DATE THEN 0 ELSE user_usage.daily_used END) + $5 <= $2
                    AND (CASE WHEN user_usage.week_start <> {_week_start_expr()} THEN 0 ELSE user_usage.weekly_used END) + $5 <= $3
                    AND (CASE WHEN user_usage.month_start <> {_month_start_expr()} THEN 0 ELSE user_usage.monthly_used END) + $5 <= $4
                RETURNING user_id, day_start, week_start, month_start, daily_used, weekly_used, monthly_used
                """,
                user_id, plan["daily_limit"], plan["weekly_limit"], plan["monthly_limit"], units,
            )
            if not row:
                await conn.execute(
                    "INSERT INTO request_logs (user_id, endpoint, status, error_text) VALUES ($1, $2, 'blocked', 'quota_exceeded')",
                    user_id, endpoint,
                )
                usage = await get_user_usage_snapshot(user_id, conn=conn)
                return {"allowed": False, "reason": "quota_exceeded", "remaining": usage}

            log_row = await conn.fetchrow(
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


async def get_user_usage_snapshot(user_id: int, conn: asyncpg.Connection | None = None) -> dict[str, Any]:
    own_conn = False
    if conn is None:
        own_conn = True
        conn = await pool().acquire()
    try:
        row = await conn.fetchrow(
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
        if not row:
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
    finally:
        if own_conn and conn is not None:
            await pool().release(conn)


async def finalize_request_usage(
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
) -> None:
    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
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
            await conn.execute(
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


async def fail_and_refund_request(request_log_id: int, error_text: str = "") -> None:
    async with pool().acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow(
                """
                SELECT user_id, period_day_start, period_week_start, period_month_start, consumed_units
                FROM request_logs
                WHERE id = $1
                """,
                request_log_id,
            )
            if not r:
                return
            units = max(1, int(r["consumed_units"] or DEFAULT_INTERNAL_TOKENS_PER_REQUEST))
            await conn.execute(
                """
                UPDATE user_usage
                SET
                    daily_used = CASE WHEN day_start = $2 THEN GREATEST(daily_used - $5, 0) ELSE daily_used END,
                    weekly_used = CASE WHEN week_start = $3 THEN GREATEST(weekly_used - $5, 0) ELSE weekly_used END,
                    monthly_used = CASE WHEN month_start = $4 THEN GREATEST(monthly_used - $5, 0) ELSE monthly_used END,
                    updated_at = now()
                WHERE user_id = $1
                """,
                r["user_id"], r["period_day_start"], r["period_week_start"], r["period_month_start"], units,
            )
            await conn.execute(
                "UPDATE request_logs SET status='failed', error_text=$2, completed_at=now() WHERE id=$1",
                request_log_id, (error_text or "")[:400],
            )

# ── Message queries ────────────────────────────────────────────────────────────
async def get_messages(chat_id: str) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM messages WHERE chat_id=$1 ORDER BY created_at ASC",
            chat_id,
        )
        msgs = [dict(r) for r in rows]
        for m in msgs:
            file_rows = await conn.fetch(
                """SELECT f.sha256, f.mime_type, f.compressed, f.original_size,
                          mf.original_filename
                   FROM message_files mf
                   JOIN files f ON f.sha256 = mf.sha256
                   WHERE mf.message_id=$1
                   ORDER BY mf.display_order""",
                m["id"],
            )
            m["files"] = [dict(r) for r in file_rows]
        return msgs


async def save_message(
    chat_id: str, role: str, content: str,
    tokens: int = 0, model: str = "", cost_usd: float = 0,
    file_metas: list[dict] = None,
) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO messages (chat_id, role, content, tokens_used, model, cost_usd)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING *""",
            chat_id, role, content, tokens, model, cost_usd,
        )
        msg = dict(row)
        if file_metas:
            for i, fm in enumerate(file_metas):
                await conn.execute(
                    """INSERT INTO message_files (message_id, sha256, original_filename, display_order)
                       VALUES ($1,$2,$3,$4)""",
                    msg["id"], fm["sha256"], fm["original_filename"], i,
                )
        msg["files"] = file_metas or []
        return msg


# ── File registry ──────────────────────────────────────────────────────────────
async def register_file(sha256: str, mime: str, compressed: bool, orig_size: int, stored_size: int) -> dict:
    """Upsert file record, increment ref_count on existing."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO files (sha256, mime_type, compressed, original_size, stored_size, ref_count)
               VALUES ($1,$2,$3,$4,$5,1)
               ON CONFLICT (sha256) DO UPDATE
                 SET ref_count = files.ref_count + 1
               RETURNING *""",
            sha256, mime, compressed, orig_size, stored_size,
        )
        return dict(row)


async def release_file(sha256: str) -> int:
    """Decrement ref_count. Returns new count (0 = can delete from disk)."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE files SET ref_count=ref_count-1 WHERE sha256=$1 RETURNING ref_count",
            sha256,
        )
        return row["ref_count"] if row else 0


async def get_file_meta(sha256: str) -> Optional[dict]:
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM files WHERE sha256=$1", sha256)
        return dict(row) if row else None


# ── Mindmap queries ────────────────────────────────────────────────────────────
async def get_mindmap(chat_id: str) -> Optional[dict]:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT markdown, updated_at FROM mindmaps WHERE chat_id=$1", chat_id
        )
        return dict(row) if row else None


async def save_mindmap(chat_id: str, markdown: str):
    async with pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO mindmaps (chat_id, markdown, updated_at)
               VALUES ($1, $2, now())
               ON CONFLICT (chat_id) DO UPDATE
                 SET markdown = EXCLUDED.markdown,
                     updated_at = now()""",
            chat_id, markdown,
        )


# ── Admin queries ──────────────────────────────────────────────────────────────
async def list_users(search: str = "", limit: int = 100, offset: int = 0) -> list[dict]:
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
        params: list = []
        if search:
            sql += " WHERE u.username ILIKE $1 OR u.email ILIKE $1"
            params.append(f"%{search}%")
        sql += f" ORDER BY u.created_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
        params += [limit, offset]
        async with pool().acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]


async def count_users(search: str = "") -> int:
    sql, params = "SELECT COUNT(*) FROM users", []
    if search:
        sql += " WHERE username ILIKE $1 OR email ILIKE $1"
        params.append(f"%{search}%")
    async with pool().acquire() as conn:
        return await conn.fetchval(sql, *params)


async def admin_set_user_field(uid: int, field: str, value):
    allowed = {
        "plan",
        "period",
        "usage_count",
        "usage_reset_at",
        "is_admin",
        "is_blocked"
    }
    if field not in allowed:
        raise ValueError(f"Field {field} not allowed")
    async with pool().acquire() as conn:
        await conn.execute(f"UPDATE users SET {field} = $1 WHERE id = $2", value, uid)


async def admin_set_user_plan(uid: int, plan_key: str) -> bool:
    async with pool().acquire() as conn:
        plan = await conn.fetchrow(
            "SELECT id, plan_key FROM subscriptions WHERE plan_key=$1 AND is_active",
            plan_key,
        )
        if not plan:
            return False
        result = await conn.execute(
            "UPDATE users SET subscription_id=$1, plan=$2 WHERE id=$3",
            plan["id"], plan["plan_key"], uid,
        )
        return result != "UPDATE 0"


async def admin_delete_user(uid: int):
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM users WHERE id = $1", uid)


async def get_platform_stats() -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow("""
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
        plan_rows = await conn.fetch("""
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


async def get_recent_activity(limit: int = 50) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch("""
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


async def get_model_usage() -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch("""
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


async def get_admin_metrics() -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
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
        model_rows = await conn.fetch(
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


async def insert_rag_metric(
    *,
    user_id: int,
    query: str,
    chunks_used: int,
    context_tokens: int,
    total_tokens: int,
    estimated_tokens_no_rag: int,
    savings_percent: float,
    latency_ms: int,
    cache_hit: bool,
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rag_metrics (
                user_id, query, chunks_used, context_tokens, total_tokens,
                estimated_tokens_no_rag, savings_percent, latency_ms, cache_hit
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            user_id, query[:4000], chunks_used, context_tokens, total_tokens,
            estimated_tokens_no_rag, savings_percent, latency_ms, cache_hit,
        )


async def insert_funnel_event(
    *,
    user_id: int | None,
    event_name: str,
    source: str | None = None,
    campaign: str | None = None,
    metadata: dict | None = None,
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO funnel_events (user_id, event_name, source, campaign, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            user_id, event_name, source, campaign, json.dumps(metadata or {}),
        )


async def log_request_metrics(
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
) -> None:
    async with pool().acquire() as conn:
        async with conn.transaction():
            if request_log_id:
                await conn.execute(
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
                await conn.execute(
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

            await conn.execute(
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

            await conn.execute(
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


async def admin_metrics_overview() -> dict:
    async with pool().acquire() as conn:
        return dict(await conn.fetchrow(
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


async def admin_metrics_rag() -> dict:
    async with pool().acquire() as conn:
        summary = await conn.fetchrow(
            """
            SELECT
                COALESCE(AVG(chunks_used), 0) AS avg_chunks_used,
                COALESCE(AVG(context_tokens), 0) AS avg_context_size,
                COALESCE(AVG(savings_percent), 0) AS avg_savings_percent,
                COALESCE(AVG(CASE WHEN cache_hit THEN 100 ELSE 0 END), 0) AS cache_hit_percent
            FROM rag_metrics
            """
        )
        slow = await conn.fetch(
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


async def admin_metrics_usage() -> dict:
    async with pool().acquire() as conn:
        daily = await conn.fetch(
            """
            SELECT date, SUM(requests_count) AS requests, SUM(cost_usd) AS cost
            FROM user_activity_daily
            WHERE date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY date
            ORDER BY date
            """
        )
        top_users = await conn.fetch(
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
        top_models = await conn.fetch(
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


async def admin_metrics_marketing() -> dict:
    async with pool().acquire() as conn:
        funnel = await conn.fetchrow(
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
        sources = await conn.fetch(
            """
            SELECT COALESCE(source, 'unknown') AS source, COUNT(*) AS events
            FROM funnel_events
            WHERE created_at >= now() - INTERVAL '30 days'
            GROUP BY COALESCE(source, 'unknown')
            ORDER BY events DESC
            """
        )
        campaigns = await conn.fetch(
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
