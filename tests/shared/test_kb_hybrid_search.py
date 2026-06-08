"""Tests for shared/kb_hybrid_search.py.

ADR-042: the dense-vector lane was removed — retrieval is BM25 (FTS5) +
wikilink expansion, fused with RRF k=60. These tests use in-memory SQLite
with pre-inserted chunks to verify:
  - RRF math (hand-calculated against known rankings)
  - BM25 ranking
  - Wikilink lane expansion (outgoing / incoming / rank / score)
  - Token budget truncation
  - Legacy "vec" lane name is accepted but inert
"""

from __future__ import annotations

import pytest

from shared.kb_hybrid_search import _RRF_K, make_conn, search

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_chunk(conn, rowid: int, chunk_text: str, section: str, page_title: str, path: str):
    conn.execute(
        "INSERT INTO kb_chunks(rowid, chunk_text, section, heading_context, path) "
        "VALUES (?,?,?,?,?)",
        (rowid, chunk_text, section, page_title, path),
    )


def _insert_wikilink(conn, src_path: str, dst_path: str) -> None:
    conn.execute(
        "INSERT INTO kb_wikilinks(src_path, dst_path) VALUES (?,?)",
        (src_path, dst_path),
    )


# ---------------------------------------------------------------------------
# RRF math verification
#
# Setup (3 chunks):
#   BM25 query "sleep": A=rank1 (3× sleep), B=rank2 (1× sleep), C=no match
#   wikilink: A links OUT to C → C surfaces at wikilink rank 1
#
# Hand-calculated RRF (k=60):
#   A: bm25=1            → 1/61          ≈ 0.016393
#   B: bm25=2            → 1/62          ≈ 0.016129
#   C: wikilink=1        → 1/61          ≈ 0.016393
# ---------------------------------------------------------------------------


@pytest.fixture
def rrf_db():
    """In-memory DB with 3 pre-inserted chunks for RRF math tests."""
    conn = make_conn()

    # A (rowid=1): strongest BM25 match for "sleep"; links out to C
    _insert_chunk(
        conn,
        1,
        "sleep sleep sleep recovery benefits",
        "Benefits",
        "Sleep",
        "KB/Wiki/Concepts/sleep",
    )
    # B (rowid=2): weaker BM25 match
    _insert_chunk(
        conn,
        2,
        "sleep duration quality research",
        "Research",
        "Sleep",
        "KB/Wiki/Concepts/sleep-research",
    )
    # C (rowid=3): no BM25 match; reachable from A via wikilink only
    _insert_chunk(
        conn,
        3,
        "exercise workout training zone",
        "Training",
        "Exercise",
        "KB/Wiki/Concepts/exercise",
    )
    _insert_wikilink(conn, "KB/Wiki/Concepts/sleep", "KB/Wiki/Concepts/exercise")

    conn.commit()
    return conn


def test_bm25_ranks_strongest_match_first(rrf_db):
    """A (3× 'sleep') outranks B (1× 'sleep') on the BM25 lane."""
    hits = search("sleep", top_k=10, lanes=("bm25",), db=rrf_db)
    paths = [h.path for h in hits]
    assert paths == ["KB/Wiki/Concepts/sleep", "KB/Wiki/Concepts/sleep-research"]


def test_rrf_scores_match_hand_calculation(rrf_db):
    """RRF scores for A, B, C match expected values within tolerance."""
    hits = search("sleep", top_k=10, lanes=("bm25", "wikilink"), db=rrf_db)
    hit_by_path = {h.path: h for h in hits}

    expected_a = 1.0 / (_RRF_K + 1)  # bm25 rank 1
    expected_b = 1.0 / (_RRF_K + 2)  # bm25 rank 2
    expected_c = 1.0 / (_RRF_K + 1)  # wikilink rank 1

    assert abs(hit_by_path["KB/Wiki/Concepts/sleep"].rrf_score - expected_a) < 1e-9
    assert abs(hit_by_path["KB/Wiki/Concepts/sleep-research"].rrf_score - expected_b) < 1e-9
    assert abs(hit_by_path["KB/Wiki/Concepts/exercise"].rrf_score - expected_c) < 1e-9


def test_rrf_lane_ranks_stored_per_hit(rrf_db):
    """Each SearchHit.lane_ranks records which lanes contributed and their rank."""
    hits = search("sleep", top_k=10, lanes=("bm25", "wikilink"), db=rrf_db)
    hit_by_path = {h.path: h for h in hits}

    a = hit_by_path["KB/Wiki/Concepts/sleep"]
    assert "bm25" in a.lane_ranks
    assert "wikilink" not in a.lane_ranks

    c = hit_by_path["KB/Wiki/Concepts/exercise"]
    assert "bm25" not in c.lane_ranks  # C didn't match BM25
    assert "wikilink" in c.lane_ranks


# ---------------------------------------------------------------------------
# Lane fusion toggles
# ---------------------------------------------------------------------------


def test_bm25_only_lane(rrf_db):
    """lanes=('bm25',) → only A and B returned (C has no BM25 match)."""
    hits = search("sleep", top_k=10, lanes=("bm25",), db=rrf_db)

    paths = {h.path for h in hits}
    assert "KB/Wiki/Concepts/sleep" in paths
    assert "KB/Wiki/Concepts/sleep-research" in paths
    assert "KB/Wiki/Concepts/exercise" not in paths
    for h in hits:
        assert "bm25" in h.lane_ranks


def test_legacy_vec_lane_is_inert(rrf_db):
    """ADR-042: passing the removed 'vec' lane name is accepted but adds nothing.

    Callers (closed_pool / Brook) may still hand us ('bm25','vec'); the result
    must equal a plain BM25 search and never raise.
    """
    with_vec = search("sleep", top_k=10, lanes=("bm25", "vec"), db=rrf_db)
    bm25_only = search("sleep", top_k=10, lanes=("bm25",), db=rrf_db)

    assert [h.path for h in with_vec] == [h.path for h in bm25_only]
    for h in with_vec:
        assert "vec" not in h.lane_ranks


# ---------------------------------------------------------------------------
# top_k cutoff
# ---------------------------------------------------------------------------


def test_top_k_limits_results(rrf_db):
    """top_k=1 → at most 1 result returned."""
    hits = search("sleep", top_k=1, lanes=("bm25", "wikilink"), db=rrf_db)
    assert len(hits) <= 1


# ---------------------------------------------------------------------------
# Token budget truncation
# ---------------------------------------------------------------------------


def test_chunk_text_truncated_to_token_budget():
    """chunk_text in SearchHit is capped at _TOKEN_BUDGET_CHARS chars."""
    from shared.kb_hybrid_search import _TOKEN_BUDGET_CHARS

    conn = make_conn()
    long_text = "word " * 1000  # ~5000 chars
    _insert_chunk(conn, 1, long_text, "Sec", "Title", "KB/Wiki/Concepts/long")
    conn.commit()

    hits = search("word", top_k=1, lanes=("bm25",), db=conn)

    assert hits
    assert len(hits[0].chunk_text) <= _TOKEN_BUDGET_CHARS


# ---------------------------------------------------------------------------
# Empty DB
# ---------------------------------------------------------------------------


def test_search_empty_db_returns_empty():
    """Querying an empty index returns []."""
    conn = make_conn()
    hits = search("anything", db=conn)
    assert hits == []


# ---------------------------------------------------------------------------
# make_conn schema check
# ---------------------------------------------------------------------------


def test_make_conn_creates_required_tables():
    """make_conn() initializes the FTS5 + bookkeeping tables, and NOT kb_vectors."""
    conn = make_conn()
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
        ).fetchall()
    }
    assert "kb_index_meta" in tables
    assert "kb_wikilinks" in tables
    # FTS5 shadow tables include the _data table
    assert any("kb_chunks" in t for t in tables)
    # ADR-042: the dense-vector table must no longer be created
    assert not any("kb_vectors" in t for t in tables)


# ---------------------------------------------------------------------------
# Wikilink lane (issue #433 Phase 1b)
# ---------------------------------------------------------------------------


@pytest.fixture
def wikilink_db():
    """In-memory DB for wikilink lane tests.

    Structure:
      concept-a (rowid=1): BM25-matches "conceptalpha unique distinctive"
      sources-x (rowid=2): concept-a links OUT to sources-x
      sources-y (rowid=3): concept-a links OUT to sources-y
      concept-b (rowid=4): concept-a links OUT to concept-b
    """
    conn = make_conn()

    _insert_chunk(
        conn,
        1,
        "conceptalpha unique distinctive text long",
        "",
        "Concept A",
        "KB/Wiki/Concepts/concept-a",
    )
    _insert_chunk(
        conn,
        2,
        "sourceresearch material content findings",
        "",
        "Source X",
        "KB/Wiki/Sources/sources-x",
    )
    _insert_chunk(
        conn,
        3,
        "source y background context information",
        "",
        "Source Y",
        "KB/Wiki/Sources/sources-y",
    )
    _insert_chunk(
        conn,
        4,
        "concept b related information details",
        "",
        "Concept B",
        "KB/Wiki/Concepts/concept-b",
    )

    _insert_wikilink(conn, "KB/Wiki/Concepts/concept-a", "KB/Wiki/Sources/sources-x")
    _insert_wikilink(conn, "KB/Wiki/Concepts/concept-a", "KB/Wiki/Sources/sources-y")
    _insert_wikilink(conn, "KB/Wiki/Concepts/concept-a", "KB/Wiki/Concepts/concept-b")

    conn.commit()
    return conn


def test_make_conn_creates_wikilinks_table():
    """make_conn() must initialize kb_wikilinks table."""
    conn = make_conn()
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert "kb_wikilinks" in tables


def test_wikilink_lane_outgoing_links(wikilink_db):
    """BM25 hits concept-a; wikilink lane pulls in its 3 outgoing neighbors."""
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
    hits = search(
        "sourceresearch material content",
        top_k=10,
        lanes=("bm25", "wikilink"),
        db=wikilink_db,
    )

    paths = {h.path for h in hits}
    assert "KB/Wiki/Concepts/concept-a" in paths


def test_wikilink_lane_rank_in_lane_ranks(wikilink_db):
    """Wikilink-only hits must have 'wikilink' key in lane_ranks, no BM25."""
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


def test_bm25_only_no_wikilink_key(rrf_db):
    """lanes=('bm25',) → no 'wikilink' key leaks into lane_ranks."""
    hits = search("sleep", top_k=10, lanes=("bm25",), db=rrf_db)
    for h in hits:
        assert "wikilink" not in h.lane_ranks


def test_rrf_wikilink_only_score():
    """Hand-calc: page that appears only in wikilink lane at rank 1 → score = 1/(60+1)."""
    conn = make_conn()

    _insert_chunk(
        conn, 1, "anchor page text for query match", "", "Anchor", "KB/Wiki/Concepts/anchor"
    )
    _insert_chunk(
        conn, 2, "unrelated filler words zzz qqq xxx", "", "Target", "KB/Wiki/Concepts/wl-target"
    )
    _insert_wikilink(conn, "KB/Wiki/Concepts/anchor", "KB/Wiki/Concepts/wl-target")
    conn.commit()

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
