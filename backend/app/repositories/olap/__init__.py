"""OLAP/reporting repositories."""

from .analytics_events import AnalyticsEventRepository
from .admin_reports import AdminReportRepository

__all__ = ["AdminReportRepository", "AnalyticsEventRepository"]
