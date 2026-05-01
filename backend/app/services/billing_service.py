"""Billing and internal cost calculation service."""

from __future__ import annotations

from typing import Any


MODEL_PRICING: dict[str, dict[str, float]] = {
    "gemini-3.1-flash-lite-preview": {
        "input_per_1m": 0.10,
        "output_per_1m": 0.40,
        "context_multiplier": 0.15,
    },
    "gemini-2.0-flash": {
        "input_per_1m": 0.20,
        "output_per_1m": 0.80,
        "context_multiplier": 0.15,
    },
    "gemini-2.5-flash-lite": {
        "input_per_1m": 0.10,
        "output_per_1m": 0.40,
        "context_multiplier": 0.15,
    },
    "gemini-2.5-pro": {
        "input_per_1m": 1.25,
        "output_per_1m": 5.00,
        "context_multiplier": 0.15,
    },
}

DEFAULT_PRICING = {
    "input_per_1m": 0.30,
    "output_per_1m": 1.20,
    "context_multiplier": 0.15,
}


def calculate_cost_units(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    context_tokens: int,
) -> float:
    p = MODEL_PRICING.get(model_name, DEFAULT_PRICING)
    in_cost = (max(input_tokens, 0) / 1_000_000) * p["input_per_1m"]
    out_cost = (max(output_tokens, 0) / 1_000_000) * p["output_per_1m"]
    ctx_cost = (max(context_tokens, 0) / 1_000_000) * p["input_per_1m"] * p["context_multiplier"]
    return round(in_cost + out_cost + ctx_cost, 8)


class BillingService:
    def calculate_turn_usage(
        self,
        *,
        content: str,
        assistant_text: str,
        model_key: str,
        course_id: str | None,
        rag_result: dict,
        cache_hit: bool,
    ) -> dict[str, Any]:
        input_tokens = max(1, len(content or "") // 4)
        context_tokens = rag_result.get("context_tokens", 0) if course_id else 0
        output_tokens = max(1, len(assistant_text or "") // 4)
        total_tokens = input_tokens + context_tokens + output_tokens
        estimated_without_rag = (
            rag_result.get("estimated_without_rag_tokens", total_tokens)
            if course_id else total_tokens
        )
        actual_with_rag = (
            rag_result.get("actual_with_rag_tokens", total_tokens)
            if course_id else total_tokens
        )
        savings_pct = 0.0
        if estimated_without_rag > 0:
            savings_pct = round(max((estimated_without_rag - actual_with_rag) / estimated_without_rag, 0) * 100, 3)
        cost_usd = calculate_cost_units(model_key, input_tokens, output_tokens, context_tokens)
        if cache_hit:
            cost_usd = round(cost_usd * 0.01, 8)

        return {
            "input_tokens": input_tokens,
            "context_tokens": context_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_without_rag": estimated_without_rag,
            "actual_with_rag": actual_with_rag,
            "savings_pct": savings_pct,
            "cost_usd": cost_usd,
        }

    def build_request_billing_usage(
        self,
        *,
        model_key: str,
        cache_hit: bool,
        usage: dict,
        course_id: str | None,
        rag_result: dict,
        content: str,
    ) -> dict[str, Any]:
        return {
            "model_name": model_key,
            "cache_hit": cache_hit,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "context_tokens": usage["context_tokens"],
            "total_tokens": usage["total_tokens"],
            "estimated_no_rag": usage["estimated_without_rag"],
            "actual_with_rag": usage["actual_with_rag"],
            "savings_pct": usage["savings_pct"],
            "cost_units": usage["cost_usd"],
            "status": "completed",
            "rag_metrics": {
                "query": content or "",
                "chunks_used": rag_result.get("chunks_used", 0) if course_id else 0,
                "context_tokens": usage["context_tokens"],
                "estimated_tokens_no_rag": usage["estimated_without_rag"],
                "latency_ms": rag_result.get("latency_ms", 0) if course_id else 0,
            } if course_id else None,
        }
