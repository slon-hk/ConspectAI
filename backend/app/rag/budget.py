"""Budget-aware model tier selection for the RAG pipeline."""

from __future__ import annotations

from typing import Any

from app.infrastructure.cache import CacheClient, NullCache

TIER_CONFIG: dict[str, dict[str, Any]] = {
    "lite": {
        "models":         ["gemini-2.5-flash-lite", "gemini-3.1-flash-lite-preview"],
        "default_model":  "gemini-2.5-flash-lite",
        "context_budget": 2000,
        "history_budget": 1500,
        "allow_expand":   False,
        "allow_rerank":   True,
        "allow_compress": True,
    },
    "standard": {
        "models":         ["gemini-3.1-flash-lite-preview", "gemini-2.5-flash"],
        "default_model":  "gemini-3.1-flash-lite-preview",
        "context_budget": 3000,
        "history_budget": 3000,
        "allow_expand":   True,
        "allow_rerank":   True,
        "allow_compress": True,
    },
    "pro": {
        "models":         ["gemini-2.5-flash", "gemini-2.5-pro"],
        "default_model":  "gemini-2.5-flash",
        "context_budget": 4500,
        "history_budget": 5000,
        "allow_expand":   True,
        "allow_rerank":   True,
        "allow_compress": False,
    },
}

_PRO_PLAN_KEYS = {"pro", "premium", "enterprise"}


class BudgetGate:
    """
    Evaluates the user's remaining daily budget and selects a model tier.
    Results are cached in Redis for 60 seconds to avoid a DB hit per request.
    """

    def __init__(self, usage_repo: Any, redis: CacheClient = NullCache()) -> None:
        self._usage = usage_repo
        self._redis = redis

    async def evaluate(self, user_id: int, preferred_model: str = "") -> dict[str, Any]:
        import msgpack

        cache_key = f"rag:budget:{user_id}"
        snapshot: dict[str, Any] | None = None

        raw = await self._redis.get(cache_key)
        if raw:
            try:
                snapshot = msgpack.unpackb(raw, raw=False)
            except Exception:
                snapshot = None

        if snapshot is None:
            snapshot = await self._usage.get_usage_snapshot(user_id)
            try:
                await self._redis.set(cache_key, msgpack.packb(snapshot), ttl_seconds=60)
            except Exception:
                pass

        daily_limit    = max(snapshot.get("daily_limit", 1), 1)
        remaining      = snapshot.get("daily_remaining", daily_limit)
        remaining_pct  = remaining / daily_limit
        plan_key       = snapshot.get("plan_key", "free")
        is_pro         = plan_key in _PRO_PLAN_KEYS

        if remaining_pct > 0.5 and is_pro:
            tier = "pro"
        elif remaining_pct > 0.2:
            tier = "standard"
        else:
            tier = "lite"

        config = TIER_CONFIG[tier]
        model = preferred_model if preferred_model in config["models"] else config["default_model"]

        return {
            "tier":           tier,
            "model":          model,
            "context_budget": config["context_budget"],
            "history_budget": config["history_budget"],
            "allow_expand":   config["allow_expand"],
            "allow_rerank":   config["allow_rerank"],
            "allow_compress": config["allow_compress"],
            "remaining_pct":  remaining_pct,
            "tier_degraded":  tier != ("pro" if is_pro else "standard"),
        }
