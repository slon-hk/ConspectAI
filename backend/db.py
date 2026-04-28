"""
Async PostgreSQL layer using asyncpg directly (no ORM overhead).
Connection pool is created once at startup and shared across requests.
"""

import os
import asyncio
import asyncpg
from typing import Optional

_pool: Optional[asyncpg.Pool] = None

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://orion:orion@localhost:5432/orion"
)


async def create_pool():
    global _pool
    last_err = None
    # Retry briefly in case Postgres is still warming up after healthcheck
    for attempt in range(20):
        try:
            _pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=2,
                max_size=20,
                command_timeout=30,
            )
            break
        except (OSError, asyncpg.PostgresError) as e:
            last_err = e
            await asyncio.sleep(1.5)
    else:
        raise RuntimeError(f"Could not connect to Postgres after 20 tries: {last_err}")
    await init_schema()


async def close_pool():
    if _pool:
        await _pool.close()


def pool() -> asyncpg.Pool:
    assert _pool is not None, "DB pool not initialized"
    return _pool


# ── Schema ─────────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id               SERIAL PRIMARY KEY,
    username         TEXT    NOT NULL UNIQUE,
    email            TEXT    NOT NULL UNIQUE,
    password_hash    TEXT    NOT NULL,
    tokens_remaining INTEGER NOT NULL DEFAULT 10000,
    is_trial         BOOLEAN NOT NULL DEFAULT TRUE,
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
            ):
                try:
                    await conn.execute(stmt)
                except Exception as e:
                    print(f"[migrate] {stmt}: {e}")


# ── User queries ───────────────────────────────────────────────────────────────
async def create_user(username: str, email: str, password_hash: str, trial_tokens: int) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO users (username, email, password_hash, tokens_remaining)
               VALUES ($1, $2, $3, $4) RETURNING *""",
            username, email, password_hash, trial_tokens,
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


async def deduct_tokens(uid: int, tokens: int, cost_usd: float):
    async with pool().acquire() as conn:
        await conn.execute(
            """UPDATE users
               SET tokens_remaining = GREATEST(0, tokens_remaining - $1),
                   total_spent_usd  = total_spent_usd + $2,
                   is_trial         = CASE WHEN tokens_remaining - $1 <= 0 THEN is_trial ELSE is_trial END
               WHERE id=$3""",
            tokens, cost_usd, uid,
        )


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
    allowed = {"template", "model", "title", "updated_at"}
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
    sql = """SELECT id, username, email, tokens_remaining, is_trial, is_admin, is_blocked,
                    total_spent_usd, created_at,
                    (SELECT COUNT(*) FROM chats c WHERE c.user_id = users.id)    AS chat_count,
                    (SELECT COUNT(*) FROM messages m JOIN chats c ON c.id = m.chat_id
                                     WHERE c.user_id = users.id)                  AS message_count
             FROM users"""
    params: list = []
    if search:
        sql += " WHERE username ILIKE $1 OR email ILIKE $1"
        params.append(f"%{search}%")
    sql += f" ORDER BY created_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
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
    allowed = {"tokens_remaining", "is_admin", "is_blocked", "is_trial"}
    if field not in allowed:
        raise ValueError(f"Field {field} not allowed")
    async with pool().acquire() as conn:
        await conn.execute(f"UPDATE users SET {field} = $1 WHERE id = $2", value, uid)


async def admin_grant_tokens(uid: int, amount: int):
    """Add tokens to a user's balance and exit trial if it was on."""
    async with pool().acquire() as conn:
        await conn.execute(
            """UPDATE users
               SET tokens_remaining = tokens_remaining + $1,
                   is_trial = FALSE
               WHERE id = $2""",
            amount, uid,
        )


async def admin_delete_user(uid: int):
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM users WHERE id = $1", uid)


async def get_platform_stats() -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
              (SELECT COUNT(*) FROM users)                              AS user_count,
              (SELECT COUNT(*) FROM users WHERE is_trial)               AS trial_count,
              (SELECT COUNT(*) FROM users WHERE is_blocked)             AS blocked_count,
              (SELECT COUNT(*) FROM chats)                              AS chat_count,
              (SELECT COUNT(*) FROM messages)                           AS message_count,
              (SELECT COUNT(*) FROM messages WHERE role = 'assistant')  AS reply_count,
              (SELECT COALESCE(SUM(tokens_used), 0) FROM messages)      AS total_tokens,
              (SELECT COALESCE(SUM(cost_usd), 0)    FROM messages)      AS total_cost,
              (SELECT COUNT(*) FROM users WHERE created_at > now() - INTERVAL '24 hours') AS new_users_24h,
              (SELECT COUNT(*) FROM messages WHERE created_at > now() - INTERVAL '24 hours') AS messages_24h,
              (SELECT pg_size_pretty(SUM(stored_size)) FROM files)      AS storage_size,
              (SELECT COUNT(*) FROM files)                              AS file_count
        """)
        return dict(row)


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