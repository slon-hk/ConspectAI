"""Mindmap API routes."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.services.analytics_tracking_service import AnalyticsTrackingService
from app.services.mindmap_generation_service import MindmapGenerationService
from app.services.mindmap_service import MindmapService


def create_mindmap_router(
    *,
    current_user_id: Callable,
    mindmap_service: MindmapService,
    mindmap_generation_service: MindmapGenerationService,
    analytics_tracking_service: AnalyticsTrackingService,
) -> APIRouter:
    router = APIRouter(tags=["mindmaps"])

    @router.get("/api/chats/{chat_id}/mindmap")
    async def fetch_mindmap(chat_id: str, uid: int = Depends(current_user_id)):
        chat_id = _validate_chat_id(chat_id)
        mindmap = await mindmap_service.get_for_user_chat(chat_id=chat_id, user_id=uid)
        if mindmap is None:
            raise HTTPException(404, "Chat not found")
        analytics_tracking_service.track(
            "mindmap_opened",
            uid,
            chat_id=str(chat_id),
            has_content=bool(mindmap["markdown"]),
        )
        return mindmap

    @router.post("/api/chats/{chat_id}/mindmap/regenerate")
    async def force_regenerate_mindmap(
        chat_id: str,
        bg: BackgroundTasks,
        uid: int = Depends(current_user_id),
    ):
        chat_id = _validate_chat_id(chat_id)
        if not await mindmap_service.user_can_access_chat(chat_id=chat_id, user_id=uid):
            raise HTTPException(404, "Chat not found")
        bg.add_task(mindmap_generation_service.regenerate_background, chat_id)
        analytics_tracking_service.track("mindmap_regenerated", uid, chat_id=str(chat_id))
        return {"ok": True, "queued": True}

    return router


def _validate_chat_id(chat_id: str) -> str:
    try:
        return str(uuid.UUID(str(chat_id)))
    except Exception:
        raise HTTPException(400, "Invalid chat_id")
