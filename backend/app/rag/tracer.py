"""Per-request pipeline tracing for cost and latency dashboards."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineTracer:
    """
    Collects per-stage latency and token counts for a single RAG request.
    Call record_stage() as each step completes; call build_trace() at the end.
    """

    _started: float = field(default_factory=time.perf_counter)
    _stages: dict[str, int] = field(default_factory=dict)
    _meta: dict[str, Any] = field(default_factory=dict)

    def stage(self, name: str) -> "_StageTimer":
        return _StageTimer(name, self)

    def record_stage(self, name: str, latency_ms: int, **meta: Any) -> None:
        self._stages[name] = latency_ms
        self._meta.update(meta)

    def set(self, **meta: Any) -> None:
        self._meta.update(meta)

    def build_trace(self) -> dict[str, Any]:
        total_ms = int((time.perf_counter() - self._started) * 1000)
        return {
            "latency_embed_ms":    self._stages.get("embed"),
            "latency_retrieve_ms": self._stages.get("retrieve"),
            "latency_rerank_ms":   self._stages.get("rerank"),
            "latency_context_ms":  self._stages.get("context"),
            "latency_llm_ms":      self._stages.get("llm"),
            "latency_total_ms":    total_ms,
            **self._meta,
        }


class _StageTimer:
    def __init__(self, name: str, tracer: PipelineTracer) -> None:
        self._name = name
        self._tracer = tracer
        self._start = time.perf_counter()

    def stop(self, **meta: Any) -> int:
        ms = int((time.perf_counter() - self._start) * 1000)
        self._tracer.record_stage(self._name, ms, **meta)
        return ms
