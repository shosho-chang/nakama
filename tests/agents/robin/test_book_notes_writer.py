"""Slice 5B — ``agents/robin/book_notes_writer.py`` write_notes contract.

Behavior:

- `write_notes(book_id, comments)` writes `KB/Wiki/Sources/Books/{book_id}/notes.md`
- Comments are grouped by `chapter_ref` under H2 headings (`## Ch01`, `## Ch02`, …).
  The heading text is the chapter_ref string verbatim (the agent doesn't
  re-format chapter ids).
- Same-chapter comments collapse under one H2 (multiple body paragraphs).
- Different-chapter comments get independent H2 sections.
- Re-running with the same comment list is idempotent (no duplicate body lines).
- Frontmatter carries `book_id` + a wikilink to the Book Entity page.
- `shared/vault_rules.READER_WRITE_WHITELIST` must be extended so the writer
  can hit `KB/Wiki/Sources/Books/<id>/notes.md`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

writer_mod = pytest.importorskip(
    "agents.robin.book_notes_writer",
    reason="agents/robin/book_notes_writer.py is the production module Slice 5B must create",
)
ann_schemas = pytest.importorskip("shared.schemas.annotations")

write_notes = writer_mod.write_notes
CommentV2 = ann_schemas.CommentV2


_TS = "2026-05-05T00:00:00Z"
_HASH = "a" * 64


def _comment(chapter_ref: str, body: str) -> CommentV2:
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


# ---------------------------------------------------------------------------
# Single chapter
# ---------------------------------------------------------------------------


def test_single_comment_writes_notes_md(vault: Path):
    write_notes("how-to-live", [_comment("ch01.xhtml", "First reflection.")])
    p = vault / "KB" / "Wiki" / "Sources" / "Books" / "how-to-live" / "notes.md"
    assert p.exists()
    body = p.read_text(encoding="utf-8")
    assert "## ch01.xhtml" in body or "## Ch01" in body
    assert "First reflection." in body


def test_frontmatter_carries_book_id_and_wikilink(vault: Path):
    write_notes("how-to-live", [_comment("ch01.xhtml", "x")])
    body = (vault / "KB" / "Wiki" / "Sources" / "Books" / "how-to-live" / "notes.md").read_text(
        encoding="utf-8"
    )
    assert "book_id: how-to-live" in body
    # Some flavor of wikilink to the Book Entity page
    assert "[[Sources/Books/how-to-live]]" in body or "[[how-to-live]]" in body


# ---------------------------------------------------------------------------
# Multiple chapters
# ---------------------------------------------------------------------------


def test_multiple_chapters_get_independent_h2(vault: Path):
    write_notes(
        "how-to-live",
        [
            _comment("ch01.xhtml", "Reflection on chapter 1."),
            _comment("ch03.xhtml", "Reflection on chapter 3."),
        ],
    )
    body = (vault / "KB" / "Wiki" / "Sources" / "Books" / "how-to-live" / "notes.md").read_text(
        encoding="utf-8"
    )
    # Both chapter headings present
    assert ("## ch01.xhtml" in body) or ("## Ch01" in body)
    assert ("## ch03.xhtml" in body) or ("## Ch03" in body)
    # Bodies under correct heading (ch1 body before ch3 body)
    assert body.find("Reflection on chapter 1.") < body.find("Reflection on chapter 3.")


def test_same_chapter_comments_merge_under_one_h2(vault: Path):
    write_notes(
        "how-to-live",
        [
            _comment("ch01.xhtml", "First note."),
            _comment("ch01.xhtml", "Second note."),
        ],
    )
    body = (vault / "KB" / "Wiki" / "Sources" / "Books" / "how-to-live" / "notes.md").read_text(
        encoding="utf-8"
    )
    # Only ONE ch01 heading
    h2_count = body.count("## ch01.xhtml") + body.count("## Ch01")
    assert h2_count == 1
    assert "First note." in body
    assert "Second note." in body


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_re_run_is_idempotent(vault: Path):
    cs = [_comment("ch01.xhtml", "Same body.")]
    write_notes("how-to-live", cs)
    write_notes("how-to-live", cs)
    body = (vault / "KB" / "Wiki" / "Sources" / "Books" / "how-to-live" / "notes.md").read_text(
        encoding="utf-8"
    )
    # body line appears exactly once
    assert body.count("Same body.") == 1


def test_replace_semantics_drops_removed_comments(vault: Path):
    """write_notes is full-replace: passing fewer comments removes the old ones."""
    write_notes(
        "how-to-live",
        [
            _comment("ch01.xhtml", "Will-be-kept."),
            _comment("ch02.xhtml", "Will-be-removed."),
        ],
    )
    write_notes(
        "how-to-live",
        [_comment("ch01.xhtml", "Will-be-kept.")],
    )
    body = (vault / "KB" / "Wiki" / "Sources" / "Books" / "how-to-live" / "notes.md").read_text(
        encoding="utf-8"
    )
    assert "Will-be-kept." in body
    assert "Will-be-removed." not in body


# ---------------------------------------------------------------------------
# Empty list
# ---------------------------------------------------------------------------


def test_empty_comments_does_not_crash(vault: Path):
    """An empty list is a no-op (or writes a stub frontmatter-only file)."""
    write_notes("how-to-live", [])
    # Either no file exists OR the file has no body lines — either is acceptable.
    p = vault / "KB" / "Wiki" / "Sources" / "Books" / "how-to-live" / "notes.md"
    if p.exists():
        body = p.read_text(encoding="utf-8")
        assert "## ch" not in body.lower()


# ---------------------------------------------------------------------------
# Vault rules — write must be allowed
# ---------------------------------------------------------------------------


def test_vault_rules_allow_book_notes_path():
    """Rejects the bug where book_notes_writer would otherwise hit
    VaultRuleViolation because the new path isn't in the whitelist."""
    rules = pytest.importorskip("shared.vault_rules")
    assert "KB/Wiki/Sources/Books/" in rules.READER_WRITE_WHITELIST
