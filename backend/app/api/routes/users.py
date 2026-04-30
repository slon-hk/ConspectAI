"""User profile and usage API routes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException

from app.services.usage_service import UsageService
from app.services.user_service import UserService


def create_user_router(
    *,
    current_user_id: Callable,
    user_service: UserService,
    usage_service: UsageService,
) -> APIRouter:
    router = APIRouter(tags=["users"])

    @router.get("/api/user")
    async def get_user(uid: int = Depends(current_user_id)):
        user = await user_service.get_safe_user_by_id(uid)
        if not user:
            raise HTTPException(404, "User not found")
        return user

    @router.get("/api/usage")
    async def get_usage(uid: int = Depends(current_user_id)):
        return await usage_service.get_usage_snapshot(uid)

    @router.get("/usage")
    async def get_usage_public(uid: int = Depends(current_user_id)):
        return await usage_service.get_usage_snapshot(uid)

    return router
