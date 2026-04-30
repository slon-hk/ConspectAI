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
from app.api.router import register_routes
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
container = create_container(database=database, gemini_api_key=GEMINI_API_KEY)
register_http_metrics_middleware(app, container.analytics_tracking_service)
register_subscription_quota_middleware(
    app,
    decode_token=auth.decode_token,
    quota_service=container.quota_service,
    usage_service=container.usage_service,
    request_metrics_service=container.request_metrics_service,
)
# Static assets (error-page backgrounds, etc.) — served directly without auth
app.mount("/static", StaticFiles(directory="static"), name="static")
register_exception_handlers(app, jinja)


current_user_id = create_current_user_id_dependency(
    token_dependency=auth.oauth2,
    decode_token=auth.decode_token,
    user_service=container.user_service,
)
register_routes(
    app,
    container=container,
    current_user_id=current_user_id,
    templates=jinja,
    token_dependency=auth.oauth2,
    decode_token=auth.decode_token,
    admin_router=admin.router,
    require_admin=admin.require_admin,
    rag_router=rag_routes.router,
)
