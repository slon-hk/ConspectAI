"""Compatibility wrapper for the admin API router."""

from app.api.routes.admin import create_admin_router, create_require_admin_dependency
from app.db.pool import database
from app.repositories.olap import AdminReportRepository, AnalyticsEventRepository
from app.repositories.oltp import AdminUserRepository, UserRepository
from app.services import (
    AdminAccessService,
    AdminAnalyticsService,
    AdminMetricsService,
    AdminUserService,
)
from app.domain.subscriptions import PLAN_KEYS

_admin_access_service = AdminAccessService(UserRepository(database))
require_admin = create_require_admin_dependency(_admin_access_service)
router = create_admin_router(
    require_admin=require_admin,
    admin_analytics_service=AdminAnalyticsService(AnalyticsEventRepository(database)),
    admin_metrics_service=AdminMetricsService(AdminReportRepository(database)),
    admin_user_service=AdminUserService(AdminUserRepository(database), PLAN_KEYS),
)

__all__ = ["require_admin", "router"]
