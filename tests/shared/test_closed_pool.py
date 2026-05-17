"""Tests for shared.repurpose.closed_pool.closed_pool_search.

Covers ADR-027 §Decision 6 Layer 1:
- Filters out hits whose path is not in the allowed pool.
- Empty allowed_slugs returns [] defensively.
- Does NOT do transitive backlink traversal — even if a chunk inside the
  pool has a kb_wikilinks edge to an outside slug, the outside slug's
  chunks are never returned.
- Wikilink lane is never activated by this wrapper.
"""

from __future__ import annotations

import numpy as np
import pytest

from shared.kb_hybrid_search import make_conn
from shared.repurpose.closed_pool import closed_pool_search


def _insert_chunk(conn, rowid: int, chunk_text: str, path: str):
    conn.execute(
        "INSERT INTO kb_chunks(rowid, chunk_text, section, heading_context, path) "
        "VALUES (?,?,?,?,?)",
        (rowid, chunk_text, "", path.rsplit("/", 1)[-1], path),
    )


def _insert_vec(conn, rowid: int, dim: int, pos: int):
    v = np.zeros(dim, dtype=np.float32)
    v[pos % dim] = 1.0
    conn.execute(
        "INSERT INTO kb_vectors(rowid, embedding) VALUES (?,?)",
        (rowid, v.tobytes()),
    )


@pytest.fixture
def kb_with_5_chunks():
    """5 chunks across 5 distinct slugs; 3 inside the pool, 2 outside."""
    dim = 256
    conn = make_conn(":memory:", dim=dim)
    # Pool slugs (inside)
    _insert_chunk(conn, 1, "sleep is essential for recovery", "KB/Wiki/Sources/article-a")
    _insert_chunk(conn, 2, "sleep cycles in athletes recover muscle", "KB/Wiki/Sources/article-b")
    _insert_chunk(conn, 3, "interview transcript on sleep", "KB/Wiki/Sources/transcript-x")
    # Outside slugs
    _insert_chunk(conn, 4, "sleep myths debunked here", "KB/Wiki/Concepts/sleep-myths")
    _insert_chunk(conn, 5, "deep sleep architecture in elders", "KB/Wiki/Concepts/sleep-elders")
    # Mock vectors via embed patching: use unit vectors anchored at pos=0 → all close to query
    for rowid in (1, 2, 3, 4, 5):
        _insert_vec(conn, rowid, dim, pos=0)
    conn.commit()
    return conn


def _patch_embed(monkeypatch, dim: int = 256, pos: int = 0):
    from shared import kb_embedder

    def fake_embed(_q: str):
        v = np.zeros(dim, dtype=np.float32)
        v[pos] = 1.0
        return v

    monkeypatch.setattr(kb_embedder, "embed", fake_embed)


def test_closed_pool_filters_out_chunks_outside_allowed_slugs(kb_with_5_chunks, monkeypatch):
    _patch_embed(monkeypatch)
    allowed = {
        "KB/Wiki/Sources/article-a",
        "KB/Wiki/Sources/article-b",
        "KB/Wiki/Sources/transcript-x",
    }

    hits = closed_pool_search(
        "sleep",
        allowed_slugs=allowed,
        top_k=10,
        db=kb_with_5_chunks,
    )

    returned_paths = {h.path for h in hits}
    assert returned_paths.issubset(allowed)
    # Outside slugs MUST NOT appear
    assert "KB/Wiki/Concepts/sleep-myths" not in returned_paths
    assert "KB/Wiki/Concepts/sleep-elders" not in returned_paths
    # At least one inside hit returned (the BM25 corpus contains "sleep")
    assert len(hits) >= 1


def test_closed_pool_empty_allowed_returns_empty(kb_with_5_chunks, monkeypatch):
    _patch_embed(monkeypatch)
    hits = closed_pool_search("sleep", allowed_slugs=set(), db=kb_with_5_chunks)
    assert hits == []


def test_closed_pool_does_not_follow_wikilink_to_outside_slug(kb_with_5_chunks, monkeypatch):
    """Transitive-leak guard.

    Even with a wikilink edge from an inside-pool slug to an outside slug,
    the wrapper must NOT surface the outside slug's chunks. closed_pool_search
    is hard-coded to lanes=("bm25", "vec") — the wikilink lane is never
    activated.
    """
    _patch_embed(monkeypatch)
    conn = kb_with_5_chunks
    # article-a (inside) → sleep-myths (outside) backlink
    conn.execute(
        "INSERT INTO kb_wikilinks(src_path, dst_path) VALUES (?, ?)",
        ("KB/Wiki/Sources/article-a", "KB/Wiki/Concepts/sleep-myths"),
    )
    conn.execute(
        "INSERT INTO kb_wikilinks(src_path, dst_path) VALUES (?, ?)",
        ("KB/Wiki/Concepts/sleep-elders", "KB/Wiki/Sources/article-b"),
    )
    conn.commit()

    allowed = {
        "KB/Wiki/Sources/article-a",
        "KB/Wiki/Sources/article-b",
        "KB/Wiki/Sources/transcript-x",
    }
    hits = closed_pool_search("sleep", allowed_slugs=allowed, top_k=10, db=conn)
    returned_paths = {h.path for h in hits}
    assert "KB/Wiki/Concepts/sleep-myths" not in returned_paths
    assert "KB/Wiki/Concepts/sleep-elders" not in returned_paths
    assert returned_paths.issubset(allowed)


def test_closed_pool_respects_top_k(kb_with_5_chunks, monkeypatch):
    _patch_embed(monkeypatch)
    allowed = {
        "KB/Wiki/Sources/article-a",
        "KB/Wiki/Sources/article-b",
        "KB/Wiki/Sources/transcript-x",
    }
    hits = closed_pool_search("sleep", allowed_slugs=allowed, top_k=2, db=kb_with_5_chunks)
    assert len(hits) <= 2
