"""Tests for shared/kb_hybrid_search.py.

Uses in-memory SQLite + pre-inserted chunks/vectors to verify:
  - RRF math (hand-calculated against known rankings)
  - Lane fusion (both lanes vs single lane)
  - Token budget truncation
  - Lane disable toggle
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from shared.kb_hybrid_search import _RRF_K, make_conn, search

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unit_vec(dim: int, pos: int) -> np.ndarray:
    """256-dim float32 array with 1.0 at `pos`, 0.0 elsewhere."""
    v = np.zeros(dim, dtype=np.float32)
    v[pos] = 1.0
    return v


def _insert_chunk(conn, rowid: int, chunk_text: str, section: str, page_title: str, path: str):
    conn.execute(
        "INSERT INTO kb_chunks(rowid, chunk_text, section, heading_context, path) "
        "VALUES (?,?,?,?,?)",
        (rowid, chunk_text, section, page_title, path),
    )


def _insert_vec(conn, rowid: int, emb: np.ndarray):
    conn.execute(
        "INSERT INTO kb_vectors(rowid, embedding) VALUES (?,?)",
        (rowid, emb.tobytes()),
    )


# ---------------------------------------------------------------------------
# RRF math verification
#
# Setup (3 chunks, both lanes active):
#   BM25 query "sleep": A=rank1, B=rank2, C=no match
#   vec query:          B=rank1, C=rank2, A=rank3
#
# Hand-calculated RRF (k=60):
#   A: 1/61 + 1/63  ≈ 0.032266
#   B: 1/62 + 1/61  ≈ 0.032522
#   C: 1/62         ≈ 0.016129
#
# Expected order: B > A > C
# ---------------------------------------------------------------------------


@pytest.fixture
def rrf_db():
    """In-memory DB with 3 pre-inserted chunks/vectors for RRF math tests."""
    conn = make_conn(dim=256)

    # A (rowid=1): matches "sleep sleep" BM25 query; far from query vec
    _insert_chunk(
        conn,
        1,
        "sleep sleep sleep recovery benefits",
        "Benefits",
        "Sleep",
        "KB/Wiki/Concepts/sleep",
    )
    _insert_vec(conn, 1, _unit_vec(256, 1))  # at dim 1 (far from query at dim 0)

    # B (rowid=2): matches BM25 query; closest to query vec
    _insert_chunk(
        conn,
        2,
        "sleep duration quality research",
        "Research",
        "Sleep",
        "KB/Wiki/Concepts/sleep-research",
    )
    _insert_vec(conn, 2, _unit_vec(256, 0))  # at dim 0 (closest to query)

    # C (rowid=3): does NOT match BM25; second closest to query vec
    _insert_chunk(
        conn,
        3,
        "exercise workout training zone",
        "Training",
        "Exercise",
        "KB/Wiki/Concepts/exercise",
    )
    v = np.zeros(256, dtype=np.float32)
    v[0] = 0.8  # slightly less close to query than B
    _insert_vec(conn, 3, v)

    conn.commit()
    return conn


def _query_emb_at_dim0() -> np.ndarray:
    """Query vector pointing at dim 0 → distances: B(0.0) < C(small) < A(1.0)."""
    return _unit_vec(256, 0)


def test_rrf_both_lanes_order_b_a_c(rrf_db):
    """Both lanes active → B > A > C (exact RRF math matches hand calc)."""
    with patch("shared.kb_embedder.embed", return_value=_query_emb_at_dim0()):
        hits = search("sleep sleep sleep", top_k=10, db=rrf_db)

    assert len(hits) == 3
    paths = [h.path for h in hits]
    # B must come first (highest RRF combining both lanes)
    assert paths[0] == "KB/Wiki/Concepts/sleep-research"
    # C must come last (only vec lane, rank 2)
    assert paths[-1] == "KB/Wiki/Concepts/exercise"


def test_rrf_scores_match_hand_calculation(rrf_db):
    """RRF scores for B, A, C match expected values within tolerance."""
    with patch("shared.kb_embedder.embed", return_value=_query_emb_at_dim0()):
        hits = search("sleep sleep sleep", top_k=10, db=rrf_db)

    hit_by_path = {h.path: h for h in hits}

    # B: bm25=2, vec=1 → 1/(60+2) + 1/(60+1)
    expected_b = 1.0 / (_RRF_K + 2) + 1.0 / (_RRF_K + 1)
    # A: bm25=1, vec=3 → 1/(60+1) + 1/(60+3)
    expected_a = 1.0 / (_RRF_K + 1) + 1.0 / (_RRF_K + 3)
    # C: vec=2 only → 1/(60+2)
    expected_c = 1.0 / (_RRF_K + 2)

    b = hit_by_path["KB/Wiki/Concepts/sleep-research"]
    a = hit_by_path["KB/Wiki/Concepts/sleep"]
    c = hit_by_path["KB/Wiki/Concepts/exercise"]

    assert abs(b.rrf_score - expected_b) < 1e-9
    assert abs(a.rrf_score - expected_a) < 1e-9
    assert abs(c.rrf_score - expected_c) < 1e-9


def test_rrf_lane_ranks_stored_per_hit(rrf_db):
    """Each SearchHit.lane_ranks records which lanes contributed and their rank."""
    with patch("shared.kb_embedder.embed", return_value=_query_emb_at_dim0()):
        hits = search("sleep sleep sleep", top_k=10, db=rrf_db)

    hit_by_path = {h.path: h for h in hits}

    b = hit_by_path["KB/Wiki/Concepts/sleep-research"]
    assert "bm25" in b.lane_ranks
    assert "vec" in b.lane_ranks

    c = hit_by_path["KB/Wiki/Concepts/exercise"]
    assert "bm25" not in c.lane_ranks  # C didn't match BM25
    assert "vec" in c.lane_ranks


# ---------------------------------------------------------------------------
# Lane fusion toggles
# ---------------------------------------------------------------------------


def test_bm25_only_lane(rrf_db):
    """lanes=('bm25',) → only A and B returned (C has no BM25 match for 'sleep')."""
    with patch("shared.kb_embedder.embed", return_value=_query_emb_at_dim0()):
        hits = search("sleep sleep sleep", top_k=10, lanes=("bm25",), db=rrf_db)

    paths = {h.path for h in hits}
    assert "KB/Wiki/Concepts/sleep" in paths
    assert "KB/Wiki/Concepts/sleep-research" in paths
    assert "KB/Wiki/Concepts/exercise" not in paths
    for h in hits:
        assert "bm25" in h.lane_ranks
        assert "vec" not in h.lane_ranks


def test_vec_only_lane(rrf_db):
    """lanes=('vec',) → all 3 chunks returned, no BM25 rank in lane_ranks."""
    with patch("shared.kb_embedder.embed", return_value=_query_emb_at_dim0()):
        hits = search("sleep", top_k=10, lanes=("vec",), db=rrf_db)

    assert len(hits) == 3
    for h in hits:
        assert "vec" in h.lane_ranks
        assert "bm25" not in h.lane_ranks


# ---------------------------------------------------------------------------
# top_k cutoff
# ---------------------------------------------------------------------------


def test_top_k_limits_results(rrf_db):
    """top_k=2 → at most 2 results returned."""
    with patch("shared.kb_embedder.embed", return_value=_query_emb_at_dim0()):
        hits = search("sleep sleep sleep", top_k=2, db=rrf_db)

    assert len(hits) <= 2


# ---------------------------------------------------------------------------
# Token budget truncation
# ---------------------------------------------------------------------------


def test_chunk_text_truncated_to_token_budget():
    """chunk_text in SearchHit is capped at _TOKEN_BUDGET_CHARS chars."""
    from shared.kb_hybrid_search import _TOKEN_BUDGET_CHARS

    conn = make_conn(dim=256)
    long_text = "word " * 1000  # ~5000 chars
    _insert_chunk(conn, 1, long_text, "Sec", "Title", "KB/Wiki/Concepts/long")
    _insert_vec(conn, 1, _unit_vec(256, 0))
    conn.commit()

    with patch("shared.kb_embedder.embed", return_value=_unit_vec(256, 0)):
        hits = search("word", top_k=1, lanes=("vec",), db=conn)

    assert hits
    assert len(hits[0].chunk_text) <= _TOKEN_BUDGET_CHARS


# ---------------------------------------------------------------------------
# Empty DB
# ---------------------------------------------------------------------------


def test_search_empty_db_returns_empty():
    """Querying an empty index returns []."""
    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed", return_value=_unit_vec(256, 0)):
        hits = search("anything", db=conn)
    assert hits == []


# ---------------------------------------------------------------------------
# make_conn schema check
# ---------------------------------------------------------------------------


def test_make_conn_creates_all_tables():
    """make_conn() initializes all 3 required tables."""
    conn = make_conn(dim=256)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
        ).fetchall()
    }
    assert "kb_index_meta" in tables
    # FTS5 shadow tables include the _data table
    assert any("kb_chunks" in t for t in tables)
    assert any("kb_vectors" in t for t in tables)


# ---------------------------------------------------------------------------
# Wikilink lane (issue #433 Phase 1b)
# ---------------------------------------------------------------------------


def _insert_wikilink(conn, src_path: str, dst_path: str) -> None:
    conn.execute(
        "INSERT INTO kb_wikilinks(src_path, dst_path) VALUES (?,?)",
        (src_path, dst_path),
    )


@pytest.fixture
def wikilink_db():
    """In-memory DB for wikilink lane tests.

    Structure:
      concept-a (rowid=1): BM25-matches "concept-a unique alpha"
      sources-x (rowid=2): concept-a links OUT to sources-x
      sources-y (rowid=3): concept-a links OUT to sources-y
      concept-b (rowid=4): concept-a links OUT to concept-b

    Wikilinks:
      KB/Wiki/Concepts/concept-a → KB/Wiki/Sources/sources-x
      KB/Wiki/Concepts/concept-a → KB/Wiki/Sources/sources-y
      KB/Wiki/Concepts/concept-a → KB/Wiki/Concepts/concept-b
    """
    conn = make_conn(dim=256)

    _insert_chunk(
        conn,
        1,
        "conceptalpha unique distinctive text long",
        "",
        "Concept A",
        "KB/Wiki/Concepts/concept-a",
    )
    _insert_vec(conn, 1, _unit_vec(256, 20))

    _insert_chunk(
        conn,
        2,
        "sourceresearch material content findings",
        "",
        "Source X",
        "KB/Wiki/Sources/sources-x",
    )
    _insert_vec(conn, 2, _unit_vec(256, 21))

    _insert_chunk(
        conn,
        3,
        "source y background context information",
        "",
        "Source Y",
        "KB/Wiki/Sources/sources-y",
    )
    _insert_vec(conn, 3, _unit_vec(256, 22))

    _insert_chunk(
        conn,
        4,
        "concept b related information details",
        "",
        "Concept B",
        "KB/Wiki/Concepts/concept-b",
    )
    _insert_vec(conn, 4, _unit_vec(256, 23))

    _insert_wikilink(conn, "KB/Wiki/Concepts/concept-a", "KB/Wiki/Sources/sources-x")
    _insert_wikilink(conn, "KB/Wiki/Concepts/concept-a", "KB/Wiki/Sources/sources-y")
    _insert_wikilink(conn, "KB/Wiki/Concepts/concept-a", "KB/Wiki/Concepts/concept-b")

    conn.commit()
    return conn


def test_make_conn_creates_wikilinks_table():
    """make_conn() must initialize kb_wikilinks table."""
    conn = make_conn(dim=256)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert "kb_wikilinks" in tables


def test_wikilink_lane_outgoing_links(wikilink_db):
    """BM25 hits concept-a; wikilink lane pulls in its 3 outgoing neighbors."""
    with patch("shared.kb_embedder.embed", return_value=_unit_vec(256, 99)):
        hits = search(
            "conceptalpha unique distinctive",
            top_k=10,
            lanes=("bm25", "wikilink"),
            db=wikilink_db,
        )

    paths = {h.path for h in hits}
    assert "KB/Wiki/Sources/sources-x" in paths
    assert "KB/Wiki/Sources/sources-y" in paths
    assert "KB/Wiki/Concepts/concept-b" in paths


def test_wikilink_lane_incoming_links(wikilink_db):
    """Query hits sources-x; wikilink lane finds concept-a via incoming edge."""
    with patch("shared.kb_embedder.embed", return_value=_unit_vec(256, 99)):
        hits = search(
            "sourceresearch material content",
            top_k=10,
            lanes=("bm25", "wikilink"),
            db=wikilink_db,
        )

    paths = {h.path for h in hits}
    assert "KB/Wiki/Concepts/concept-a" in paths


def test_wikilink_lane_rank_in_lane_ranks(wikilink_db):
    """Wikilink-only hits must have 'wikilink' key in lane_ranks, no BM25/vec."""
    with patch("shared.kb_embedder.embed", return_value=_unit_vec(256, 99)):
        hits = search(
            "conceptalpha unique distinctive",
            top_k=10,
            lanes=("bm25", "wikilink"),
            db=wikilink_db,
        )

    hit_by_path = {h.path: h for h in hits}
    sx = hit_by_path.get("KB/Wiki/Sources/sources-x")
    assert sx is not None, "sources-x must be in results"
    assert "wikilink" in sx.lane_ranks
    assert "bm25" not in sx.lane_ranks
    assert "vec" not in sx.lane_ranks


def test_bm25_vec_lanes_no_wikilink_regression(rrf_db):
    """lanes=('bm25','vec') without 'wikilink' — exact pre-#433 behavior, no wikilink key."""
    with patch("shared.kb_embedder.embed", return_value=_query_emb_at_dim0()):
        hits = search("sleep sleep sleep", top_k=10, lanes=("bm25", "vec"), db=rrf_db)

    assert len(hits) == 3
    paths = [h.path for h in hits]
    assert paths[0] == "KB/Wiki/Concepts/sleep-research"
    assert paths[-1] == "KB/Wiki/Concepts/exercise"
    for h in hits:
        assert "wikilink" not in h.lane_ranks


def test_rrf_3lane_wikilink_score():
    """Hand-calc: page that appears only in wikilink lane at rank 1 → score = 1/(60+1)."""
    conn = make_conn(dim=256)

    # bm25-hit: matches query text, links to wikilink-target
    _insert_chunk(
        conn, 1, "anchor page text for query match", "", "Anchor", "KB/Wiki/Concepts/anchor"
    )
    _insert_vec(conn, 1, _unit_vec(256, 0))

    # wikilink-target: does NOT match BM25/vec query
    _insert_chunk(
        conn, 2, "unrelated filler words zzz qqq xxx", "", "Target", "KB/Wiki/Concepts/wl-target"
    )
    _insert_vec(conn, 2, _unit_vec(256, 1))

    _insert_wikilink(conn, "KB/Wiki/Concepts/anchor", "KB/Wiki/Concepts/wl-target")
    conn.commit()

    with patch("shared.kb_embedder.embed", return_value=_unit_vec(256, 99)):
        hits = search(
            "anchor page text query match",
            top_k=10,
            lanes=("bm25", "wikilink"),
            db=conn,
        )

    hit_by_path = {h.path: h for h in hits}
    tgt = hit_by_path.get("KB/Wiki/Concepts/wl-target")
    assert tgt is not None, "wikilink-target must appear in results"
    assert "wikilink" in tgt.lane_ranks
    expected_score = 1.0 / (_RRF_K + tgt.lane_ranks["wikilink"])
    assert abs(tgt.rrf_score - expected_score) < 1e-9
