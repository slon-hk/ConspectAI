"""Public catalog/static-data routes."""

from __future__ import annotations

from fastapi import APIRouter

<<<<<<< HEAD
from app.services.catalog_service import CatalogService


def create_catalog_router(catalog_service: CatalogService) -> APIRouter:
    router = APIRouter(tags=["catalog"])

    @router.get("/api/models")
    async def get_models() -> dict:
        return catalog_service.public_models()

    @router.get("/api/templates")
    async def get_templates() -> dict:
        return catalog_service.templates()

    @router.get("/api/subscription-plans")
    async def get_subscription_plans() -> list[dict]:
        return catalog_service.subscription_plans()

    return router
=======
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
>>>>>>> 65d9c6e (fix bag)
