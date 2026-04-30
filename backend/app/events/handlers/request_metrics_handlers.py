"""Request and RAG metric event handlers."""

from __future__ import annotations

from app.events.base import BaseEvent
from app.repositories.olap import RagMetricRepository, RequestMetricRepository

REQUEST_METRICS_EVENT_TYPE = "metrics.request.completed"
RAG_METRICS_EVENT_TYPE = "metrics.rag.query"


class RequestMetricsEventHandler:
    def __init__(self, repository: RequestMetricRepository) -> None:
        self._repository = repository

    async def __call__(self, event: BaseEvent) -> None:
        payload = event.payload
        await self._repository.log_request_metrics(
            request_log_id=payload.get("request_log_id"),
            user_id=int(payload["user_id"]),
            model=str(payload.get("model", "unknown")),
            input_tokens=int(payload.get("input_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
            total_tokens=int(payload.get("total_tokens", 0)),
            cost_usd=float(payload.get("cost_usd", 0)),
            status=str(payload.get("status", "")),
            error_message=str(payload.get("error_message", "")),
            latency_ms=int(payload.get("latency_ms", 0)),
            cache_hit=bool(payload.get("cache_hit", False)),
            rag_savings_percent=float(payload.get("rag_savings_percent", 0)),
            session_count_inc=int(payload.get("session_count_inc", 1)),
        )


class RagMetricsEventHandler:
    def __init__(self, repository: RagMetricRepository) -> None:
        self._repository = repository

    async def __call__(self, event: BaseEvent) -> None:
        payload = event.payload
        await self._repository.record_query(
            user_id=int(payload["user_id"]),
            query=str(payload.get("query", "")),
            chunks_used=int(payload.get("chunks_used", 0)),
            context_tokens=int(payload.get("context_tokens", 0)),
            total_tokens=int(payload.get("total_tokens", 0)),
            estimated_tokens_no_rag=int(payload.get("estimated_tokens_no_rag", 0)),
            savings_percent=float(payload.get("savings_percent", 0)),
            latency_ms=int(payload.get("latency_ms", 0)),
            cache_hit=bool(payload.get("cache_hit", False)),
        )
