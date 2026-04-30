"""Server-rendered page routes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.catalog_service import CatalogService
from app.services.funnel_service import FunnelService


def create_pages_router(
    *,
    templates: Jinja2Templates,
    catalog_service: CatalogService,
    funnel_service: FunnelService,
) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def landing(request: Request):
        await funnel_service.record_visit(path="/")
        return templates.TemplateResponse("landing.html", {"request": request})

    @router.get("/app", response_class=HTMLResponse)
    async def app_page(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    @router.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request):
        return templates.TemplateResponse("admin.html", {"request": request})

    @router.get("/privacy", response_class=HTMLResponse)
    async def privacy_page(request: Request):
        return templates.TemplateResponse("privacy.html", {"request": request})

    @router.get("/offer", response_class=HTMLResponse)
    async def offer_page(request: Request):
        return templates.TemplateResponse("offer.html", {"request": request})

    @router.get("/contacts", response_class=HTMLResponse)
    async def contacts_page(request: Request):
        return templates.TemplateResponse("contacts.html", {"request": request})

    @router.get("/pricing", response_class=HTMLResponse)
    async def pricing_page(request: Request):
        plans = []
        for plan in catalog_service.subscription_plans():
            price = int(plan["price_rub"])
            estimated_requests = int(plan.get("estimated_monthly_requests", 0) or 0)
            plans.append({
                **plan,
                "price_label": "₽0" if price == 0 else f"₽{price:,}".replace(",", " "),
                "estimated_requests_label": (
                    "без включённых AI-запросов"
                    if estimated_requests <= 0
                    else f"≈ {estimated_requests:,}".replace(",", " ") + " запросов в месяц"
                ),
                "featured": plan["plan_key"] == "plus",
                "cta_label": (
                    "Начать бесплатно"
                    if plan["plan_key"] == "free"
                    else f"Выбрать {plan['display_name']}"
                ),
            })
        return templates.TemplateResponse("pricing.html", {"request": request, "plans": plans})

    return router
