"""Feedback repository — writes user signals to rag_feedback."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class FeedbackRepository(BaseRepository):
    async def insert_feedback(
        self,
        *,
        user_id: int,
        trace_id: int | None,
        chat_id: str | None,
        signal: str,
        signal_value: int,
        comment: str | None = None,
        query_text: str | None = None,
        answer_text: str | None = None,
        chunk_ids: list[str] | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> int:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """
                INSERT INTO rag_feedback (
                    user_id, trace_id, chat_id, signal, signal_value,
                    comment, query_text, answer_text, chunk_ids
                )
                VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8, $9)
                RETURNING id
                """,
                user_id,
                trace_id,
                chat_id,
                signal,
                signal_value,
                comment,
                query_text[:4000] if query_text else None,
                answer_text[:8000] if answer_text else None,
                chunk_ids or [],
            )
            return row["id"]

    async def verify_trace_owner(
        self,
        *,
        trace_id: int,
        user_id: int,
        conn: asyncpg.Connection | None = None,
    ) -> bool:
        """Return True if the trace belongs to the user."""
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                "SELECT 1 FROM rag_pipeline_traces WHERE id = $1 AND user_id = $2",
                trace_id,
                user_id,
            )
            return row is not None
