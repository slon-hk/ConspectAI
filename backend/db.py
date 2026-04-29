"""Legacy compatibility data access layer.

The pool lifecycle has moved to app.db. SQL functions remain here during the
incremental repository extraction so existing imports keep working.
"""

import asyncpg
import json
from typing import Optional, Any

from app.db.pool import database
from app.repositories.olap import AdminReportRepository
from app.repositories.oltp import (
    AdminUserRepository,
    ChatRepository,
    FileRepository,
    MessageRepository,
    MindmapRepository,
    UsageRepository,
    UserRepository,
)
from app.services import QuotaService, UsageService
from billing_plans import DEFAULT_INTERNAL_TOKENS_PER_REQUEST, DEFAULT_PLAN_KEY, SUBSCRIPTION_PLANS

_users = UserRepository(database)
_chats = ChatRepository(database)
_messages = MessageRepository(database)
_files = FileRepository(database)
_mindmaps = MindmapRepository(database)
_admin_users = AdminUserRepository(database)
_admin_reports = AdminReportRepository(database)
_usage_repository = UsageRepository(database)
_quota_service = QuotaService(_usage_repository, DEFAULT_INTERNAL_TOKENS_PER_REQUEST)
_usage_service = UsageService(_usage_repository, DEFAULT_INTERNAL_TOKENS_PER_REQUEST)


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
    return await _users.create(username, email, password_hash, DEFAULT_PLAN_KEY)


async def get_user_by_email(email: str) -> Optional[dict]:
    return await _users.get_by_email(email)


async def get_user_by_id(uid: int) -> Optional[dict]:
    return await _users.get_by_id(uid)


async def get_user_by_username(username: str) -> Optional[dict]:
    return await _users.get_by_username(username)


# ── Chat queries ───────────────────────────────────────────────────────────────
async def get_chats(uid: int) -> list[dict]:
    return await _chats.list_for_user(uid)


async def create_chat(uid: int, template: str, model: str) -> dict:
    return await _chats.create(uid, template, model)


async def update_chat_settings(chat_id: str, uid: int, **kwargs):
    await _chats.update_settings(chat_id, uid, **kwargs)


async def delete_chat(chat_id: str, uid: int):
    await _chats.delete(chat_id, uid)


async def get_chat(chat_id: str, uid: int) -> Optional[dict]:
    return await _chats.get(chat_id, uid)

def check_limits(user):
    limits = {plan["plan_key"]: plan["monthly_limit"] for plan in SUBSCRIPTION_PLANS}
    plan_key = user.get("plan") or DEFAULT_PLAN_KEY
    usage_count = int(user.get("usage_count") or 0)

    if usage_count >= limits.get(plan_key, limits[DEFAULT_PLAN_KEY]):
        return False

    return True


async def check_and_consume_limit(
    user_id: int,
    endpoint: str,
    units: int = DEFAULT_INTERNAL_TOKENS_PER_REQUEST,
) -> dict[str, Any]:
    """
    Atomically consume internal quota units for daily/weekly/monthly quota.
    Returns {allowed, request_log_id, remaining{...}}.
    """
    return await _quota_service.check_and_consume_limit(user_id, endpoint, units)


async def get_user_usage_snapshot(user_id: int, conn: asyncpg.Connection | None = None) -> dict[str, Any]:
    return await _usage_service.get_usage_snapshot(user_id, conn=conn)


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
    await _usage_service.finalize_request_usage(
        request_log_id,
        model_name=model_name,
        cache_hit=cache_hit,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        context_tokens=context_tokens,
        total_tokens=total_tokens,
        estimated_no_rag=estimated_no_rag,
        actual_with_rag=actual_with_rag,
        savings_pct=savings_pct,
        cost_units=cost_units,
        status=status,
    )


async def fail_and_refund_request(request_log_id: int, error_text: str = "") -> None:
    await _usage_service.fail_and_refund_request(request_log_id, error_text)

# ── Message queries ────────────────────────────────────────────────────────────
async def get_messages(chat_id: str) -> list[dict]:
    return await _messages.list_by_chat(chat_id)


async def save_message(
    chat_id: str, role: str, content: str,
    tokens: int = 0, model: str = "", cost_usd: float = 0,
    file_metas: list[dict] = None,
) -> dict:
    return await _messages.create(
        chat_id,
        role,
        content,
        tokens=tokens,
        model=model,
        cost_usd=cost_usd,
        file_metas=file_metas,
    )


# ── File registry ──────────────────────────────────────────────────────────────
async def register_file(sha256: str, mime: str, compressed: bool, orig_size: int, stored_size: int) -> dict:
    """Upsert file record, increment ref_count on existing."""
    return await _files.register(sha256, mime, compressed, orig_size, stored_size)


async def release_file(sha256: str) -> int:
    """Decrement ref_count. Returns new count (0 = can delete from disk)."""
    return await _files.release(sha256)


async def get_file_meta(sha256: str) -> Optional[dict]:
    return await _files.get(sha256)


# ── Mindmap queries ────────────────────────────────────────────────────────────
async def get_mindmap(chat_id: str) -> Optional[dict]:
    return await _mindmaps.get(chat_id)


async def save_mindmap(chat_id: str, markdown: str):
    await _mindmaps.save(chat_id, markdown)


# ── Admin queries ──────────────────────────────────────────────────────────────
async def list_users(search: str = "", limit: int = 100, offset: int = 0) -> list[dict]:
    return await _admin_users.list_users(search, limit, offset)


async def count_users(search: str = "") -> int:
    return await _admin_users.count_users(search)


async def admin_set_user_field(uid: int, field: str, value):
    await _admin_users.set_user_field(uid, field, value)


async def admin_set_user_plan(uid: int, plan_key: str) -> bool:
    return await _admin_users.set_user_plan(uid, plan_key)


async def admin_delete_user(uid: int):
    await _admin_users.delete_user(uid)


async def get_platform_stats() -> dict:
    return await _admin_reports.platform_stats()


async def get_recent_activity(limit: int = 50) -> list[dict]:
    return await _admin_reports.recent_activity(limit)


async def get_model_usage() -> list[dict]:
    return await _admin_reports.model_usage()


async def get_admin_metrics() -> dict:
    return await _admin_reports.admin_metrics()


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
    return await _admin_reports.overview_metrics()


async def admin_metrics_rag() -> dict:
    return await _admin_reports.rag_metrics()


async def admin_metrics_usage() -> dict:
    return await _admin_reports.usage_metrics()


async def admin_metrics_marketing() -> dict:
    return await _admin_reports.marketing_metrics()
