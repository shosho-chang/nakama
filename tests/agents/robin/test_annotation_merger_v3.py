"""ADR-024 Slice 2 (#510) — annotation_merger V3 dispatch.

The V3 unified annotation store (ADR-021 §1) introduces ``AnnotationSetV3``
with discriminated items: ``HighlightV3`` / ``AnnotationV3`` / ``ReflectionV3``.
The merger must route V3 sets correctly:

- V3 book set (``book_id is not None``):
  * whole set → ``KB/Literature/{slug}.md`` via ``literature_writer`` (N521;
    replaces the legacy ``book_notes_writer`` notes.md path)
  * ``AnnotationV3`` → Concept page ``## 讀者註記`` via _ask_merger_llm_v2
  * ``HighlightV3`` → skipped (ADR-017 §Q4)
- V3 paper set (``book_id is None``):
  * ``AnnotationV3`` → Concept page ``## 個人觀點`` via _ask_merger_llm
  * ``ReflectionV3`` → dropped + warned (no V1-paper Reader UI surface)
  * ``HighlightV3`` → skipped

Pre-fix bug: V3 sets fell through to ``_sync_v1`` which dropped reflections
and routed book annotations into the wrong section.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    (tmp_path / "KB" / "Wiki" / "Concepts").mkdir(parents=True)
    (tmp_path / "KB" / "Annotations").mkdir(parents=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _stub_kb_search(monkeypatch):
    """literature_writer 的 ``🔗 KB 相關`` 段呼叫 search_kb；測試環境 stub 掉避免
    依賴 kb_index.db。"""
    import agents.robin.kb_search as kb

    monkeypatch.setattr(kb, "search_kb", lambda *a, **k: [], raising=False)


def _write_concept_stub(vault: Path, slug: str = "anchoring-effect") -> Path:
    p = vault / "KB" / "Wiki" / "Concepts" / f"{slug}.md"
    p.write_text(
        f"""---
slug: {slug}
type: concept
schema_version: 2
---

# {slug}

## Definition
A cognitive bias.

## 個人觀點
""",
        encoding="utf-8",
    )
    return p


def _write_v3_book_set(vault: Path, book_id: str = "how-to-live") -> None:
    """Write an AnnotationSetV3 (book-shaped) via the public store API."""
    from shared.annotation_store import AnnotationStore
    from shared.schemas.annotations import (
        AnnotationSetV3,
        AnnotationV3,
        HighlightV3,
        ReflectionV3,
    )

    ts = "2026-05-09T00:00:00Z"
    h = "a" * 64
    s = AnnotationSetV3(
        slug=book_id,
        base="books",
        book_id=book_id,
        book_version_hash=h,
        items=[
            HighlightV3(
                cfi="epubcfi(/6/4!/4/2:0)",
                text_excerpt="anchoring effect biases all subsequent estimates",
                book_version_hash=h,
                text="anchoring effect biases all subsequent estimates",
                created_at=ts,
                modified_at=ts,
            ),
            AnnotationV3(
                cfi="epubcfi(/6/6!/4/2:0)",
                text_excerpt="anchoring is the dominant prior",
                note="this is the key insight",
                book_version_hash=h,
                created_at=ts,
                modified_at=ts,
            ),
            ReflectionV3(
                chapter_ref="ch03.xhtml",
                cfi_anchor=None,
                book_version_hash=h,
                body="Long V3 reflection prose ...",
                created_at=ts,
                modified_at=ts,
            ),
        ],
        updated_at=ts,
    )
    AnnotationStore().save(s)


def _write_v3_paper_set(vault: Path, slug: str = "paper-x") -> None:
    """Write an AnnotationSetV3 (paper-shaped, book_id=None)."""
    from shared.annotation_store import AnnotationStore
    from shared.schemas.annotations import (
        AnnotationSetV3,
        AnnotationV3,
        HighlightV3,
        ReflectionV3,
    )

    ts = "2026-05-09T00:00:00Z"
    s = AnnotationSetV3(
        slug=slug,
        base="inbox",
        source_filename=f"{slug}.md",
        items=[
            HighlightV3(
                text_excerpt="some highlighted text",
                text="some highlighted text",
                created_at=ts,
                modified_at=ts,
            ),
            AnnotationV3(
                text_excerpt="reference text",
                ref="reference text",
                note="my paper note",
                created_at=ts,
                modified_at=ts,
            ),
            ReflectionV3(
                chapter_ref=None,
                cfi_anchor=None,
                body="paper-side reflection that should be dropped + warned",
                created_at=ts,
                modified_at=ts,
            ),
        ],
        updated_at=ts,
    )
    AnnotationStore().save(s)


# ---------------------------------------------------------------------------
# T-N3: V3 book sync routes ReflectionV3 → notes.md and AnnotationV3 → ## 讀者註記
# ---------------------------------------------------------------------------


def test_n3_v3_book_routes_reflections_to_literature_note(vault: Path, monkeypatch):
    """V3 book set: whole set renders into KB/Literature/{slug}.md (N521).

    Replaces the legacy notes.md assertion — reflections now appear as 章末心得
    in the unified Literature Note, grouped by chapter.
    """
    import agents.robin.annotation_merger as merger_mod

    _write_concept_stub(vault, "anchoring-effect")
    _write_v3_book_set(vault, "how-to-live")

    monkeypatch.setattr(
        merger_mod,
        "_ask_merger_llm_v2",
        lambda items, _slugs: {},
        raising=False,
    )
    merger_mod.sync_annotations_for_slug("how-to-live")

    lit_path = vault / "KB" / "Literature" / "how-to-live.md"
    assert lit_path.exists(), f"Literature Note not written at {lit_path}"
    body = lit_path.read_text(encoding="utf-8")
    assert "type: literature" in body
    assert "Long V3 reflection prose" in body
    assert "**章末心得**" in body
    # legacy notes.md must NOT be written anymore
    notes_path = vault / "KB" / "Wiki" / "Sources" / "Books" / "how-to-live" / "notes.md"
    assert not notes_path.exists(), "legacy notes.md should be retired (N521)"


def test_n3_v3_book_routes_annotations_to_dukezhuji_section(vault: Path, monkeypatch):
    """V3 book set: AnnotationV3 items route to ## 讀者註記 with per-book markers."""
    import agents.robin.annotation_merger as merger_mod

    _write_concept_stub(vault, "anchoring-effect")
    _write_v3_book_set(vault, "how-to-live")

    monkeypatch.setattr(
        merger_mod,
        "_ask_merger_llm_v2",
        lambda items, _slugs: {
            "anchoring-effect": (
                "> [!quote] from how-to-live 讀者註記\n> anchoring is the dominant prior"
            ),
        },
        raising=False,
    )
    merger_mod.sync_annotations_for_slug("how-to-live")

    body = (vault / "KB" / "Wiki" / "Concepts" / "anchoring-effect.md").read_text(encoding="utf-8")
    assert "## 讀者註記" in body
    assert "<!-- annotation-from: how-to-live -->" in body
    assert "from how-to-live 讀者註記" in body
    assert "## 個人觀點" in body  # original section preserved


def test_n3_v3_book_skips_highlights(vault: Path, monkeypatch):
    """ADR-017 §Q4: HighlightV3 items must NOT reach the LLM extractor."""
    import agents.robin.annotation_merger as merger_mod

    _write_concept_stub(vault, "anchoring-effect")
    _write_v3_book_set(vault, "how-to-live")

    captured: list = []

    def fake_extract(items, _slugs):
        captured.extend(items)
        return {}

    monkeypatch.setattr(merger_mod, "_ask_merger_llm_v2", fake_extract, raising=False)
    merger_mod.sync_annotations_for_slug("how-to-live")

    types_seen = {getattr(it, "type", None) for it in captured}
    assert "highlight" not in types_seen
    assert "reflection" not in types_seen
    assert types_seen == {"annotation"} or types_seen == set()


def test_n3_v3_book_keeps_reflections_without_chapter_ref(vault: Path, monkeypatch):
    """N521: reflections without chapter_ref are no longer dropped — the Literature
    writer groups them under an ``unknown`` chapter section (no data loss)."""
    import agents.robin.annotation_merger as merger_mod
    from shared.annotation_store import AnnotationStore
    from shared.schemas.annotations import AnnotationSetV3, ReflectionV3

    ts = "2026-05-09T00:00:00Z"
    h = "a" * 64
    AnnotationStore().save(
        AnnotationSetV3(
            slug="book-orphan",
            base="books",
            book_id="book-orphan",
            book_version_hash=h,
            items=[
                ReflectionV3(
                    chapter_ref=None,
                    cfi_anchor=None,
                    body="reflection without chapter — kept under unknown",
                    created_at=ts,
                    modified_at=ts,
                ),
                ReflectionV3(
                    chapter_ref="ch01.xhtml",
                    cfi_anchor=None,
                    body="kept reflection",
                    created_at=ts,
                    modified_at=ts,
                ),
            ],
            updated_at=ts,
        )
    )
    monkeypatch.setattr(merger_mod, "_ask_merger_llm_v2", lambda items, _slugs: {}, raising=False)

    merger_mod.sync_annotations_for_slug("book-orphan")

    lit_path = vault / "KB" / "Literature" / "book-orphan.md"
    body = lit_path.read_text(encoding="utf-8")
    assert "kept reflection" in body
    assert "reflection without chapter — kept under unknown" in body
    assert "### ch01.xhtml" in body
    assert "## None" not in body


# ---------------------------------------------------------------------------
# T-N4: V3 paper sync — AnnotationV3 → ## 個人觀點; ReflectionV3 dropped + warned
# ---------------------------------------------------------------------------


def test_n4_v3_paper_routes_annotations_to_geren_guandian_section(vault: Path, monkeypatch):
    """V3 paper set: AnnotationV3 → ## 個人觀點 with <!-- annotation-from: {slug} --> markers."""
    import agents.robin.annotation_merger as merger_mod

    _write_concept_stub(vault, "sleep-debt")
    _write_v3_paper_set(vault, "paper-x")

    monkeypatch.setattr(
        merger_mod,
        "_ask_merger_llm",
        lambda _prompt: {"sleep-debt": "> [!annotation] from paper-x\n> my paper note"},
        raising=False,
    )
    merger_mod.sync_annotations_for_slug("paper-x")

    body = (vault / "KB" / "Wiki" / "Concepts" / "sleep-debt.md").read_text(encoding="utf-8")
    assert "## 個人觀點" in body
    assert "<!-- annotation-from: paper-x -->" in body
    assert "from paper-x" in body
    assert "## 讀者註記" not in body


def test_n4_v3_paper_drops_reflections_with_warning(vault: Path, monkeypatch, caplog):
    """V3 paper sets carry reflections only post-migration; they have no Reader UI
    surface (V1 paper had no comment kind), so we drop + warn."""
    import agents.robin.annotation_merger as merger_mod

    _write_concept_stub(vault, "sleep-debt")
    _write_v3_paper_set(vault, "paper-x")

    monkeypatch.setattr(merger_mod, "_ask_merger_llm", lambda _prompt: {}, raising=False)

    with caplog.at_level(logging.WARNING, logger="nakama.robin.annotation_merger"):
        merger_mod.sync_annotations_for_slug("paper-x")

    assert any("v3 paper sync: dropping reflections" in r.getMessage() for r in caplog.records)


def test_n4_v3_paper_skips_highlights(vault: Path, monkeypatch):
    """V3 paper: HighlightV3 must NOT reach the LLM input (ADR-017 §Q4)."""
    import agents.robin.annotation_merger as merger_mod

    _write_concept_stub(vault, "sleep-debt")
    _write_v3_paper_set(vault, "paper-x")

    captured_prompts: list[str] = []

    def fake_llm(prompt: str) -> dict:
        captured_prompts.append(prompt)
        return {}

    monkeypatch.setattr(merger_mod, "_ask_merger_llm", fake_llm, raising=False)
    merger_mod.sync_annotations_for_slug("paper-x")

    # Prompt should mention the annotation note, NOT the highlight body.
    assert captured_prompts, "LLM should have been called for V3 paper annotations"
    prompt = captured_prompts[0]
    assert "my paper note" in prompt
    # Highlight body / reflection body must not leak into the prompt.
    assert "some highlighted text" not in prompt
    assert "paper-side reflection" not in prompt


# ---------------------------------------------------------------------------
# T-N1 / T-N2: byte-equal V1 + V2 regression (V3 dispatch must NOT touch them)
# ---------------------------------------------------------------------------


def test_n1_v1_paper_set_unchanged_after_v3_branch(vault: Path, monkeypatch):
    """V1 AnnotationSetV1 still routes to ## 個人觀點 — V3 dispatch is additive."""
    import agents.robin.annotation_merger as merger_mod
    from shared.annotation_store import (
        Annotation,
        AnnotationSet,
        AnnotationStore,
    )

    _write_concept_stub(vault, "creatine-metabolism")
    ts = "2026-01-01T00:00:00Z"
    AnnotationStore().save(
        AnnotationSet(
            slug="paper-v1",
            source_filename="paper-v1.md",
            base="inbox",
            items=[
                Annotation(
                    ref="reference text",
                    note="v1 paper note",
                    created_at=ts,
                    modified_at=ts,
                )
            ],
            updated_at=ts,
        )
    )

    monkeypatch.setattr(
        merger_mod,
        "_ask_merger_llm",
        lambda _prompt: {"creatine-metabolism": "> from paper-v1"},
        raising=False,
    )
    merger_mod.sync_annotations_for_slug("paper-v1")

    body = (vault / "KB" / "Wiki" / "Concepts" / "creatine-metabolism.md").read_text(
        encoding="utf-8"
    )
    assert "## 個人觀點" in body
    assert "from paper-v1" in body
    assert "<!-- annotation-from: paper-v1 -->" in body
    assert "## 讀者註記" not in body


def test_n2_v2_book_set_unchanged_after_v3_branch(vault: Path, monkeypatch):
    """V2 AnnotationSetV2 still routes to v2 path — V3 dispatch precedes V2 only
    when isinstance(ann_set, AnnotationSetV3) is True. V2 sets must take v2 branch."""
    import agents.robin.annotation_merger as merger_mod
    from shared.annotation_store import AnnotationStore
    from shared.schemas.annotations import (
        AnnotationSetV2,
        AnnotationV2,
        CommentV2,
    )

    _write_concept_stub(vault, "anchoring-effect")
    ts = "2026-05-05T00:00:00Z"
    h = "a" * 64
    AnnotationStore().save(
        AnnotationSetV2(
            slug="v2-book",
            book_id="v2-book",
            book_version_hash=h,
            items=[
                AnnotationV2(
                    cfi="epubcfi(/6/6!/4/2:0)",
                    text_excerpt="anchoring",
                    note="key insight",
                    book_version_hash=h,
                    created_at=ts,
                    modified_at=ts,
                ),
                CommentV2(
                    chapter_ref="ch01.xhtml",
                    cfi_anchor=None,
                    body="V2 comment prose",
                    book_version_hash=h,
                    created_at=ts,
                    modified_at=ts,
                ),
            ],
            updated_at=ts,
        )
    )

    monkeypatch.setattr(
        merger_mod,
        "_ask_merger_llm_v2",
        lambda items, _slugs: {"anchoring-effect": "> from v2-book"},
        raising=False,
    )
    merger_mod.sync_annotations_for_slug("v2-book")

    # V2 still produces the ## 讀者註記 output; the comment prose now lands in
    # the unified Literature Note (N521) instead of the retired notes.md.
    body = (vault / "KB" / "Wiki" / "Concepts" / "anchoring-effect.md").read_text(encoding="utf-8")
    assert "## 讀者註記" in body
    assert "<!-- annotation-from: v2-book -->" in body

    lit_path = vault / "KB" / "Literature" / "v2-book.md"
    assert lit_path.exists()
    lit_body = lit_path.read_text(encoding="utf-8")
    assert "V2 comment prose" in lit_body
    assert "**章末心得**" in lit_body
    assert not (vault / "KB" / "Wiki" / "Sources" / "Books" / "v2-book" / "notes.md").exists()


# ---------------------------------------------------------------------------
# F1 (codex review): malformed V3 set with base="books" + book_id=None must
# NOT silently route to paper sync — it must surface as a SyncReport error.
# ---------------------------------------------------------------------------


def test_v3_book_set_missing_book_id_returns_error_not_paper_route(vault: Path, monkeypatch):
    """base='books' + book_id=None must surface a SyncReport error, not misroute."""
    import agents.robin.annotation_merger as merger_mod
    from shared.annotation_store import AnnotationStore
    from shared.schemas.annotations import AnnotationSetV3, AnnotationV3

    ts = "2026-05-09T00:00:00Z"
    h = "a" * 64
    AnnotationStore().save(
        AnnotationSetV3(
            slug="orphan-book-set",
            base="books",
            book_id=None,
            book_version_hash=h,
            items=[
                AnnotationV3(
                    cfi="epubcfi(/6/4!/4/2:0)",
                    text_excerpt="some excerpt",
                    note="some note",
                    book_version_hash=h,
                    created_at=ts,
                    modified_at=ts,
                ),
            ],
            updated_at=ts,
        )
    )

    paper_called: list[str] = []
    book_called: list[str] = []
    monkeypatch.setattr(
        merger_mod.ConceptPageAnnotationMerger,
        "_sync_v3_paper",
        lambda self, ann_set, slug: paper_called.append(slug) or None,
        raising=False,
    )
    monkeypatch.setattr(
        merger_mod.ConceptPageAnnotationMerger,
        "_sync_v3_book",
        lambda self, ann_set: book_called.append(ann_set.slug) or None,
        raising=False,
    )

    report = merger_mod.sync_annotations_for_slug("orphan-book-set")
    assert paper_called == [], "must not route base='books' book_id=None to paper sync"
    assert book_called == [], "must not route base='books' book_id=None to book sync either"
    assert report.errors and any("book_id" in e for e in report.errors)
    assert report.annotations_merged == 0


# ---------------------------------------------------------------------------
# F2 (codex review): empty-string chapter_ref must be passed through (V2 parity),
# not dropped by truthy filter — V2 rendered '## ' empty heading + body, which
# is degenerate but not silent data loss. Drop only when chapter_ref is None.
# ---------------------------------------------------------------------------


def test_v3_book_all_reflections_survive_in_literature_note(vault: Path, monkeypatch):
    """N521: both empty-string and None chapter_ref reflections survive into the
    Literature Note (no data loss). None-chapter reflections group under the
    ``unknown`` chapter section rather than being dropped."""
    import agents.robin.annotation_merger as merger_mod
    from shared.annotation_store import AnnotationStore
    from shared.schemas.annotations import AnnotationSetV3, ReflectionV3

    ts = "2026-05-09T00:00:00Z"
    h = "a" * 64
    AnnotationStore().save(
        AnnotationSetV3(
            slug="emptychap-book",
            base="books",
            book_id="emptychap-book",
            book_version_hash=h,
            items=[
                ReflectionV3(
                    chapter_ref="",
                    cfi_anchor=None,
                    body="empty-chapter reflection must survive",
                    created_at=ts,
                    modified_at=ts,
                ),
                ReflectionV3(
                    chapter_ref=None,
                    cfi_anchor=None,
                    body="None-chapter reflection also survives now",
                    created_at=ts,
                    modified_at=ts,
                ),
            ],
            updated_at=ts,
        )
    )
    monkeypatch.setattr(merger_mod, "_ask_merger_llm_v2", lambda items, _slugs: {}, raising=False)

    merger_mod.sync_annotations_for_slug("emptychap-book")

    lit_path = vault / "KB" / "Literature" / "emptychap-book.md"
    body = lit_path.read_text(encoding="utf-8")
    assert "empty-chapter reflection must survive" in body
    assert "None-chapter reflection also survives now" in body
