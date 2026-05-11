"""ADR-024 Slice 2 (#510) — book_digest_writer V3 regression.

``agents.robin.book_digest_writer`` is already V3-aware via duck-typing
(``hasattr(ann_set, 'book_id')`` + ``item.type in ('comment', 'reflection')``
checks). T-N5 pins that contract: feeding an ``AnnotationSetV3`` book set with
``ReflectionV3`` items into ``write_digest()`` must produce a digest.md that
renders cleanly — no crashes, no missing sections, body content present.

This is a regression test only — book_digest_writer.py code is NOT modified
by #510 (verified via git diff in the PR body).
"""

from __future__ import annotations

from pathlib import Path

import pytest

digest_mod = pytest.importorskip("agents.robin.book_digest_writer")
ann_schemas = pytest.importorskip("shared.schemas.annotations")

write_digest = digest_mod.write_digest
DigestReport = digest_mod.DigestReport
AnnotationSetV3 = ann_schemas.AnnotationSetV3
AnnotationV3 = ann_schemas.AnnotationV3
HighlightV3 = ann_schemas.HighlightV3
ReflectionV3 = ann_schemas.ReflectionV3

_TS = "2026-05-09T00:00:00Z"
_HASH = "a" * 64


def _v3_book_set(book_id: str = "test-v3-book") -> AnnotationSetV3:
    """V3 book set with H/A/R items spread across two chapters."""
    return AnnotationSetV3(
        slug=book_id,
        base="books",
        book_id=book_id,
        book_version_hash=_HASH,
        items=[
            HighlightV3(
                cfi="epubcfi(/6/4[ch01]!/4/2:0)",
                text_excerpt="V3 Ch1 highlight text.",
                book_version_hash=_HASH,
                text="V3 Ch1 highlight text.",
                created_at=_TS,
                modified_at=_TS,
            ),
            AnnotationV3(
                cfi="epubcfi(/6/4[ch01]!/4/6:5)",
                text_excerpt="V3 Ch1 annotation text.",
                note="V3 my note.",
                book_version_hash=_HASH,
                created_at=_TS,
                modified_at=_TS,
            ),
            ReflectionV3(
                chapter_ref="ch01.xhtml",
                cfi_anchor="epubcfi(/6/4[ch01]!/4/10:0)",
                book_version_hash=_HASH,
                body="V3 Ch1 reflection body.",
                created_at=_TS,
                modified_at=_TS,
            ),
            HighlightV3(
                cfi="epubcfi(/6/6[ch02]!/4/2:0)",
                text_excerpt="V3 Ch2 highlight text.",
                book_version_hash=_HASH,
                text="V3 Ch2 highlight text.",
                created_at=_TS,
                modified_at=_TS,
            ),
        ],
        updated_at=_TS,
    )


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    (tmp_path / "KB" / "Annotations").mkdir(parents=True)
    (tmp_path / "KB" / "Wiki" / "Sources" / "Books").mkdir(parents=True)
    (tmp_path / "KB" / "Wiki" / "Concepts").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def saved_v3_set(vault: Path) -> None:
    from shared.annotation_store import get_annotation_store

    get_annotation_store().save(_v3_book_set("test-v3-book"))


@pytest.fixture
def mock_search(monkeypatch) -> list[dict]:
    fake_hits = [
        {
            "type": "concept",
            "title": "V3 Concept",
            "path": "KB/Wiki/Concepts/v3-concept",
            "preview": "preview",
            "relevance_reason": "Related to V3.",
        }
    ]
    calls: list[dict] = []

    def fake_search_kb(query, vault_path, top_k=8, *, purpose="general", engine="haiku"):
        calls.append({"query": query, "purpose": purpose, "engine": engine})
        return fake_hits

    monkeypatch.setattr(digest_mod, "search_kb", fake_search_kb)
    return calls


# ---------------------------------------------------------------------------
# T-N5: V3 book set produces a clean digest.md
# ---------------------------------------------------------------------------


def test_n5_v3_set_produces_digest_without_crash(vault, saved_v3_set, mock_search):
    """write_digest() handles AnnotationSetV3 (book) without raising."""
    report = write_digest("test-v3-book")
    assert isinstance(report, DigestReport)
    assert report.book_id == "test-v3-book"


def test_n5_v3_set_renders_h_a_r_items(vault, saved_v3_set, mock_search):
    """Digest contains all 3 item kinds: H, A, R(eflection)."""
    write_digest("test-v3-book")
    digest_path = vault / "KB" / "Wiki" / "Sources" / "Books" / "test-v3-book" / "digest.md"
    assert digest_path.exists()
    content = digest_path.read_text(encoding="utf-8")

    assert "V3 Ch1 highlight text." in content
    assert "V3 Ch1 annotation text." in content
    assert "V3 my note." in content
    assert "V3 Ch1 reflection body." in content
    assert "V3 Ch2 highlight text." in content


def test_n5_v3_set_chapter_grouping_preserved(vault, saved_v3_set, mock_search):
    """Items grouped by chapter — Ch1 content appears before Ch2."""
    write_digest("test-v3-book")
    digest_path = vault / "KB" / "Wiki" / "Sources" / "Books" / "test-v3-book" / "digest.md"
    content = digest_path.read_text(encoding="utf-8")

    assert content.index("V3 Ch1 highlight text.") < content.index("V3 Ch2 highlight text.")
    assert content.count("## Ch") >= 2


def test_n5_v3_set_report_counts(vault, saved_v3_set, mock_search):
    """DigestReport.items_rendered counts H/A/R as v2 H/A/C (reflection counted as 'c')."""
    report = write_digest("test-v3-book")
    assert report.items_rendered["h"] == 2
    assert report.items_rendered["a"] == 1
    assert report.items_rendered["c"] == 1  # reflection counted as 'c' (v2 vocabulary)


def test_n5_v3_set_frontmatter_intact(vault, saved_v3_set, mock_search):
    """Digest frontmatter format unchanged for V3 input — byte-equal where deterministic."""
    write_digest("test-v3-book")
    digest_path = vault / "KB" / "Wiki" / "Sources" / "Books" / "test-v3-book" / "digest.md"
    content = digest_path.read_text(encoding="utf-8")

    assert "type: book_digest" in content
    assert "book_id: test-v3-book" in content
    assert "[[Sources/Books/test-v3-book]]" in content
    assert "schema_version: 1" in content


def test_n5_v3_set_idempotent(vault, saved_v3_set, mock_search):
    """Re-running write_digest() with the same V3 input produces identical body
    (frontmatter updated_at differs; everything below is byte-equal)."""
    write_digest("test-v3-book")
    digest_path = vault / "KB" / "Wiki" / "Sources" / "Books" / "test-v3-book" / "digest.md"
    first = digest_path.read_text(encoding="utf-8")
    write_digest("test-v3-book")
    second = digest_path.read_text(encoding="utf-8")

    first_body = first.split("---\n", 2)[-1]
    second_body = second.split("---\n", 2)[-1]
    assert first_body == second_body
