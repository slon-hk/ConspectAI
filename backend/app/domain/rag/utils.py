"""Pure RAG domain helpers.

These helpers are intentionally independent from async I/O, FastAPI, database
connections, and external AI clients so they can be reused by services,
repositories, and workers without importing legacy orchestration code.
"""

from __future__ import annotations

import hashlib
import re

DEFAULT_EMBED_DIM = 1536
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 80


def rough_token_count(text: str) -> int:
    """Fast approximation for mixed RU/EN text.

    UTF-8 byte length divided by 6 gives a better estimate than char-count/4
    for Cyrillic (2 bytes/char) while remaining accurate for ASCII.
    """
    return max(1, len(text.encode("utf-8")) // 6)


def split_text(
    text: str,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks without breaking sentences mid-way."""
    sentences = re.split(r"(?<=[.!?\n])\s+", text.strip())
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sentence_tokens = rough_token_count(sentence)
        if current_tokens + sentence_tokens > size and current:
            chunks.append(" ".join(current))

            tail_tokens = 0
            tail: list[str] = []
            for previous_sentence in reversed(current):
                previous_tokens = rough_token_count(previous_sentence)
                if tail_tokens + previous_tokens <= overlap:
                    tail.insert(0, previous_sentence)
                    tail_tokens += previous_tokens
                else:
                    break
            current = tail
            current_tokens = tail_tokens

        current.append(sentence)
        current_tokens += sentence_tokens

    if current:
        chunks.append(" ".join(current))

    return chunks


def sha256_digest(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def to_pgvector(vec: list[float], expected_dim: int = DEFAULT_EMBED_DIM) -> str:
    if len(vec) != expected_dim:
        raise ValueError(
            f"Embedding dimension mismatch: expected {expected_dim}, got {len(vec)}"
        )
    return "[" + ",".join(f"{value:.6f}" for value in vec) + "]"
