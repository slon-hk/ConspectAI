"""
rag.py — Production-grade RAG pipeline for ConspectAI.

Architecture:
  Ingestion:  source → extract → chunk → embed → deduplicate → store
  Query:      q → cache? → embed → hybrid_search → build_ctx → llm → cache → return

Design decisions:
  - pgvector for hybrid search (cosine + BM25 via tsvector).
  - SHA-256 dedup: same content from 5 users stored once (shared embeddings).
  - Two visibility scopes: 'private' (user-only) and 'public' (all users share).
  - Images stored as files; only their CAPTION is embedded (cost ~0 vs. multimodal).
  - Answer cache keyed on hash(query_embedding + context_hashes): same context
    → instant return, no LLM call.
  - All heavy I/O is async; CPU-bound PDF parsing runs in a thread pool.
  - Ingestion is fully separated from query path (different code paths, can run
    as separate workers later).
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import asyncpg
import google.generativeai as genai
import tiktoken

import db
import storage

# ── Configuration ─────────────────────────────────────────────────────────────

EMBED_MODEL     = "models/gemini-embedding-2-preview"  # Gemini, $0.025/1M tokens
EMBED_DIM       = 1536  # reduced to fit pgvector index limit (<=2000)
CAPTION_MODEL   = "gemini-2.0-flash-lite"
CHUNK_SIZE      = 500   # target tokens per chunk
CHUNK_OVERLAP   = 80    # overlap tokens between chunks
TOP_K           = 5     # chunks returned per query
HYBRID_ALPHA    = 0.70  # weight for cosine vs BM25 (0.7 cosine + 0.3 BM25)
MAX_CTX_TOKENS  = 3000  # hard limit on context sent to LLM
IMAGE_CTX_LIMIT = 3     # max images surfaced per answer

IMAGES_DIR = Path(os.getenv("UPLOADS_DIR", "uploads")) / "rag_images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

_executor = ThreadPoolExecutor(max_workers=2)  # for blocking PDF/file ops

# ── Schema additions (called from db.init_schema) ─────────────────────────────

RAG_SCHEMA = """
-- pgvector extension (must be enabled once per DB)
CREATE EXTENSION IF NOT EXISTS vector;

-- Courses: user-created knowledge collections
CREATE TABLE IF NOT EXISTS courses (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    scope       TEXT    NOT NULL DEFAULT 'private',  -- 'private' | 'public'
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS courses_user_idx ON courses(user_id);

-- Source documents tracked per course
CREATE TABLE IF NOT EXISTS rag_documents (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id   UUID    NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename    TEXT    NOT NULL,
    source_type TEXT    NOT NULL,  -- 'pdf' | 'txt' | 'md' | 'docx' | 'youtube' | 'url'
    source_ref  TEXT    NOT NULL,  -- original path or URL
    sha256      TEXT    NOT NULL,  -- hash of raw source for dedup at doc level
    status      TEXT    NOT NULL DEFAULT 'pending',  -- pending|indexing|ready|error
    error_msg   TEXT,
    chunk_count INTEGER DEFAULT 0,
    is_public   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rag_doc_course_idx ON rag_documents(course_id);
CREATE INDEX IF NOT EXISTS rag_doc_sha256_idx ON rag_documents(sha256);
CREATE INDEX IF NOT EXISTS rag_doc_public_idx ON rag_documents(is_public, status, created_at DESC);

ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE;

-- Text chunks with embeddings (shared across users via content_hash)
CREATE TABLE IF NOT EXISTS rag_chunks (
    id           UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id  UUID    REFERENCES rag_documents(id) ON DELETE CASCADE,
    content      TEXT    NOT NULL,
    content_hash TEXT    NOT NULL,       -- SHA-256 of content: dedup key
    embedding    vector(1536),            -- Reduced embedding dimension to 1536 due to pgvector index limit
    tsv          tsvector,               -- for BM25 full-text search
    chunk_index  INTEGER NOT NULL,       -- position within document
    char_start   INTEGER,                -- character offset in source
    char_end     INTEGER,
    source_count INTEGER NOT NULL DEFAULT 1,  -- how many docs reference this content
    created_at   TIMESTAMPTZ DEFAULT now()
);
-- Unique on content so duplicates are merged, not inserted
CREATE UNIQUE INDEX IF NOT EXISTS rag_chunks_hash_idx ON rag_chunks(content_hash);
CREATE INDEX IF NOT EXISTS rag_chunks_doc_idx   ON rag_chunks(document_id);
CREATE INDEX IF NOT EXISTS rag_chunks_tsv_idx   ON rag_chunks USING GIN(tsv);
CREATE INDEX IF NOT EXISTS rag_chunks_emb_idx   ON rag_chunks
    USING hnsw (embedding vector_cosine_ops);

-- Images extracted from documents
CREATE TABLE IF NOT EXISTS rag_images (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID    NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    sha256      TEXT    NOT NULL UNIQUE,  -- dedup raw image bytes
    file_path   TEXT    NOT NULL,
    mime_type   TEXT    NOT NULL DEFAULT 'image/png',
    caption     TEXT    NOT NULL DEFAULT '',
    embedding   vector(1536),
    page_num    INTEGER,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rag_img_doc_idx  ON rag_images(document_id);
CREATE INDEX IF NOT EXISTS rag_img_hash_idx ON rag_images(sha256);

-- Many-to-many: which images belong to which chunk (by proximity in document)
CREATE TABLE IF NOT EXISTS rag_chunk_images (
    chunk_id  UUID REFERENCES rag_chunks(id) ON DELETE CASCADE,
    image_id  UUID REFERENCES rag_images(id) ON DELETE CASCADE,
    PRIMARY KEY (chunk_id, image_id)
);

-- Embedding cache: avoid re-calling Gemini embed for seen query strings
CREATE TABLE IF NOT EXISTS rag_query_cache (
    query_hash TEXT    PRIMARY KEY,  -- SHA-256 of query text
    embedding  vector(1536),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Answer cache: same query against same context → instant return
CREATE TABLE IF NOT EXISTS rag_answer_cache (
    cache_key   TEXT PRIMARY KEY,   -- SHA-256 of (query_hash + sorted chunk ids)
    answer      TEXT NOT NULL,
    image_ids   TEXT NOT NULL DEFAULT '[]',  -- JSON array of rag_image UUIDs
    hit_count   INTEGER NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ DEFAULT now(),
    last_used   TIMESTAMPTZ DEFAULT now()
);

-- Chats can optionally be linked to a course
ALTER TABLE chats ADD COLUMN IF NOT EXISTS course_id UUID REFERENCES courses(id) ON DELETE SET NULL;
"""


# ── Text splitting ─────────────────────────────────────────────────────────────

def _rough_token_count(text: str) -> int:
    """Fast approximation: 1 token ≈ 4 chars for mixed RU/EN text."""
    return max(1, len(text) // 4)

_tokenizer = tiktoken.get_encoding("cl100k_base")

def split_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Split text into overlapping chunks without breaking sentences mid-way.
    Strategy: accumulate sentences until we hit `size` tokens, then backtrack
    `overlap` tokens for the next chunk.
    """
    # Split on sentence boundaries (., !, ?, newline sequences)
    sentences = re.split(r'(?<=[.!?\n])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sent in sentences:
        sent_tokens = _rough_token_count(sent)
        if current_tokens + sent_tokens > size and current:
            chunks.append(" ".join(current))
            # keep tail for overlap
            tail_tokens = 0
            tail: list[str] = []
            for s in reversed(current):
                t = _rough_token_count(s)
                if tail_tokens + t <= overlap:
                    tail.insert(0, s)
                    tail_tokens += t
                else:
                    break
            current = tail
            current_tokens = tail_tokens
        current.append(sent)
        current_tokens += sent_tokens

    if current:
        chunks.append(" ".join(current))

    return chunks


# ── Hashing ────────────────────────────────────────────────────────────────────

def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()



# ── Embedding ─────────────────────────────────────────────────────────────────

async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Call Gemini text-embedding-004 in batches of 100 (API limit).
    Returns list of 768-dim float vectors.
    """
    if not texts:
        return []

    results: list[list[float]] = []
    batch_size = 100

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            _executor,
            lambda b=batch: genai.embed_content(
                model=EMBED_MODEL,
                content=b,
                task_type="RETRIEVAL_DOCUMENT",
            ),
        )
        results.extend(resp["embedding"])
        # Truncate embeddings to match EMBED_DIM (pgvector limit workaround)
        results = [vec[:EMBED_DIM] for vec in results]

    return results


async def embed_query(query: str) -> list[float]:
    """Embed a single query string with caching in rag_query_cache."""
    q_hash = _sha256(query)

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT embedding FROM rag_query_cache WHERE query_hash = $1", q_hash
        )
        if row:
            return list(row["embedding"])

    # Not cached → embed
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(
        _executor,
        lambda: genai.embed_content(
            model=EMBED_MODEL,
            content=query,
            task_type="RETRIEVAL_QUERY",
        ),
    )
    vec = resp["embedding"]
    # Truncate embedding to match EMBED_DIM (pgvector limit workaround)
    vec = vec[:EMBED_DIM]

    # Store in cache (fire and forget — don't block query path)
    asyncio.create_task(_store_query_cache(q_hash, vec))
    return vec


async def _store_query_cache(q_hash: str, embedding: list[float]) -> None:
    try:
        async with db.pool().acquire() as conn:
            await conn.execute(
                """INSERT INTO rag_query_cache (query_hash, embedding)
                VALUES ($1, $2::vector)""",
                q_hash, to_pgvector(embedding),
            )
    except Exception as e:
        print(f"[rag] query cache write failed: {e}")


# ── Image extraction + captioning ─────────────────────────────────────────────

def _extract_images_from_pdf(pdf_bytes: bytes) -> list[dict]:
    """
    Extract images from PDF using PyMuPDF.
    Returns list of {sha256, bytes, mime, page_num, surrounding_text}.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return []

    results = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for page_num, page in enumerate(doc):
        # Get page text for context linking
        page_text = page.get_text()[:400]

        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_img = doc.extract_image(xref)
            except Exception:
                continue

            img_bytes = base_img["image"]
            ext = base_img.get("ext", "png")
            mime = f"image/{ext}"
            sha = _sha256(img_bytes)

            # Skip very small images (icons, bullets) < 5KB
            if len(img_bytes) < 5120:
                continue

            results.append({
                "sha256":           sha,
                "bytes":            img_bytes,
                "mime":             mime,
                "page_num":         page_num,
                "surrounding_text": page_text,
            })

    doc.close()
    return results


async def _caption_image(img_bytes: bytes, surrounding_text: str) -> str:
    """
    Generate a concise caption for an image using the cheapest Gemini model.
    The surrounding_text provides context (e.g. 'Chapter 3: Harmonic oscillator').
    """
    import PIL.Image
    import io

    try:
        pil_img = PIL.Image.open(io.BytesIO(img_bytes))
        model = genai.GenerativeModel(CAPTION_MODEL)
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            _executor,
            lambda: model.generate_content([
                pil_img,
                f"Context from surrounding text: {surrounding_text[:200]}\n\n"
                "Write a single precise caption (max 30 words) describing this image "
                "for a STEM student. Focus on what is shown: graph type, axes, "
                "variables, key features. No preamble.",
            ]),
        )
        return resp.text.strip()[:200]
    except Exception as e:
        print(f"[rag] captioning failed: {e}")
        return surrounding_text[:80] or "figure"


# ── PDF ingestion ──────────────────────────────────────────────────────────────

async def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract raw text from PDF using pdfplumber (runs in thread)."""
    def _sync():
        try:
            import pdfplumber, io
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                return "\n\n".join(
                    (p.extract_text() or "") for p in pdf.pages
                )
        except Exception as e:
            print(f"[rag] pdfplumber failed: {e}")
            return ""

    return await asyncio.get_event_loop().run_in_executor(_executor, _sync)


async def _extract_text_from_docx(file_bytes: bytes) -> str:
    def _sync():
        try:
            import docx, io
            doc = docx.Document(io.BytesIO(file_bytes))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:
            print(f"[rag] docx failed: {e}")
            return ""

    return await asyncio.get_event_loop().run_in_executor(_executor, _sync)


async def _extract_text_from_youtube(url: str) -> str:
    """Fetch transcript using youtube-transcript-api."""
    def _sync():
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            import re as _re
            vid_match = _re.search(
                r"(?:v=|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})", url
            )
            if not vid_match:
                return ""
            vid_id = vid_match.group(1)
            transcript = YouTubeTranscriptApi.get_transcript(
                vid_id, languages=["ru", "en"]
            )
            return " ".join(t["text"] for t in transcript)
        except Exception as e:
            print(f"[rag] YouTube transcript failed: {e}")
            return ""

    return await asyncio.get_event_loop().run_in_executor(_executor, _sync)


# ── Core ingestion ─────────────────────────────────────────────────────────────

async def ingest_document(
    *,
    document_id: str,
    course_id: str,
    user_id: int,
    filename: str,
    source_type: str,   # 'pdf' | 'txt' | 'md' | 'docx' | 'youtube'
    raw_bytes: bytes | None = None,
    source_url: str | None = None,
) -> dict:
    """
    Full ingestion pipeline for a single document.
    Updates rag_documents.status throughout.
    Returns {chunk_count, image_count, error}.
    """
    start = time.monotonic()

    async def _set_status(status: str, msg: str = "") -> None:
        async with db.pool().acquire() as conn:
            await conn.execute(
                "UPDATE rag_documents SET status=$1, error_msg=$2 WHERE id=$3",
                status, msg or None, document_id,
            )

    await _set_status("indexing")

    try:
        # ── 1. Extract text ────────────────────────────────────────────────
        text = ""
        raw_images_data: list[dict] = []

        if source_type == "pdf" and raw_bytes:
            text = await _extract_text_from_pdf(raw_bytes)
            raw_images_data = await asyncio.get_event_loop().run_in_executor(
                _executor, _extract_images_from_pdf, raw_bytes
            )
        elif source_type in ("txt", "md") and raw_bytes:
            text = raw_bytes.decode("utf-8", errors="replace")
        elif source_type == "docx" and raw_bytes:
            text = await _extract_text_from_docx(raw_bytes)
        elif source_type == "youtube" and source_url:
            text = await _extract_text_from_youtube(source_url)
        else:
            raise ValueError(f"Unsupported source_type={source_type}")

        if not text.strip():
            raise ValueError("Extracted text is empty")

        # ── 2. Split into chunks ───────────────────────────────────────────
        raw_chunks = split_text(text)
        if not raw_chunks:
            raise ValueError("No chunks produced")

        # ── 3. Deduplicate chunks by content hash ──────────────────────────
        chunk_hashes = [_sha256(c) for c in raw_chunks]
        async with db.pool().acquire() as conn:
            existing = await conn.fetch(
                "SELECT content_hash, id FROM rag_chunks WHERE content_hash = ANY($1)",
                chunk_hashes,
            )
        existing_map: dict[str, str] = {r["content_hash"]: str(r["id"]) for r in existing}

        new_chunks = [
            (i, c, h) for i, (c, h) in enumerate(zip(raw_chunks, chunk_hashes))
            if h not in existing_map
        ]

        # ── 4. Embed only NEW chunks (never re-embed existing) ─────────────
        chunk_ids: dict[str, str] = dict(existing_map)  # hash → id

        if new_chunks:
            texts_to_embed = [c for _, c, _ in new_chunks]
            embeddings = await embed_texts(texts_to_embed)

            async with db.pool().acquire() as conn:
                async with conn.transaction():
                    for (idx, content, chash), emb in zip(new_chunks, embeddings):
                        row = await conn.fetchrow(
                            """
                            INSERT INTO rag_chunks
                                (document_id, content, content_hash, embedding, tsv,
                                chunk_index, char_start, char_end)
                            VALUES (
                                $1, $2, $3, $4::vector,
                                to_tsvector('russian', $2),
                                $5, NULL, NULL
                            )
                            ON CONFLICT (content_hash) DO UPDATE
                                SET source_count = rag_chunks.source_count + 1
                            RETURNING id
                            """,
                            document_id, content, chash, to_pgvector(emb), idx,
                        )
                        chunk_ids[chash] = str(row["id"])
        else:
            # All chunks already exist — update source_count for existing ones
            async with db.pool().acquire() as conn:
                await conn.execute(
                    """UPDATE rag_chunks SET source_count = source_count + 1
                       WHERE content_hash = ANY($1)""",
                    chunk_hashes,
                )

        # ── 5. Process images (PDF only) ───────────────────────────────────
        image_count = 0

        for img_data in raw_images_data:
            sha = img_data["sha256"]
            img_bytes = img_data["bytes"]
            mime = img_data["mime"]
            page_num = img_data["page_num"]
            surrounding = img_data["surrounding_text"]

            # Check if image already stored (dedup)
            async with db.pool().acquire() as conn:
                existing_img = await conn.fetchrow(
                    "SELECT id FROM rag_images WHERE sha256 = $1", sha
                )

            if existing_img:
                img_id = str(existing_img["id"])
            else:
                # Save to disk
                ext = mime.split("/")[-1]
                img_path = IMAGES_DIR / f"{sha}.{ext}"
                img_path.write_bytes(img_bytes)

                # Generate caption (cheap LLM call)
                caption = await _caption_image(img_bytes, surrounding)

                # Embed caption (not the image itself — key cost optimization)
                cap_embeddings = await embed_texts([caption])
                cap_emb = cap_embeddings[0] if cap_embeddings else None

                async with db.pool().acquire() as conn:
                    row = await conn.fetchrow(
                        """INSERT INTO rag_images
                               (document_id, sha256, file_path, mime_type,
                                caption, embedding, page_num)
                           VALUES ($1, $2, $3, $4, $5, $6, $7)
                           ON CONFLICT (sha256) DO UPDATE
                               SET caption = EXCLUDED.caption
                           RETURNING id""",
                        document_id, sha, str(img_path), mime,
                        caption, to_pgvector(cap_emb), page_num,
                    )
                    img_id = str(row["id"])

            # Link image to nearby chunks (same page heuristic)
            for _, content, chash in (new_chunks or [(None, None, h) for h in chunk_hashes]):
                if chash in chunk_ids:
                    try:
                        async with db.pool().acquire() as conn:
                            await conn.execute(
                                """INSERT INTO rag_chunk_images (chunk_id, image_id)
                                   VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                                chunk_ids[chash], img_id,
                            )
                    except Exception:
                        pass
            image_count += 1

        # ── 6. Finalise document record ────────────────────────────────────
        async with db.pool().acquire() as conn:
            await conn.execute(
                """UPDATE rag_documents
                   SET status = 'ready', chunk_count = $1
                   WHERE id = $2""",
                len(raw_chunks), document_id,
            )

        elapsed = round(time.monotonic() - start, 2)
        print(f"[rag] ingested {filename}: {len(raw_chunks)} chunks, "
              f"{image_count} images in {elapsed}s")

        return {"chunk_count": len(raw_chunks), "image_count": image_count, "error": None}

    except Exception as e:
        err = str(e)[:300]
        await _set_status("error", err)
        print(f"[rag] ingestion error for {filename}: {err}")
        return {"chunk_count": 0, "image_count": 0, "error": err}


# ── Hybrid retrieval ───────────────────────────────────────────────────────────

async def retrieve(
    query: str,
    course_id: str,
    top_k: int = TOP_K,
) -> tuple[list[dict], list[dict]]:
    """
    Hybrid search: cosine_similarity * HYBRID_ALPHA + BM25 * (1 - HYBRID_ALPHA).
    Returns (chunks, images) where images are linked to top chunks.
    """
    q_vec = to_pgvector(await embed_query(query))

    async with db.pool().acquire() as conn:
        # Get all document IDs in this course + globally public materials
        doc_ids = await conn.fetch(
            """
            SELECT id
            FROM rag_documents
            WHERE status = 'ready'
              AND (course_id = $1 OR is_public = TRUE)
            """,
            course_id,
        )
        if not doc_ids:
            return [], []

        doc_id_list = [str(r["id"]) for r in doc_ids]

        # Hybrid search: cosine + BM25
        rows = await conn.fetch(
            """
            WITH semantic AS (
                SELECT
                    id,
                    content,
                    content_hash,
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
                    ($5 * s.cos_sim + $6 * COALESCE(b.bm25_score, 0)) AS score
                FROM semantic s
                LEFT JOIN bm25 b ON s.id = b.id
            )
            SELECT id, content, content_hash, score
            FROM combined
            ORDER BY score DESC
            LIMIT $3
            """,
            q_vec,
            doc_id_list,
            top_k,
            query,
            HYBRID_ALPHA,
            1.0 - HYBRID_ALPHA,
        )

        chunks = [
            {
                "id": str(r["id"]),
                "content": r["content"],
                "content_hash": r["content_hash"],
                "score": float(r["score"]),
            }
            for r in rows
        ]

        if not chunks:
            return [], []

        # Retrieve images linked to top chunks
        chunk_ids = [c["id"] for c in chunks]
        img_rows = await conn.fetch(
            """
            SELECT DISTINCT ri.id, ri.file_path, ri.caption, ri.mime_type
            FROM rag_chunk_images rci
            JOIN rag_images ri ON ri.id = rci.image_id
            WHERE rci.chunk_id = ANY($1::uuid[])
            LIMIT $2
            """,
            chunk_ids,
            IMAGE_CTX_LIMIT,
        )

        images = [
            {
                "id": str(r["id"]),
                "file_path": r["file_path"],
                "caption": r["caption"],
                "mime_type": r["mime_type"],
            }
            for r in img_rows
        ]

    return chunks, images


# ── Context builder ────────────────────────────────────────────────────────────

def build_context(chunks: list[dict], images: list[dict]) -> str:
    """
    Construct minimal context string to send to LLM.
    Keeps total size under MAX_CTX_TOKENS.
    Images are represented only by their captions (never raw bytes).
    """
    parts: list[str] = []
    total_tokens = 0

    for i, chunk in enumerate(chunks, 1):
        chunk_text = chunk["content"]
        chunk_tokens = _rough_token_count(chunk_text)

        if total_tokens + chunk_tokens > MAX_CTX_TOKENS:
            # Truncate last chunk to fit
            remaining = MAX_CTX_TOKENS - total_tokens
            chunk_text = chunk_text[: remaining * 4]  # ~4 chars/token
            if chunk_text:
                parts.append(f"[Material {i}]\n{chunk_text}")
            break

        parts.append(f"[Material {i}]\n{chunk_text}")
        total_tokens += chunk_tokens

    for img in images:
        caption_line = f"[Figure: {img['caption']}]"
        parts.append(caption_line)

    return "\n\n".join(parts)


# ── Answer cache ───────────────────────────────────────────────────────────────

def _answer_cache_key(query: str, chunks: list[dict]) -> str:
    """Key is hash(query + sorted chunk content hashes)."""
    sorted_hashes = sorted(c["content_hash"] for c in chunks)
    raw = query + "|" + "|".join(sorted_hashes)
    return _sha256(raw)


async def _get_answer_cache(key: str) -> dict | None:
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT answer, image_ids FROM rag_answer_cache WHERE cache_key = $1",
            key,
        )
        if row:
            # Update usage stats (fire and forget)
            asyncio.create_task(conn.execute(
                """UPDATE rag_answer_cache
                   SET hit_count = hit_count + 1, last_used = now()
                   WHERE cache_key = $1""",
                key,
            ))
            return {"answer": row["answer"], "image_ids": json.loads(row["image_ids"])}
    return None


async def _set_answer_cache(key: str, answer: str, images: list[dict]) -> None:
    image_ids = [img["id"] for img in images]
    try:
        async with db.pool().acquire() as conn:
            await conn.execute(
                """INSERT INTO rag_answer_cache (cache_key, answer, image_ids)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (cache_key) DO UPDATE
                       SET hit_count = rag_answer_cache.hit_count + 1,
                           last_used = now()""",
                key, answer, json.dumps(image_ids),
            )
    except Exception as e:
        print(f"[rag] answer cache write failed: {e}")


# ── Main query entry point ─────────────────────────────────────────────────────

async def rag_query(
    *,
    query: str,
    course_id: str,
    system_prompt: str,
    model_name: str = "gemini-2.0-flash",
    conversation_history: list[dict] | None = None,
) -> dict:
    """
    Full RAG query pipeline.
    Returns:
        {
          answer:        str,
          chunks:        list of retrieved chunks (for citation UI),
          images:        list of image dicts to display in UI,
          from_cache:    bool,
          sources_found: bool,  -- False if answer is from general knowledge
        }
    """
    started = time.perf_counter()
    # ── 1. Retrieve relevant chunks ────────────────────────────────────────
    chunks, images = await retrieve(query, course_id)
    query_tokens = _rough_token_count(query)

    # ── 2. Check answer cache ──────────────────────────────────────────────
    cache_key = _answer_cache_key(query, chunks)
    cached = await _get_answer_cache(cache_key)
    if cached:
        # Resolve image objects from cached IDs
        cached_images = await _resolve_images(cached["image_ids"])
        context_tokens = _rough_token_count(build_context(chunks, cached_images)) if chunks else 0
        output_tokens = _rough_token_count(cached["answer"])
        actual_with_rag = query_tokens + context_tokens + output_tokens
        estimated_without_rag = query_tokens + max(context_tokens * 2, context_tokens + 500)
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "answer":        cached["answer"],
            "chunks":        chunks,
            "images":        cached_images,
            "from_cache":    True,
            "sources_found": len(chunks) > 0,
            "query_tokens": query_tokens,
            "context_tokens": context_tokens,
            "response_tokens": output_tokens,
            "actual_with_rag_tokens": actual_with_rag,
            "estimated_without_rag_tokens": estimated_without_rag,
            "chunks_used": len(chunks),
            "latency_ms": latency_ms,
        }

    # ── 3. Build minimal context ───────────────────────────────────────────
    sources_found = len(chunks) > 0
    context = build_context(chunks, images) if sources_found else ""

    # ── 4. Build prompt ────────────────────────────────────────────────────
    if sources_found:
        rag_instruction = (
            "Below are relevant excerpts from the user's course materials. "
            "Base your answer primarily on these materials. "
            "If the materials don't fully cover the question, supplement with "
            "your general knowledge and clearly note: «(из общих знаний)».\n\n"
            f"COURSE MATERIALS:\n{context}\n\n"
            "ANSWER FORMAT: Use the same language as the user's question. "
            "Cite [Material N] inline where relevant. "
            "If a [Figure: ...] caption is present, mention it at the end of your answer."
        )
    else:
        rag_instruction = (
            "No relevant excerpts were found in the user's course materials. "
            "Answer using your general knowledge and note at the end: "
            "«(Источники курса не найдены — ответ основан на общих знаниях)»."
        )

    full_system = f"{system_prompt}\n\n{rag_instruction}"

    # ── 5. Call LLM ────────────────────────────────────────────────────────
    model = genai.GenerativeModel(
        model_name,
        system_instruction=full_system,
    )

    history = conversation_history or []
    chat = model.start_chat(history=history)

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        _executor,
        lambda: chat.send_message(query),
    )
    answer = response.text
    context_tokens = _rough_token_count(context) if sources_found else 0
    output_tokens = _rough_token_count(answer)
    actual_with_rag = query_tokens + context_tokens + output_tokens
    estimated_without_rag = query_tokens + max(context_tokens * 2, context_tokens + 500)
    latency_ms = int((time.perf_counter() - started) * 1000)

    # ── 6. Cache answer ────────────────────────────────────────────────────
    asyncio.create_task(_set_answer_cache(cache_key, answer, images))

    return {
        "answer":        answer,
        "chunks":        chunks,
        "images":        images,
        "from_cache":    False,
        "sources_found": sources_found,
        "query_tokens": query_tokens,
        "context_tokens": context_tokens,
        "response_tokens": output_tokens,
        "actual_with_rag_tokens": actual_with_rag,
        "estimated_without_rag_tokens": estimated_without_rag,
        "chunks_used": len(chunks),
        "latency_ms": latency_ms,
    }


async def _resolve_images(image_ids: list[str]) -> list[dict]:
    if not image_ids:
        return []
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, file_path, caption, mime_type FROM rag_images WHERE id = ANY($1::uuid[])",
            image_ids,
        )
    return [
        {"id": str(r["id"]), "file_path": r["file_path"],
         "caption": r["caption"], "mime_type": r["mime_type"]}
        for r in rows
    ]


# ── Course helpers (used by API layer) ────────────────────────────────────────

async def get_user_courses(user_id: int) -> list[dict]:
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, title, description, scope, created_at,
                      (SELECT COUNT(*) FROM rag_documents d
                       WHERE d.course_id = courses.id AND d.status = 'ready') AS doc_count,
                      (SELECT COALESCE(SUM(chunk_count),0) FROM rag_documents d
                       WHERE d.course_id = courses.id AND d.status = 'ready') AS chunk_count
               FROM courses WHERE user_id = $1 ORDER BY updated_at DESC""",
            user_id,
        )
    return [dict(r) for r in rows]


async def get_course_documents(course_id: str, user_id: int) -> list[dict]:
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, filename, source_type, source_ref,
                      status, error_msg, chunk_count, is_public, created_at
               FROM rag_documents
               WHERE course_id = $1 AND user_id = $2
               ORDER BY created_at DESC""",
            course_id, user_id,
        )
    return [dict(r) for r in rows]


async def delete_course(course_id: str, user_id: int) -> bool:
    """Cascade deletes documents, chunks (if orphaned), images."""
    async with db.pool().acquire() as conn:
        result = await conn.execute(
            "DELETE FROM courses WHERE id = $1 AND user_id = $2",
            course_id, user_id,
        )
    return result != "DELETE 0"


async def cleanup_answer_cache(max_age_days: int = 7) -> None:
    """Evict old unused cache entries. Call from analytics cleanup_loop."""
    async with db.pool().acquire() as conn:
        result = await conn.execute(
            """DELETE FROM rag_answer_cache
               WHERE last_used < now() - ($1 || ' days')::interval""",
            str(max_age_days),
        )
    print(f"[rag] answer cache cleanup: {result}")


async def ensure_chat_course_and_ingest_uploads(
    *,
    chat_id: str,
    user_id: int,
    file_refs: list[dict],
) -> str | None:
    """
    Ensure chat has a linked private course and ingest uploaded files into it
    so attached files can participate in RAG retrieval.
    """
    if not file_refs:
        return None
    chat = await db.get_chat(chat_id, user_id)
    if not chat:
        return None
    course_id = chat.get("course_id")
    if not course_id:
        async with db.pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO courses (user_id, title, description, scope)
                VALUES ($1, $2, $3, 'private')
                RETURNING id
                """,
                user_id, f"Chat files {chat_id[:8]}", "Auto-generated course for chat file RAG",
            )
            course_id = str(row["id"])
        await db.update_chat_settings(chat_id, user_id, course_id=course_id)

    for f in file_refs:
        sha = f.get("sha256")
        if not sha:
            continue
        async with db.pool().acquire() as conn:
            dup = await conn.fetchrow(
                "SELECT id, status FROM rag_documents WHERE course_id=$1 AND sha256=$2",
                course_id, sha,
            )
        if dup and dup["status"] == "ready":
            continue
        meta = await db.get_file_meta(sha)
        if not meta:
            continue
        ext = (f.get("original_filename", "") or "").lower()
        if meta["mime_type"] == "application/pdf" or ext.endswith(".pdf"):
            source_type = "pdf"
        elif "markdown" in (meta["mime_type"] or "") or ext.endswith(".md"):
            source_type = "md"
        elif "wordprocessingml" in (meta["mime_type"] or "") or ext.endswith((".doc", ".docx")):
            source_type = "docx"
        else:
            source_type = "txt"
        raw = storage.read_file(meta["sha256"], meta["compressed"])
        async with db.pool().acquire() as conn:
            doc = await conn.fetchrow(
                """
                INSERT INTO rag_documents
                    (course_id, user_id, filename, source_type, source_ref, sha256, status, is_public)
                VALUES ($1, $2, $3, $4, $5, $6, 'pending', FALSE)
                RETURNING id
                """,
                course_id, user_id, f.get("original_filename") or "uploaded_file", source_type,
                f.get("original_filename") or "uploaded_file", sha,
            )
        await ingest_document(
            document_id=str(doc["id"]),
            course_id=str(course_id),
            user_id=user_id,
            filename=f.get("original_filename") or "uploaded_file",
            source_type=source_type,
            raw_bytes=raw,
        )
    return str(course_id)

def to_pgvector(vec: list[float]) -> str:
    if len(vec) != EMBED_DIM:
        raise ValueError(f"Embedding dimension mismatch: expected {EMBED_DIM}, got {len(vec)}")
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"