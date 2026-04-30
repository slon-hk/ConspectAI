"""RAG course/document orchestration service."""

from __future__ import annotations

from typing import Literal

from app.repositories.oltp import RagRouteRepository

ChatCourseLinkResult = Literal["ok", "course_not_found", "chat_not_found"]


class RagService:
    def __init__(self, rag_repository: RagRouteRepository) -> None:
        self._rag_repository = rag_repository

    async def list_courses(self, user_id: int) -> list[dict]:
        return await self._rag_repository.list_user_courses(user_id=user_id)

    async def create_course(
        self,
        *,
        user_id: int,
        title: str,
        description: str,
        scope: str,
    ) -> dict:
        if scope not in ("private", "public"):
            raise ValueError("scope must be 'private' or 'public'")

        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("title is required")

        return await self._rag_repository.create_course(
            user_id=user_id,
            title=normalized_title,
            description=description.strip(),
            scope=scope,
        )

    async def update_course(
        self,
        *,
        course_id: str,
        user_id: int,
        title: str | None = None,
        description: str | None = None,
    ) -> bool:
        if not await self._rag_repository.user_owns_course(course_id=course_id, user_id=user_id):
            return False

        await self._rag_repository.update_course(
            course_id=course_id,
            title=title.strip() if title is not None else None,
            description=description.strip() if description is not None else None,
        )
        return True

    async def delete_course(self, *, course_id: str, user_id: int) -> bool:
        return await self._rag_repository.delete_course(course_id=course_id, user_id=user_id)

    async def list_course_documents(self, *, course_id: str, user_id: int) -> list[dict] | None:
        if not await self._rag_repository.user_owns_course(course_id=course_id, user_id=user_id):
            return None
        return await self._rag_repository.list_course_documents(course_id=course_id, user_id=user_id)

    async def delete_document(
        self,
        *,
        document_id: str,
        course_id: str,
        user_id: int,
    ) -> bool:
        return await self._rag_repository.delete_document_for_user(
            document_id=document_id,
            course_id=course_id,
            user_id=user_id,
        )

    async def get_image_for_user(self, *, image_id: str, user_id: int) -> dict | None:
        return await self._rag_repository.get_image_for_user(image_id=image_id, user_id=user_id)

    async def get_chat_course(self, *, chat_id: str, user_id: int) -> dict | None:
        return await self._rag_repository.get_chat_course(chat_id=chat_id, user_id=user_id)

    async def link_chat_course(
        self,
        *,
        chat_id: str,
        course_id: str | None,
        user_id: int,
    ) -> ChatCourseLinkResult:
        if course_id is not None:
            can_access = await self._rag_repository.user_can_access_course(
                course_id=course_id,
                user_id=user_id,
            )
            if not can_access:
                return "course_not_found"

        updated = await self._rag_repository.link_chat_course(
            chat_id=chat_id,
            course_id=course_id,
            user_id=user_id,
        )
        if not updated:
            return "chat_not_found"
        return "ok"
