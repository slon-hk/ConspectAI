"""Subscription plan loader and derived billing budgets."""

from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


DEFAULT_PLAN_KEY = "free"
CONFIG_PATH = Path(__file__).with_name("subscription_plans.json")
PERCENT_BASE = Decimal("100")
PRICE_RUB_PER_MILLION_INTERNAL_TOKENS = Decimal("100")
INTERNAL_TOKENS_IN_MILLION = Decimal("1000000")
DEFAULT_INTERNAL_TOKENS_PER_REQUEST = 1_000
REFERENCE_MODEL_KEY = "gemini-2.5-flash-lite"
REFERENCE_MODEL_NAME = "Gemini 2.5 Flash Lite"


def _round_int(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _billing_budget_share(config: dict[str, Any]) -> Decimal:
    margin_percent = Decimal(str(config["margin_percent"]))
    return (PERCENT_BASE - margin_percent) / PERCENT_BASE


def _monthly_tokens_for_price(price_rub: int, budget_share: Decimal) -> int:
    if price_rub <= 0:
        return 0
    return _round_int(
        Decimal(price_rub)
        * budget_share
        / PRICE_RUB_PER_MILLION_INTERNAL_TOKENS
        * INTERNAL_TOKENS_IN_MILLION
    )


def _with_calculated_limits(plan: dict[str, Any], budget_share: Decimal) -> dict[str, Any]:
    monthly_limit = _monthly_tokens_for_price(int(plan["price_rub"]), budget_share)
    return {
        **plan,
        "daily_limit": _round_int(Decimal(monthly_limit) / Decimal("31")),
        "weekly_limit": _round_int(Decimal(monthly_limit) / Decimal("4")),
        "monthly_limit": monthly_limit,
        "estimated_monthly_requests": _round_int(
            Decimal(monthly_limit) / Decimal(DEFAULT_INTERNAL_TOKENS_PER_REQUEST)
        ),
        "reference_model_key": REFERENCE_MODEL_KEY,
        "reference_model_name": REFERENCE_MODEL_NAME,
    }


_CONFIG = _load_config()
_BUDGET_SHARE = _billing_budget_share(_CONFIG)

SUBSCRIPTION_PLANS: tuple[dict[str, Any], ...] = tuple(
    _with_calculated_limits(plan, _BUDGET_SHARE) for plan in _CONFIG["plans"]
)
PLAN_KEYS = frozenset(plan["plan_key"] for plan in SUBSCRIPTION_PLANS)


def public_plans() -> list[dict[str, Any]]:
    return [
        {
            "plan_key": plan["plan_key"],
            "display_name": plan["display_name"],
            "price_rub": plan["price_rub"],
            "sort_order": plan["sort_order"],
            "estimated_monthly_requests": plan["estimated_monthly_requests"],
            "reference_model_key": plan["reference_model_key"],
            "reference_model_name": plan["reference_model_name"],
        }
        for plan in SUBSCRIPTION_PLANS
    ]
