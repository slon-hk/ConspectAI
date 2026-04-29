from __future__ import annotations

from typing import Dict


MODEL_PRICING: Dict[str, dict] = {
    "gemini-3.1-flash-lite-preview": {"input_per_1m": 0.10, "output_per_1m": 0.40, "context_multiplier": 0.15},
    "gemini-2.0-flash": {"input_per_1m": 0.20, "output_per_1m": 0.80, "context_multiplier": 0.15},
    "gemini-2.5-flash-lite": {"input_per_1m": 0.10, "output_per_1m": 0.40, "context_multiplier": 0.15},
    "gemini-2.5-pro": {"input_per_1m": 1.25, "output_per_1m": 5.00, "context_multiplier": 0.15},
}

DEFAULT_PRICING = {"input_per_1m": 0.30, "output_per_1m": 1.20, "context_multiplier": 0.15}


def calculate_cost_units(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    context_tokens: int,
) -> float:
    """
    Internal cost units (USD-like) for billing analytics.
    """
    p = MODEL_PRICING.get(model_name, DEFAULT_PRICING)
    in_cost = (max(input_tokens, 0) / 1_000_000) * p["input_per_1m"]
    out_cost = (max(output_tokens, 0) / 1_000_000) * p["output_per_1m"]
    ctx_cost = (max(context_tokens, 0) / 1_000_000) * p["input_per_1m"] * p["context_multiplier"]
    return round(in_cost + out_cost + ctx_cost, 8)
