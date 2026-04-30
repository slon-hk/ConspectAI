"""OLTP repository methods for RAG caches and read-side image lookup."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class RagCacheRepository(BaseRepository):
    async def get_query_embedding(
        self,
        *,
        query_hash: str,
        conn: asyncpg.Connection | None = None,
    ) -> list[float] | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                "SELECT embedding FROM rag_query_cache WHERE query_hash = $1",
                query_hash,
            )
            return list(row["embedding"]) if row else None

    async def store_query_embedding(
        self,
        *,
        query_hash: str,
        embedding_pgvector: str,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                """INSERT INTO rag_query_cache (query_hash, embedding)
                   VALUES ($1, $2::vector)""",
                query_hash,
                embedding_pgvector,
            )

    async def get_answer_cache(
        self,
        *,
        cache_key: str,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                "SELECT answer, image_ids FROM rag_answer_cache WHERE cache_key = $1",
                cache_key,
            )
            return dict(row) if row else None

    async def touch_answer_cache(
        self,
        *,
        cache_key: str,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                """UPDATE rag_answer_cache
                   SET hit_count = hit_count + 1, last_used = now()
                   WHERE cache_key = $1""",
                cache_key,
            )

    async def set_answer_cache(
        self,
        *,
        cache_key: str,
        answer: str,
        image_ids_json: str,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                """INSERT INTO rag_answer_cache (cache_key, answer, image_ids)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (cache_key) DO UPDATE
                       SET hit_count = rag_answer_cache.hit_count + 1,
                           last_used = now()""",
                cache_key,
                answer,
                image_ids_json,
            )

    async def resolve_images(
        self,
        *,
        image_ids: list[str],
        conn: asyncpg.Connection | None = None,
    ) -> list[dict]:
        async with self.connection(conn) as db_conn:
            rows = await db_conn.fetch(
                "SELECT id, file_path, caption, mime_type FROM rag_images WHERE id = ANY($1::uuid[])",
                image_ids,
            )
            return [
                {
                    "id": str(row["id"]),
                    "file_path": row["file_path"],
                    "caption": row["caption"],
                    "mime_type": row["mime_type"],
                }
                for row in rows
            ]

    async def cleanup_answer_cache(
        self,
        *,
        max_age_days: int,
        conn: asyncpg.Connection | None = None,
    ) -> str:
        async with self.connection(conn) as db_conn:
            return await db_conn.execute(
                """DELETE FROM rag_answer_cache
                   WHERE last_used < now() - ($1 || ' days')::interval""",
                str(max_age_days),
            )
