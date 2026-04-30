"""Chat and message API routes."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Container
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request

from app.services.ai_chat_service import (
    AccountBlockedError,
    AiChatService,
    ChatNotFoundError,
    EmptyMessageError,
    GeminiApiKeyMissingError,
)
from app.services.analytics_tracking_service import AnalyticsTrackingService
from app.services.chat_service import ChatService


def create_chat_router(
    *,
    current_user_id: Callable,
    chat_service: ChatService,
    ai_chat_service: AiChatService,
    analytics_tracking_service: AnalyticsTrackingService,
    regenerate_mindmap: Callable[[str], Awaitable[None]],
    system_prompts: Container[str],
    models: Container[str],
    default_template: str,
    default_model: str,
) -> APIRouter:
    router = APIRouter(tags=["chats"])

    @router.get("/api/chats")
    async def get_chats(uid: int = Depends(current_user_id)):
        rows = await chat_service.list_chats(uid)
        return [_serialize(row) for row in rows]

    @router.post("/api/chats")
    async def create_chat(
        template: str = Form(default_template),
        model: str = Form(default_model),
        uid: int = Depends(current_user_id),
    ):
        row, tpl, mdl = await chat_service.create_chat(
            user_id=uid,
            template=template,
            model=model,
            allowed_templates=system_prompts,
            allowed_models=models,
            default_template=default_template,
            default_model=default_model,
        )
        analytics_tracking_service.track("chat_created", uid, template=tpl, model=mdl)
        return _serialize(row)

    @router.patch("/api/chats/{chat_id}/settings")
    async def update_settings(
        chat_id: str,
        template: str = Form(None),
        model: str = Form(None),
        uid: int = Depends(current_user_id),
    ):
        chat_id = _validate_chat_id(chat_id)
        try:
            chat, updates = await chat_service.update_chat_settings(
                chat_id=chat_id,
                user_id=uid,
                template=template,
                model=model,
                allowed_templates=system_prompts,
                allowed_models=models,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        if not chat:
            raise HTTPException(404, "Chat not found")
        if "template" in updates:
            analytics_tracking_service.track(
                "template_switched",
                uid,
                chat_id=str(chat_id),
                template=updates["template"],
            )
        if "model" in updates:
            analytics_tracking_service.track(
                "model_switched",
                uid,
                chat_id=str(chat_id),
                model=updates["model"],
            )
        return _serialize(chat)

    @router.delete("/api/chats/{chat_id}")
    async def delete_chat(chat_id: str, uid: int = Depends(current_user_id)):
        chat_id = _validate_chat_id(chat_id)
        await chat_service.delete_chat(chat_id=chat_id, user_id=uid)
        return {"ok": True}

    @router.get("/api/chats/{chat_id}/messages")
    async def get_messages(chat_id: str, uid: int = Depends(current_user_id)):
        chat_id = _validate_chat_id(chat_id)
        msgs = await chat_service.list_messages_for_user_chat(chat_id=chat_id, user_id=uid)
        if msgs is None:
            raise HTTPException(404, "Chat not found")
        return [_serialize_msg(message) for message in msgs]

    @router.post("/api/chats/{chat_id}/messages")
    async def send_message(
        chat_id: str,
        request: Request,
        bg: BackgroundTasks,
        content: str = Form(""),
        files_json: str = Form("[]"),
        uid: int = Depends(current_user_id),
    ):
        chat_id = _validate_chat_id(chat_id)
        try:
            result = await ai_chat_service.send_message(
                chat_id=chat_id,
                user_id=uid,
                content=content,
                files_json=files_json,
            )
        except GeminiApiKeyMissingError as exc:
            raise HTTPException(500, str(exc)) from exc
        except ChatNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except AccountBlockedError as exc:
            raise HTTPException(403, str(exc)) from exc
        except EmptyMessageError as exc:
            raise HTTPException(400, str(exc)) from exc

        request.state.billing_usage = result["billing_usage"]
        bg.add_task(regenerate_mindmap, chat_id)
        return _serialize_msg(result["assistant_message"])

    return router


def _validate_chat_id(chat_id: str) -> str:
    try:
        return str(uuid.UUID(str(chat_id)))
    except Exception:
        raise HTTPException(400, "Invalid chat_id")


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif hasattr(value, "__str__") and type(value).__name__ in ("UUID",):
            out[key] = str(value)
        else:
            out[key] = value
    return out


def _serialize_msg(message: dict[str, Any]) -> dict[str, Any]:
    serialized = _serialize(message)
    if "files" in serialized:
        serialized["files"] = [_serialize(file_meta) for file_meta in (serialized["files"] or [])]
    if "cost_usd" in serialized and serialized["cost_usd"] is not None:
        serialized["cost_usd"] = float(serialized["cost_usd"])
    return serialized
