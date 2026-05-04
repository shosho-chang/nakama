"""Unit tests for agents.robin.annotation_merger.

Coverage:
- _replace_marker_block: boundary marker find/replace logic (no LLM)
  - creates ## 個人觀點 section when absent
  - appends inside existing section when no marker yet
  - replaces existing marker block (idempotent)
  - per-source isolation (source A block unchanged when syncing source B)
- ConceptPageAnnotationMerger.sync_source_to_concepts:
  - Annotation.type="highlight" items are not processed
  - empty annotations → noop SyncReport
  - store returns None → error in report
  - missing concept page → skip + increment skipped count
  - happy path: annotations + existing concept page → page updated

LLM boundary (_ask_merger_llm) is monkeypatched throughout.
AnnotationStore is monkeypatched on the merger module's namespace.
Vault is isolated to tmp_path via VAULT_PATH env.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import agents.robin.annotation_merger as mod
from agents.robin.annotation_merger import (
    ConceptPageAnnotationMerger,
    SyncReport,
    _replace_marker_block,
)
from shared.annotation_store import Annotation, AnnotationSet, Highlight

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    """Redirect vault to tmp_path."""
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    import shared.config as cfg

    cfg._config = None
    return tmp_path


@pytest.fixture
def mock_llm(monkeypatch):
    """Replace _ask_merger_llm with a stub returning empty dict by default."""
    calls: list[str] = []

    def fake(prompt: str) -> dict[str, str]:
        calls.append(prompt)
        return {}

    monkeypatch.setattr(mod, "_ask_merger_llm", fake)
    return calls


# ---------------------------------------------------------------------------
# _replace_marker_block: pure logic, no I/O
# ---------------------------------------------------------------------------


def test_replace_creates_section_when_absent():
    body = "## Definition\n\nsome text\n"
    result = _replace_marker_block(body, "book-ch1", "> [!annotation] note")
    assert "## 個人觀點" in result
    assert "<!-- annotation-from: book-ch1 -->" in result
    assert "<!-- /annotation-from: book-ch1 -->" in result
    assert "> [!annotation] note" in result


def test_replace_appends_inside_existing_section_no_marker():
    body = "## Definition\n\nsome text\n\n## 個人觀點\n\n_already here_\n"
    result = _replace_marker_block(body, "book-ch1", "> [!annotation] note")
    assert "<!-- annotation-from: book-ch1 -->" in result
    assert "> [!annotation] note" in result
    # existing section content preserved
    assert "_already here_" in result


def test_replace_replaces_existing_marker():
    body = (
        "## Definition\n\ntext\n\n"
        "## 個人觀點\n\n"
        "<!-- annotation-from: book-ch1 -->\n"
        "> [!annotation] OLD NOTE\n"
        "<!-- /annotation-from: book-ch1 -->\n"
    )
    result = _replace_marker_block(body, "book-ch1", "> [!annotation] NEW NOTE")
    assert "> [!annotation] NEW NOTE" in result
    assert "> [!annotation] OLD NOTE" not in result
    assert result.count("<!-- annotation-from: book-ch1 -->") == 1
    assert result.count("<!-- /annotation-from: book-ch1 -->") == 1


def test_replace_idempotent():
    body = "## Definition\n\ntext\n"
    callout = "> [!annotation] some note"
    first = _replace_marker_block(body, "src", callout)
    second = _replace_marker_block(first, "src", callout)
    assert first == second


def test_replace_source_isolation():
    body = (
        "## 個人觀點\n\n"
        "<!-- annotation-from: source-a -->\n"
        "> [!annotation] FROM A\n"
        "<!-- /annotation-from: source-a -->\n"
    )
    result = _replace_marker_block(body, "source-b", "> [!annotation] FROM B")
    # A's block untouched
    assert "<!-- annotation-from: source-a -->" in result
    assert "> [!annotation] FROM A" in result
    # B's block added
    assert "<!-- annotation-from: source-b -->" in result
    assert "> [!annotation] FROM B" in result


def test_replace_updates_source_a_without_touching_source_b():
    body = (
        "## 個人觀點\n\n"
        "<!-- annotation-from: source-a -->\n"
        "> [!annotation] OLD A\n"
        "<!-- /annotation-from: source-a -->\n\n"
        "<!-- annotation-from: source-b -->\n"
        "> [!annotation] FROM B\n"
        "<!-- /annotation-from: source-b -->\n"
    )
    result = _replace_marker_block(body, "source-a", "> [!annotation] NEW A")
    assert "> [!annotation] NEW A" in result
    assert "> [!annotation] OLD A" not in result
    # B untouched
    assert "<!-- annotation-from: source-b -->" in result
    assert "> [!annotation] FROM B" in result


# ---------------------------------------------------------------------------
# ConceptPageAnnotationMerger.sync_source_to_concepts
# ---------------------------------------------------------------------------


def test_sync_store_returns_none(vault, mock_llm, monkeypatch):
    monkeypatch.setattr(mod, "get_annotation_store", lambda: _make_store(None))
    merger = ConceptPageAnnotationMerger()
    report = merger.sync_source_to_concepts("missing-slug")
    assert isinstance(report, SyncReport)
    assert report.annotations_merged == 0
    assert len(report.errors) > 0
    assert mock_llm == []  # LLM not called


def test_sync_empty_items_returns_noop(vault, mock_llm, monkeypatch):
    ann_set = AnnotationSet(slug="src", source_filename="src.md", base="inbox", items=[])
    monkeypatch.setattr(mod, "get_annotation_store", lambda: _make_store(ann_set))
    merger = ConceptPageAnnotationMerger()
    report = merger.sync_source_to_concepts("src")
    assert report.annotations_merged == 0
    assert report.concepts_updated == []
    assert mock_llm == []


def test_sync_highlights_only_treated_as_empty(vault, mock_llm, monkeypatch):
    ann_set = AnnotationSet(
        slug="src",
        source_filename="src.md",
        base="inbox",
        items=[Highlight(text="highlighted text", created_at="2026-05-04T00:00:00Z")],
    )
    monkeypatch.setattr(mod, "get_annotation_store", lambda: _make_store(ann_set))
    merger = ConceptPageAnnotationMerger()
    report = merger.sync_source_to_concepts("src")
    assert report.annotations_merged == 0
    assert mock_llm == []


def test_sync_missing_concept_skipped(vault, mock_llm, monkeypatch):
    ann_set = AnnotationSet(
        slug="src",
        source_filename="src.md",
        base="inbox",
        items=[Annotation(ref="some ref", note="my note", created_at="2026-05-04T00:00:00Z")],
    )
    monkeypatch.setattr(mod, "get_annotation_store", lambda: _make_store(ann_set))

    # LLM says concept exists but we don't create the file
    def fake_llm(prompt: str) -> dict[str, str]:
        mock_llm.append(prompt)
        return {"nonexistent-concept": "> [!annotation] note"}

    monkeypatch.setattr(mod, "_ask_merger_llm", fake_llm)

    merger = ConceptPageAnnotationMerger()
    report = merger.sync_source_to_concepts("src")
    assert report.annotations_merged == 1
    assert report.concepts_updated == []
    assert report.skipped_annotations == 1


def test_sync_happy_path_updates_concept(vault, monkeypatch):
    # Create a concept page
    concept_dir = vault / "KB" / "Wiki" / "Concepts"
    concept_dir.mkdir(parents=True)
    concept_page = concept_dir / "肌酸代謝.md"
    concept_page.write_text(
        "---\ntitle: 肌酸代謝\n---\n\n## Definition\n\nCreatine metabolism.\n",
        encoding="utf-8",
    )

    ann_set = AnnotationSet(
        slug="sport-nutrition-ch3",
        source_filename="sport-nutrition.md",
        base="sources",
        items=[
            Annotation(
                ref="Creatine supplementation",
                note="很重要的筆記",
                created_at="2026-05-04T00:00:00Z",
            )
        ],
    )
    monkeypatch.setattr(mod, "get_annotation_store", lambda: _make_store(ann_set))

    callout = (
        "> [!annotation] from [[sport-nutrition-ch3]] · 2026-05-04\n"
        "> **Ref**: Creatine supplementation\n"
        "> 很重要的筆記"
    )

    def fake_llm(prompt: str) -> dict[str, str]:
        return {"肌酸代謝": callout}

    monkeypatch.setattr(mod, "_ask_merger_llm", fake_llm)

    merger = ConceptPageAnnotationMerger()
    report = merger.sync_source_to_concepts("sport-nutrition-ch3")

    assert report.annotations_merged == 1
    assert "肌酸代謝" in report.concepts_updated
    assert report.skipped_annotations == 0
    assert report.errors == []

    updated = concept_page.read_text(encoding="utf-8")
    assert "<!-- annotation-from: sport-nutrition-ch3 -->" in updated
    assert "<!-- /annotation-from: sport-nutrition-ch3 -->" in updated
    assert "很重要的筆記" in updated
    assert "## 個人觀點" in updated


def test_sync_idempotent(vault, monkeypatch):
    concept_dir = vault / "KB" / "Wiki" / "Concepts"
    concept_dir.mkdir(parents=True)
    concept_page = concept_dir / "睡眠品質.md"
    concept_page.write_text(
        "---\ntitle: 睡眠品質\n---\n\n## Definition\n\nSleep quality.\n",
        encoding="utf-8",
    )

    ann_set = AnnotationSet(
        slug="book-ch5",
        source_filename="book.md",
        base="sources",
        items=[Annotation(ref="sleep ref", note="sleep note", created_at="2026-05-04T00:00:00Z")],
    )
    monkeypatch.setattr(mod, "get_annotation_store", lambda: _make_store(ann_set))

    callout = "> [!annotation] from [[book-ch5]] · 2026-05-04\n> sleep note"

    def fake_llm(prompt: str) -> dict[str, str]:
        return {"睡眠品質": callout}

    monkeypatch.setattr(mod, "_ask_merger_llm", fake_llm)

    merger = ConceptPageAnnotationMerger()
    merger.sync_source_to_concepts("book-ch5")
    after_first = concept_page.read_text(encoding="utf-8")

    merger.sync_source_to_concepts("book-ch5")
    after_second = concept_page.read_text(encoding="utf-8")

    assert after_first == after_second


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(ann_set: AnnotationSet | None):
    """Stub AnnotationStore that returns a fixed AnnotationSet (or None)."""

    class _Stub:
        def load(self, slug: str) -> AnnotationSet | None:
            return ann_set

    return _Stub()
