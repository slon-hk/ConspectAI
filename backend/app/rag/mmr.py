"""MMR (Maximal Marginal Relevance) diversifier for retrieved chunks.

Scores: lambda * sim(q, d) - (1 - lambda) * max_sim(d, already_selected)
O(k² * dim) but k ≤ 10, so negligible latency.
"""

from __future__ import annotations

import math
from typing import Any


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class MMRDiversifier:
    """Select top_k chunks from candidates to maximise relevance + diversity.

    Args:
        lambda_: Trade-off between relevance (1.0) and diversity (0.0).
    """

    def __init__(self, lambda_: float = 0.7) -> None:
        self._lambda = lambda_

    def diversify(
        self,
        query_vec: list[float],
        candidates: list[dict],
        *,
        top_k: int,
        score_key: str = "score",
        vec_key: str = "embedding",
    ) -> list[dict]:
        """Return up to top_k chunks, balancing relevance and diversity.

        Candidates without an embedding vector fall back to relevance-only selection.
        """
        if not candidates:
            return []
        if self._lambda >= 1.0 or not any(vec_key in c for c in candidates):
            return candidates[:top_k]

        remaining = list(candidates)
        selected: list[dict] = []
        selected_vecs: list[list[float]] = []

        while remaining and len(selected) < top_k:
            best_idx = -1
            best_score = float("-inf")

            for i, chunk in enumerate(remaining):
                relevance = float(chunk.get(score_key, 0.0))
                chunk_vec = chunk.get(vec_key)
                if chunk_vec and selected_vecs:
                    diversity_penalty = max(
                        _cosine(chunk_vec, sv) for sv in selected_vecs
                    )
                else:
                    diversity_penalty = 0.0

                mmr_score = self._lambda * relevance - (1 - self._lambda) * diversity_penalty
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = i

            if best_idx < 0:
                break
            chosen = remaining.pop(best_idx)
            selected.append(chosen)
            vec = chosen.get(vec_key)
            if vec:
                selected_vecs.append(vec)

        return selected
