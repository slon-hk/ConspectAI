"""Funnel metric write repository."""

from __future__ import annotations

import json

import asyncpg

from app.repositories.base import BaseRepository


class FunnelMetricRepository(BaseRepository):
    async def record_event(
        self,
        *,
        user_id: int | None,
        event_name: str,
        source: str | None = None,
        campaign: str | None = None,
        metadata: dict | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                """
                INSERT INTO funnel_events (user_id, event_name, source, campaign, metadata)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                user_id, event_name, source, campaign, json.dumps(metadata or {}),
            )

