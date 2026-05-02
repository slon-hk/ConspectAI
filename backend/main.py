from app.main import app, create_app

__all__ = ["app", "create_app"]
import os
from contextlib import asynccontextmanager
import re
import uuid

import google.generativeai as genai
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

import auth
import admin
import rag_routes
from app.api.routes.analytics import create_analytics_router
from app.api.routes.auth import create_auth_router
from app.api.routes.catalog import create_catalog_router
from app.services.catalog_service import CatalogService
from app.api.routes.chats import create_chat_router
from app.api.routes.files import create_file_router
from app.api.routes.users import create_user_router
from app.db.pool import database
from app.workers import start_analytics_cleanup_task
from app.services import QuotaService
from billing_plans import DEFAULT_INTERNAL_TOKENS_PER_REQUEST

from db import init_schema
from app.repositories.olap import (
    AdminReportRepository,
    AnalyticsEventRepository,
    FunnelMetricRepository,
    RagMetricRepository,
    RequestMetricRepository,
)
from app.repositories.oltp import (
    ChatRepository,
    FileRepository,
    MessageRepository,
    MindmapRepository,
    UsageRepository,
    UserRepository,
)
from app.services import (
    AdminMetricsService,
    AnalyticsTrackingService,
    ChatService,
    FileService,
    FunnelService,
    MindmapGenerationService,
    MindmapService,
    RequestMetricsService,
    UsageService,
    UserService,
)
from app.services.auth_service import AuthService
from app.services.ai_chat_service import AiChatService
from app.services.billing_service import BillingService
from app.services.analytics_maintenance_service import AnalyticsMaintenanceService
from app.infrastructure.storage import FileStorage
from app.infrastructure.ai import RagEngine
from app.events import InProcessEventBus
from promts import SYSTEM_PROMPTS, MODELS, MINDMAP_PROMPT, TEMPLATE_META
from billing_plans import DEFAULT_INTERNAL_TOKENS_PER_REQUEST, DEFAULT_PLAN_KEY, public_plans

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.create_pool()
    await init_schema()
    # Periodic cleanup of old analytics events (older than 90 days)
    cleanup_task = start_analytics_cleanup_task(
        AnalyticsMaintenanceService(AnalyticsEventRepository(database))
    )
    yield
    cleanup_task.cancel()
    await database.close_pool()


app = FastAPI(title="ConspectAI", lifespan=lifespan)
jinja = Jinja2Templates(directory="templates")
app.include_router(admin.router)
app.include_router(create_catalog_router(
    CatalogService(models=MODELS, template_meta=TEMPLATE_META, public_plans=public_plans),
    rag_service=rag_routes.rag_service,
))
app.include_router(rag_routes.router)
chat_repository = ChatRepository(database)
message_repository = MessageRepository(database)
usage_repository = UsageRepository(database)
file_repository = FileRepository(database)
chat_service = ChatService(chat_repository, message_repository)
mindmap_service = MindmapService(chat_repository, message_repository, MindmapRepository(database))
usage_service = UsageService(usage_repository, DEFAULT_INTERNAL_TOKENS_PER_REQUEST)
quota_service = QuotaService(usage_repository, DEFAULT_INTERNAL_TOKENS_PER_REQUEST)
user_repository = UserRepository(database)
user_service = UserService(user_repository, usage_service)
auth_service = AuthService(user_repository, user_service, DEFAULT_PLAN_KEY)
file_service = FileService(file_repository, FileStorage())
funnel_service = FunnelService(FunnelMetricRepository(database), InProcessEventBus())
request_metrics_service = RequestMetricsService(
    RequestMetricRepository(database),
    RagMetricRepository(database),
    InProcessEventBus(),
)
admin_metrics_service = AdminMetricsService(AdminReportRepository(database))
analytics_tracking_service = AnalyticsTrackingService(AnalyticsEventRepository(database), InProcessEventBus())
mindmap_generation_service = MindmapGenerationService(
    mindmap_service=mindmap_service,
    analytics_tracking_service=analytics_tracking_service,
    gemini_api_key=GEMINI_API_KEY,
    model_key="gemini-2.5-flash-lite",
    system_prompt=MINDMAP_PROMPT,
)
ai_chat_service = AiChatService(
    chat_service=chat_service,
    user_service=user_service,
    billing_service=BillingService(),
    analytics_tracking_service=analytics_tracking_service,
    file_repository=file_repository,
    file_storage=FileStorage(),
    rag_engine=RagEngine(),
    system_prompts=SYSTEM_PROMPTS,
    models=MODELS,
    default_template="deep",
    default_model="gemini-3.1-flash-lite-preview",
    gemini_api_key=GEMINI_API_KEY,
)
app.include_router(
    create_auth_router(
        auth_service=auth_service,
        analytics_tracking_service=analytics_tracking_service,
        funnel_service=funnel_service,
    )
)
# Static assets (error-page backgrounds, etc.) — served directly without auth
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── 404 handler ───────────────────────────────────────────────────────────────
@app.exception_handler(404)
async def not_found(request: Request, exc):
    """Pretty 404 page for browser routes; JSON for API endpoints."""
    from fastapi.responses import JSONResponse
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return jinja.TemplateResponse("404.html", {"request": request}, status_code=404)


@app.exception_handler(500)
async def internal_error(request: Request, exc):
    """Pretty 503 page for browser; JSON for API endpoints."""
    from fastapi.responses import JSONResponse
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
    return jinja.TemplateResponse("503.html", {"request": request}, status_code=503)


# ── HTTP metrics middleware ───────────────────────────────────────────────────
@app.middleware("http")
async def http_metrics_middleware(request: Request, call_next):
    import time as _t
    start = _t.perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    except Exception:
        status = 500
        raise
    finally:
        elapsed_ms = (_t.perf_counter() - start) * 1000
        analytics_tracking_service.record_http(request.url.path, status, elapsed_ms)


def _needs_quota_check(path: str, method: str) -> bool:
    return method.upper() == "POST" and bool(re.match(r"^/api/chats/[^/]+/messages$", path))


@app.middleware("http")
async def subscription_quota_middleware(request: Request, call_next):
    request.state.request_log_id = None
    request.state.current_uid = None
    request.state._metrics_started = __import__("time").perf_counter()
    if _needs_quota_check(request.url.path, request.method):
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else None
        uid = auth.decode_token(token) if token else None
        if not uid:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        request.state.current_uid = uid
        quota = await quota_service.check_and_consume_limit(uid, request.url.path)
        if not quota.get("allowed"):
            return Response(
                content='{"detail":"Доступный объём тарифа закончился","code":"quota_exceeded"}',
                status_code=429,
                media_type="application/json",
            )
        request.state.request_log_id = quota["request_log_id"]
        request.state.usage_remaining = quota["remaining"]
    try:
        response = await call_next(request)
    except Exception as e:
        if request.state.request_log_id:
            await usage_service.fail_and_refund_request(request.state.request_log_id, str(e))
            usage = getattr(request.state, "billing_usage", {})
            latency_ms = int((__import__("time").perf_counter() - request.state._metrics_started) * 1000)
            await request_metrics_service.log_request_from_usage(
                request_log_id=request.state.request_log_id,
                user_id=request.state.current_uid,
                usage=usage,
                status="error",
                error_message=str(e),
                latency_ms=latency_ms,
                session_count_inc=0,
            )
        raise

    if request.state.request_log_id:
        usage = getattr(request.state, "billing_usage", None)
        latency_ms = int((__import__("time").perf_counter() - request.state._metrics_started) * 1000)
        if response.status_code >= 400:
            await usage_service.fail_and_refund_request(request.state.request_log_id, f"http_{response.status_code}")
        if usage:
            await request_metrics_service.log_request_from_usage(
                request_log_id=request.state.request_log_id,
                user_id=request.state.current_uid,
                usage=usage,
                status="success" if response.status_code < 400 else "error",
                error_message="" if response.status_code < 400 else f"http_{response.status_code}",
                latency_ms=latency_ms,
            )
            if request.state.current_uid:
                await request_metrics_service.log_rag_from_usage(
                    user_id=request.state.current_uid,
                    usage=usage,
                    latency_ms=latency_ms,
                )
    return response


# ── Auth dependency ────────────────────────────────────────────────────────────
async def current_user_id(token: str = Depends(auth.oauth2)) -> int:
    """Resolve the JWT to a user id, then verify the user still exists.

    A token is "valid" if it's signed correctly and not expired, but the user
    it points to may have been deleted (or the DB may have been recreated).
    In either case we return 401 so the frontend forces a fresh login instead
    of silently sending requests for a ghost user.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    uid = auth.decode_token(token)
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = await user_service.get_by_id(uid)
    if not user:
        # Token's signature is valid but the user no longer exists.
        raise HTTPException(status_code=401, detail="User no longer exists — please log in again")
    if user.get("is_blocked"):
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован администратором")
    return uid


app.include_router(
    create_user_router(
        current_user_id=current_user_id,
        user_service=user_service,
        usage_service=usage_service,
    )
)
app.include_router(
    create_file_router(
        current_user_id=current_user_id,
        file_service=file_service,
        analytics_tracking_service=analytics_tracking_service,
        usage_service=usage_service,
    )
)
app.include_router(
    create_analytics_router(
        token_dependency=auth.oauth2,
        decode_token=auth.decode_token,
        analytics_tracking_service=analytics_tracking_service,
    )
)


# ── Pages ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    await funnel_service.record_visit(path="/")
    return jinja.TemplateResponse("landing.html", {"request": request})


@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request):
    return jinja.TemplateResponse("index.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return jinja.TemplateResponse("admin.html", {"request": request})


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return jinja.TemplateResponse("privacy.html", {"request": request})


@app.get("/offer", response_class=HTMLResponse)
async def offer_page(request: Request):
    return jinja.TemplateResponse("offer.html", {"request": request})


@app.get("/contacts", response_class=HTMLResponse)
async def contacts_page(request: Request):
    return jinja.TemplateResponse("contacts.html", {"request": request})


_MODEL_DISPLAY: dict[str, dict[str, str]] = {
    "gemini-2.5-flash-lite": {"icon": "⚡⚡", "short": "Flash Lite 2.5"},
    "gemini-2.0-flash":      {"icon": "⚡",  "short": "Flash 2.0"},
    "gemini-1.5-pro":        {"icon": "🧠",  "short": "Gemini 1.5 Pro"},
}
_RAG_PLANS = frozenset({"plus", "pro", "max"})


def _plan_features(plan: dict) -> list[str]:
    max_mb = plan.get("max_upload_mb", 2)
    features = [
        f"Загрузка файлов до {max_mb} МБ",
        "Все шаблоны и карта тем",
        "Экспорт в Markdown и PDF",
    ]
    if plan["plan_key"] in _RAG_PLANS:
        features.append("Доступ к общей базе знаний (RAG)")
    return features


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    plans = []
    for plan in public_plans():
        price = int(plan["price_rub"])
        estimated_requests = int(plan.get("estimated_monthly_requests", 0) or 0)
        models = plan.get("available_models", ["gemini-2.5-flash-lite"])
        model_chips = [
            {"icon": _MODEL_DISPLAY.get(m, {}).get("icon", ""), "label": _MODEL_DISPLAY.get(m, {}).get("short", m)}
            for m in models
        ]
        plans.append({
            **plan,
            "price_label": "₽0" if price == 0 else f"₽{price:,}".replace(",", " "),
            "estimated_requests_label": (
                "без включённых AI-запросов"
                if estimated_requests <= 0
                else f"≈ {estimated_requests:,}".replace(",", " ") + " запросов в месяц"
            ),
            "featured": plan["plan_key"] == "plus",
            "cta_label": "Начать бесплатно" if plan["plan_key"] == "free" else f"Выбрать {plan['display_name']}",
            "model_chips": model_chips,
            "features": _plan_features(plan),
        })
    return jinja.TemplateResponse("pricing.html", {"request": request, "plans": plans})


def _validate_chat_id(chat_id: str) -> str:
    try:
        return str(uuid.UUID(str(chat_id)))
    except Exception:
        raise HTTPException(400, "Invalid chat_id")


@app.get("/admin/metrics")
async def get_admin_metrics_public(_=Depends(admin.require_admin)):
    return await admin_metrics_service.admin_metrics()


@app.get("/admin/metrics/overview")
async def get_admin_metrics_overview_public(_=Depends(admin.require_admin)):
    return await admin_metrics_service.overview()


@app.get("/admin/metrics/rag")
async def get_admin_metrics_rag_public(_=Depends(admin.require_admin)):
    return await admin_metrics_service.rag()


@app.get("/admin/metrics/usage")
async def get_admin_metrics_usage_public(_=Depends(admin.require_admin)):
    return await admin_metrics_service.usage()


@app.get("/admin/metrics/marketing")
async def get_admin_metrics_marketing_public(_=Depends(admin.require_admin)):
    return await admin_metrics_service.marketing()


app.include_router(
    create_chat_router(
        current_user_id=current_user_id,
        chat_service=chat_service,
        ai_chat_service=ai_chat_service,
        analytics_tracking_service=analytics_tracking_service,
        regenerate_mindmap=mindmap_generation_service.regenerate_background,
        system_prompts=SYSTEM_PROMPTS,
        models=MODELS,
        default_template="deep",
        default_model="gemini-3.1-flash-lite-preview",
    )
)


@app.get("/api/chats/{chat_id}/mindmap")
async def fetch_mindmap(chat_id: str, uid: int = Depends(current_user_id)):
    chat_id = _validate_chat_id(chat_id)
    mindmap = await mindmap_service.get_for_user_chat(chat_id=chat_id, user_id=uid)
    if mindmap is None:
        raise HTTPException(404, "Chat not found")
    analytics_tracking_service.track("mindmap_opened", uid, chat_id=str(chat_id), has_content=bool(mindmap["markdown"]))
    return mindmap


@app.post("/api/chats/{chat_id}/mindmap/regenerate")
async def force_regenerate_mindmap(
    chat_id: str,
    bg:      BackgroundTasks,
    uid:     int = Depends(current_user_id),
):
    chat_id = _validate_chat_id(chat_id)
    if not await mindmap_service.user_can_access_chat(chat_id=chat_id, user_id=uid):
        raise HTTPException(404, "Chat not found")
    bg.add_task(mindmap_generation_service.regenerate_background, chat_id)
    analytics_tracking_service.track("mindmap_regenerated", uid, chat_id=str(chat_id))
    return {"ok": True, "queued": True}
