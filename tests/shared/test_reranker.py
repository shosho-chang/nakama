"""Tests for shared.reranker (ADR-020 S6 bge-reranker-large wrapper)."""

from __future__ import annotations

from shared.reranker import RankedResult, rerank


def _make_candidates(n: int) -> list[RankedResult]:
    return [RankedResult(chunk_id=f"c{i}", text=f"Text chunk {i}", score=0.5) for i in range(n)]


# ---------------------------------------------------------------------------
# rerank — top_n
# ---------------------------------------------------------------------------


def test_rerank_returns_top_n():
    candidates = _make_candidates(10)
    results = rerank("query", candidates, top_n=3, _score_fn=lambda q, t: 1.0)
    assert len(results) == 3


def test_rerank_fewer_candidates_than_top_n():
    candidates = _make_candidates(2)
    results = rerank("query", candidates, top_n=5, _score_fn=lambda q, t: 1.0)
    assert len(results) == 2


def test_rerank_empty_candidates():
    results = rerank("query", [], top_n=5, _score_fn=lambda q, t: 1.0)
    assert results == []


def test_rerank_orders_by_score_descending():
    candidates = [
        RankedResult("c1", "short", 0.5),
        RankedResult("c2", "medium length text", 0.5),
        RankedResult("c3", "the longest text in this set by far", 0.5),
    ]
    results = rerank("query", candidates, top_n=3, _score_fn=lambda q, t: len(t))
    assert results[0].chunk_id == "c3"
    assert results[-1].chunk_id == "c1"


def test_rerank_scores_updated():
    candidates = [RankedResult("c1", "text", 0.5)]
    results = rerank("query", candidates, top_n=1, _score_fn=lambda q, t: 0.99)
    assert results[0].score == 0.99


def test_rerank_uses_query_in_score_fn():
    calls = []
    candidates = [RankedResult("c1", "text about ATP", 0.5)]

    def score_fn(query, text):
        calls.append((query, text))
        return 0.8

    rerank("ATP metabolism", candidates, top_n=1, _score_fn=score_fn)
    assert calls[0][0] == "ATP metabolism"
    assert calls[0][1] == "text about ATP"


def test_rerank_result_type():
    candidates = [RankedResult("c1", "text", 0.5)]
    results = rerank("query", candidates, top_n=1, _score_fn=lambda q, t: 1.0)
    assert all(isinstance(r, RankedResult) for r in results)


# ---------------------------------------------------------------------------
# RankedResult dataclass
# ---------------------------------------------------------------------------


def test_ranked_result_fields():
    r = RankedResult(chunk_id="abc", text="hello", score=0.75)
    assert r.chunk_id == "abc"
    assert r.text == "hello"
    assert r.score == 0.75
