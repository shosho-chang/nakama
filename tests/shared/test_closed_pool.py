"""Tests for shared.repurpose.closed_pool.closed_pool_search.

Covers ADR-027 §Decision 6 Layer 1:
- Filters out hits whose path is not in the allowed pool.
- Empty allowed_slugs returns [] defensively.
- Does NOT do transitive backlink traversal — even if a chunk inside the
  pool has a kb_wikilinks edge to an outside slug, the outside slug's
  chunks are never returned.
- Wikilink lane is never activated by this wrapper.

ADR-042: retrieval is BM25 (FTS5) only — the dense-vector lane is gone, so
these tests rely on BM25 matching the chunk text directly (no embedder mock).
"""

from __future__ import annotations

import pytest

from shared.kb_hybrid_search import make_conn
from shared.repurpose.closed_pool import closed_pool_search


def _insert_chunk(conn, rowid: int, chunk_text: str, path: str):
    conn.execute(
        "INSERT INTO kb_chunks(rowid, chunk_text, section, heading_context, path) "
        "VALUES (?,?,?,?,?)",
        (rowid, chunk_text, "", path.rsplit("/", 1)[-1], path),
    )


@pytest.fixture
def kb_with_5_chunks():
    """5 chunks across 5 distinct slugs; 3 inside the pool, 2 outside."""
    conn = make_conn()
    # Pool slugs (inside)
    _insert_chunk(conn, 1, "sleep is essential for recovery", "KB/Wiki/Sources/article-a")
    _insert_chunk(conn, 2, "sleep cycles in athletes recover muscle", "KB/Wiki/Sources/article-b")
    _insert_chunk(conn, 3, "interview transcript on sleep", "KB/Wiki/Sources/transcript-x")
    # Outside slugs
    _insert_chunk(conn, 4, "sleep myths debunked here", "KB/Wiki/Concepts/sleep-myths")
    _insert_chunk(conn, 5, "deep sleep architecture in elders", "KB/Wiki/Concepts/sleep-elders")
    conn.commit()
    return conn


def test_closed_pool_filters_out_chunks_outside_allowed_slugs(kb_with_5_chunks):
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


def test_closed_pool_empty_allowed_returns_empty(kb_with_5_chunks):
    hits = closed_pool_search("sleep", allowed_slugs=set(), db=kb_with_5_chunks)
    assert hits == []


def test_closed_pool_does_not_follow_wikilink_to_outside_slug(kb_with_5_chunks):
    """Transitive-leak guard.

    Even with a wikilink edge from an inside-pool slug to an outside slug,
    the wrapper must NOT surface the outside slug's chunks. closed_pool_search
    is hard-coded to lanes=("bm25",) — the wikilink lane is never activated.
    """
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


def test_closed_pool_respects_top_k(kb_with_5_chunks):
    allowed = {
        "KB/Wiki/Sources/article-a",
        "KB/Wiki/Sources/article-b",
        "KB/Wiki/Sources/transcript-x",
    }
    hits = closed_pool_search("sleep", allowed_slugs=allowed, top_k=2, db=kb_with_5_chunks)
    assert len(hits) <= 2
