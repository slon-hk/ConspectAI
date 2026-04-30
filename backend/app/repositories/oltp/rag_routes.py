"""OLTP repository methods used by RAG API routes."""

from __future__ import annotations

import asyncpg

from app.repositories.base import BaseRepository


class RagRouteRepository(BaseRepository):
    async def create_course(
        self,
        *,
        user_id: int,
        title: str,
        description: str,
        scope: str,
        conn: asyncpg.Connection | None = None,
    ) -> dict:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """INSERT INTO courses (user_id, title, description, scope)
                   VALUES ($1, $2, $3, $4) RETURNING *""",
                user_id,
                title,
                description,
                scope,
            )
            return dict(row)

    async def user_owns_course(
        self,
        *,
        course_id: str,
        user_id: int,
        conn: asyncpg.Connection | None = None,
    ) -> bool:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                "SELECT id FROM courses WHERE id = $1 AND user_id = $2",
                course_id,
                user_id,
            )
            return bool(row)

    async def update_course(
        self,
        *,
        course_id: str,
        title: str | None = None,
        description: str | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        updates: dict[str, str] = {}
        if title is not None:
            updates["title"] = title
        if description is not None:
            updates["description"] = description
        if not updates:
            return

        sets = ", ".join(f"{key} = ${index + 2}" for index, key in enumerate(updates))
        values = list(updates.values())
        async with self.connection(conn) as db_conn:
            await db_conn.execute(
                f"UPDATE courses SET {sets}, updated_at = now() WHERE id = $1",
                course_id,
                *values,
            )

    async def find_document_duplicate(
        self,
        *,
        course_id: str,
        sha256: str,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """SELECT id, status FROM rag_documents
                   WHERE course_id = $1 AND sha256 = $2""",
                course_id,
                sha256,
            )
            return dict(row) if row else None

    async def create_file_document(
        self,
        *,
        course_id: str,
        user_id: int,
        filename: str,
        source_type: str,
        source_ref: str,
        sha256: str,
        is_public: bool,
        conn: asyncpg.Connection | None = None,
    ) -> str:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """INSERT INTO rag_documents
                       (course_id, user_id, filename, source_type, source_ref, sha256, status, is_public)
                   VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7)
                   RETURNING id""",
                course_id,
                user_id,
                filename,
                source_type,
                source_ref,
                sha256,
                is_public,
            )
            return str(row["id"])

    async def create_url_document(
        self,
        *,
        course_id: str,
        user_id: int,
        filename: str,
        source_ref: str,
        sha256: str,
        conn: asyncpg.Connection | None = None,
    ) -> str:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """INSERT INTO rag_documents
                       (course_id, user_id, filename, source_type, source_ref, sha256)
                   VALUES ($1, $2, $3, 'youtube', $4, $5) RETURNING id""",
                course_id,
                user_id,
                filename,
                source_ref,
                sha256,
            )
            return str(row["id"])

    async def delete_document_for_user(
        self,
        *,
        document_id: str,
        course_id: str,
        user_id: int,
        conn: asyncpg.Connection | None = None,
    ) -> bool:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """SELECT id FROM rag_documents
                   WHERE id = $1 AND course_id = $2 AND user_id = $3""",
                document_id,
                course_id,
                user_id,
            )
            if not row:
                return False
            await db_conn.execute("DELETE FROM rag_documents WHERE id = $1", document_id)
            return True

    async def get_image_for_user(
        self,
        *,
        image_id: str,
        user_id: int,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """
                SELECT ri.file_path, ri.mime_type
                FROM rag_images ri
                JOIN rag_documents rd ON rd.id = ri.document_id
                JOIN courses c ON c.id = rd.course_id
                WHERE ri.id = $1 AND c.user_id = $2
                """,
                image_id,
                user_id,
            )
            return dict(row) if row else None

    async def get_chat_course(
        self,
        *,
        chat_id: str,
        user_id: int,
        conn: asyncpg.Connection | None = None,
    ) -> dict | None:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                """SELECT c.id, c.title, c.description, c.scope,
                          (SELECT COUNT(*) FROM rag_documents d
                           WHERE d.course_id = c.id AND d.status = 'ready') AS doc_count
                   FROM chats ch
                   JOIN courses c ON c.id = ch.course_id
                   WHERE ch.id = $1 AND ch.user_id = $2""",
                chat_id,
                user_id,
            )
            return dict(row) if row else None

    async def user_can_access_course(
        self,
        *,
        course_id: str,
        user_id: int,
        conn: asyncpg.Connection | None = None,
    ) -> bool:
        async with self.connection(conn) as db_conn:
            row = await db_conn.fetchrow(
                "SELECT id FROM courses WHERE id = $1 AND (user_id = $2 OR scope = 'public')",
                course_id,
                user_id,
            )
            return bool(row)

    async def link_chat_course(
        self,
        *,
        chat_id: str,
        course_id: str | None,
        user_id: int,
        conn: asyncpg.Connection | None = None,
    ) -> bool:
        async with self.connection(conn) as db_conn:
            result = await db_conn.execute(
                "UPDATE chats SET course_id = $1 WHERE id = $2 AND user_id = $3",
                course_id,
                chat_id,
                user_id,
            )
            return result != "UPDATE 0"

