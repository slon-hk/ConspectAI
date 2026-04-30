"""Application analytics tracking facade."""

from __future__ import annotations

from typing import Any

import analytics
from app.infrastructure.observability import system_metrics


class AnalyticsTrackingService:
    def track(self, event: str, user_id: int | None = None, **props: Any) -> None:
        analytics.track(event, user_id, **props)

    def record_http(self, path: str, status: int, latency_ms: float) -> None:
        system_metrics.record_http(path, status, latency_ms)

    def record_gemini(self, model: str, latency_ms: float, *, ok: bool) -> None:
        system_metrics.record_gemini(model, latency_ms, ok=ok)

    def increment_mindmap_runs(self) -> None:
        system_metrics.bg_mindmap_runs += 1

    def increment_mindmap_failures(self) -> None:
        system_metrics.bg_mindmap_failed += 1
