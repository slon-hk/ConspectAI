"""OLTP repository methods for RAG document ingestion writes."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class RagIngestionRepository(BaseRepository):
    async def set_document_status(
        self,
        *,
        document_id: str,
        status: str,
        error_msg: str | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                "UPDATE rag_documents SET status=$1, error_msg=$2 WHERE id=$3",
                status,
                error_msg,
                document_id,
            )

    async def find_existing_chunks(
        self,
        *,
        content_hashes: list[str],
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, str]:
        async with self.connection(conn) as db_conn:
            rows = await db_conn.fetch(
                "SELECT content_hash, id FROM rag_chunks WHERE content_hash = ANY($1)",
                content_hashes,
            )
            return {row["content_hash"]: str(row["id"]) for row in rows}

    async def upsert_chunks(
        self,
        *,
        document_id: str,
        chunks: list[tuple[int, str, str, str, int, float]],
        conn: asyncpg.Connection | None = None,
    ) -> dict[str, str]:
        """chunks: (chunk_index, content, content_hash, embedding_pgvector, token_count, importance_hint)"""
        chunk_ids: dict[str, str] = {}
        async with self.connection(conn) as db_conn:
            async with db_conn.transaction():
                for chunk_index, content, content_hash, embedding_pgvector, token_count, importance_hint in chunks:
                    row = await db_conn.fetchrow(
                        """
                        INSERT INTO rag_chunks
                            (document_id, content, content_hash, embedding, tsv,
                            chunk_index, char_start, char_end, token_count, importance_hint)
                        VALUES (
                            $1, $2, $3, $4::vector,
                            to_tsvector('russian', $2),
                            $5, NULL, NULL, $6, $7
                        )
                        ON CONFLICT (content_hash) DO UPDATE
                            SET source_count = rag_chunks.source_count + 1
                        RETURNING id
                        """,
                        document_id,
                        content,
                        content_hash,
                        embedding_pgvector,
                        chunk_index,
                        token_count,
                        importance_hint,
                    )
                    chunk_ids[content_hash] = str(row["id"])
        return chunk_ids

    async def increment_chunk_sources(
        self,
        *,
        content_hashes: list[str],
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                """UPDATE rag_chunks SET source_count = source_count + 1
                   WHERE content_hash = ANY($1)""",
                content_hashes,
            )

    async def get_image_id_by_sha(
        self,
        *,
        sha256: str,
        conn: asyncpg.Connection | None = None,
    ) -> str | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                "SELECT id FROM rag_images WHERE sha256 = $1",
                sha256,
            )
            return str(row["id"]) if row else None

    async def upsert_image(
        self,
        *,
        document_id: str,
        sha256: str,
        file_path: str,
        mime_type: str,
        caption: str,
        embedding_pgvector: str,
        page_num: int,
        conn: asyncpg.Connection | None = None,
    ) -> str:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """INSERT INTO rag_images
                       (document_id, sha256, file_path, mime_type,
                        caption, embedding, page_num)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (sha256) DO UPDATE
                       SET caption = EXCLUDED.caption
                   RETURNING id""",
                document_id,
                sha256,
                file_path,
                mime_type,
                caption,
                embedding_pgvector,
                page_num,
            )
            return str(row["id"])

    async def link_chunk_image(
        self,
        *,
        chunk_id: str,
        image_id: str,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                """INSERT INTO rag_chunk_images (chunk_id, image_id)
                   VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                chunk_id,
                image_id,
            )

    async def mark_document_ready(
        self,
        *,
        document_id: str,
        chunk_count: int,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                """UPDATE rag_documents
                   SET status = 'ready', chunk_count = $1
                   WHERE id = $2""",
                chunk_count,
                document_id,
            )
