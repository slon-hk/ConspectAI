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
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import asyncpg
import google.generativeai as genai

from app.infrastructure.storage import FileStorage
from app.domain.rag import (
    RAG_SCHEMA,
    rough_token_count,
    sha256_digest,
    split_text,
    to_pgvector,
)
from app.repositories.oltp import (
    ChatRepository,
    FileRepository,
    RagCacheRepository,
    RagIngestionRepository,
    RagRetrievalRepository,
    RagRouteRepository,
)
from app.rag.cache_manager import configure_rag_cache, get_cache_manager
from app.rag.context import ContextBuilder, HeuristicReranker, ImportanceScorer

_reranker = HeuristicReranker()
_context_builder = ContextBuilder()
_importance_scorer = ImportanceScorer()

_chat_repository = ChatRepository()
_file_repository = FileRepository()
_rag_cache_repository = RagCacheRepository()
_rag_ingestion_repository = RagIngestionRepository()
_rag_retrieval_repository = RagRetrievalRepository()
_rag_routes_repository = RagRouteRepository()
_file_storage = FileStorage()

# ── Configuration ─────────────────────────────────────────────────────────────

EMBED_MODEL     = "models/gemini-embedding-2-preview"  # Gemini, $0.025/1M tokens
EMBED_DIM       = 1536  # reduced to fit pgvector index limit (<=2000)
CAPTION_MODEL   = "gemini-2.0-flash-lite"
CHUNK_SIZE      = 500   # target tokens per chunk
CHUNK_OVERLAP   = 80    # overlap tokens between chunks
TOP_K           = 10    # candidates fetched from DB (reranker selects final 5)
TOP_K_FINAL     = 5     # chunks passed to context builder after reranking
HYBRID_ALPHA    = 0.70  # weight for cosine vs BM25 (0.7 cosine + 0.3 BM25)
MAX_CTX_TOKENS  = 3000  # hard limit on context sent to LLM
IMAGE_CTX_LIMIT = 3     # max images surfaced per answer

IMAGES_DIR = Path(os.getenv("UPLOADS_DIR", "uploads")) / "rag_images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

_executor = ThreadPoolExecutor(max_workers=2)  # for blocking PDF/file ops

# Compatibility aliases for legacy imports.
_rough_token_count = rough_token_count
_sha256 = sha256_digest


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
    """Embed a single query string with three-layer caching (L1→L2→L3)."""
    q_hash = _sha256(query)
    mgr = get_cache_manager()

    cached = await mgr.get_query_embedding(q_hash)
    if cached is not None:
        return cached

    # Full miss → embed via Gemini
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(
        _executor,
        lambda: genai.embed_content(
            model=EMBED_MODEL,
            content=query,
            task_type="RETRIEVAL_QUERY",
        ),
    )
    vec = resp["embedding"][:EMBED_DIM]

    # Write to L1+L2 (fire and forget), L3 (PG) separately
    pgvec = to_pgvector(vec)
    asyncio.create_task(mgr.store_query_embedding(q_hash, vec, pgvec))
    asyncio.create_task(_store_query_cache(q_hash, pgvec))
    return vec


async def _store_query_cache(q_hash: str, embedding_pgvector: str) -> None:
    try:
        await _rag_cache_repository.store_query_embedding(
            query_hash=q_hash,
            embedding_pgvector=embedding_pgvector,
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
        await _rag_ingestion_repository.set_document_status(
            document_id=document_id,
            status=status,
            error_msg=msg or None,
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
        existing_map = await _rag_ingestion_repository.find_existing_chunks(
            content_hashes=chunk_hashes,
        )

        new_chunks = [
            (i, c, h) for i, (c, h) in enumerate(zip(raw_chunks, chunk_hashes))
            if h not in existing_map
        ]

        # ── 4. Embed only NEW chunks (never re-embed existing) ─────────────
        chunk_ids: dict[str, str] = dict(existing_map)  # hash → id

        if new_chunks:
            texts_to_embed = [c for _, c, _ in new_chunks]
            embeddings = await embed_texts(texts_to_embed)

            chunk_ids.update(
                await _rag_ingestion_repository.upsert_chunks(
                    document_id=document_id,
                    chunks=[
                        (
                            idx,
                            content,
                            chash,
                            to_pgvector(emb),
                            rough_token_count(content),
                            _importance_scorer.score(content, idx, 1),
                        )
                        for (idx, content, chash), emb in zip(new_chunks, embeddings)
                    ],
                )
            )
        else:
            # All chunks already exist — update source_count for existing ones
            await _rag_ingestion_repository.increment_chunk_sources(content_hashes=chunk_hashes)

        # ── 5. Process images (PDF only) ───────────────────────────────────
        image_count = 0

        for img_data in raw_images_data:
            sha = img_data["sha256"]
            img_bytes = img_data["bytes"]
            mime = img_data["mime"]
            page_num = img_data["page_num"]
            surrounding = img_data["surrounding_text"]

            # Check if image already stored (dedup)
            existing_img_id = await _rag_ingestion_repository.get_image_id_by_sha(sha256=sha)
            if existing_img_id:
                img_id = existing_img_id
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

                img_id = await _rag_ingestion_repository.upsert_image(
                    document_id=document_id,
                    sha256=sha,
                    file_path=str(img_path),
                    mime_type=mime,
                    caption=caption,
                    embedding_pgvector=to_pgvector(cap_emb),
                    page_num=page_num,
                )

            # Link image to nearby chunks (same page heuristic)
            for _, content, chash in (new_chunks or [(None, None, h) for h in chunk_hashes]):
                if chash in chunk_ids:
                    try:
                        await _rag_ingestion_repository.link_chunk_image(
                            chunk_id=chunk_ids[chash],
                            image_id=img_id,
                        )
                    except Exception:
                        pass
            image_count += 1

        # ── 6. Finalise document record ────────────────────────────────────
        await _rag_ingestion_repository.mark_document_ready(
            document_id=document_id,
            chunk_count=len(raw_chunks),
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
    Retrieval results are cached in Redis for 5 minutes.
    """
    q_vec_list = await embed_query(query)
    q_hash = _sha256(query)
    mgr = get_cache_manager()

    # Check retrieval cache (L2 Redis, TTL=5m)
    cached_ret = await mgr.get_retrieval_result(course_id, q_hash)
    if cached_ret is not None:
        return cached_ret

    q_vec = to_pgvector(q_vec_list)
    chunks, images = await _rag_retrieval_repository.retrieve_chunks_and_images(
        course_id=course_id,
        query=query,
        query_embedding_pgvector=q_vec,
        top_k=top_k,
        hybrid_alpha=HYBRID_ALPHA,
        image_ctx_limit=IMAGE_CTX_LIMIT,
    )

    # Cache result for 5 minutes (fire and forget)
    asyncio.create_task(mgr.set_retrieval_result(course_id, q_hash, chunks, images))
    return chunks, images



# ── Answer cache ───────────────────────────────────────────────────────────────

def _answer_cache_key(query: str, chunks: list[dict]) -> str:
    """Key is hash(query + sorted chunk content hashes)."""
    sorted_hashes = sorted(c["content_hash"] for c in chunks)
    raw = query + "|" + "|".join(sorted_hashes)
    return _sha256(raw)


async def _get_answer_cache(key: str) -> dict | None:
    mgr = get_cache_manager()

    # L1 + L2 check first (fast path, no DB)
    l1l2 = await mgr.get_answer_l1l2(key)
    if l1l2:
        return l1l2

    # L3: PostgreSQL
    row = await _rag_cache_repository.get_answer_cache(cache_key=key)
    if row:
        asyncio.create_task(_rag_cache_repository.touch_answer_cache(cache_key=key))
        data = {"answer": row["answer"], "image_ids": json.loads(row["image_ids"])}
        # Promote to L1+L2 for next request
        asyncio.create_task(mgr.set_answer_l1l2(key, data))
        return data
    return None


async def _set_answer_cache(key: str, answer: str, images: list[dict]) -> None:
    image_ids = [img["id"] for img in images]
    data = {"answer": answer, "image_ids": image_ids}
    mgr = get_cache_manager()
    try:
        # Write L1+L2 and L3 concurrently
        await asyncio.gather(
            mgr.set_answer_l1l2(key, data),
            _rag_cache_repository.set_answer_cache(
                cache_key=key,
                answer=answer,
                image_ids_json=json.dumps(image_ids),
            ),
            return_exceptions=True,
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
    # ── 1. Retrieve candidates (TOP_K=10) ──────────────────────────────────
    chunks_raw, images = await retrieve(query, course_id)
    query_tokens = rough_token_count(query)

    # ── 2. Rerank → top TOP_K_FINAL (5) ───────────────────────────────────
    chunks = _reranker.rerank(query, chunks_raw, top_k=TOP_K_FINAL) if chunks_raw else []

    # ── 3. Check answer cache ──────────────────────────────────────────────
    cache_key = _answer_cache_key(query, chunks)
    cached = await _get_answer_cache(cache_key)
    if cached:
        cached_images = await _resolve_images(cached["image_ids"])
        context_str, context_tokens, _ = _context_builder.build(chunks, cached_images)
        output_tokens = rough_token_count(cached["answer"])
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

    # ── 4. Build dynamic context ───────────────────────────────────────────
    sources_found = len(chunks) > 0
    if sources_found:
        context, context_tokens, _ctx_stats = _context_builder.build(chunks, images)
    else:
        context, context_tokens = "", 0

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
    output_tokens = rough_token_count(answer)
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
    return await _rag_cache_repository.resolve_images(image_ids=image_ids)


# ── Course helpers (used by API layer) ────────────────────────────────────────

async def get_user_courses(user_id: int) -> list[dict]:
    return await _rag_routes_repository.list_user_courses(user_id=user_id)


async def get_course_documents(course_id: str, user_id: int) -> list[dict]:
    return await _rag_routes_repository.list_course_documents(course_id=course_id, user_id=user_id)


async def delete_course(course_id: str, user_id: int) -> bool:
    """Cascade deletes documents, chunks (if orphaned), images."""
    return await _rag_routes_repository.delete_course(course_id=course_id, user_id=user_id)


async def cleanup_answer_cache(max_age_days: int = 7) -> None:
    """Evict old unused cache entries. Call from analytics cleanup_loop."""
    result = await _rag_cache_repository.cleanup_answer_cache(max_age_days=max_age_days)
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
    chat = await _chat_repository.get(chat_id, user_id)
    if not chat:
        return None
    course_id = chat.get("course_id")
    if not course_id:
        course = await _rag_routes_repository.create_course(
            user_id=user_id,
            title=f"Chat files {chat_id[:8]}",
            description="Auto-generated course for chat file RAG",
            scope="private",
        )
        course_id = str(course["id"])
        await _chat_repository.update_settings(chat_id, user_id, course_id=course_id)

    for f in file_refs:
        sha = f.get("sha256")
        if not sha:
            continue
        dup = await _rag_routes_repository.find_document_duplicate(course_id=course_id, sha256=sha)
        if dup and dup["status"] == "ready":
            continue
        meta = await _file_repository.get(sha)
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
        raw = _file_storage.read_file(meta["sha256"], meta["compressed"])
        filename = f.get("original_filename") or "uploaded_file"
        document_id = await _rag_routes_repository.create_file_document(
            course_id=str(course_id),
            user_id=user_id,
            filename=filename,
            source_type=source_type,
            source_ref=filename,
            sha256=sha,
            is_public=False,
        )
        await ingest_document(
            document_id=document_id,
            course_id=str(course_id),
            user_id=user_id,
            filename=filename,
            source_type=source_type,
            raw_bytes=raw,
        )
    return str(course_id)
