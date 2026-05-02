"""Public catalog/static-data routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.services.catalog_service import CatalogService


def create_catalog_router(catalog_service: CatalogService, rag_service=None) -> APIRouter:
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

    @router.get("/api/public-stats")
    async def public_stats() -> dict:
        if rag_service is None:
            return {"doc_count": 0, "chunk_count": 0, "contributor_count": 0, "subjects": []}
        return await rag_service.get_public_stats()

    return router
