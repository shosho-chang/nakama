"""Slice 5A — annotation_merger v2 dispatch (book sources).

The existing v1 path (paper-source AnnotationSetV1 → Concept ``## 個人觀點``
section) stays unchanged. v2 introduces a parallel path:

- ``AnnotationSetV2`` (schema_version=2, base="books", book_id present) →
  Concept page ``## 讀者註記`` section, attribution ``from {book_id} 讀者註記``
  (distinct from book-as-source ``mentioned_in:`` frontmatter so the same
  Concept page can carry both the textbook reference AND the user's reading notes).
- v2 only syncs ``HighlightV2`` + ``AnnotationV2`` items; ``CommentV2`` is
  routed to ``book_notes_writer`` (Slice 5B), NOT to Concept pages.
- Idempotent per-source: re-syncing the same v2 set must NOT double-append.
- v1 e2e behavior unchanged (regression guard).
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    (tmp_path / "KB" / "Wiki" / "Concepts").mkdir(parents=True)
    (tmp_path / "KB" / "Annotations").mkdir(parents=True)
    return tmp_path


def _write_concept_stub(vault: Path, slug: str = "anchoring-effect") -> Path:
    """Create a minimal ConceptPageV2 stub so the merger can upsert into it."""
    p = vault / "KB" / "Wiki" / "Concepts" / f"{slug}.md"
    p.write_text(
        f"""---
slug: {slug}
type: concept
schema_version: 2
mentioned_in:
- "[[Sources/Books/how-to-live]]"
---

# {slug}

## Definition
A cognitive bias.

## 個人觀點
""",
        encoding="utf-8",
    )
    return p


def _write_v2_annotation_set(vault: Path, book_id: str = "how-to-live") -> None:
    """Write an AnnotationSetV2 to KB/Annotations/{book_id}.md via the public store API."""
    pytest.importorskip("shared.schemas.annotations")
    pytest.importorskip("shared.annotation_store")
    from shared.annotation_store import AnnotationStore
    from shared.schemas.annotations import (
        AnnotationSetV2,
        AnnotationV2,
        CommentV2,
        HighlightV2,
    )

    ts = "2026-05-05T00:00:00Z"
    h = "a" * 64
    s = AnnotationSetV2(
        slug=book_id,
        book_id=book_id,
        book_version_hash=h,
        items=[
            HighlightV2(
                cfi="epubcfi(/6/4!/4/2:0)",
                text_excerpt="anchoring effect biases all subsequent estimates",
                book_version_hash=h,
                created_at=ts,
                modified_at=ts,
            ),
            AnnotationV2(
                cfi="epubcfi(/6/6!/4/2:0)",
                text_excerpt="anchoring is the dominant prior",
                note="this is the key insight",
                book_version_hash=h,
                created_at=ts,
                modified_at=ts,
            ),
            CommentV2(
                chapter_ref="ch03.xhtml",
                cfi_anchor=None,
                body="Long reflection prose ...",
                book_version_hash=h,
                created_at=ts,
                modified_at=ts,
            ),
        ],
        updated_at=ts,
    )
    AnnotationStore().save(s)


# ---------------------------------------------------------------------------
# Schema-version dispatch
# ---------------------------------------------------------------------------


def test_v2_set_dispatches_to_v2_merger(vault: Path, monkeypatch):
    """A v2 AnnotationSet must route to the v2 sync path. Pin the contract via
    a sentinel — the v2 path writes a section header `## 讀者註記` that v1
    never produces. We assert the file's body content delta on a stub Concept
    page after running the merger."""
    merger_mod = pytest.importorskip("agents.robin.annotation_merger")
    if not hasattr(merger_mod, "sync_annotations_for_slug"):
        pytest.skip("v2 dispatch entry point not yet implemented")

    _write_concept_stub(vault, "anchoring-effect")
    _write_v2_annotation_set(vault, "how-to-live")

    # Stub the LLM call so the test stays hermetic.
    def fake_extract(items, _concept_slugs):
        return {
            "anchoring-effect": (
                "> [!quote] from how-to-live 讀者註記\n> anchoring is the dominant prior"
            ),
        }

    monkeypatch.setattr(merger_mod, "_ask_merger_llm_v2", fake_extract, raising=False)

    merger_mod.sync_annotations_for_slug("how-to-live")

    body = (vault / "KB" / "Wiki" / "Concepts" / "anchoring-effect.md").read_text(encoding="utf-8")
    assert "## 讀者註記" in body
    assert "from how-to-live 讀者註記" in body


def test_v2_attribution_distinguishes_from_paper_attribution(vault: Path, monkeypatch):
    """A Concept page that already has ## 個人觀點 (v1 paper) MUST keep that
    section AND get the new ## 讀者註記 (v2 book) section side-by-side."""
    merger_mod = pytest.importorskip("agents.robin.annotation_merger")
    if not hasattr(merger_mod, "sync_annotations_for_slug"):
        pytest.skip("v2 dispatch entry point not yet implemented")

    p = vault / "KB" / "Wiki" / "Concepts" / "anchoring-effect.md"
    p.write_text(
        """---
slug: anchoring-effect
type: concept
schema_version: 2
mentioned_in: []
---

## 個人觀點
<!-- annotation-from: existing-paper -->
> from a paper
<!-- end-annotation-from: existing-paper -->
""",
        encoding="utf-8",
    )
    _write_v2_annotation_set(vault, "how-to-live")

    monkeypatch.setattr(
        merger_mod,
        "_ask_merger_llm_v2",
        lambda items, _slugs: {"anchoring-effect": "> from how-to-live"},
        raising=False,
    )
    merger_mod.sync_annotations_for_slug("how-to-live")

    body = p.read_text(encoding="utf-8")
    assert "## 個人觀點" in body
    assert "## 讀者註記" in body
    assert "existing-paper" in body  # v1 not erased


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_v2_re_sync_does_not_double_append(vault: Path, monkeypatch):
    merger_mod = pytest.importorskip("agents.robin.annotation_merger")
    if not hasattr(merger_mod, "sync_annotations_for_slug"):
        pytest.skip("v2 dispatch entry point not yet implemented")

    _write_concept_stub(vault, "anchoring-effect")
    _write_v2_annotation_set(vault, "how-to-live")

    monkeypatch.setattr(
        merger_mod,
        "_ask_merger_llm_v2",
        lambda items, _slugs: {"anchoring-effect": "> from how-to-live"},
        raising=False,
    )
    merger_mod.sync_annotations_for_slug("how-to-live")
    merger_mod.sync_annotations_for_slug("how-to-live")

    body = (vault / "KB" / "Wiki" / "Concepts" / "anchoring-effect.md").read_text(encoding="utf-8")
    # Each annotation-from boundary marker appears at most once
    assert body.count("<!-- annotation-from: how-to-live -->") <= 1
    assert body.count("from how-to-live") <= 2  # heading + body line


# ---------------------------------------------------------------------------
# Comment items routing
# ---------------------------------------------------------------------------


def test_v2_comment_items_not_synced_to_concept_pages(vault: Path, monkeypatch):
    """Comment-type items belong to book_notes_writer (Slice 5B), NOT to
    Concept pages. The merger must filter them out before LLM extraction."""
    merger_mod = pytest.importorskip("agents.robin.annotation_merger")
    if not hasattr(merger_mod, "sync_annotations_for_slug"):
        pytest.skip("v2 dispatch entry point not yet implemented")

    captured: list = []

    def fake_extract(items, _concept_slugs):
        captured.extend(items)
        return {}

    _write_concept_stub(vault, "anchoring-effect")
    _write_v2_annotation_set(vault, "how-to-live")
    monkeypatch.setattr(merger_mod, "_ask_merger_llm_v2", fake_extract, raising=False)

    merger_mod.sync_annotations_for_slug("how-to-live")

    types_seen = {getattr(it, "type", None) for it in captured}
    assert "comment" not in types_seen, f"merger leaked comments to LLM input: {types_seen}"


def test_v2_comment_items_routed_to_notes_md(vault: Path, monkeypatch):
    """End-to-end: a v2 sync run with a Comment item must produce
    KB/Wiki/Sources/Books/{book_id}/notes.md grouped by chapter (Slice 5B).
    Catches the Slice 5 wiring gap where book_notes_writer was defined but
    never invoked from the sync entry point."""
    merger_mod = pytest.importorskip("agents.robin.annotation_merger")
    if not hasattr(merger_mod, "sync_annotations_for_slug"):
        pytest.skip("v2 dispatch entry point not yet implemented")

    _write_concept_stub(vault, "anchoring-effect")
    _write_v2_annotation_set(vault, "how-to-live")  # contains 1 comment in ch03.xhtml

    monkeypatch.setattr(
        merger_mod,
        "_ask_merger_llm_v2",
        lambda items, _slugs: {},
        raising=False,
    )
    merger_mod.sync_annotations_for_slug("how-to-live")

    notes_path = vault / "KB" / "Wiki" / "Sources" / "Books" / "how-to-live" / "notes.md"
    assert notes_path.exists(), f"notes.md not written at {notes_path}"
    body = notes_path.read_text(encoding="utf-8")
    assert "Long reflection prose" in body
    assert "ch03.xhtml" in body  # chapter_ref preserved as H2
    assert "book_id: how-to-live" in body  # frontmatter wired


# ---------------------------------------------------------------------------
# v1 regression — existing paper path unchanged
# ---------------------------------------------------------------------------


def test_v1_paper_set_still_works_after_dispatch_extension(vault: Path, monkeypatch):
    """A v1 AnnotationSet must still route to the legacy ## 個人觀點 path."""
    merger_mod = pytest.importorskip("agents.robin.annotation_merger")
    if not hasattr(merger_mod, "sync_annotations_for_slug"):
        pytest.skip("v2 dispatch entry point not yet implemented")

    from shared.annotation_store import (
        Annotation,
        AnnotationSet,
        AnnotationStore,
    )

    _write_concept_stub(vault, "sleep-debt")
    ts = "2026-01-01T00:00:00Z"
    AnnotationStore().save(
        AnnotationSet(
            slug="paper-x",
            source_filename="paper-x.md",
            base="inbox",
            items=[
                Annotation(
                    ref="reference text", note="my paper note", created_at=ts, modified_at=ts
                )
            ],
            updated_at=ts,
        )
    )

    monkeypatch.setattr(
        merger_mod,
        "_ask_merger_llm",
        lambda _prompt: {"sleep-debt": "> from paper-x"},
        raising=False,
    )
    merger_mod.sync_annotations_for_slug("paper-x")

    body = (vault / "KB" / "Wiki" / "Concepts" / "sleep-debt.md").read_text(encoding="utf-8")
    assert "## 個人觀點" in body
    assert "from paper-x" in body
    assert "## 讀者註記" not in body  # v1 doesn't trigger v2 section
