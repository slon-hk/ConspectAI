"""Protocol ABCs for the RAG pipeline. Zero concrete deps — only stdlib + types."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.rag.types import (
    ChunkCandidate,
    ContextResult,
    FeedbackEvent,
    RetrieveResult,
    RewriteResult,
    TrainingBatch,
)


@runtime_checkable
class QueryRewriter(Protocol):
    async def rewrite(
        self,
        query: str,
        chat_history: list[dict],
        *,
        budget_tier: str,
    ) -> RewriteResult: ...


@runtime_checkable
class Retriever(Protocol):
    async def retrieve(
        self,
        query: str,
        query_vec: list[float],
        *,
        course_ids: list[str] | None,
        alpha: float,
        top_k: int,
    ) -> RetrieveResult: ...


@runtime_checkable
class Reranker(Protocol):
    async def rerank(
        self,
        query: str,
        candidates: list[ChunkCandidate],
        *,
        top_k: int,
    ) -> list[ChunkCandidate]: ...


@runtime_checkable
class ContextBuilderP(Protocol):
    async def build(
        self,
        query: str,
        chunks: list[dict],
        images: list[dict],
        *,
        template: str,
        budget_tokens: int,
    ) -> ContextResult: ...


@runtime_checkable
class FeedbackCollector(Protocol):
    def emit(self, event: FeedbackEvent) -> None:
        """Fire-and-forget — must not block the calling coroutine."""
        ...


@runtime_checkable
class TrainingDataExtractor(Protocol):
    async def extract(
        self,
        *,
        since: datetime,
        min_feedback_count: int,
        max_rows: int,
    ) -> TrainingBatch: ...
