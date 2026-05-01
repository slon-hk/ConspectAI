"""File upload and serving orchestration service."""

from __future__ import annotations

from app.infrastructure.storage import FileStorage
from app.repositories.oltp import FileRepository


class FileService:
    def __init__(self, file_repository: FileRepository, file_storage: FileStorage) -> None:
        self._file_repository = file_repository
        self._file_storage = file_storage

    async def store_upload(
        self,
        *,
        raw: bytes,
        filename: str | None,
        content_type: str | None,
    ) -> dict:
        mime = content_type or self._file_storage.guess_mime(filename or "")
        meta = self._file_storage.store_file(raw, mime)
        await self._file_repository.register(
            meta["sha256"],
            mime,
            meta["compressed"],
            meta["original_size"],
            meta["stored_size"],
        )

        saved_kb = round((meta["original_size"] - meta["stored_size"]) / 1024, 1)
        compression = round((1 - meta["stored_size"] / max(meta["original_size"], 1)) * 100, 1)

        return {
            "sha256": meta["sha256"],
            "original_filename": filename,
            "mime_type": mime,
            "compressed": meta["compressed"],
            "original_size": meta["original_size"],
            "stored_size": meta["stored_size"],
            "saved_kb": saved_kb,
            "compression_pct": compression,
            "preview_url": f"/api/files/{meta['sha256']}/raw" if mime.startswith("image/") else None,
        }

    async def read_raw_file(self, *, sha256: str) -> tuple[bytes, str] | None:
        meta = await self._file_repository.get(sha256)
        if not meta:
            return None
        raw = self._file_storage.read_file(meta["sha256"], meta["compressed"])
        return raw, meta["mime_type"]
