"""Chat and message orchestration service."""

from __future__ import annotations

from collections.abc import Container
from datetime import datetime

from app.repositories.oltp import ChatRepository, MessageRepository


class ChatService:
    def __init__(
        self,
        chat_repository: ChatRepository,
        message_repository: MessageRepository,
    ) -> None:
        self._chat_repository = chat_repository
        self._message_repository = message_repository

    async def list_chats(self, user_id: int) -> list[dict]:
        return await self._chat_repository.list_for_user(user_id)

    async def create_chat(
        self,
        *,
        user_id: int,
        template: str,
        model: str,
        allowed_templates: Container[str],
        allowed_models: Container[str],
        default_template: str,
        default_model: str,
    ) -> tuple[dict, str, str]:
        normalized_template = template if template in allowed_templates else default_template
        normalized_model = model if model in allowed_models else default_model
        chat = await self._chat_repository.create(
            user_id,
            normalized_template,
            normalized_model,
        )
        return chat, normalized_template, normalized_model

    async def update_chat_settings(
        self,
        *,
        chat_id: str,
        user_id: int,
        template: str | None,
        model: str | None,
        allowed_templates: Container[str],
        allowed_models: Container[str],
    ) -> tuple[dict | None, dict]:
        updates = {}
        if template and template in allowed_templates:
            updates["template"] = template
        if model and model in allowed_models:
            updates["model"] = model
        if not updates:
            raise ValueError("Nothing to update")

        await self._chat_repository.update_settings(chat_id, user_id, **updates)
        chat = await self._chat_repository.get(chat_id, user_id)
        return chat, updates

    async def delete_chat(self, *, chat_id: str, user_id: int) -> None:
        await self._chat_repository.delete(chat_id, user_id)

    async def get_chat(self, *, chat_id: str, user_id: int) -> dict | None:
        return await self._chat_repository.get(chat_id, user_id)

    async def list_messages_for_user_chat(
        self,
        *,
        chat_id: str,
        user_id: int,
    ) -> list[dict] | None:
        chat = await self._chat_repository.get(chat_id, user_id)
        if not chat:
            return None
        return await self._message_repository.list_by_chat(chat_id)

    async def update_title_after_message(
        self,
        *,
        chat_id: str,
        user_id: int,
        current_title: str,
        content: str,
    ) -> None:
        updates = {"updated_at": datetime.utcnow()}
        if current_title in ("Новый чат", "New Chat") and content:
            updates["title"] = content[:55] + ("…" if len(content) > 55 else "")
        await self._chat_repository.update_settings(chat_id, user_id, **updates)
