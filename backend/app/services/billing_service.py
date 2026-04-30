"""Billing and internal cost calculation service."""

from __future__ import annotations

from billing import calculate_cost_units


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
    ) -> dict:
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
    ) -> dict:
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
