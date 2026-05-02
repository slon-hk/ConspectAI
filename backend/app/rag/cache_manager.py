"""Three-layer RAG cache: L1 (in-process LRU) → L2 (Redis) → L3 (PostgreSQL)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.infrastructure.cache import CacheClient, NullCache
from app.repositories.oltp.rag_cache import RagCacheRepository

# Populated at startup by configure_rag_cache(); falls back to NullCache if never called.
_manager: ThreeLayerCacheManager | None = None


def configure_rag_cache(redis: CacheClient, pg_cache: RagCacheRepository) -> None:
    global _manager
    _manager = ThreeLayerCacheManager(redis=redis, pg_cache=pg_cache)


def get_cache_manager() -> ThreeLayerCacheManager:
    global _manager
    if _manager is None:
        # Lazy default: no Redis, no L3 (safe for tests / early startup).
        _manager = ThreeLayerCacheManager(redis=NullCache(), pg_cache=None)  # type: ignore[arg-type]
    return _manager


def _pack(value: Any) -> bytes:
    import msgpack
    return msgpack.packb(value, use_bin_type=True)


def _unpack(raw: bytes) -> Any:
    import msgpack
    return msgpack.unpackb(raw, raw=False)


class ThreeLayerCacheManager:
    """
    Waterfall cache for the RAG pipeline.

    L1 — process-local dict (LRU eviction, no serialization overhead)
    L2 — Redis (shared across workers, TTL-managed)
    L3 — PostgreSQL (durable, managed by RagCacheRepository)
    """

    _L1_MAXSIZE = 512

    def __init__(self, redis: CacheClient, pg_cache: RagCacheRepository) -> None:
        self._redis = redis
        self._pg = pg_cache
        self._l1: dict[str, tuple[Any, float]] = {}

    # ── Query embedding ────────────────────────────────────────────────────────

    async def get_query_embedding(self, query_hash: str) -> list[float] | None:
        l1_key = f"emb:{query_hash}"

        if hit := self._l1_get(l1_key):
            return hit

        raw = await self._redis.get(f"rag:emb:q:{query_hash}")
        if raw:
            vec = _unpack(raw)
            self._l1_set(l1_key, vec, ttl=3600)
            return vec

        if self._pg is not None:
            vec = await self._pg.get_query_embedding(query_hash=query_hash)
            if vec:
                asyncio.create_task(
                    self._redis.set(f"rag:emb:q:{query_hash}", _pack(vec), ttl_seconds=86400)
                )
                self._l1_set(l1_key, vec, ttl=3600)
                return vec

        return None

    async def store_query_embedding(self, query_hash: str, embedding: list[float], pgvector_str: str) -> None:
        """Write embedding to L1 + L2; caller is responsible for L3 (PG) write."""
        self._l1_set(f"emb:{query_hash}", embedding, ttl=3600)
        try:
            await self._redis.set(f"rag:emb:q:{query_hash}", _pack(embedding), ttl_seconds=86400)
        except Exception as exc:
            print(f"[cache] L2 query-emb write failed: {exc}")

    # ── Retrieval results ──────────────────────────────────────────────────────

    async def get_retrieval_result(
        self, course_id: str, query_hash: str
    ) -> tuple[list[dict], list[dict]] | None:
        key = f"rag:ret:{course_id}:{query_hash[:20]}"
        raw = await self._redis.get(key)
        if raw:
            data = _unpack(raw)
            return data.get("chunks", []), data.get("images", [])
        return None

    async def set_retrieval_result(
        self, course_id: str, query_hash: str, chunks: list[dict], images: list[dict]
    ) -> None:
        key = f"rag:ret:{course_id}:{query_hash[:20]}"
        try:
            payload = _pack({"chunks": chunks, "images": images})
            await self._redis.set(key, payload, ttl_seconds=300)
        except Exception as exc:
            print(f"[cache] L2 retrieval write failed: {exc}")

    async def invalidate_retrieval_for_course(self, course_id: str) -> None:
        """Called when a new document is indexed into a course."""
        if hasattr(self._redis, "scan_delete_pattern"):
            try:
                deleted = await self._redis.scan_delete_pattern(f"rag:ret:{course_id}:*")
                if deleted:
                    print(f"[cache] invalidated {deleted} retrieval keys for course {course_id}")
            except Exception as exc:
                print(f"[cache] retrieval invalidation failed: {exc}")

    # ── Answer cache ───────────────────────────────────────────────────────────

    async def get_answer_l1l2(self, cache_key: str) -> dict | None:
        """Check L1 + L2 only; L3 (PG) remains the caller's responsibility."""
        l1_key = f"ans:{cache_key}"

        if hit := self._l1_get(l1_key):
            return hit

        raw = await self._redis.get(f"rag:ans:{cache_key}")
        if raw:
            data = _unpack(raw)
            self._l1_set(l1_key, data, ttl=1800)
            return data

        return None

    async def set_answer_l1l2(self, cache_key: str, data: dict) -> None:
        """Write answer to L1 + L2 (fire-and-forget for L2)."""
        self._l1_set(f"ans:{cache_key}", data, ttl=1800)
        try:
            await self._redis.set(f"rag:ans:{cache_key}", _pack(data), ttl_seconds=3600)
        except Exception as exc:
            print(f"[cache] L2 answer write failed: {exc}")

    # ── L1 helpers ─────────────────────────────────────────────────────────────

    def _l1_get(self, key: str) -> Any | None:
        entry = self._l1.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() < expires_at:
            return value
        del self._l1[key]
        return None

    def _l1_set(self, key: str, value: Any, ttl: int) -> None:
        if len(self._l1) >= self._L1_MAXSIZE:
            # Evict oldest ~10% by expiry time.
            by_expiry = sorted(self._l1.items(), key=lambda kv: kv[1][1])
            for k, _ in by_expiry[: self._L1_MAXSIZE // 10]:
                del self._l1[k]
        self._l1[key] = (value, time.monotonic() + ttl)
