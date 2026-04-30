"""Admin user-management orchestration service."""

from __future__ import annotations

from app.repositories.oltp import AdminUserRepository
from billing_plans import PLAN_KEYS


class UnknownPlanError(ValueError):
    pass


class AdminUserService:
    def __init__(self, admin_user_repository: AdminUserRepository) -> None:
        self._admin_user_repository = admin_user_repository

    async def list_users(self, *, search: str, limit: int, offset: int) -> dict:
        rows = await self._admin_user_repository.list_users(search, limit, offset)
        total = await self._admin_user_repository.count_users(search)
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": rows,
        }

    async def set_plan(self, *, user_id: int, plan_key: str) -> bool:
        if plan_key not in PLAN_KEYS:
            raise UnknownPlanError("Unknown plan")
        return await self._admin_user_repository.set_user_plan(user_id, plan_key)

    async def set_blocked(self, *, user_id: int, is_blocked: bool) -> None:
        await self._admin_user_repository.set_user_field(user_id, "is_blocked", is_blocked)

    async def set_admin(self, *, user_id: int, is_admin: bool) -> None:
        await self._admin_user_repository.set_user_field(user_id, "is_admin", is_admin)

    async def delete_user(self, *, user_id: int) -> None:
        await self._admin_user_repository.delete_user(user_id)
