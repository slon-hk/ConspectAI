"""
rag_routes.py — FastAPI router for RAG/courses functionality.

Endpoints:
  GET    /api/courses                 list user's courses
  POST   /api/courses                 create course
  DELETE /api/courses/{id}            delete course
  GET    /api/courses/{id}/documents  list documents in course
  POST   /api/courses/{id}/ingest     upload + ingest a document
  POST   /api/courses/{id}/ingest-url ingest YouTube URL
  GET    /api/rag/images/{image_id}   serve extracted image file
  GET    /api/chats/{chat_id}/course  get course linked to chat (used by UI)
  PATCH  /api/chats/{chat_id}/course  link/unlink course from chat
"""

import asyncio
import mimetypes
from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
)
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import db
import rag as rag_engine
from app.repositories.oltp import RagRouteRepository
from app.services import RagService

router = APIRouter(prefix="/api", tags=["rag"])
rag_routes_repository = RagRouteRepository()
rag_service = RagService(rag_routes_repository)

# ── Auth dependency (same pattern as main.py) ─────────────────────────────────

import auth as _auth


async def current_uid(token: str = Depends(_auth.oauth2)) -> int:
    if not token:
        raise HTTPException(401, "Not authenticated")
    uid = _auth.decode_token(token)
    if not uid:
        raise HTTPException(401, "Invalid token")
    user = await db.get_user_by_id(uid)
    if not user:
        raise HTTPException(401, "User not found")
    if user.get("is_blocked"):
        raise HTTPException(403, "Account blocked")
    return uid


# ── Schemas ───────────────────────────────────────────────────────────────────

class CourseCreate(BaseModel):
    title: str
    description: str = ""
    scope: str = "private"  # 'private' | 'public'


class CoursePatch(BaseModel):
    title: str | None = None
    description: str | None = None


class ChatCourseLink(BaseModel):
    course_id: str | None = None  # None to unlink


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize(d: dict) -> dict:
    """Convert asyncpg Record fields to JSON-safe types."""
    out = {}
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "__str__") and type(v).__name__ in ("UUID",):
            out[k] = str(v)
        else:
            out[k] = v
    return out


ALLOWED_MIME = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_FILE_MB = 50


# ── Course CRUD ───────────────────────────────────────────────────────────────

@router.get("/courses")
async def list_courses(uid: int = Depends(current_uid)):
    rows = await rag_service.list_courses(uid)
    return [_serialize(r) for r in rows]


@router.post("/courses", status_code=201)
async def create_course(body: CourseCreate, uid: int = Depends(current_uid)):
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
    uid: int = Depends(current_uid),
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
async def delete_course(course_id: str, uid: int = Depends(current_uid)):
    deleted = await rag_service.delete_course(course_id=course_id, user_id=uid)
    if not deleted:
        raise HTTPException(404, "Course not found")
    return {"ok": True}


# ── Documents ─────────────────────────────────────────────────────────────────

@router.get("/courses/{course_id}/documents")
async def list_documents(course_id: str, uid: int = Depends(current_uid)):
    docs = await rag_service.list_course_documents(course_id=course_id, user_id=uid)
    if docs is None:
        raise HTTPException(404, "Course not found")
    return [_serialize(d) for d in docs]


@router.post("/courses/{course_id}/ingest", status_code=202)
async def ingest_file(
    course_id: str,
    file: UploadFile = File(...),
    is_public: bool = Form(False),
    uid: int = Depends(current_uid),
):
    """Upload and ingest a file (PDF, TXT, MD, DOCX). Returns immediately;
    indexing runs in the background."""

    if not await rag_routes_repository.user_owns_course(course_id=course_id, user_id=uid):
        raise HTTPException(404, "Course not found")

    # Validate file
    raw = await file.read()
    if len(raw) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {MAX_FILE_MB}MB)")

    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime not in ALLOWED_MIME:
        # Be permissive with text files regardless of MIME
        if not (file.filename or "").endswith((".txt", ".md", ".pdf", ".docx", ".doc")):
            raise HTTPException(415, f"Unsupported file type: {mime}")

    # Detect source type
    fname = file.filename or "document"
    ext = Path(fname).suffix.lower()
    source_type_map = {
        ".pdf": "pdf", ".txt": "txt", ".md": "md",
        ".docx": "docx", ".doc": "docx",
    }
    source_type = source_type_map.get(ext, "txt")

    # SHA256 of raw bytes — skip if same doc already in this course
    doc_sha = rag_engine._sha256(raw)
    dup = await rag_routes_repository.find_document_duplicate(
        course_id=course_id,
        sha256=doc_sha,
    )
    if dup and dup["status"] == "ready":
        return {"status": "already_indexed", "document_id": str(dup["id"])}

    # Create document record
    document_id = await rag_routes_repository.create_file_document(
        course_id=course_id,
        user_id=uid,
        filename=fname,
        source_type=source_type,
        source_ref=fname,
        sha256=doc_sha,
        is_public=is_public,
    )

    # Fire ingestion in background (non-blocking)
    asyncio.create_task(
        rag_engine.ingest_document(
            document_id=document_id,
            course_id=course_id,
            user_id=uid,
            filename=fname,
            source_type=source_type,
            raw_bytes=raw,
        )
    )

    return {"status": "indexing", "document_id": document_id}


class IngestURLBody(BaseModel):
    url: str
    title: str | None = None


@router.post("/courses/{course_id}/ingest-url", status_code=202)
async def ingest_url(
    course_id: str,
    body: IngestURLBody,
    uid: int = Depends(current_uid),
):
    """Ingest a YouTube URL (transcript extraction)."""
    url = body.url.strip()

    if "youtube.com" not in url and "youtu.be" not in url:
        raise HTTPException(400, "Only YouTube URLs are supported currently")

    if not await rag_routes_repository.user_owns_course(course_id=course_id, user_id=uid):
        raise HTTPException(404, "Course not found")

    url_hash = rag_engine._sha256(url)
    dup = await rag_routes_repository.find_document_duplicate(
        course_id=course_id,
        sha256=url_hash,
    )
    if dup and dup["status"] == "ready":
        return {"status": "already_indexed", "document_id": str(dup["id"])}

    fname = body.title or url
    document_id = await rag_routes_repository.create_url_document(
        course_id=course_id,
        user_id=uid,
        filename=fname,
        source_ref=url,
        sha256=url_hash,
    )

    asyncio.create_task(
        rag_engine.ingest_document(
            document_id=document_id,
            course_id=course_id,
            user_id=uid,
            filename=fname,
            source_type="youtube",
            source_url=url,
        )
    )

    return {"status": "indexing", "document_id": document_id}


@router.delete("/courses/{course_id}/documents/{doc_id}")
async def delete_document(
    course_id: str,
    doc_id: str,
    uid: int = Depends(current_uid),
):
    deleted = await rag_service.delete_document(
        document_id=doc_id,
        course_id=course_id,
        user_id=uid,
    )
    if not deleted:
        raise HTTPException(404, "Document not found")
    return {"ok": True}


# ── Image serving ─────────────────────────────────────────────────────────────

@router.get("/rag/images/{image_id}")
async def serve_image(image_id: str, uid: int = Depends(current_uid)):
    """
    Serve an extracted image from a document the user has access to.
    Checks that the image belongs to a course the user owns.
    """
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


# ── Chat ↔ Course linking ─────────────────────────────────────────────────────

@router.get("/chats/{chat_id}/course")
async def get_chat_course(chat_id: str, uid: int = Depends(current_uid)):
    row = await rag_service.get_chat_course(chat_id=chat_id, user_id=uid)
    if not row:
        return {"course": None}
    return {"course": _serialize(row)}


@router.patch("/chats/{chat_id}/course")
async def link_chat_course(
    chat_id: str,
    body: ChatCourseLink,
    uid: int = Depends(current_uid),
):
    """Link a course to a chat. Pass course_id=null to unlink."""
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
