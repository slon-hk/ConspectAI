"""Dynamic context building with chunk prioritization and extractive compression."""

from __future__ import annotations

import re

from app.domain.rag.utils import rough_token_count

IMAGE_CTX_LIMIT = 3

# Token budgets per prompt template
TEMPLATE_BUDGETS: dict[str, int] = {
    "deep":    4500,
    "solver":  4500,
    "exam":    2500,
    "summary": 2000,
    "concept": 3000,
}
DEFAULT_BUDGET = 3000

# Thresholds: chunks above these scores are never compressed/dropped
NO_COMPRESS_IMPORTANCE = 0.8
NO_COMPRESS_SCORE      = 0.85


class HeuristicReranker:
    """
    Score-based reranker using hybrid search score, ingestion-time importance hint,
    keyword overlap, and position bias. No model calls required.
    """

    def rerank(self, query: str, chunks: list[dict], top_k: int = 5) -> list[dict]:
        q_words = set(re.findall(r"\w+", query.lower()))

        for chunk in chunks:
            c_words = set(re.findall(r"\w+", (chunk.get("content") or "").lower()))
            union = q_words | c_words
            jaccard = len(q_words & c_words) / len(union) if union else 0.0

            importance = float(chunk.get("importance_hint") or 0.5)
            hybrid     = min(float(chunk.get("score") or 0.0), 1.0)
            position   = 1.0 if (chunk.get("chunk_index") or 99) < 3 else 0.5

            chunk["rank_score"] = (
                0.40 * hybrid
                + 0.25 * importance
                + 0.20 * jaccard
                + 0.15 * position
            )

        return sorted(chunks, key=lambda c: c["rank_score"], reverse=True)[:top_k]


class ContextBuilder:
    """
    Builds the context string for the LLM with dynamic token budgeting,
    priority-sorted chunks, and extractive compression for low-priority chunks.
    """

    def build(
        self,
        chunks: list[dict],
        images: list[dict],
        template: str = "default",
        override_budget: int | None = None,
    ) -> tuple[str, int, dict]:
        """
        Returns (context_str, tokens_used, stats).
        stats = {chunks_full, chunks_compressed, chunks_dropped}
        """
        budget = override_budget or TEMPLATE_BUDGETS.get(template, DEFAULT_BUDGET)
        allow_compress = len(chunks) > 2 and template not in ("deep", "solver")

        sorted_chunks = sorted(chunks, key=lambda c: c.get("rank_score", 0.0), reverse=True)

        parts: list[str] = []
        used_tokens = 0
        stats = {"chunks_full": 0, "chunks_compressed": 0, "chunks_dropped": 0}

        for i, chunk in enumerate(sorted_chunks):
            ct = chunk.get("token_count") or rough_token_count(chunk["content"])
            importance = float(chunk.get("importance_hint") or 0.5)
            rank_score = float(chunk.get("rank_score") or 0.0)

            can_compress = (
                allow_compress
                and importance < NO_COMPRESS_IMPORTANCE
                and rank_score   < NO_COMPRESS_SCORE
                and used_tokens  < budget * 0.85
            )

            if used_tokens + ct <= budget:
                parts.append(f"[Material {i + 1}]\n{chunk['content']}")
                used_tokens += ct
                stats["chunks_full"] += 1

            elif can_compress:
                remaining = budget - used_tokens
                if remaining >= 80:
                    compressed = _extractive_compress(chunk["content"], remaining)
                    parts.append(f"[Material {i + 1} — excerpt]\n{compressed}")
                    used_tokens += rough_token_count(compressed)
                    stats["chunks_compressed"] += 1
                else:
                    stats["chunks_dropped"] += 1

            else:
                stats["chunks_dropped"] += 1

        return "\n\n".join(parts), used_tokens, stats


def _extractive_compress(text: str, max_tokens: int) -> str:
    """
    Sentence-level extractive compression: score sentences by unique-word density,
    then reassemble top sentences in original order up to max_tokens.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if not sentences:
        return text

    scored = [
        (s, len(set(s.lower().split())) / max(len(s.split()), 1))
        for s in sentences
    ]
    by_score = sorted(scored, key=lambda x: -x[1])

    kept: set[str] = set()
    tokens_used = 0
    for s, _ in by_score:
        t = rough_token_count(s)
        if tokens_used + t <= max_tokens:
            kept.add(s)
            tokens_used += t
        else:
            break

    return " ".join(s for s in sentences if s in kept)


class ImportanceScorer:
    """
    Computes importance_hint for a chunk at ingestion time.
    Range [0.0, 1.0]. Written to rag_chunks.importance_hint.
    """

    _HEADING_RE   = re.compile(r"^#{1,3}\s|^[A-ZА-Я][^\n]{0,40}\n[=\-]{3,}", re.MULTILINE)
    _FORMULA_RE   = re.compile(r"\$\$|\\\(|\\\[|\\frac|\\int|\\sum|\\partial")
    _DEFN_RU_RE   = re.compile(r"\bназывается\b|\bэто\b|\bявляется\b|\bопределение\b", re.I)
    _DEFN_EN_RE   = re.compile(r"\bis defined as\b|\bdefinition\b|\bdenote\b", re.I)

    def score(self, content: str, chunk_index: int, source_count: int) -> float:
        hint = 0.0

        if self._HEADING_RE.search(content[:200]):
            hint += 0.3

        if self._FORMULA_RE.search(content):
            hint += 0.2

        if self._DEFN_RU_RE.search(content) or self._DEFN_EN_RE.search(content):
            hint += 0.2

        if source_count >= 3:
            hint += 0.1

        return min(hint, 1.0)
