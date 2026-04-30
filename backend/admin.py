"""Compatibility wrapper for the admin API router."""

from app.api.routes.admin import require_admin, router

__all__ = ["require_admin", "router"]
