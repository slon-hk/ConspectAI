"""Request/RAG metrics orchestration service."""

from __future__ import annotations

from app.repositories.olap import RagMetricRepository, RequestMetricRepository


class RequestMetricsService:
    def __init__(
        self,
        request_metric_repository: RequestMetricRepository,
        rag_metric_repository: RagMetricRepository,
    ) -> None:
        self._request_metric_repository = request_metric_repository
        self._rag_metric_repository = rag_metric_repository

    async def log_request_from_usage(
        self,
        *,
        request_log_id: int | None,
        user_id: int,
        usage: dict,
        status: str,
        error_message: str,
        latency_ms: int,
        session_count_inc: int = 1,
    ) -> None:
        await self._request_metric_repository.log_request_metrics(
            request_log_id=request_log_id,
            user_id=user_id,
            model=usage.get("model_name", "unknown"),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cost_usd=float(usage.get("cost_units", 0)),
            status=status,
            error_message=error_message,
            latency_ms=latency_ms,
            cache_hit=bool(usage.get("cache_hit", False)),
            rag_savings_percent=float(usage.get("savings_pct", 0)),
            session_count_inc=session_count_inc,
        )

    async def log_rag_from_usage(
        self,
        *,
        user_id: int,
        usage: dict,
        latency_ms: int,
    ) -> None:
        rag_meta = usage.get("rag_metrics")
        if not rag_meta:
            return
        await self._rag_metric_repository.record_query(
            user_id=user_id,
            query=rag_meta.get("query", ""),
            chunks_used=int(rag_meta.get("chunks_used", 0)),
            context_tokens=int(rag_meta.get("context_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
            estimated_tokens_no_rag=int(rag_meta.get("estimated_tokens_no_rag", 0)),
            savings_percent=float(usage.get("savings_pct", 0)),
            latency_ms=int(rag_meta.get("latency_ms", latency_ms)),
            cache_hit=bool(usage.get("cache_hit", False)),
        )
