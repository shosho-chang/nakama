"""BGE-reranker-large cross-encoder wrapper for ADR-020 S6.

rerank(query, candidates, *, top_n, _score_fn):
  Re-scores a candidate list with a cross-encoder and returns the top_n
  results sorted by score descending.

  Production path uses bge-reranker-large (FlagEmbedding).
  Tests inject ``_score_fn`` to avoid loading a large model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from shared.log import get_logger

logger = get_logger("nakama.shared.reranker")


@dataclass
class RankedResult:
    """A candidate chunk with its reranker score."""

    chunk_id: str
    text: str
    score: float


def rerank(
    query: str,
    candidates: list[RankedResult],
    *,
    top_n: int,
    _score_fn: Callable[[str, str], float] | None = None,
) -> list[RankedResult]:
    """Re-rank candidates with a cross-encoder and return top_n by score.

    Args:
        query:       User query string.
        candidates:  Candidate chunks to re-rank.
        top_n:       Maximum number of results to return.
        _score_fn:   Optional scoring function ``(query, text) -> float``.
                     If None, uses the bge-reranker-large model.

    Returns:
        List of at most ``top_n`` RankedResult objects sorted by score
        descending, with scores updated to the reranker output.
    """
    if not candidates:
        return []

    score_fn = _score_fn if _score_fn is not None else _bge_score_fn()

    scored = [
        RankedResult(chunk_id=c.chunk_id, text=c.text, score=score_fn(query, c.text))
        for c in candidates
    ]
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:top_n]


def _bge_score_fn() -> Callable[[str, str], float]:
    """Lazy-load bge-reranker-large and return a scoring callable."""
    try:
        from FlagEmbedding import FlagReranker  # type: ignore[import]

        model = FlagReranker("BAAI/bge-reranker-large", use_fp16=True)
        logger.info("bge-reranker-large loaded")

        def _score(query: str, text: str) -> float:
            return float(model.compute_score([[query, text]])[0])

        return _score
    except ImportError:
        logger.warning("FlagEmbedding not installed — reranker returning 0.0 for all candidates")

        def _fallback(query: str, text: str) -> float:  # noqa: ARG001
            return 0.0

        return _fallback
