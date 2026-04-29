"""RAG metric write repository."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class RagMetricRepository(BaseRepository):
    async def record_query(
        self,
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
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
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

