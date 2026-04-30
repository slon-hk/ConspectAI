import os

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
from app.core.container import create_container
from app.core.exceptions import register_exception_handlers
from app.core.lifecycle import create_lifespan
from app.db.pool import database
from app.middleware import (
    register_http_metrics_middleware,
    register_subscription_quota_middleware,
)
from app.workers import start_analytics_cleanup_task

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


lifespan = create_lifespan(
    database=database,
    start_analytics_cleanup_task=start_analytics_cleanup_task,
)
app = FastAPI(title="ConspectAI", lifespan=lifespan)
jinja = Jinja2Templates(directory="templates")
app.include_router(admin.router)
app.include_router(catalog_router)
app.include_router(rag_routes.router)
container = create_container(database=database, gemini_api_key=GEMINI_API_KEY)
register_http_metrics_middleware(app, container.analytics_tracking_service)
register_subscription_quota_middleware(
    app,
    decode_token=auth.decode_token,
    quota_service=container.quota_service,
    usage_service=container.usage_service,
    request_metrics_service=container.request_metrics_service,
)
app.include_router(
    create_auth_router(
        auth_service=container.auth_service,
        analytics_tracking_service=container.analytics_tracking_service,
        funnel_service=container.funnel_service,
    )
)
# Static assets (error-page backgrounds, etc.) — served directly without auth
app.mount("/static", StaticFiles(directory="static"), name="static")
register_exception_handlers(app, jinja)


current_user_id = create_current_user_id_dependency(
    token_dependency=auth.oauth2,
    decode_token=auth.decode_token,
    user_service=container.user_service,
)
app.include_router(
    create_user_router(
        current_user_id=current_user_id,
        user_service=container.user_service,
        usage_service=container.usage_service,
    )
)
app.include_router(
    create_file_router(
        current_user_id=current_user_id,
        file_service=container.file_service,
        analytics_tracking_service=container.analytics_tracking_service,
    )
)
app.include_router(
    create_analytics_router(
        token_dependency=auth.oauth2,
        decode_token=auth.decode_token,
        analytics_tracking_service=container.analytics_tracking_service,
    )
)
app.include_router(create_pages_router(templates=jinja, funnel_service=container.funnel_service))
app.include_router(
    create_admin_metrics_router(
        require_admin=admin.require_admin,
        admin_metrics_service=container.admin_metrics_service,
    )
)
app.include_router(
    create_chat_router(
        current_user_id=current_user_id,
        chat_service=container.chat_service,
        ai_chat_service=container.ai_chat_service,
        analytics_tracking_service=container.analytics_tracking_service,
        regenerate_mindmap=container.mindmap_generation_service.regenerate_background,
        system_prompts=container.system_prompts,
        models=container.models,
        default_template=container.default_template,
        default_model=container.default_model,
    )
)
app.include_router(
    create_mindmap_router(
        current_user_id=current_user_id,
        mindmap_service=container.mindmap_service,
        mindmap_generation_service=container.mindmap_generation_service,
        analytics_tracking_service=container.analytics_tracking_service,
    )
)
