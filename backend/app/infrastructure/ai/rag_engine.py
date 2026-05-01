"""Adapter over the legacy RAG engine module."""

from __future__ import annotations

import hashlib
from typing import Any


class RagEngine:
    def sha256(self, value: bytes | str) -> str:
        if isinstance(value, str):
            value = value.encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    async def ingest_document(self, **kwargs: Any) -> Any:
        import rag as rag_engine

        return await rag_engine.ingest_document(**kwargs)

    async def ensure_chat_course_and_ingest_uploads(self, **kwargs: Any) -> Any:
        import rag as rag_engine

        return await rag_engine.ensure_chat_course_and_ingest_uploads(**kwargs)

    async def rag_query(self, **kwargs: Any) -> dict:
        import rag as rag_engine

        return await rag_engine.rag_query(**kwargs)
