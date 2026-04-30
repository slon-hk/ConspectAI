"""Application router registration."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from app.api.routes.admin import create_admin_router, create_require_admin_dependency
from app.api.routes.admin_metrics import create_admin_metrics_router
from app.api.routes.analytics import create_analytics_router
from app.api.routes.auth import create_auth_router
from app.api.routes.catalog import router as catalog_router
from app.api.routes.chats import create_chat_router
from app.api.routes.files import create_file_router
from app.api.routes.mindmaps import create_mindmap_router
from app.api.routes.pages import create_pages_router
from app.api.routes.rag import create_rag_router
from app.api.routes.users import create_user_router
from app.core.container import AppContainer


def register_routes(
    app: FastAPI,
    *,
    container: AppContainer,
    current_user_id: Callable,
    templates: Jinja2Templates,
    token_dependency: Callable,
    decode_token: Callable[[str], int | None],
) -> None:
    require_admin = create_require_admin_dependency(container.admin_access_service)
    app.include_router(
        create_admin_router(
            require_admin=require_admin,
            admin_analytics_service=container.admin_analytics_service,
            admin_metrics_service=container.admin_metrics_service,
            admin_user_service=container.admin_user_service,
        )
    )
    app.include_router(catalog_router)
    app.include_router(
        create_rag_router(
            current_user_id=current_user_id,
            rag_service=container.rag_service,
        )
    )
    app.include_router(
        create_auth_router(
            auth_service=container.auth_service,
            analytics_tracking_service=container.analytics_tracking_service,
            funnel_service=container.funnel_service,
        )
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
            token_dependency=token_dependency,
            decode_token=decode_token,
            analytics_tracking_service=container.analytics_tracking_service,
        )
    )
    app.include_router(create_pages_router(templates=templates, funnel_service=container.funnel_service))
    app.include_router(
        create_admin_metrics_router(
            require_admin=require_admin,
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
