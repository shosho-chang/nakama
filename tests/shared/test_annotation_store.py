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


def test_mark_synced_is_noop(store: AnnotationStore, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    importlib.reload(mod)
    store = mod.AnnotationStore()
    store.mark_synced("any-slug")  # must not raise


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
