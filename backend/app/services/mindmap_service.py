"""Mindmap orchestration service."""

from __future__ import annotations

import asyncio

import google.generativeai as genai

from app.repositories.oltp import ChatRepository, MessageRepository, MindmapRepository


class MindmapService:
    def __init__(
        self,
        chat_repository: ChatRepository,
        message_repository: MessageRepository,
        mindmap_repository: MindmapRepository,
    ) -> None:
        self._chat_repository = chat_repository
        self._message_repository = message_repository
        self._mindmap_repository = mindmap_repository

    async def user_can_access_chat(self, *, chat_id: str, user_id: int) -> bool:
        chat = await self._chat_repository.get(chat_id, user_id)
        return bool(chat)

    async def get_for_user_chat(self, *, chat_id: str, user_id: int) -> dict | None:
        if not await self.user_can_access_chat(chat_id=chat_id, user_id=user_id):
            return None
        mindmap = await self._mindmap_repository.get(chat_id)
        if not mindmap:
            return {"markdown": "", "updated_at": None}
        return {
            "markdown": mindmap["markdown"],
            "updated_at": mindmap["updated_at"].isoformat() if mindmap["updated_at"] else None,
        }

    async def regenerate(
        self,
        *,
        chat_id: str,
        model_name: str,
        system_prompt: str,
        enabled: bool,
    ) -> bool:
        if not enabled:
            return False

        messages = await self._message_repository.list_by_chat(chat_id)
        if len(messages) < 2:
            return False

        existing = await self._mindmap_repository.get(chat_id)
        existing_markdown = existing["markdown"] if existing else ""

        conversation_parts = []
        for message in messages[-12:]:
            role = "User" if message["role"] == "user" else "Tutor"
            content = (message["content"] or "")[:1500]
            conversation_parts.append(f"--- {role} ---\n{content}")
        conversation = "\n\n".join(conversation_parts)

        prompt = (
            f"EXISTING MINDMAP:\n{existing_markdown or '(empty — first generation)'}\n\n"
            f"=== CONVERSATION ===\n{conversation}\n\n"
            f"Now produce the updated mindmap."
        )

        model = genai.GenerativeModel(model_name=model_name, system_instruction=system_prompt)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: model.generate_content(prompt))
        text = (response.text or "").strip()

        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        if not text:
            return False

        await self._mindmap_repository.save(chat_id, text)
        return True
