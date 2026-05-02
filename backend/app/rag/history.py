"""Chat history management: sliding window + async summarization."""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg

from app.db.pool import Database, database as _default_db
from app.domain.rag.utils import rough_token_count

SUMMARY_MODEL = "gemini-2.5-flash-lite"
RECENT_TURNS_ALWAYS_KEPT = 6
SUMMARIZE_THRESHOLD_TOKENS = 6000
SUMMARIZE_THRESHOLD_MESSAGES = 40
SUMMARY_MAX_WORDS = 200

HISTORY_BUDGET_BY_TIER: dict[str, int] = {
    "lite":     1500,
    "standard": 3000,
    "pro":      5000,
}


class HistoryManager:
    """Trims gemini_history to a token budget and triggers async summarization."""

    def __init__(self, db: Database = _default_db, gemini_api_key: str = "") -> None:
        self._db = db
        self._gemini_api_key = gemini_api_key

    async def get_trimmed_history(
        self,
        chat_id: str,
        messages: list[dict],
        tier: str = "standard",
    ) -> tuple[list[dict], int]:
        """
        Returns (gemini_history, tokens_used).
        Keeps last RECENT_TURNS_ALWAYS_KEPT turns verbatim; injects summary for older turns.
        """
        budget = HISTORY_BUDGET_BY_TIER.get(tier, 3000)

        gemini_msgs = [
            {
                "role": "user" if m["role"] == "user" else "model",
                "parts": [m.get("content") or "…"],
            }
            for m in messages
        ]

        total_tokens = sum(rough_token_count(m.get("content") or "") for m in messages)
        if total_tokens <= budget:
            return gemini_msgs, total_tokens

        recent = gemini_msgs[-RECENT_TURNS_ALWAYS_KEPT:]
        older  = gemini_msgs[:-RECENT_TURNS_ALWAYS_KEPT]
        recent_tokens = sum(rough_token_count(m["parts"][0]) for m in recent)
        remaining_budget = budget - recent_tokens

        result: list[dict] = []

        summary_row = await self._get_latest_summary(chat_id)
        if summary_row and summary_row["summary_text"]:
            s_tokens = rough_token_count(summary_row["summary_text"])
            if s_tokens <= remaining_budget * 0.35:
                result.append({
                    "role": "user",
                    "parts": [f"[Summary of earlier conversation]: {summary_row['summary_text']}"],
                })
                remaining_budget -= s_tokens

        for msg in reversed(older):
            t = rough_token_count(msg["parts"][0])
            if remaining_budget - t >= 0:
                result.insert(len(result) - (1 if result and result[0]["parts"][0].startswith("[Summary") else 0), msg)
                remaining_budget -= t
            else:
                break

        result.extend(recent)
        used = budget - remaining_budget + recent_tokens
        return result, used

    async def maybe_summarize(self, chat_id: str, messages: list[dict]) -> None:
        """Async, never blocks the response path. Call via asyncio.create_task()."""
        total_tokens = sum(rough_token_count(m.get("content") or "") for m in messages)
        if (
            total_tokens < SUMMARIZE_THRESHOLD_TOKENS
            and len(messages) < SUMMARIZE_THRESHOLD_MESSAGES
        ):
            return

        summary_row = await self._get_latest_summary(chat_id)
        if summary_row:
            # Estimate how many messages arrived after the last summary
            msgs_after = len(messages) - summary_row.get("message_count", 0)
            if msgs_after < 10:
                return

        to_summarize = messages[:-RECENT_TURNS_ALWAYS_KEPT]
        if not to_summarize:
            return

        conversation_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {(m.get('content') or '')[:500]}"
            for m in to_summarize
        )
        prompt = (
            f"Produce a dense summary (max {SUMMARY_MAX_WORDS} words) of the following "
            "study assistant conversation. Preserve: topics covered, unresolved questions, "
            "key formulas or concepts, user preferences.\n\n" + conversation_text
        )

        try:
            import google.generativeai as genai

            model = genai.GenerativeModel(SUMMARY_MODEL)
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: model.generate_content(prompt),
            )
            summary_text = resp.text.strip()[:800]

            last_msg = to_summarize[-1]
            await self._upsert_summary(
                chat_id=chat_id,
                summary_text=summary_text,
                covers_up_to_id=str(last_msg.get("id", "")),
                message_count=len(to_summarize),
                token_count=rough_token_count(summary_text),
                model_used=SUMMARY_MODEL,
            )
        except Exception as exc:
            print(f"[history] summarization failed for chat {chat_id}: {exc}")

    # ── DB helpers ─────────────────────────────────────────────────────────────

    async def _get_latest_summary(self, chat_id: str) -> dict | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT summary_text, covers_up_to_id, message_count, token_count "
                "FROM chat_history_summaries WHERE chat_id = $1",
                chat_id,
            )
            return dict(row) if row else None

    async def _upsert_summary(
        self,
        *,
        chat_id: str,
        summary_text: str,
        covers_up_to_id: str,
        message_count: int,
        token_count: int,
        model_used: str,
    ) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO chat_history_summaries
                    (chat_id, summary_text, covers_up_to_id, message_count, token_count, model_used)
                VALUES ($1, $2, $3::uuid, $4, $5, $6)
                ON CONFLICT (chat_id) DO UPDATE
                    SET summary_text    = EXCLUDED.summary_text,
                        covers_up_to_id = EXCLUDED.covers_up_to_id,
                        message_count   = EXCLUDED.message_count,
                        token_count     = EXCLUDED.token_count,
                        model_used      = EXCLUDED.model_used,
                        created_at      = now()
                """,
                chat_id,
                summary_text,
                covers_up_to_id,
                message_count,
                token_count,
                model_used,
            )
