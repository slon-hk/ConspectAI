"""Admin access checks."""

from __future__ import annotations

from app.repositories.oltp import UserRepository


class AdminAccessService:
    def __init__(self, user_repository: UserRepository) -> None:
        self._user_repository = user_repository

    async def get_admin_user(self, user_id: int) -> dict | None:
        user = await self._user_repository.get_by_id(user_id)
        if not user or not user.get("is_admin"):
            return None
        return user
