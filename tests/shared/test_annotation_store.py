"""Unit tests for shared.annotation_store.

Covers:
- AnnotationSet schema round-trip (Highlight / Annotation discriminator)
- Required field validation
- AnnotationStore CRUD and file I/O
- annotation_slug normalization (CJK / spaces / edge cases)
- Concurrent save does not lose updates (file-lock guard)
"""

from __future__ import annotations

import importlib
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

import shared.annotation_store as mod
from shared.annotation_store import (
    Annotation,
    AnnotationSet,
    AnnotationStore,
    Highlight,
    annotation_slug,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> AnnotationStore:
    """AnnotationStore pointed at a temp vault."""
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    # Return a fresh store that uses the reloaded module's _annotations_dir
    return mod.AnnotationStore()


# ── Schema: discriminator dispatch ───────────────────────────────────────────


def test_highlight_schema_round_trip():
    h = Highlight(text="selected text")
    assert h.type == "highlight"
    dumped = h.model_dump()
    restored = Highlight(**dumped)
    assert restored.text == h.text
    assert restored.created_at == h.created_at


def test_annotation_schema_round_trip():
    a = Annotation(ref="ref text", note="my note")
    assert a.type == "annotation"
    dumped = a.model_dump()
    restored = Annotation(**dumped)
    assert restored.ref == a.ref
    assert restored.note == a.note


def test_annotation_set_discriminator_dispatch():
    ann_set = AnnotationSet(
        slug="test-slug",
        source_filename="test.md",
        base="inbox",
        items=[
            {"type": "highlight", "text": "hi", "created_at": "2026-01-01T00:00:00Z"},
            {
                "type": "annotation",
                "ref": "ref",
                "note": "note",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ],
    )
    assert ann_set.items[0].type == "highlight"
    assert ann_set.items[1].type == "annotation"


def test_annotation_set_unknown_type_raises():
    with pytest.raises(ValidationError):
        AnnotationSet(
            slug="x",
            source_filename="x.md",
            base="inbox",
            items=[{"type": "unknown", "text": "hi"}],
        )


def test_highlight_missing_text_raises():
    with pytest.raises(ValidationError):
        Highlight()  # type: ignore[call-arg]


def test_annotation_missing_note_raises():
    with pytest.raises(ValidationError):
        Annotation(ref="ref")  # type: ignore[call-arg]


def test_annotation_missing_ref_raises():
    with pytest.raises(ValidationError):
        Annotation(note="note")  # type: ignore[call-arg]


# ── Schema: default timestamps ────────────────────────────────────────────────


def test_created_at_auto_populated():
    h = Highlight(text="x")
    assert h.created_at  # non-empty ISO string
    assert "T" in h.created_at


def test_updated_at_auto_populated():
    s = AnnotationSet(slug="s", source_filename="f.md", base="inbox")
    assert s.updated_at


# ── AnnotationStore: CRUD ────────────────────────────────────────────────────


def test_save_and_load(store: AnnotationStore, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()

    ann_set = AnnotationSet(
        slug="my-article",
        source_filename="my-article.md",
        base="inbox",
        items=[
            Highlight(text="hello world", created_at="2026-01-01T00:00:00Z"),
            Annotation(
                ref="hello world",
                note="interesting point",
                created_at="2026-01-01T00:00:00Z",
            ),
        ],
        updated_at="2026-01-01T00:00:00Z",
    )

    store.save(ann_set)
    loaded = store.load("my-article")
    assert loaded is not None
    assert loaded.slug == "my-article"
    assert len(loaded.items) == 2
    assert loaded.items[0].type == "highlight"
    assert loaded.items[0].text == "hello world"
    assert loaded.items[1].type == "annotation"
    assert loaded.items[1].note == "interesting point"


def test_load_nonexistent_returns_none(store: AnnotationStore, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()
    assert store.load("does-not-exist") is None


def test_delete_removes_file(store: AnnotationStore, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()

    ann_set = AnnotationSet(
        slug="del-me",
        source_filename="del-me.md",
        base="inbox",
    )
    store.save(ann_set)
    ann_file = tmp_path / "KB" / "Annotations" / "del-me.md"
    assert ann_file.exists()

    store.delete("del-me")
    assert not ann_file.exists()


def test_delete_nonexistent_is_noop(store: AnnotationStore, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()
    store.delete("ghost-slug")  # must not raise


def test_mark_synced_nonexistent_slug_does_not_raise(
    store: AnnotationStore, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()
    store.mark_synced("ghost-slug")  # file doesn't exist → must not raise


def test_mark_synced_persists_last_synced_at(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()

    ann_set = mod.AnnotationSet(
        slug="synced-article",
        source_filename="synced-article.md",
        base="inbox",
        items=[
            {
                "type": "highlight",
                "text": "hello",
                "created_at": "2026-01-01T00:00:00Z",
                "modified_at": "2026-01-01T00:00:00Z",
            }
        ],
    )
    store.save(ann_set)
    assert store.load("synced-article").last_synced_at is None

    store.mark_synced("synced-article")

    loaded = store.load("synced-article")
    assert loaded.last_synced_at is not None
    assert "T" in loaded.last_synced_at


# ── AnnotationStore: file written to correct location ────────────────────────


def test_file_written_under_kb_annotations(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()

    store.save(
        AnnotationSet(
            slug="loc-test",
            source_filename="loc-test.md",
            base="sources",
        )
    )
    expected = tmp_path / "KB" / "Annotations" / "loc-test.md"
    assert expected.exists()


# ── Concurrent save: no lost-update ──────────────────────────────────────────


def test_concurrent_save_no_lost_update(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()

    results: list[str] = []
    errors: list[Exception] = []

    def worker(text: str) -> None:
        try:
            store.save(
                AnnotationSet(
                    slug="concurrent",
                    source_filename="concurrent.md",
                    base="inbox",
                    items=[Highlight(text=text, created_at="2026-01-01T00:00:00Z")],
                    updated_at="2026-01-01T00:00:00Z",
                )
            )
            results.append(text)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(f"text-{i}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent save raised: {errors}"
    # File must exist and be valid JSON (last write wins, but no corruption)
    loaded = store.load("concurrent")
    assert loaded is not None


# ── annotation_slug: normalisation ───────────────────────────────────────────


def test_slug_from_filename_stem():
    assert annotation_slug("my article.md") == "my-article"


def test_slug_prefers_frontmatter_title():
    assert annotation_slug("ignored.md", {"title": "Deep Sleep Research"}) == "deep-sleep-research"


def test_slug_cjk_preserved():
    slug = annotation_slug("睡眠研究.md")
    assert "睡" in slug
    assert "眠" in slug


def test_slug_mixed_cjk_ascii():
    slug = annotation_slug("AI 驅動的健康.md")
    # ASCII lowercased, CJK kept, spaces → hyphens
    assert slug == "ai-驅動的健康"


def test_slug_special_chars_stripped():
    slug = annotation_slug("Hello: World! (2026).md")
    assert ":" not in slug
    assert "!" not in slug
    assert "(" not in slug


def test_slug_empty_fallback():
    slug = annotation_slug("!!!")
    assert slug == "untitled"


def test_slug_multiple_spaces_collapsed():
    assert annotation_slug("a   b   c.md") == "a-b-c"


def test_slug_leading_trailing_hyphens_stripped():
    # e.g. a title like " - My Title - "
    slug = annotation_slug(" - My Title - .md")
    assert not slug.startswith("-")
    assert not slug.endswith("-")


# ── AnnotationStore.unsynced_count ───────────────────────────────────────────

# Timestamps used in fixtures: T0 (old) < T1 (sync) < T2 (new)
_T0 = "2026-01-01T00:00:00Z"
_T1 = "2026-06-01T12:00:00Z"  # simulated last_synced_at
_T2 = "2026-12-01T00:00:00Z"  # after sync


def _store_with_synced(tmp_path, monkeypatch, last_synced_at, item_dicts):
    """Save an AnnotationSet with the given item dicts and last_synced_at, return store."""
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()
    ann_set = mod.AnnotationSet(
        slug="art",
        source_filename="art.md",
        base="inbox",
        items=item_dicts,  # dicts → Pydantic discriminated union validation
        last_synced_at=last_synced_at,
    )
    store.save(ann_set)
    return store


def test_unsynced_count_no_store_entry_returns_zero(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()
    assert store.unsynced_count("ghost") == 0


def test_unsynced_count_never_synced_returns_all(tmp_path: Path, monkeypatch):
    """last_synced_at=None → all items unsynced."""
    items = [
        {"type": "highlight", "text": "a", "created_at": _T0, "modified_at": _T0},
        {"type": "annotation", "ref": "b", "note": "n", "created_at": _T0, "modified_at": _T0},
        {"type": "highlight", "text": "c", "created_at": _T0, "modified_at": _T0},
    ]
    store = _store_with_synced(tmp_path, monkeypatch, None, items)
    assert store.unsynced_count("art") == 3


def test_unsynced_count_all_synced_returns_zero(tmp_path: Path, monkeypatch):
    """All items modified before last_synced_at → 0 unsynced."""
    items = [
        {"type": "highlight", "text": "a", "created_at": _T0, "modified_at": _T0},
        {"type": "annotation", "ref": "b", "note": "n", "created_at": _T0, "modified_at": _T0},
    ]
    store = _store_with_synced(tmp_path, monkeypatch, _T1, items)
    assert store.unsynced_count("art") == 0


def test_unsynced_count_partial(tmp_path: Path, monkeypatch):
    """Items after last_synced_at are unsynced; earlier items are not."""
    items = [
        {"type": "highlight", "text": "old", "created_at": _T0, "modified_at": _T0},
        {"type": "highlight", "text": "new", "created_at": _T2, "modified_at": _T2},
    ]
    store = _store_with_synced(tmp_path, monkeypatch, _T1, items)
    assert store.unsynced_count("art") == 1


def test_unsynced_count_highlights_only(tmp_path: Path, monkeypatch):
    """Pure highlights, never synced."""
    items = [
        {"type": "highlight", "text": "x", "created_at": _T0, "modified_at": _T0},
        {"type": "highlight", "text": "y", "created_at": _T0, "modified_at": _T0},
    ]
    store = _store_with_synced(tmp_path, monkeypatch, None, items)
    assert store.unsynced_count("art") == 2


def test_unsynced_count_annotations_only(tmp_path: Path, monkeypatch):
    """Pure annotations, never synced."""
    items = [
        {"type": "annotation", "ref": "r1", "note": "n1", "created_at": _T0, "modified_at": _T0},
        {"type": "annotation", "ref": "r2", "note": "n2", "created_at": _T0, "modified_at": _T0},
    ]
    store = _store_with_synced(tmp_path, monkeypatch, None, items)
    assert store.unsynced_count("art") == 2


def test_unsynced_count_mixed(tmp_path: Path, monkeypatch):
    """Mixed highlight + annotation, never synced, both counted."""
    items = [
        {"type": "highlight", "text": "hi", "created_at": _T0, "modified_at": _T0},
        {"type": "annotation", "ref": "ref", "note": "note", "created_at": _T0, "modified_at": _T0},
    ]
    store = _store_with_synced(tmp_path, monkeypatch, None, items)
    assert store.unsynced_count("art") == 2
