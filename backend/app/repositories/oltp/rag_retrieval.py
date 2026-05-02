"""OLTP repository methods for RAG hybrid retrieval."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository

SOURCE_BOOST = 0.10


class RagRetrievalRepository(BaseRepository):
    async def retrieve_chunks_and_images(
        self,
        *,
        course_ids: list[str] | None,
        query: str,
        query_embedding_pgvector: str,
        top_k: int,
        hybrid_alpha: float,
        image_ctx_limit: int,
        conn: asyncpg.Connection | None = None,
    ) -> tuple[list[dict], list[dict]]:
        async with self.connection(conn) as db_conn:
            if course_ids:
                doc_rows = await db_conn.fetch(
                    """
                    SELECT id,
                        COALESCE(course_id = ANY($1::uuid[]), FALSE) AS is_primary
                    FROM rag_documents
                    WHERE status = 'ready'
                      AND (course_id = ANY($1::uuid[]) OR is_public = TRUE)
                    """,
                    course_ids,
                )
            else:
                doc_rows = await db_conn.fetch(
                    """
                    SELECT id, FALSE AS is_primary
                    FROM rag_documents
                    WHERE status = 'ready' AND is_public = TRUE
                    """
                )
            if not doc_rows:
                return [], []

            doc_ids = [str(row["id"]) for row in doc_rows]
            primary_doc_ids = [str(row["id"]) for row in doc_rows if row["is_primary"]]
            chunk_rows = await db_conn.fetch(
                """
                WITH semantic AS (
                    SELECT
                        id,
                        content,
                        content_hash,
                        document_id,
                        1 - (embedding <=> $1::vector) AS cos_sim
                    FROM rag_chunks
                    WHERE document_id = ANY($2::uuid[])
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> $1::vector
                    LIMIT $3
                ),
                bm25 AS (
                    SELECT
                        id,
                        ts_rank(tsv, plainto_tsquery('russian', $4)) AS bm25_score
                    FROM rag_chunks
                    WHERE document_id = ANY($2::uuid[])
                      AND tsv @@ plainto_tsquery('russian', $4)
                    LIMIT $3
                ),
                combined AS (
                    SELECT
                        s.id,
                        s.content,
                        s.content_hash,
                        ($5 * s.cos_sim + $6 * COALESCE(b.bm25_score, 0)
                         + CASE WHEN s.document_id = ANY($7::uuid[]) THEN $8 ELSE 0.0 END) AS score
                    FROM semantic s
                    LEFT JOIN bm25 b ON s.id = b.id
                )
                SELECT id, content, content_hash, score
                FROM combined
                ORDER BY score DESC
                LIMIT $3
                """,
                query_embedding_pgvector,
                doc_ids,
                top_k,
                query,
                hybrid_alpha,
                1.0 - hybrid_alpha,
                primary_doc_ids,
                SOURCE_BOOST,
            )

            chunks = [
                {
                    "id": str(row["id"]),
                    "content": row["content"],
                    "content_hash": row["content_hash"],
                    "score": float(row["score"]),
                }
                for row in chunk_rows
            ]
            if not chunks:
                return [], []

            chunk_ids = [chunk["id"] for chunk in chunks]
            image_rows = await db_conn.fetch(
                """
                SELECT DISTINCT ri.id, ri.file_path, ri.caption, ri.mime_type
                FROM rag_chunk_images rci
                JOIN rag_images ri ON ri.id = rci.image_id
                WHERE rci.chunk_id = ANY($1::uuid[])
                LIMIT $2
                """,
                chunk_ids,
                image_ctx_limit,
            )
            images = [
                {
                    "id": str(row["id"]),
                    "file_path": row["file_path"],
                    "caption": row["caption"],
                    "mime_type": row["mime_type"],
                }
                for row in image_rows
            ]

            return chunks, images
