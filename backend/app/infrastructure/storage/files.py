"""File storage adapter over the legacy content-addressed storage module."""

from __future__ import annotations

import storage


class FileStorage:
    def guess_mime(self, filename: str, fallback: str = "application/octet-stream") -> str:
        return storage.guess_mime(filename, fallback)

    def store_file(self, raw_bytes: bytes, mime: str) -> dict:
        return storage.store_file(raw_bytes, mime)

    def read_file(self, sha256: str, compressed: bool) -> bytes:
        return storage.read_file(sha256, compressed)
