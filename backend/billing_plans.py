"""Compatibility wrapper for subscription plan configuration."""

from app.domain.subscriptions.plans import (
    DEFAULT_INTERNAL_TOKENS_PER_REQUEST,
    DEFAULT_PLAN_KEY,
    PLAN_KEYS,
    SUBSCRIPTION_PLANS,
    public_plans,
)

__all__ = [
    "DEFAULT_INTERNAL_TOKENS_PER_REQUEST",
    "DEFAULT_PLAN_KEY",
    "PLAN_KEYS",
    "SUBSCRIPTION_PLANS",
    "public_plans",
]
