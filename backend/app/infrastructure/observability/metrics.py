"""In-memory process metrics.

These counters are intentionally local to the running process. They back the
admin live-system endpoint today and can later be mirrored to Prometheus or
another metrics backend without changing API/services code.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque


class SysMetrics:
    """Lightweight per-process metrics for live monitoring."""

    def __init__(self) -> None:
        self.started_at = time.time()
        self.http_calls = defaultdict(int)
        self.http_errors = 0
        self.http_latencies = deque(maxlen=2000)
        self.gemini_calls = defaultdict(int)
        self.gemini_errors = defaultdict(int)
        self.gemini_latencies = deque(maxlen=500)
        self.bg_mindmap_runs = 0
        self.bg_mindmap_failed = 0

    def record_http(self, path: str, status: int, latency_ms: float) -> None:
        key_path = self._normalise_path(path)
        cls = f"{status // 100}xx"
        self.http_calls[(key_path, cls)] += 1
        if status >= 500:
            self.http_errors += 1
        self.http_latencies.append(latency_ms)

    def record_gemini(self, model: str, latency_ms: float, ok: bool) -> None:
        if ok:
            self.gemini_calls[model] += 1
            self.gemini_latencies.append(latency_ms)
        else:
            self.gemini_errors[model] += 1

    @staticmethod
    def _normalise_path(path: str) -> str:
        path = re.sub(
            r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "/{uuid}",
            path,
            flags=re.I,
        )
        return re.sub(r"/\d+(?=/|$)", "/{id}", path)

    def snapshot(self) -> dict:
        lat = sorted(self.http_latencies) if self.http_latencies else [0]
        gem_lat = sorted(self.gemini_latencies) if self.gemini_latencies else [0]

        def pct(values: list[float] | list[int], point: float) -> float:
            if not values:
                return 0
            return round(values[min(len(values) - 1, int(len(values) * point))], 1)

        top_routes = sorted(
            ((path, cls, count) for (path, cls), count in self.http_calls.items()),
            key=lambda item: -item[2],
        )[:12]

        return {
            "uptime_seconds": int(time.time() - self.started_at),
            "http_total": sum(self.http_calls.values()),
            "http_errors": self.http_errors,
            "http_p50_ms": pct(lat, 0.50),
            "http_p95_ms": pct(lat, 0.95),
            "http_p99_ms": pct(lat, 0.99),
            "top_routes": [{"path": p, "status": s, "count": c} for p, s, c in top_routes],
            "gemini_calls": dict(self.gemini_calls),
            "gemini_errors": dict(self.gemini_errors),
            "gemini_p50_ms": pct(gem_lat, 0.50),
            "gemini_p95_ms": pct(gem_lat, 0.95),
            "bg_mindmap_runs": self.bg_mindmap_runs,
            "bg_mindmap_failed": self.bg_mindmap_failed,
        }


system_metrics = SysMetrics()
