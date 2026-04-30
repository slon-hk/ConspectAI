"""Public catalog/static-data routes."""

from __future__ import annotations

from fastapi import APIRouter

from billing_plans import public_plans
from promts import MODELS, TEMPLATE_META

router = APIRouter(tags=["catalog"])


@router.get("/api/models")
async def get_models() -> dict:
    return {
        key: {
            "name": info["name"],
            "desc": info["desc"],
            "speed": info["speed"],
            "recommended": bool(info.get("recommended", False)),
        }
        for key, info in MODELS.items()
    }


@router.get("/api/templates")
async def get_templates() -> dict:
    return TEMPLATE_META


@router.get("/api/subscription-plans")
async def get_subscription_plans() -> list[dict]:
    return public_plans()
