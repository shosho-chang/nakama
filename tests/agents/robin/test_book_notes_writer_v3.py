"""ADR-024 Slice 2 (#510) — book_notes_writer accepts ReflectionV3 via duck-typing.

T-N6: ``book_notes_writer.write_notes(book_id, reflections)`` produces the
same notes.md shape whether the items are V2 ``CommentV2`` or V3
``ReflectionV3``. Both expose ``chapter_ref`` and ``body`` fields with
matching shapes; the writer never does ``isinstance`` checks, so duck-typing
works at runtime.

This test exists to keep the duck-typing contract honest: any future change
that adds an ``isinstance(c, CommentV2)`` filter inside write_notes would
silently regress the V3 book sync path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

writer_mod = pytest.importorskip("agents.robin.book_notes_writer")
ann_schemas = pytest.importorskip("shared.schemas.annotations")

write_notes = writer_mod.write_notes
ReflectionV3 = ann_schemas.ReflectionV3
CommentV2 = ann_schemas.CommentV2

_TS = "2026-05-09T00:00:00Z"
_HASH = "a" * 64


def _v3_reflection(chapter_ref: str, body: str) -> ReflectionV3:
    return ReflectionV3(
        chapter_ref=chapter_ref,
        cfi_anchor=None,
        book_version_hash=_HASH,
        body=body,
        created_at=_TS,
        modified_at=_TS,
    )


def _v2_comment(chapter_ref: str, body: str) -> CommentV2:
    return CommentV2(
        chapter_ref=chapter_ref,
        cfi_anchor=None,
        body=body,
        book_version_hash=_HASH,
        created_at=_TS,
        modified_at=_TS,
    )


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    return tmp_path


def test_n6_write_notes_accepts_reflection_v3(vault: Path):
    """ReflectionV3 list produces notes.md with chapter heading + body."""
    write_notes(
        "v3-book",
        [_v3_reflection("ch01.xhtml", "V3 reflection prose.")],
    )
    p = vault / "KB" / "Wiki" / "Sources" / "Books" / "v3-book" / "notes.md"
    assert p.exists()
    body = p.read_text(encoding="utf-8")
    assert "## ch01.xhtml" in body
    assert "V3 reflection prose." in body
    assert "book_id: v3-book" in body


def test_n6_write_notes_v3_v2_byte_equal_for_equivalent_input(vault: Path, monkeypatch):
    """V2 CommentV2 input and V3 ReflectionV3 input with the same chapter_ref +
    body produce byte-equal notes.md (modulo updated_at timestamp)."""
    # Freeze timestamps so the frontmatter line matches byte-for-byte.
    monkeypatch.setattr(writer_mod, "_now_iso", lambda: "2026-05-09T00:00:00Z")

    write_notes("v2-book", [_v2_comment("ch01.xhtml", "Same body content.")])
    v2_body = (vault / "KB" / "Wiki" / "Sources" / "Books" / "v2-book" / "notes.md").read_text(
        encoding="utf-8"
    )

    write_notes("v3-book", [_v3_reflection("ch01.xhtml", "Same body content.")])
    v3_body = (vault / "KB" / "Wiki" / "Sources" / "Books" / "v3-book" / "notes.md").read_text(
        encoding="utf-8"
    )

    # Substitute book_id so we can compare structures.
    assert v2_body.replace("v2-book", "BOOK") == v3_body.replace("v3-book", "BOOK")


def test_n6_write_notes_v3_multiple_chapters(vault: Path):
    """Multiple V3 reflections across chapters get independent H2 sections."""
    write_notes(
        "v3-book",
        [
            _v3_reflection("ch01.xhtml", "Chapter 1 prose."),
            _v3_reflection("ch03.xhtml", "Chapter 3 prose."),
        ],
    )
    body = (vault / "KB" / "Wiki" / "Sources" / "Books" / "v3-book" / "notes.md").read_text(
        encoding="utf-8"
    )
    assert "## ch01.xhtml" in body
    assert "## ch03.xhtml" in body
    assert body.find("Chapter 1 prose.") < body.find("Chapter 3 prose.")


def test_n6_write_notes_v3_same_chapter_collapses(vault: Path):
    """V3 reflections with the same chapter_ref collapse under one H2."""
    write_notes(
        "v3-book",
        [
            _v3_reflection("ch01.xhtml", "First V3 prose."),
            _v3_reflection("ch01.xhtml", "Second V3 prose."),
        ],
    )
    body = (vault / "KB" / "Wiki" / "Sources" / "Books" / "v3-book" / "notes.md").read_text(
        encoding="utf-8"
    )
    assert body.count("## ch01.xhtml") == 1
    assert "First V3 prose." in body
    assert "Second V3 prose." in body
