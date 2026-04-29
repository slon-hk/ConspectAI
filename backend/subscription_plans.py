"""Subscription plan configuration."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


DEFAULT_PLAN_KEY = "free"
PRICE_RUB_PER_MILLION_INTERNAL_TOKENS = Decimal("100")
BILLING_TOKEN_BUDGET_SHARE = Decimal("0.2")
INTERNAL_TOKENS_IN_MILLION = Decimal("1000000")
DEFAULT_INTERNAL_TOKENS_PER_REQUEST = 1_000
REFERENCE_MODEL_KEY = "gemini-2.5-flash-lite"
REFERENCE_MODEL_NAME = "Gemini 2.5 Flash Lite"


def _round_int(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _monthly_tokens_for_price(price_rub: int) -> int:
    if price_rub <= 0:
        return 0
    return _round_int(
        Decimal(price_rub)
        * BILLING_TOKEN_BUDGET_SHARE
        / PRICE_RUB_PER_MILLION_INTERNAL_TOKENS
        * INTERNAL_TOKENS_IN_MILLION
    )


def _with_calculated_limits(plan: dict[str, Any]) -> dict[str, Any]:
    monthly_limit = _monthly_tokens_for_price(int(plan["price_rub"]))
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


_PLAN_DEFS: tuple[dict[str, Any], ...] = (
    {
        "plan_key": "free",
        "display_name": "Free",
        "price_rub": 0,
        "sort_order": 0,
    },
    {
        "plan_key": "starter",
        "display_name": "Starter",
        "price_rub": 300,
        "sort_order": 1,
    },
    {
        "plan_key": "plus",
        "display_name": "Plus",
        "price_rub": 500,
        "sort_order": 2,
    },
    {
        "plan_key": "pro",
        "display_name": "Pro",
        "price_rub": 1500,
        "sort_order": 3,
    },
    {
        "plan_key": "max",
        "display_name": "Max",
        "price_rub": 2500,
        "sort_order": 4,
    },
)

SUBSCRIPTION_PLANS: tuple[dict[str, Any], ...] = tuple(
    _with_calculated_limits(plan) for plan in _PLAN_DEFS
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


def plan_by_key(plan_key: str) -> dict[str, Any] | None:
    return next((plan for plan in SUBSCRIPTION_PLANS if plan["plan_key"] == plan_key), None)
