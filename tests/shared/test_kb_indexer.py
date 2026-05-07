"""Tests for shared/kb_indexer.py.

Uses in-memory SQLite (via kb_hybrid_search.make_conn) + fake vault fixtures.
kb_embedder is mocked to return deterministic 256-dim vectors without loading
the real model.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from shared.kb_hybrid_search import make_conn
from shared.kb_indexer import _split_h2_chunks, index_vault

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fixed_embed_batch(texts: list[str]) -> list[np.ndarray]:
    """Deterministic fake embedder — one unique vector per text (hash-seeded)."""
    out = []
    for t in texts:
        rng = np.random.default_rng(abs(hash(t)) % (2**31))
        out.append(rng.random(256).astype(np.float32))
    return out


def _make_page(
    dir_path: Path,
    stem: str,
    *,
    title: str = "",
    body: str = "",
    frontmatter_extra: str = "",
) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    fm = f"---\ntitle: {title}\n{frontmatter_extra}---\n" if title else ""
    (dir_path / f"{stem}.md").write_text(fm + body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Vault fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_10(tmp_path):
    """Vault with 10 fake pages (mix Concept/Source/Entity + frontmatter + wikilinks)."""
    wiki = tmp_path / "KB" / "Wiki"

    # 4 Concepts
    (wiki / "Concepts").mkdir(parents=True)
    for i in range(4):
        title = f"Concept {i}"
        body = (
            f"## 定義\n"
            f"這是 concept {i} 的定義，具有多個重要特性。"
            f"[[Concepts/related_{i}]] 是相關概念。\n\n"
            f"## 研究證據\n"
            f"研究顯示 concept {i} 有顯著效果。"
        )
        _make_page(wiki / "Concepts", f"concept-{i}", title=title, body=body)

    # 4 Sources
    (wiki / "Sources").mkdir(parents=True)
    for i in range(4):
        title = f"Source Paper {i}"
        body = (
            f"## 摘要\n"
            f"本研究探討 source paper {i} 的主要發現。"
            f"[[Sources/ref_{i}]] 是引用來源。\n\n"
            f"## 方法論\n"
            f"採用隨機對照試驗設計。結果顯示統計顯著性。\n\n"
            f"## References\n"
            f"- Ref A\n- Ref B"
        )
        _make_page(wiki / "Sources", f"source-{i}", title=title, body=body)

    # 2 Entities
    (wiki / "Entities").mkdir(parents=True)
    for i in range(2):
        title = f"Entity Person {i}"
        body = (
            f"## 背景\n"
            f"Entity person {i} 是健康領域的重要人物，長期致力於推廣科學飲食與運動知識。\n\n"
            f"## 代表作\n"
            f"著有多本暢銷書籍，深入研究睡眠科學、有氧運動與長壽的關聯性。"
        )
        _make_page(wiki / "Entities", f"entity-{i}", title=title, body=body)

    return tmp_path


# ---------------------------------------------------------------------------
# _split_h2_chunks unit tests
# ---------------------------------------------------------------------------


def test_split_h2_chunks_preamble_extracted():
    """Text before first ## becomes a preamble chunk with empty section."""
    chunks = _split_h2_chunks("Intro text that is long enough.\n", "Page", "KB/Wiki/Concepts/x")
    assert any(c["section"] == "" for c in chunks)


def test_split_h2_chunks_sections_extracted():
    """Each ## heading produces a separate chunk (body must be >= 30 chars each)."""
    body = "## 定義\n" + "x" * 50 + "\n\n## 方法\n" + "y" * 50
    chunks = _split_h2_chunks(body, "Test", "KB/Wiki/Concepts/test")
    sections = [c["section"] for c in chunks]
    assert "定義" in sections
    assert "方法" in sections


def test_split_h2_chunks_skip_sections_excluded():
    """References / See Also / Related sections are skipped."""
    body = "## 定義\n" + "x" * 50 + "\n\n## References\n- ref1\n\n## See Also\n- link"
    chunks = _split_h2_chunks(body, "Page", "KB/Wiki/Concepts/x")
    skip_headings = {c["section"] for c in chunks}
    assert "References" not in skip_headings
    assert "See Also" not in skip_headings
    assert "定義" in skip_headings


def test_split_h2_chunks_min_length_filter():
    """Chunks shorter than 30 chars are discarded."""
    body = "## Short\nTiny.\n\n## Long\n" + "x" * 50
    chunks = _split_h2_chunks(body, "P", "path")
    sections = [c["section"] for c in chunks]
    assert "Short" not in sections
    assert "Long" in sections


def test_split_h2_chunks_heading_context_is_page_title():
    """heading_context should equal the page_title passed in."""
    chunks = _split_h2_chunks(
        "## Section\n" + "x" * 50, "My Page Title", "KB/Wiki/Concepts/my-page"
    )
    assert all(c["heading_context"] == "My Page Title" for c in chunks)


def test_split_h2_chunks_path_stored():
    """path field matches the page_path argument."""
    chunks = _split_h2_chunks("## Sec\n" + "x" * 50, "T", "KB/Wiki/Sources/paper1")
    assert all(c["path"] == "KB/Wiki/Sources/paper1" for c in chunks)


# ---------------------------------------------------------------------------
# index_vault: basic integration
# ---------------------------------------------------------------------------


def test_index_vault_10_pages(vault_10):
    """index_vault on 10-page fixture produces correct files_indexed count."""
    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        stats = index_vault(vault_10, conn)

    assert stats.files_indexed == 10
    assert stats.files_skipped == 0
    assert stats.chunks_added > 0


def test_index_vault_frontmatter_title_stored(vault_10):
    """Frontmatter title should appear as heading_context in kb_chunks."""
    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        index_vault(vault_10, conn)

    rows = conn.execute(
        "SELECT heading_context FROM kb_chunks WHERE path = 'KB/Wiki/Concepts/concept-0'"
    ).fetchall()
    assert rows, "no chunks for concept-0"
    assert all(r[0] == "Concept 0" for r in rows)


def test_index_vault_references_section_not_indexed(vault_10):
    """Source pages have ## References — those chunks must NOT be indexed."""
    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        index_vault(vault_10, conn)

    # Verify that there are no chunks with section="References"
    rows = conn.execute("SELECT section FROM kb_chunks WHERE section = 'References'").fetchall()
    assert rows == []


def test_index_vault_wikilinks_extracted(vault_10):
    """Wikilinks [[...]] appear in IndexStats.wikilinks."""
    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        stats = index_vault(vault_10, conn)

    assert any("related_" in wl for wl in stats.wikilinks)


def test_index_vault_chunk_and_vector_rowids_match(vault_10):
    """Every rowid in kb_chunks should have a matching rowid in kb_vectors."""
    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        index_vault(vault_10, conn)

    chunk_rowids = {r[0] for r in conn.execute("SELECT rowid FROM kb_chunks").fetchall()}
    vec_rowids = {r[0] for r in conn.execute("SELECT rowid FROM kb_vectors").fetchall()}
    assert chunk_rowids == vec_rowids


# ---------------------------------------------------------------------------
# Incremental indexing (mtime_ns short-circuit)
# ---------------------------------------------------------------------------


def test_index_vault_incremental_skips_unchanged_files(tmp_path):
    """Second index_vault call with 0 file changes completes in <1 s with files_skipped=N."""
    wiki = tmp_path / "KB" / "Wiki" / "Concepts"
    wiki.mkdir(parents=True)
    _make_page(wiki, "stable", title="Stable Page", body="## Section\n" + "x" * 60)

    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        stats1 = index_vault(tmp_path, conn)
        stats2 = index_vault(tmp_path, conn)

    assert stats1.files_indexed == 1
    assert stats1.files_skipped == 0
    assert stats2.files_indexed == 0
    assert stats2.files_skipped == 1


def test_index_vault_reindexes_changed_file(tmp_path):
    """Modifying a file triggers re-index (old chunks removed, new chunks added)."""
    wiki = tmp_path / "KB" / "Wiki" / "Concepts"
    wiki.mkdir(parents=True)
    page = wiki / "mutable.md"
    page.write_text("---\ntitle: Mutable\n---\n## Section\n" + "x" * 60, encoding="utf-8")

    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        index_vault(tmp_path, conn)

    chunks_after_first = conn.execute("SELECT count(*) FROM kb_chunks").fetchone()[0]

    # Modify the file (rewrite with different content, bumping mtime)
    import time

    time.sleep(0.01)  # ensure mtime_ns changes
    page.write_text(
        "---\ntitle: Mutable\n---\n## Section\n" + "y" * 60 + "\n## Extra\n" + "z" * 60,
        encoding="utf-8",
    )

    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        stats2 = index_vault(tmp_path, conn)

    assert stats2.files_indexed == 1
    assert stats2.files_skipped == 0
    # After reindex, chunks should reflect the new content (2 sections now)
    chunks_after_second = conn.execute("SELECT count(*) FROM kb_chunks").fetchone()[0]
    assert chunks_after_second > chunks_after_first  # Extra section added


# ---------------------------------------------------------------------------
# Subdirectory types
# ---------------------------------------------------------------------------


def test_index_vault_sources_concepts_entities_all_indexed(vault_10):
    """All 3 subdirectory types produce chunks with correct path prefixes."""
    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        index_vault(vault_10, conn)

    for subdir in ("Sources", "Concepts", "Entities"):
        rows = conn.execute(
            "SELECT count(*) FROM kb_chunks WHERE path LIKE ?",
            (f"KB/Wiki/{subdir}/%",),
        ).fetchone()
        assert rows[0] > 0, f"no chunks for subdir {subdir}"


# ---------------------------------------------------------------------------
# Wikilink persistence (issue #433 Phase 1b)
# ---------------------------------------------------------------------------


def test_index_vault_wikilinks_persisted_to_db(tmp_path):
    """Wikilinks [[X]] in page body are persisted to kb_wikilinks table."""
    wiki = tmp_path / "KB" / "Wiki" / "Concepts"
    wiki.mkdir(parents=True)
    _make_page(
        wiki,
        "concept-a",
        title="Concept A",
        body=(
            "## 定義\n"
            "這是 concept-a 的定義。"
            "[[Concepts/concept-b]] 是相關概念，[[Sources/source-x]] 是引用來源。\n" + "x" * 60
        ),
    )

    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        index_vault(tmp_path, conn)

    rows = conn.execute("SELECT src_path, dst_path FROM kb_wikilinks").fetchall()
    assert rows, "kb_wikilinks must have at least one row"
    src_paths = {r[0] for r in rows}
    dst_paths = {r[1] for r in rows}
    assert "KB/Wiki/Concepts/concept-a" in src_paths
    assert "KB/Wiki/Concepts/concept-b" in dst_paths
    assert "KB/Wiki/Sources/source-x" in dst_paths


def test_index_vault_mentioned_in_frontmatter_persisted(tmp_path):
    """Wikilinks in frontmatter (mentioned_in field) are also persisted."""
    wiki = tmp_path / "KB" / "Wiki" / "Concepts"
    wiki.mkdir(parents=True)
    _make_page(
        wiki,
        "concept-c",
        title="Concept C",
        body="## Section\n" + "x" * 60,
        frontmatter_extra=(
            'mentioned_in:\n  - "[[Sources/paper-one]]"\n  - "[[Sources/paper-two]]"\n'
        ),
    )

    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        index_vault(tmp_path, conn)

    dst_paths = {r[0] for r in conn.execute("SELECT dst_path FROM kb_wikilinks").fetchall()}
    assert "KB/Wiki/Sources/paper-one" in dst_paths
    assert "KB/Wiki/Sources/paper-two" in dst_paths


def test_index_vault_wikilinks_removed_on_reindex(tmp_path):
    """Old wikilinks for a page are removed before re-indexing (no stale edges)."""
    wiki = tmp_path / "KB" / "Wiki" / "Concepts"
    wiki.mkdir(parents=True)
    page = wiki / "mutable-wl.md"
    page.write_text(
        "---\ntitle: Mutable\n---\n## Section\n[[Concepts/old-target]] mention here " + "x" * 50,
        encoding="utf-8",
    )

    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        index_vault(tmp_path, conn)

    old_rows = conn.execute("SELECT dst_path FROM kb_wikilinks").fetchall()
    assert any("old-target" in r[0] for r in old_rows)

    import time

    time.sleep(0.01)
    page.write_text(
        "---\ntitle: Mutable\n---\n## Section\n[[Concepts/new-target]] different link " + "x" * 50,
        encoding="utf-8",
    )

    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        index_vault(tmp_path, conn)

    new_rows = conn.execute("SELECT dst_path FROM kb_wikilinks").fetchall()
    dst_set = {r[0] for r in new_rows}
    assert "KB/Wiki/Concepts/new-target" in dst_set
    assert not any("old-target" in d for d in dst_set), "stale wikilink must be removed"


# ---------------------------------------------------------------------------
# Recursive rglob — nested Books directory (ADR-021 §2 / Codex amendment)
# ---------------------------------------------------------------------------


def test_index_vault_indexes_nested_books_dir(tmp_path):
    """Nested KB/Wiki/Sources/Books/{book_id}/notes.md should be indexed.

    Pre-fix the scan loop used dir_path.glob("*.md") which is non-recursive,
    so only root-level Sources/*.md got indexed. ADR-021 §2 + Codex amendment
    switch to rglob so per-book subdirs (digest/notes/etc.) are now first-class
    KB content.
    """
    nested = tmp_path / "KB" / "Wiki" / "Sources" / "Books" / "atomic-habits"
    nested.mkdir(parents=True)
    (nested / "notes.md").write_text(
        "---\ntitle: Atomic Habits Notes\n---\n## 第一章\n"
        "habit stacking 是把新習慣黏在既有習慣上的技巧，研究顯示效果顯著。",
        encoding="utf-8",
    )

    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        stats = index_vault(tmp_path, conn)

    assert stats.files_indexed == 1
    rows = conn.execute(
        "SELECT path, heading_context FROM kb_chunks WHERE path LIKE 'KB/Wiki/Sources/Books/%'"
    ).fetchall()
    assert rows, "nested Books/{book_id}/notes.md must be indexed"
    paths = {r[0] for r in rows}
    assert "KB/Wiki/Sources/Books/atomic-habits/notes" in paths
    titles = {r[1] for r in rows}
    assert "Atomic Habits Notes" in titles


# ---------------------------------------------------------------------------
# KB/Annotations indexer (ADR-021 §2)
# ---------------------------------------------------------------------------


def _write_annotation_file(tmp_path: Path, slug: str, content: str) -> Path:
    """Write a KB/Annotations/{slug}.md file and return the path."""
    d = tmp_path / "KB" / "Annotations"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text(content, encoding="utf-8")
    return p


def test_index_vault_annotations_v3_chunks_per_item(tmp_path):
    """Each v3 highlight/annotation/reflection item becomes one chunk.

    chunk_text source by item type:
      highlight  → text
      annotation → note
      reflection → body
    """
    annotation_md = """---
slug: atomic-habits
schema_version: 3
base: books
book_id: atomic-habits
book_version_hash: abc123
updated_at: "2026-05-07T00:00:00Z"
---

```json
[
  {
    "type": "highlight",
    "schema_version": 3,
    "cfi": "epubcfi(/6/2!/4/2/2)",
    "text_excerpt": "habit stacking",
    "book_version_hash": "abc123",
    "text": "habit stacking is the technique of binding new habits to existing ones",
    "created_at": "2026-05-07T00:00:00Z",
    "modified_at": "2026-05-07T00:00:00Z"
  },
  {
    "type": "annotation",
    "schema_version": 3,
    "cfi": "epubcfi(/6/2!/4/2/4)",
    "text_excerpt": "habit stacking",
    "note": "this maps to my morning coffee → push-up routine — works",
    "created_at": "2026-05-07T00:00:00Z",
    "modified_at": "2026-05-07T00:00:00Z"
  },
  {
    "type": "reflection",
    "schema_version": 3,
    "chapter_ref": "Chapter 5",
    "body": "the chapter argues identity-based habits beat outcome-based ones",
    "created_at": "2026-05-07T00:00:00Z",
    "modified_at": "2026-05-07T00:00:00Z"
  }
]
```
"""
    _write_annotation_file(tmp_path, "atomic-habits", annotation_md)

    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        stats = index_vault(tmp_path, conn)

    assert stats.files_indexed == 1
    rows = conn.execute(
        "SELECT chunk_text, section, heading_context, path FROM kb_chunks "
        "WHERE path = 'KB/Annotations/atomic-habits' ORDER BY rowid"
    ).fetchall()
    assert len(rows) == 3, "expected 3 chunks (1 per annotation item)"

    chunk_texts = [r[0] for r in rows]
    sections = [r[1] for r in rows]

    # highlight → text (the body), not the excerpt
    assert "habit stacking is the technique" in chunk_texts[0]
    assert sections[0] == "highlight"

    # annotation → note
    assert "morning coffee" in chunk_texts[1]
    assert sections[1] == "annotation"

    # reflection → body, with chapter_ref folded into section
    assert "identity-based habits" in chunk_texts[2]
    assert sections[2] == "reflection|Chapter 5"

    # heading_context carries source slug (book_id for v3 book sets)
    for row in rows:
        assert row[2] == "atomic-habits"


def test_index_vault_annotations_v1_paper_indexed(tmp_path):
    """v1 paper-style annotation files are upgraded → indexed transparently.

    AC: indexer reuses annotation_store parsing rather than re-implementing it,
    so legacy v1 (paper) files just work.
    """
    annotation_md = """---
slug: creatine-paper
source: creatine-cognitive-2024.md
base: inbox
updated_at: "2026-05-07T00:00:00Z"
---

```json
[
  {
    "type": "highlight",
    "text": "creatine supplementation improved working memory in vegetarians",
    "created_at": "2026-05-07T00:00:00Z",
    "modified_at": "2026-05-07T00:00:00Z"
  },
  {
    "type": "annotation",
    "ref": "Methods §2",
    "note": "n=121, double-blind RCT — solid design",
    "created_at": "2026-05-07T00:00:00Z",
    "modified_at": "2026-05-07T00:00:00Z"
  }
]
```
"""
    _write_annotation_file(tmp_path, "creatine-paper", annotation_md)

    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        index_vault(tmp_path, conn)

    rows = conn.execute(
        "SELECT chunk_text, section, heading_context FROM kb_chunks "
        "WHERE path = 'KB/Annotations/creatine-paper' ORDER BY rowid"
    ).fetchall()
    assert len(rows) == 2
    assert any("working memory" in r[0] for r in rows)
    assert any("double-blind" in r[0] for r in rows)
    # source slug = source_filename for paper sets
    assert all(r[2] == "creatine-cognitive-2024.md" for r in rows)


def test_index_vault_annotations_query_finds_chunk(tmp_path):
    """End-to-end: indexed annotation chunk is queryable via FTS5 MATCH."""
    annotation_md = """---
slug: longevity-book
schema_version: 3
base: books
book_id: longevity-book
updated_at: "2026-05-07T00:00:00Z"
---

```json
[
  {
    "type": "reflection",
    "schema_version": 3,
    "chapter_ref": "Chapter 3",
    "body": "rapamycin extends lifespan in mice via mTOR inhibition",
    "created_at": "2026-05-07T00:00:00Z",
    "modified_at": "2026-05-07T00:00:00Z"
  }
]
```
"""
    _write_annotation_file(tmp_path, "longevity-book", annotation_md)

    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        index_vault(tmp_path, conn)

    rows = conn.execute(
        "SELECT chunk_text, section, path FROM kb_chunks WHERE kb_chunks MATCH ?",
        ("rapamycin",),
    ).fetchall()
    assert rows, "FTS query must find the annotation chunk"
    assert any("KB/Annotations/longevity-book" == r[2] for r in rows)
    assert any("Chapter 3" in r[1] for r in rows)


def test_index_vault_annotations_incremental_skip(tmp_path):
    """Unchanged annotation file is skipped on re-index (mtime_ns short-circuit)."""
    annotation_md = """---
slug: stable
schema_version: 3
base: inbox
source: stable.md
updated_at: "2026-05-07T00:00:00Z"
---

```json
[
  {
    "type": "highlight",
    "schema_version": 3,
    "text_excerpt": "x",
    "text": "stable highlight body that is long enough to index",
    "created_at": "2026-05-07T00:00:00Z",
    "modified_at": "2026-05-07T00:00:00Z"
  }
]
```
"""
    _write_annotation_file(tmp_path, "stable", annotation_md)

    conn = make_conn(dim=256)
    with patch("shared.kb_embedder.embed_batch", side_effect=_fixed_embed_batch):
        s1 = index_vault(tmp_path, conn)
        s2 = index_vault(tmp_path, conn)

    assert s1.files_indexed == 1
    assert s2.files_indexed == 0
    assert s2.files_skipped == 1
