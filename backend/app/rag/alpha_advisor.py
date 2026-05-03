"""AlphaAdvisor — recommends HYBRID_ALPHA per (query_type, course_id) pair.

Static defaults based on query type; can be overridden by learned values from
the rag_alpha_config table (populated nightly by update_alpha_config.py).
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.cache import CacheClient

_ALPHA_BY_TYPE: dict[str, float] = {
    "definitional":  0.65,
    "computational": 0.55,
    "conceptual":    0.80,
    "procedural":    0.72,
    "unknown":       0.70,
}

# Regex heuristics for fast query classification — no LLM call.
_DEFINITIONAL_RE = re.compile(
    r"\b(what is|what are|define|definition|meaning of|explain|describe)\b", re.I
)
_COMPUTATIONAL_RE = re.compile(
    r"\b(calculate|compute|solve|how much|how many|formula|equation|result)\b", re.I
)
_PROCEDURAL_RE = re.compile(
    r"\b(how to|step[s]? (to|for)|procedure|process|method|implement|setup|configure)\b", re.I
)

_REDIS_TTL = 60  # seconds


def classify_query(query: str) -> str:
    if _COMPUTATIONAL_RE.search(query):
        return "computational"
    if _DEFINITIONAL_RE.search(query):
        return "definitional"
    if _PROCEDURAL_RE.search(query):
        return "procedural"
    return "conceptual"


class AlphaAdvisor:
    """Thread-safe, async-friendly alpha recommender."""

    def __init__(self, redis: "CacheClient | None" = None) -> None:
        self._redis = redis

    async def recommend(
        self,
        query: str,
        *,
        course_id: str | None = None,
    ) -> tuple[float, str]:
        """Return (alpha, query_type). Never raises — falls back to static defaults."""
        query_type = classify_query(query)
        static_alpha = _ALPHA_BY_TYPE.get(query_type, 0.70)

        if course_id and self._redis is not None:
            learned = await self._get_learned_alpha(course_id, query_type)
            if learned is not None:
                return learned, query_type

        return static_alpha, query_type

    async def _get_learned_alpha(self, course_id: str, query_type: str) -> float | None:
        if self._redis is None:
            return None
        cache_key = f"rag:alpha:{course_id}:{query_type}"
        try:
            raw = await self._redis.get(cache_key)
            if raw:
                return float(raw)
        except Exception:
            pass
        return None
