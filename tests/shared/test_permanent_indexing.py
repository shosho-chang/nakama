"""Centaur N520 — KB/Permanent/ indexing, Permanent-first ranking, typed edges.

Real FTS5 index + real BM25 (no mocks), mirroring test_kb_hybrid_integration.
Acceptance (task prompt §5): a card with typed edges is retrievable by kb_search
AND ranks first; a Permanent hit must survive top_k truncation (panel Codex §5).
"""

from __future__ import annotations

import pytest

from shared.kb_hybrid_search import make_conn, search
from shared.kb_indexer import index_vault


@pytest.fixture
def vault_with_permanent(tmp_path):
    """Vault with 1 Permanent card + several Concept pages sharing a query term.

    Query term is the Latin token ``willpower``: BM25 over the repo's
    ``porter unicode61`` tokenizer is reliable on whitespace-delimited Latin
    tokens but NOT on multi-char CJK queries (the existing
    test_kb_hybrid_integration suite also queries English-only for this exact
    reason). CJK multi-char retrieval is a known repo-wide limitation tracked as
    a separate backlog item — N520's deliverable is the Permanent-first RANKING
    logic, which is query-agnostic, so a reliable token fully exercises it.
    """
    wiki = tmp_path / "KB" / "Wiki" / "Concepts"
    wiki.mkdir(parents=True)
    perm = tmp_path / "KB" / "Permanent"
    perm.mkdir(parents=True)

    # Permanent card — declaration-sentence filename, typed edges in body.
    (perm / "好系統讓你不需要意志力.md").write_text(
        "---\n"
        "type: permanent\n"
        "status: seedling\n"
        "author: human\n"
        "source_refs: []\n"
        "aliases: []\n"
        "---\n\n"
        "好系統讓你不需要意志力 (willpower)。把消耗 willpower 的環節先自動化，紀律就不再稀缺。\n\n"
        "支持:: [[把摩擦降到零]] — 因為摩擦是意志力的稅\n"
        "反駁:: [[寫作產出靠的是紀律]] — 它假設人人都是村上春樹\n"
        "延伸:: [[Hell yeah or no]] — 同一原則從執行延伸到選擇\n",
        encoding="utf-8",
    )

    # Several Concept pages that ALSO contain `willpower` so they compete on BM25.
    for i in range(6):
        (wiki / f"concept-{i}.md").write_text(
            f"---\ntitle: willpower concept {i}\n---\n"
            f"## 定義\nwillpower is a finite resource; willpower depletes (concept {i}).\n",
            encoding="utf-8",
        )

    return tmp_path


def test_permanent_card_indexed_and_searchable(vault_with_permanent):
    conn = make_conn()
    stats = index_vault(vault_with_permanent, conn)
    # 1 permanent + 6 concepts indexed
    assert stats.files_indexed == 7

    hits = search("willpower", top_k=10, db=conn)
    paths = [h.path for h in hits]
    assert "KB/Permanent/好系統讓你不需要意志力" in paths, (
        f"Permanent card must be retrievable; got {paths}"
    )


def test_permanent_ranks_first(vault_with_permanent):
    """Among hits sharing the query term, the Permanent card sorts first."""
    conn = make_conn()
    index_vault(vault_with_permanent, conn)

    hits = search("willpower", top_k=10, db=conn)
    assert hits
    assert hits[0].path == "KB/Permanent/好系統讓你不需要意志力", (
        f"Permanent must rank first (handoff fork 2); got order {[h.path for h in hits]}"
    )


def test_permanent_survives_topk_truncation(vault_with_permanent):
    """Even with top_k smaller than the competing pool, Permanent isn't lost.

    panel Codex §5: the Permanent boost runs BEFORE [:top_k], so a relevant
    Permanent hit can't be truncated away by a crowd of Concept matches.
    """
    conn = make_conn()
    index_vault(vault_with_permanent, conn)

    hits = search("willpower", top_k=1, db=conn)
    assert len(hits) == 1
    assert hits[0].path == "KB/Permanent/好系統讓你不需要意志力"


def test_typed_edges_extracted_to_structured_table(vault_with_permanent):
    conn = make_conn()
    index_vault(vault_with_permanent, conn)

    rows = conn.execute(
        "SELECT edge_type, dst_path, reason FROM kb_typed_edges "
        "WHERE src_path = ? ORDER BY edge_type",
        ("KB/Permanent/好系統讓你不需要意志力",),
    ).fetchall()
    edges = {r["edge_type"]: (r["dst_path"], r["reason"]) for r in rows}

    assert set(edges) == {"support", "refute", "extend"}
    assert edges["support"][0] == "KB/Permanent/把摩擦降到零"
    assert edges["support"][1] == "因為摩擦是意志力的稅"
    assert edges["refute"][0] == "KB/Permanent/寫作產出靠的是紀律"
    assert edges["extend"][0] == "KB/Permanent/Hell yeah or no"


def test_reindex_is_idempotent_for_typed_edges(vault_with_permanent):
    """Re-running index_vault must not duplicate typed edges (DELETE-then-insert)."""
    conn = make_conn()
    index_vault(vault_with_permanent, conn)
    index_vault(vault_with_permanent, conn)  # second pass (mtime unchanged → skip)

    count = conn.execute(
        "SELECT COUNT(*) FROM kb_typed_edges WHERE src_path = ?",
        ("KB/Permanent/好系統讓你不需要意志力",),
    ).fetchone()[0]
    assert count == 3, f"expected 3 edges after reindex, got {count}"


def test_empty_permanent_dir_is_noop(tmp_path):
    """No KB/Permanent/ dir → indexer doesn't crash, just indexes Wiki."""
    wiki = tmp_path / "KB" / "Wiki" / "Concepts"
    wiki.mkdir(parents=True)
    (wiki / "c.md").write_text(
        "---\ntitle: c\n---\n## 定義\n概念內容夠長以通過最小長度門檻。\n", encoding="utf-8"
    )
    conn = make_conn()
    stats = index_vault(tmp_path, conn)
    assert stats.files_indexed == 1
