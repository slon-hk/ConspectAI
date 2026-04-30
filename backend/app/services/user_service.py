"""User profile orchestration service."""

from __future__ import annotations

from app.repositories.oltp import UserRepository
from app.services.usage_service import UsageService


class UserService:
    def __init__(self, user_repository: UserRepository, usage_service: UsageService) -> None:
        self._user_repository = user_repository
        self._usage_service = usage_service

    async def get_by_id(self, user_id: int) -> dict | None:
        return await self._user_repository.get_by_id(user_id)

    async def to_safe_user(self, user: dict) -> dict:
        usage = await self._usage_service.get_usage_snapshot(user["id"])
        return {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "subscription_id": user.get("subscription_id"),
            "plan_key": usage.get("plan_key", "free"),
            "subscription_name": usage.get("subscription_name", "Free"),
            "usage": usage,
            "is_admin": bool(user.get("is_admin", False)),
            "is_blocked": bool(user.get("is_blocked", False)),
            "total_spent_usd": float(user.get("total_spent_usd") or 0),
        }

    async def get_safe_user_by_id(self, user_id: int) -> dict | None:
        user = await self.get_by_id(user_id)
        if not user:
            return None
        return await self.to_safe_user(user)
