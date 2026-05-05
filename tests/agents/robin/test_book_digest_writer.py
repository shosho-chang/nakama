"""TDD: agents.robin.book_digest_writer.write_digest — book digest generation.

Red tests will fail until agents/robin/book_digest_writer.py is created.

Acceptance criteria (issue #432):
- write_digest(book_id) → DigestReport
- writes KB/Wiki/Sources/Books/{book_id}/digest.md  (full-replace, idempotent)
- proper frontmatter: type=book_digest, book_id, book_entity wikilink, schema_version, updated_at
- chapter H2 headings (## Ch{N} {chapter_ref})
- items in CFI/chapter order within each chapter
- hybrid KB hits rendered per item (mocked search_kb)
- deep-link format: /books/{id}#cfi=...
- wikilink surface from concept pages with annotation-from: {book_id} markers
- empty annotation set → creates file with frontmatter only (no chapters)
- returns DigestReport with correct book_id, chapters_rendered, items_rendered counts
"""

from __future__ import annotations

from pathlib import Path

import pytest

digest_mod = pytest.importorskip(
    "agents.robin.book_digest_writer",
    reason="agents/robin/book_digest_writer.py is the module under test",
)

write_digest = digest_mod.write_digest
DigestReport = digest_mod.DigestReport

ann_schemas = pytest.importorskip("shared.schemas.annotations")
AnnotationSetV2 = ann_schemas.AnnotationSetV2
AnnotationV2 = ann_schemas.AnnotationV2
HighlightV2 = ann_schemas.HighlightV2
CommentV2 = ann_schemas.CommentV2

_TS = "2026-05-05T00:00:00Z"
_HASH = "a" * 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _highlight(cfi: str, text: str) -> HighlightV2:
    return HighlightV2(
        cfi=cfi, text_excerpt=text, book_version_hash=_HASH, created_at=_TS, modified_at=_TS
    )


def _annotation(cfi: str, text: str, note: str) -> AnnotationV2:
    return AnnotationV2(
        cfi=cfi,
        text_excerpt=text,
        note=note,
        book_version_hash=_HASH,
        created_at=_TS,
        modified_at=_TS,
    )


def _comment(chapter_ref: str, body: str, cfi_anchor: str | None = None) -> CommentV2:
    return CommentV2(
        chapter_ref=chapter_ref,
        cfi_anchor=cfi_anchor,
        body=body,
        book_version_hash=_HASH,
        created_at=_TS,
        modified_at=_TS,
    )


def _multi_chapter_set(book_id: str = "test-book") -> AnnotationSetV2:
    """AnnotationSetV2 with H, A, C items spread across two chapters."""
    return AnnotationSetV2(
        slug=book_id,
        book_id=book_id,
        book_version_hash=_HASH,
        items=[
            _highlight("epubcfi(/6/4[ch01]!/4/2:0)", "Ch1 highlight text."),
            _annotation("epubcfi(/6/4[ch01]!/4/6:5)", "Ch1 annotation text.", "My note."),
            _comment(
                "ch01.xhtml", "Ch1 chapter reflection.", cfi_anchor="epubcfi(/6/4[ch01]!/4/10:0)"
            ),
            _highlight("epubcfi(/6/6[ch02]!/4/2:0)", "Ch2 highlight text."),
        ],
        updated_at=_TS,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    (tmp_path / "KB" / "Annotations").mkdir(parents=True)
    (tmp_path / "KB" / "Wiki" / "Sources" / "Books").mkdir(parents=True)
    (tmp_path / "KB" / "Wiki" / "Concepts").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def saved_annotations(vault: Path) -> None:
    """Persist a multi-chapter AnnotationSetV2 via the annotation store singleton."""
    from shared.annotation_store import get_annotation_store

    get_annotation_store().save(_multi_chapter_set("test-book"))


@pytest.fixture
def mock_search(monkeypatch) -> list[dict]:
    """Patch agents.robin.book_digest_writer.search_kb with deterministic results."""
    fake_hits = [
        {
            "type": "concept",
            "title": "Test Concept",
            "path": "KB/Wiki/Concepts/test-concept",
            "preview": "preview text",
            "relevance_reason": "Directly related.",
        }
    ]
    calls: list[dict] = []

    def fake_search_kb(query, vault_path, top_k=8, *, purpose="general", engine="haiku"):
        calls.append({"query": query, "purpose": purpose, "engine": engine, "top_k": top_k})
        return fake_hits

    monkeypatch.setattr(digest_mod, "search_kb", fake_search_kb)
    return calls


# ---------------------------------------------------------------------------
# Core: file creation and frontmatter
# ---------------------------------------------------------------------------


def test_write_digest_creates_file(vault, saved_annotations, mock_search):
    write_digest("test-book")
    p = vault / "KB" / "Wiki" / "Sources" / "Books" / "test-book" / "digest.md"
    assert p.exists(), "digest.md not created"


def test_frontmatter_fields(vault, saved_annotations, mock_search):
    write_digest("test-book")
    content = (vault / "KB" / "Wiki" / "Sources" / "Books" / "test-book" / "digest.md").read_text(
        encoding="utf-8"
    )
    assert "type: book_digest" in content
    assert "book_id: test-book" in content
    assert "[[Sources/Books/test-book]]" in content
    assert "schema_version: 1" in content
    assert "updated_at:" in content


# ---------------------------------------------------------------------------
# Chapter H2 headings
# ---------------------------------------------------------------------------


def test_chapter_h2_headings_present(vault, saved_annotations, mock_search):
    write_digest("test-book")
    content = (vault / "KB" / "Wiki" / "Sources" / "Books" / "test-book" / "digest.md").read_text(
        encoding="utf-8"
    )
    # At least two chapter headings (ch01 and ch02 items)
    assert content.count("## Ch") >= 2


def test_items_in_chapter_order(vault, saved_annotations, mock_search):
    """Ch1 content must appear before Ch2 content."""
    write_digest("test-book")
    content = (vault / "KB" / "Wiki" / "Sources" / "Books" / "test-book" / "digest.md").read_text(
        encoding="utf-8"
    )
    assert content.index("Ch1 highlight text.") < content.index("Ch2 highlight text.")


# ---------------------------------------------------------------------------
# Hit rendering
# ---------------------------------------------------------------------------


def test_hits_are_rendered(vault, saved_annotations, mock_search):
    write_digest("test-book")
    content = (vault / "KB" / "Wiki" / "Sources" / "Books" / "test-book" / "digest.md").read_text(
        encoding="utf-8"
    )
    # The mock hit title / path / reason should appear
    assert "Test Concept" in content or "test-concept" in content
    assert "Directly related." in content


def test_search_called_with_book_review_purpose(vault, saved_annotations, mock_search):
    write_digest("test-book")
    assert mock_search, "search_kb was never called"
    assert all(c["purpose"] == "book_review" for c in mock_search)
    assert all(c["engine"] == "hybrid" for c in mock_search)
    assert all(c["top_k"] == 3 for c in mock_search)


# ---------------------------------------------------------------------------
# Deep links
# ---------------------------------------------------------------------------


def test_deep_link_format(vault, saved_annotations, mock_search):
    """Each H/A item with a CFI must produce a /books/{id}#cfi=... link."""
    write_digest("test-book")
    content = (vault / "KB" / "Wiki" / "Sources" / "Books" / "test-book" / "digest.md").read_text(
        encoding="utf-8"
    )
    assert "/books/test-book#cfi=epubcfi(" in content


# ---------------------------------------------------------------------------
# Wikilink surface from concept pages
# ---------------------------------------------------------------------------


def test_wikilinks_surfaced_from_concept_markers(vault, saved_annotations, mock_search):
    """Concept pages with annotation-from: {book_id} markers should appear as wikilinks."""
    concept_page = vault / "KB" / "Wiki" / "Concepts" / "anchoring-effect.md"
    concept_page.write_text(
        "---\nslug: anchoring-effect\n---\n\n## 讀者註記\n\n"
        "<!-- annotation-from: test-book -->\ncallout\n"
        "<!-- end-annotation-from: test-book -->\n",
        encoding="utf-8",
    )
    write_digest("test-book")
    content = (vault / "KB" / "Wiki" / "Sources" / "Books" / "test-book" / "digest.md").read_text(
        encoding="utf-8"
    )
    assert "anchoring-effect" in content


# ---------------------------------------------------------------------------
# DigestReport return value
# ---------------------------------------------------------------------------


def test_returns_digest_report(vault, saved_annotations, mock_search):
    report = write_digest("test-book")
    assert isinstance(report, DigestReport)
    assert report.book_id == "test-book"
    assert report.chapters_rendered >= 2
    assert isinstance(report.items_rendered, dict)
    assert "h" in report.items_rendered
    assert "a" in report.items_rendered
    assert "c" in report.items_rendered
    assert report.items_rendered["h"] == 2  # two highlights across chapters
    assert report.items_rendered["a"] == 1
    assert report.items_rendered["c"] == 1
    assert isinstance(report.hits_per_item_avg, float)
    assert isinstance(report.render_duration_ms, int)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_same_input(vault, saved_annotations, mock_search):
    write_digest("test-book")
    first = (vault / "KB" / "Wiki" / "Sources" / "Books" / "test-book" / "digest.md").read_text(
        encoding="utf-8"
    )
    # Re-run with same annotations (same mock search results)
    write_digest("test-book")
    second = (vault / "KB" / "Wiki" / "Sources" / "Books" / "test-book" / "digest.md").read_text(
        encoding="utf-8"
    )
    # updated_at will differ; compare body excluding frontmatter
    first_body = first.split("---\n", 2)[-1]
    second_body = second.split("---\n", 2)[-1]
    assert first_body == second_body


# ---------------------------------------------------------------------------
# Empty annotations
# ---------------------------------------------------------------------------


def test_empty_annotations_no_crash(vault, mock_search):
    """No annotations saved — write_digest should return a report without crashing."""
    report = write_digest("no-annotations-book")
    assert isinstance(report, DigestReport)
    assert report.book_id == "no-annotations-book"
    # No file should be written (or file exists with just frontmatter, no chapters)
    p = vault / "KB" / "Wiki" / "Sources" / "Books" / "no-annotations-book" / "digest.md"
    if p.exists():
        assert p.read_text(encoding="utf-8").count("## Ch") == 0


# ---------------------------------------------------------------------------
# Vault rules
# ---------------------------------------------------------------------------


def test_vault_rules_allow_digest_path():
    rules = pytest.importorskip("shared.vault_rules")
    assert "KB/Wiki/Sources/Books/" in rules.READER_WRITE_WHITELIST
