"""File upload and serving API routes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.services.analytics_tracking_service import AnalyticsTrackingService
from app.services.file_service import FileService


def create_file_router(
    *,
    current_user_id: Callable,
    file_service: FileService,
    analytics_tracking_service: AnalyticsTrackingService,
) -> APIRouter:
    router = APIRouter(tags=["files"])

    @router.post("/api/upload")
    async def upload_file(
        file: UploadFile = File(...),
        uid: int = Depends(current_user_id),
    ):
        upload = await file_service.store_upload(
            raw=await file.read(),
            filename=file.filename,
            content_type=file.content_type,
        )

        analytics_tracking_service.track(
            "file_uploaded",
            uid,
            mime=upload["mime_type"],
            size=upload["original_size"],
            compressed=upload["compressed"],
            saved_kb=upload["saved_kb"],
        )
        return upload

    @router.get("/api/files/{sha256}/raw")
    async def serve_file(sha256: str, uid: int = Depends(current_user_id)):
        result = await file_service.read_raw_file(sha256=sha256)
        if not result:
            raise HTTPException(404, "File not found")
        raw, mime_type = result
        return Response(content=raw, media_type=mime_type)

    return router
