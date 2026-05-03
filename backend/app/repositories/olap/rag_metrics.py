"""RAG metric write repository."""

from __future__ import annotations
from typing import Any

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

    async def record_pipeline_trace(
        self,
        *,
        user_id: int,
        trace: dict[str, Any],
        conn: asyncpg.Connection | None = None,
    ) -> int | None:
        """Insert a pipeline trace row and return the generated trace id."""
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """
                INSERT INTO rag_pipeline_traces (
                    user_id, chat_id, course_id, model_tier,
                    history_tokens_raw, history_tokens_used,
                    context_tokens, output_tokens, total_tokens,
                    l1_hit, l2_hit, l3_hit, retrieval_cache_hit,
                    latency_embed_ms, latency_retrieve_ms, latency_rerank_ms,
                    latency_context_ms, latency_llm_ms, latency_total_ms,
                    chunks_retrieved, chunks_used, chunks_compressed,
                    context_reduction_pct, cost_usd
                )
                VALUES (
                    $1,$2,$3,$4,
                    $5,$6,$7,$8,$9,
                    $10,$11,$12,$13,
                    $14,$15,$16,$17,$18,$19,
                    $20,$21,$22,$23,$24
                )
                RETURNING id
                """,
                user_id,
                trace.get("chat_id"),
                trace.get("course_id"),
                trace.get("model_tier"),
                trace.get("history_tokens_raw"),
                trace.get("history_tokens_used"),
                trace.get("context_tokens"),
                trace.get("output_tokens"),
                trace.get("total_tokens"),
                bool(trace.get("l1_hit")),
                bool(trace.get("l2_hit")),
                bool(trace.get("l3_hit")),
                bool(trace.get("retrieval_cache_hit")),
                trace.get("latency_embed_ms"),
                trace.get("latency_retrieve_ms"),
                trace.get("latency_rerank_ms"),
                trace.get("latency_context_ms"),
                trace.get("latency_llm_ms"),
                trace.get("latency_total_ms"),
                trace.get("chunks_retrieved"),
                trace.get("chunks_used"),
                trace.get("chunks_compressed"),
                trace.get("context_reduction_pct"),
                trace.get("cost_usd"),
            )
            return row["id"] if row else None

