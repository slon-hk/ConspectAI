"""RAG course/document orchestration service."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import Literal

<<<<<<< HEAD
from app.infrastructure.ai import RagEngine
=======
import rag as rag_engine
>>>>>>> 65d9c6e (fix bag)
from app.repositories.oltp import RagRouteRepository

ChatCourseLinkResult = Literal["ok", "course_not_found", "chat_not_found"]


class RagServiceError(Exception):
    pass


class RagCourseNotFoundError(RagServiceError):
    pass


class RagFileTooLargeError(RagServiceError):
    pass


class RagUnsupportedFileError(RagServiceError):
    pass


class RagInvalidUrlError(RagServiceError):
    pass


class RagService:
    ALLOWED_MIME = {
        "application/pdf",
        "text/plain",
        "text/markdown",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    MAX_FILE_MB = 50

<<<<<<< HEAD
    def __init__(self, rag_repository: RagRouteRepository, rag_engine: RagEngine) -> None:
        self._rag_repository = rag_repository
        self._rag_engine = rag_engine
=======
    def __init__(self, rag_repository: RagRouteRepository) -> None:
        self._rag_repository = rag_repository
>>>>>>> 65d9c6e (fix bag)

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

    async def ingest_file(
        self,
        *,
        course_id: str,
        user_id: int,
        filename: str,
        content_type: str | None,
        raw: bytes,
        is_public: bool,
    ) -> dict:
        if not await self._rag_repository.user_owns_course(course_id=course_id, user_id=user_id):
            raise RagCourseNotFoundError("Course not found")

        if len(raw) > self.MAX_FILE_MB * 1024 * 1024:
            raise RagFileTooLargeError(f"File too large (max {self.MAX_FILE_MB}MB)")

        mime = content_type or mimetypes.guess_type(filename)[0] or ""
        if mime not in self.ALLOWED_MIME:
            if not filename.endswith((".txt", ".md", ".pdf", ".docx", ".doc")):
                raise RagUnsupportedFileError(f"Unsupported file type: {mime}")

        ext = Path(filename).suffix.lower()
        source_type_map = {
            ".pdf": "pdf",
            ".txt": "txt",
            ".md": "md",
            ".docx": "docx",
            ".doc": "docx",
        }
        source_type = source_type_map.get(ext, "txt")

<<<<<<< HEAD
        doc_sha = self._rag_engine.sha256(raw)
=======
        doc_sha = rag_engine._sha256(raw)
>>>>>>> 65d9c6e (fix bag)
        duplicate = await self._rag_repository.find_document_duplicate(
            course_id=course_id,
            sha256=doc_sha,
        )
        if duplicate and duplicate["status"] == "ready":
            return {"status": "already_indexed", "document_id": str(duplicate["id"])}

        document_id = await self._rag_repository.create_file_document(
            course_id=course_id,
            user_id=user_id,
            filename=filename,
            source_type=source_type,
            source_ref=filename,
            sha256=doc_sha,
            is_public=is_public,
        )

        asyncio.create_task(
<<<<<<< HEAD
            self._rag_engine.ingest_document(
=======
            rag_engine.ingest_document(
>>>>>>> 65d9c6e (fix bag)
                document_id=document_id,
                course_id=course_id,
                user_id=user_id,
                filename=filename,
                source_type=source_type,
                raw_bytes=raw,
            )
        )

        return {"status": "indexing", "document_id": document_id}

    async def ingest_url(
        self,
        *,
        course_id: str,
        user_id: int,
        url: str,
        title: str | None,
    ) -> dict:
        normalized_url = url.strip()
        if "youtube.com" not in normalized_url and "youtu.be" not in normalized_url:
            raise RagInvalidUrlError("Only YouTube URLs are supported currently")

        if not await self._rag_repository.user_owns_course(course_id=course_id, user_id=user_id):
            raise RagCourseNotFoundError("Course not found")

<<<<<<< HEAD
        url_hash = self._rag_engine.sha256(normalized_url)
=======
        url_hash = rag_engine._sha256(normalized_url)
>>>>>>> 65d9c6e (fix bag)
        duplicate = await self._rag_repository.find_document_duplicate(
            course_id=course_id,
            sha256=url_hash,
        )
        if duplicate and duplicate["status"] == "ready":
            return {"status": "already_indexed", "document_id": str(duplicate["id"])}

        filename = title or normalized_url
        document_id = await self._rag_repository.create_url_document(
            course_id=course_id,
            user_id=user_id,
            filename=filename,
            source_ref=normalized_url,
            sha256=url_hash,
        )

        asyncio.create_task(
<<<<<<< HEAD
            self._rag_engine.ingest_document(
=======
            rag_engine.ingest_document(
>>>>>>> 65d9c6e (fix bag)
                document_id=document_id,
                course_id=course_id,
                user_id=user_id,
                filename=filename,
                source_type="youtube",
                source_url=normalized_url,
            )
        )

        return {"status": "indexing", "document_id": document_id}

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
