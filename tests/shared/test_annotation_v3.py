"""ADR-021 §1: v3 annotation schema, store round-trip, and v1/v2 → v3 upgrade.

Covers:
- v3 ``HighlightV3`` / ``AnnotationV3`` / ``ReflectionV3`` discriminated union
- ``CommentV2`` alias preserved (backward import compat)
- ``AnnotationStore`` writes v3 frontmatter and reads it back as v3
- ``AnnotationStore`` still reads pre-existing v1 / v2 files unchanged
- ``upgrade_to_v3`` upgrades a v1 paper set + a v2 book set lossless
- Migration script ``scripts.migrate_annotations_v3`` upgrades v1 + skips v3
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError

import shared.annotation_store as mod
from shared.schemas.annotations import (
    AnnotationSetV1,
    AnnotationSetV2,
    AnnotationSetV3,
    AnnotationV3,
    CommentV2,
    HighlightV1,
    HighlightV3,
    ReflectionV3,
)

_TS = "2026-05-07T00:00:00Z"
_HASH = "b" * 64


# ── v3 schema ─────────────────────────────────────────────────────────────────


def test_highlight_v3_round_trip():
    h = HighlightV3(
        text_excerpt="excerpted text",
        text="excerpted text",
        cfi="epubcfi(/6/4!/4/2:0)",
        book_version_hash=_HASH,
        created_at=_TS,
        modified_at=_TS,
    )
    assert h.type == "highlight"
    assert h.schema_version == 3
    restored = HighlightV3(**h.model_dump())
    assert restored == h


def test_annotation_v3_keeps_paper_ref_field():
    a = AnnotationV3(
        text_excerpt="some span",
        ref="some span",
        note="my thought",
        created_at=_TS,
        modified_at=_TS,
    )
    assert a.type == "annotation"
    assert a.ref == "some span"


def test_reflection_v3_round_trip():
    r = ReflectionV3(
        chapter_ref="ch01.xhtml",
        cfi_anchor=None,
        body="long-form chapter reflection",
        created_at=_TS,
        modified_at=_TS,
    )
    assert r.type == "reflection"
    assert r.schema_version == 3


def test_v3_set_discriminator_dispatch():
    s = AnnotationSetV3(
        slug="mixed",
        items=[
            {
                "type": "highlight",
                "text_excerpt": "x",
                "text": "x",
                "created_at": _TS,
                "modified_at": _TS,
            },
            {
                "type": "annotation",
                "text_excerpt": "y",
                "note": "n",
                "created_at": _TS,
                "modified_at": _TS,
            },
            {
                "type": "reflection",
                "chapter_ref": "ch01",
                "body": "b",
                "created_at": _TS,
                "modified_at": _TS,
            },
        ],
    )
    assert [i.type for i in s.items] == ["highlight", "annotation", "reflection"]


def test_v3_unknown_type_raises():
    with pytest.raises(ValidationError):
        AnnotationSetV3(slug="x", items=[{"type": "comment", "body": "n"}])


def test_comment_v2_alias_still_importable():
    """Existing imports for ``CommentV2`` must keep working (alias preserved)."""
    c = CommentV2(
        chapter_ref="ch01",
        cfi_anchor=None,
        body="b",
        book_version_hash=_HASH,
        created_at=_TS,
        modified_at=_TS,
    )
    assert c.type == "comment"


# ── Store round-trip ──────────────────────────────────────────────────────────


def _store(tmp_path: Path, monkeypatch) -> mod.AnnotationStore:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    return mod.AnnotationStore()


def test_store_save_load_v3_round_trip(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    s = mod.AnnotationSetV3(
        slug="paper-x",
        base="inbox",
        source_filename="paper-x.md",
        items=[
            HighlightV3(text_excerpt="hi", text="hi", created_at=_TS, modified_at=_TS),
            AnnotationV3(
                text_excerpt="ref text",
                ref="ref text",
                note="my note",
                created_at=_TS,
                modified_at=_TS,
            ),
        ],
        updated_at=_TS,
    )
    store.save(s)
    raw = (tmp_path / "KB" / "Annotations" / "paper-x.md").read_text(encoding="utf-8")
    assert "schema_version: 3" in raw
    assert "source: paper-x.md" in raw

    loaded = store.load("paper-x")
    assert isinstance(loaded, mod.AnnotationSetV3)
    assert len(loaded.items) == 2
    assert loaded.items[0].text == "hi"
    assert loaded.items[1].note == "my note"


def test_store_load_v3_book_rooted(tmp_path, monkeypatch):
    """Pre-emptive guard: a v3 file with ``base: books`` must NOT be mis-read as v2."""
    store = _store(tmp_path, monkeypatch)
    s = mod.AnnotationSetV3(
        slug="book-y",
        base="books",
        book_id="book-y",
        book_version_hash=_HASH,
        items=[
            ReflectionV3(
                chapter_ref="ch01.xhtml",
                body="reflection body",
                created_at=_TS,
                modified_at=_TS,
            ),
        ],
        updated_at=_TS,
    )
    store.save(s)
    loaded = store.load("book-y")
    assert isinstance(loaded, mod.AnnotationSetV3)
    assert loaded.items[0].type == "reflection"


def test_store_still_reads_legacy_v1(tmp_path, monkeypatch):
    """v1 files written by previous app versions must keep loading."""
    store = _store(tmp_path, monkeypatch)
    legacy = AnnotationSetV1(
        slug="legacy-paper",
        source_filename="legacy.md",
        items=[HighlightV1(text="legacy hi", created_at=_TS, modified_at=_TS)],
        updated_at=_TS,
    )
    store.save(legacy)
    loaded = store.load("legacy-paper")
    assert isinstance(loaded, AnnotationSetV1)
    assert loaded.items[0].text == "legacy hi"


def test_store_still_reads_legacy_v2(tmp_path, monkeypatch):
    """v2 book files must keep loading after v3 dispatch lands."""
    store = _store(tmp_path, monkeypatch)
    v2 = AnnotationSetV2(
        slug="legacy-book",
        book_id="legacy-book",
        book_version_hash=_HASH,
        items=[
            CommentV2(
                chapter_ref="ch01",
                cfi_anchor=None,
                body="legacy reflection",
                book_version_hash=_HASH,
                created_at=_TS,
                modified_at=_TS,
            )
        ],
        updated_at=_TS,
    )
    store.save(v2)
    loaded = store.load("legacy-book")
    assert isinstance(loaded, AnnotationSetV2)
    assert loaded.items[0].type == "comment"


# ── upgrade_to_v3 ─────────────────────────────────────────────────────────────


def test_upgrade_v1_paper_to_v3_lossless():
    v1 = AnnotationSetV1(
        slug="paper",
        source_filename="paper.md",
        base="inbox",
        items=[
            HighlightV1(text="hl", created_at=_TS, modified_at=_TS),
            {
                "type": "annotation",
                "ref": "ref text",
                "note": "n",
                "created_at": _TS,
                "modified_at": _TS,
            },
        ],
        updated_at=_TS,
    )
    v3 = mod.upgrade_to_v3(v1)
    assert isinstance(v3, AnnotationSetV3)
    assert v3.source_filename == "paper.md"
    assert v3.items[0].type == "highlight"
    assert v3.items[0].text == "hl"
    assert v3.items[0].text_excerpt == "hl"
    assert v3.items[1].type == "annotation"
    assert v3.items[1].ref == "ref text"
    assert v3.items[1].note == "n"


def test_upgrade_v2_book_to_v3_renames_comment_to_reflection():
    v2 = AnnotationSetV2(
        slug="book",
        book_id="book",
        book_version_hash=_HASH,
        items=[
            {
                "type": "comment",
                "chapter_ref": "ch1",
                "cfi_anchor": None,
                "body": "long body",
                "book_version_hash": _HASH,
                "created_at": _TS,
                "modified_at": _TS,
            },
        ],
        updated_at=_TS,
    )
    v3 = mod.upgrade_to_v3(v2)
    assert isinstance(v3, AnnotationSetV3)
    assert v3.book_id == "book"
    assert v3.book_version_hash == _HASH
    assert v3.items[0].type == "reflection"
    assert v3.items[0].body == "long body"


def test_upgrade_v3_is_identity():
    v3 = AnnotationSetV3(slug="x", items=[])
    assert mod.upgrade_to_v3(v3) is v3


# ── Migration script ─────────────────────────────────────────────────────────


def test_migration_script_upgrades_v1_and_skips_v3(tmp_path, monkeypatch, capsys):
    """End-to-end: write a v1 file + a v3 file, run the script, assert outcomes."""
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()

    # v1 file
    v1 = AnnotationSetV1(
        slug="legacy",
        source_filename="legacy.md",
        items=[HighlightV1(text="x", created_at=_TS, modified_at=_TS)],
        updated_at=_TS,
    )
    store.save(v1)

    # already-v3 file
    v3 = AnnotationSetV3(
        slug="modern",
        base="inbox",
        source_filename="modern.md",
        items=[HighlightV3(text_excerpt="m", text="m", created_at=_TS, modified_at=_TS)],
        updated_at=_TS,
    )
    store.save(v3)

    # Run the script
    from scripts import migrate_annotations_v3 as script

    importlib.reload(script)  # pick up the new VAULT_PATH
    rc = script.main(argv=[])
    assert rc == 0

    out = capsys.readouterr().out
    assert "upgraded=1" in out
    assert "already_v3=1" in out
    assert "failed=0" in out

    # Confirm the v1 file is now v3 on disk
    importlib.reload(mod)
    legacy_loaded = mod.AnnotationStore().load("legacy")
    assert isinstance(legacy_loaded, AnnotationSetV3)


def test_migration_script_dry_run_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()

    v1 = AnnotationSetV1(
        slug="dry",
        source_filename="dry.md",
        items=[HighlightV1(text="x", created_at=_TS, modified_at=_TS)],
        updated_at=_TS,
    )
    store.save(v1)
    raw_before = (tmp_path / "KB" / "Annotations" / "dry.md").read_text(encoding="utf-8")

    from scripts import migrate_annotations_v3 as script

    importlib.reload(script)
    rc = script.main(argv=["--dry-run"])
    assert rc == 0

    raw_after = (tmp_path / "KB" / "Annotations" / "dry.md").read_text(encoding="utf-8")
    assert raw_before == raw_after  # no write performed
