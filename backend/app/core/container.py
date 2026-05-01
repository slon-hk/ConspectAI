"""Application dependency container."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from app.db.pool import Database
from app.events import event_bus
from app.infrastructure.ai import RagEngine
from app.infrastructure.storage import FileStorage
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
    RagRouteRepository,
    AdminUserRepository,
    UsageRepository,
    UserRepository,
)
from app.services import (
    AdminAccessService,
    AdminAnalyticsService,
    AdminMetricsService,
    AdminUserService,
    AnalyticsTrackingService,
    CatalogService,
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
from app.services.rag_service import RagService
from app.services.billing_service import BillingService
from billing_plans import DEFAULT_INTERNAL_TOKENS_PER_REQUEST, DEFAULT_PLAN_KEY, public_plans
from promts import MINDMAP_PROMPT, MODELS, SYSTEM_PROMPTS, TEMPLATE_META

DEFAULT_TEMPLATE = "deep"
DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"
MINDMAP_MODEL = "gemini-2.5-flash-lite"


@dataclass(frozen=True)
class AppContainer:
    admin_access_service: AdminAccessService
    admin_analytics_service: AdminAnalyticsService
    admin_metrics_service: AdminMetricsService
    admin_user_service: AdminUserService
    ai_chat_service: AiChatService
    analytics_tracking_service: AnalyticsTrackingService
    auth_service: AuthService
    catalog_service: CatalogService
    chat_service: ChatService
    file_service: FileService
    funnel_service: FunnelService
    mindmap_generation_service: MindmapGenerationService
    mindmap_service: MindmapService
    quota_service: QuotaService
    rag_service: RagService
    request_metrics_service: RequestMetricsService
    usage_service: UsageService
    user_service: UserService
    system_prompts: Mapping[str, str]
    models: Mapping[str, Mapping[str, Any]]
    default_template: str
    default_model: str


def create_container(*, database: Database, gemini_api_key: str) -> AppContainer:
    chat_repository = ChatRepository(database)
    message_repository = MessageRepository(database)
    usage_repository = UsageRepository(database)
    file_repository = FileRepository(database)
    user_repository = UserRepository(database)

    chat_service = ChatService(chat_repository, message_repository)
    mindmap_service = MindmapService(chat_repository, message_repository, MindmapRepository(database))
    usage_service = UsageService(usage_repository, DEFAULT_INTERNAL_TOKENS_PER_REQUEST)
    quota_service = QuotaService(usage_repository, DEFAULT_INTERNAL_TOKENS_PER_REQUEST)
    user_service = UserService(user_repository, usage_service)
    auth_service = AuthService(user_repository, user_service, DEFAULT_PLAN_KEY)
    catalog_service = CatalogService(
        models=MODELS,
        template_meta=TEMPLATE_META,
        public_plans=public_plans,
    )
    file_storage = FileStorage()
    file_service = FileService(file_repository, file_storage)
    rag_engine = RagEngine()
    rag_service = RagService(RagRouteRepository(database), rag_engine)
    funnel_service = FunnelService(FunnelMetricRepository(database), bus=event_bus)
    request_metrics_service = RequestMetricsService(
        RequestMetricRepository(database),
        RagMetricRepository(database),
        bus=event_bus,
    )
    admin_access_service = AdminAccessService(user_repository)
    admin_analytics_service = AdminAnalyticsService(AnalyticsEventRepository(database))
    admin_metrics_service = AdminMetricsService(AdminReportRepository(database))
    admin_user_service = AdminUserService(AdminUserRepository(database))
    analytics_tracking_service = AnalyticsTrackingService(
        AnalyticsEventRepository(database),
        bus=event_bus,
    )
    mindmap_generation_service = MindmapGenerationService(
        mindmap_service=mindmap_service,
        analytics_tracking_service=analytics_tracking_service,
        gemini_api_key=gemini_api_key,
        model_key=MINDMAP_MODEL,
        system_prompt=MINDMAP_PROMPT,
    )
    ai_chat_service = AiChatService(
        chat_service=chat_service,
        user_service=user_service,
        billing_service=BillingService(),
        analytics_tracking_service=analytics_tracking_service,
        file_repository=file_repository,
        file_storage=file_storage,
        rag_engine=rag_engine,
        system_prompts=SYSTEM_PROMPTS,
        models=MODELS,
        default_template=DEFAULT_TEMPLATE,
        default_model=DEFAULT_MODEL,
        gemini_api_key=gemini_api_key,
    )
    return AppContainer(
        admin_access_service=admin_access_service,
        admin_analytics_service=admin_analytics_service,
        admin_metrics_service=admin_metrics_service,
        admin_user_service=admin_user_service,
        ai_chat_service=ai_chat_service,
        analytics_tracking_service=analytics_tracking_service,
        auth_service=auth_service,
        catalog_service=catalog_service,
        chat_service=chat_service,
        file_service=file_service,
        funnel_service=funnel_service,
        mindmap_generation_service=mindmap_generation_service,
        mindmap_service=mindmap_service,
        quota_service=quota_service,
        rag_service=rag_service,
        request_metrics_service=request_metrics_service,
        usage_service=usage_service,
        user_service=user_service,
        system_prompts=SYSTEM_PROMPTS,
        models=MODELS,
        default_template=DEFAULT_TEMPLATE,
        default_model=DEFAULT_MODEL,
    )
