"""RAG/course API routes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.services.file_service import FileService
from app.services.rag_service import (
    RagCourseNotFoundError,
    RagFileTooLargeError,
    RagInvalidUrlError,
    RagService,
    RagUnsupportedFileError,
)


class CourseCreate(BaseModel):
    title: str
    description: str = ""
    scope: str = "private"


class CoursePatch(BaseModel):
    title: str | None = None
    description: str | None = None


class ChatCourseLink(BaseModel):
    course_id: str | None = None


class IngestURLBody(BaseModel):
    url: str
    title: str | None = None


def create_rag_router(*, current_user_id: Callable, rag_service: RagService, file_service: FileService) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["rag"])

    @router.get("/courses")
    async def list_courses(uid: int = Depends(current_user_id)):
        rows = await rag_service.list_courses(uid)
        return [_serialize(r) for r in rows]

    @router.post("/courses", status_code=201)
    async def create_course(body: CourseCreate, uid: int = Depends(current_user_id)):
        try:
            course = await rag_service.create_course(
                user_id=uid,
                title=body.title,
                description=body.description,
                scope=body.scope,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        return _serialize(course)

    @router.patch("/courses/{course_id}")
    async def update_course(
        course_id: str,
        body: CoursePatch,
        uid: int = Depends(current_user_id),
    ):
        updated = await rag_service.update_course(
            course_id=course_id,
            user_id=uid,
            title=body.title,
            description=body.description,
        )
        if not updated:
            raise HTTPException(404, "Course not found")

        return {"ok": True}

    @router.delete("/courses/{course_id}")
    async def delete_course(course_id: str, uid: int = Depends(current_user_id)):
        deleted = await rag_service.delete_course(course_id=course_id, user_id=uid)
        if not deleted:
            raise HTTPException(404, "Course not found")
        return {"ok": True}

    @router.get("/courses/{course_id}/documents")
    async def list_documents(course_id: str, uid: int = Depends(current_user_id)):
        docs = await rag_service.list_course_documents(course_id=course_id, user_id=uid)
        if docs is None:
            raise HTTPException(404, "Course not found")
        return [_serialize(d) for d in docs]

    @router.post("/courses/{course_id}/ingest", status_code=202)
    async def ingest_file(
        course_id: str,
        file: UploadFile = File(...),
        is_public: bool = Form(False),
        uid: int = Depends(current_user_id),
    ):
        raw = await file.read()
        try:
            return await rag_service.ingest_file(
                course_id=course_id,
                user_id=uid,
                filename=file.filename or "document",
                content_type=file.content_type,
                raw=raw,
                is_public=is_public,
            )
        except RagCourseNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except RagFileTooLargeError as exc:
            raise HTTPException(413, str(exc)) from exc
        except RagUnsupportedFileError as exc:
            raise HTTPException(415, str(exc)) from exc

    @router.post("/courses/{course_id}/ingest-url", status_code=202)
    async def ingest_url(
        course_id: str,
        body: IngestURLBody,
        uid: int = Depends(current_user_id),
    ):
        try:
            return await rag_service.ingest_url(
                course_id=course_id,
                user_id=uid,
                url=body.url,
                title=body.title,
            )
        except RagInvalidUrlError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RagCourseNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.delete("/courses/{course_id}/documents/{doc_id}")
    async def delete_document(
        course_id: str,
        doc_id: str,
        uid: int = Depends(current_user_id),
    ):
        deleted = await rag_service.delete_document(
            document_id=doc_id,
            course_id=course_id,
            user_id=uid,
        )
        if not deleted:
            raise HTTPException(404, "Document not found")
        return {"ok": True}

    @router.get("/rag/images/{image_id}")
    async def serve_image(image_id: str, uid: int = Depends(current_user_id)):
        row = await rag_service.get_image_for_user(image_id=image_id, user_id=uid)
        if not row:
            raise HTTPException(404, "Image not found or access denied")

        file_path = Path(row["file_path"])
        if not file_path.exists():
            raise HTTPException(404, "Image file missing on disk")

        return FileResponse(
            str(file_path),
            media_type=row["mime_type"],
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @router.get("/chats/{chat_id}/course")
    async def get_chat_course(chat_id: str, uid: int = Depends(current_user_id)):
        row = await rag_service.get_chat_course(chat_id=chat_id, user_id=uid)
        if not row:
            return {"course": None}
        return {"course": _serialize(row)}

    @router.patch("/chats/{chat_id}/course")
    async def link_chat_course(
        chat_id: str,
        body: ChatCourseLink,
        uid: int = Depends(current_user_id),
    ):
        result = await rag_service.link_chat_course(
            chat_id=chat_id,
            course_id=body.course_id,
            user_id=uid,
        )
        if result == "course_not_found":
            raise HTTPException(404, "Course not found")
        if result == "chat_not_found":
            raise HTTPException(404, "Chat not found")

        return {"ok": True, "course_id": body.course_id}

    @router.post("/public-kb/ingest", status_code=202)
    async def ingest_public(
        file: UploadFile = File(...),
        uid: int = Depends(current_user_id),
    ):
        raw = await file.read()
        filename = file.filename or "document"
        try:
            upload_meta = await file_service.store_upload(
                raw=raw,
                filename=filename,
                content_type=file.content_type,
            )
            ingest_result = await rag_service.ingest_public_file(
                user_id=uid,
                filename=filename,
                content_type=file.content_type,
                raw=raw,
            )
        except RagFileTooLargeError as exc:
            raise HTTPException(413, str(exc)) from exc
        except RagUnsupportedFileError as exc:
            raise HTTPException(415, str(exc)) from exc

        return {**upload_meta, **ingest_result}

    return router


def _serialize(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "__str__") and type(v).__name__ in ("UUID",):
            out[k] = str(v)
        else:
            out[k] = v
    return out
