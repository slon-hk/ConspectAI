import os
from contextlib import asynccontextmanager

import google.generativeai as genai
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

import auth
import admin
import rag_routes
from app.api.dependencies import create_current_user_id_dependency
from app.api.routes.admin_metrics import create_admin_metrics_router
from app.api.routes.analytics import create_analytics_router
from app.api.routes.auth import create_auth_router
from app.api.routes.catalog import router as catalog_router
from app.api.routes.chats import create_chat_router
from app.api.routes.files import create_file_router
from app.api.routes.mindmaps import create_mindmap_router
from app.api.routes.pages import create_pages_router
from app.api.routes.users import create_user_router
from app.core.exceptions import register_exception_handlers
from app.db.pool import database
from app.middleware import (
    register_http_metrics_middleware,
    register_subscription_quota_middleware,
)
from app.workers import start_analytics_cleanup_task
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
    QuotaService,
    RequestMetricsService,
    UsageService,
    UserService,
)
from app.services.auth_service import AuthService
from app.services.ai_chat_service import AiChatService
from app.services.billing_service import BillingService
from promts import SYSTEM_PROMPTS, MODELS, MINDMAP_PROMPT
from billing_plans import DEFAULT_INTERNAL_TOKENS_PER_REQUEST, DEFAULT_PLAN_KEY

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.create_pool()
    # Periodic cleanup of old analytics events (older than 90 days)
    cleanup_task = start_analytics_cleanup_task()
    yield
    cleanup_task.cancel()
    await database.close_pool()


app = FastAPI(title="ConspectAI", lifespan=lifespan)
jinja = Jinja2Templates(directory="templates")
app.include_router(admin.router)
app.include_router(catalog_router)
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
file_service = FileService(file_repository)
funnel_service = FunnelService(FunnelMetricRepository(database))
request_metrics_service = RequestMetricsService(
    RequestMetricRepository(database),
    RagMetricRepository(database),
)
admin_metrics_service = AdminMetricsService(AdminReportRepository(database))
analytics_tracking_service = AnalyticsTrackingService(AnalyticsEventRepository(database))
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
    system_prompts=SYSTEM_PROMPTS,
    models=MODELS,
    default_template="deep",
    default_model="gemini-3.1-flash-lite-preview",
    gemini_api_key=GEMINI_API_KEY,
)
register_http_metrics_middleware(app, analytics_tracking_service)
register_subscription_quota_middleware(
    app,
    decode_token=auth.decode_token,
    quota_service=quota_service,
    usage_service=usage_service,
    request_metrics_service=request_metrics_service,
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
register_exception_handlers(app, jinja)


current_user_id = create_current_user_id_dependency(
    token_dependency=auth.oauth2,
    decode_token=auth.decode_token,
    user_service=user_service,
)
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
    )
)
app.include_router(
    create_analytics_router(
        token_dependency=auth.oauth2,
        decode_token=auth.decode_token,
        analytics_tracking_service=analytics_tracking_service,
    )
)
app.include_router(create_pages_router(templates=jinja, funnel_service=funnel_service))
app.include_router(
    create_admin_metrics_router(
        require_admin=admin.require_admin,
        admin_metrics_service=admin_metrics_service,
    )
)
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
app.include_router(
    create_mindmap_router(
        current_user_id=current_user_id,
        mindmap_service=mindmap_service,
        mindmap_generation_service=mindmap_generation_service,
        analytics_tracking_service=analytics_tracking_service,
    )
)
