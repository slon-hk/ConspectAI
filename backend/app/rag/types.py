"""Shared value types for the RAG pipeline. No external deps — stdlib only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ChunkCandidate:
    chunk_id: str
    text: str
    score: float
    cos_sim: float = 0.0
    bm25_score: float = 0.0
    importance_hint: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RewriteResult:
    original: str
    rewritten: str
    rewrite_type: str        # 'expand' | 'rephrase' | 'decompose' | 'noop'
    rewrite_model: str
    latency_ms: int = 0
    embedding_delta: float | None = None


@dataclass
class RetrieveResult:
    chunks: list[dict]
    images: list[dict]
    retrieval_cache_hit: bool
    candidates: list[ChunkCandidate] = field(default_factory=list)
    hybrid_alpha: float = 0.70


@dataclass
class ContextResult:
    context_str: str
    context_tokens: int
    chunks_compressed: int = 0
    context_reduction_pct: float = 0.0


@dataclass
class FeedbackEvent:
    user_id: int
    trace_id: int | None
    chat_id: str | None
    signal: str              # 'thumbs_up' | 'thumbs_down' | 'regenerate' | 'follow_up' | 'copy_answer'
    signal_value: int        # +1 | -1 | 0
    query_text: str | None = None
    answer_text: str | None = None
    chunk_ids: list[str] = field(default_factory=list)
    comment: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class TrainingBatch:
    reranker_rows: list[dict]    # {query, positive, negatives}
    sft_rows: list[dict]         # {messages: [{role, content}]}
    extracted_at: datetime = field(default_factory=datetime.utcnow)
    since: datetime | None = None
