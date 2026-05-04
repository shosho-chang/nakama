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
    MergerLLMResult,
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
    """Replace _ask_merger_llm with a stub returning empty MergerLLMResult by default."""
    calls: list[str] = []

    def fake(prompt: str) -> MergerLLMResult:
        calls.append(prompt)
        return MergerLLMResult(matches={}, error=None)

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
    def fake_llm(prompt: str) -> MergerLLMResult:
        mock_llm.append(prompt)
        return MergerLLMResult(matches={"nonexistent-concept": "> [!annotation] note"}, error=None)

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

    def fake_llm(prompt: str) -> MergerLLMResult:
        return MergerLLMResult(matches={"肌酸代謝": callout}, error=None)

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

    def fake_llm(prompt: str) -> MergerLLMResult:
        return MergerLLMResult(matches={"睡眠品質": callout}, error=None)

    monkeypatch.setattr(mod, "_ask_merger_llm", fake_llm)

    merger = ConceptPageAnnotationMerger()
    merger.sync_source_to_concepts("book-ch5")
    after_first = concept_page.read_text(encoding="utf-8")

    merger.sync_source_to_concepts("book-ch5")
    after_second = concept_page.read_text(encoding="utf-8")

    assert after_first == after_second


# ---------------------------------------------------------------------------
# _ask_merger_llm: tool_use forced JSON contract (QA 2026-05-04 #7 fix)
# ---------------------------------------------------------------------------


class _FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, payload: dict):
        self.input = payload


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _FakeMessage:
    def __init__(self, content: list, stop_reason: str = "tool_use"):
        self.content = content
        self.stop_reason = stop_reason


def test_ask_merger_llm_returns_typed_result_on_success(monkeypatch):
    """Happy path: LLM invokes tool with valid input → MergerLLMResult(matches=..., error=None)."""
    captured: dict = {}

    def fake_call(messages, tools, *, tool_choice=None, model=None, max_tokens=0, **_):
        captured["tool_choice"] = tool_choice
        captured["model"] = model
        captured["tool_name"] = tools[0]["name"]
        return _FakeMessage(
            [_FakeToolUseBlock({"concept_matches": {"肌酸代謝": "> note", "睡眠品質": "> n2"}})]
        )

    monkeypatch.setattr("shared.anthropic_client.call_claude_with_tools", fake_call)

    result = mod._ask_merger_llm("any prompt")

    assert result.error is None
    assert result.matches == {"肌酸代謝": "> note", "睡眠品質": "> n2"}
    # Forced tool_choice and Opus 4.7 model
    assert captured["tool_choice"] == {"type": "tool", "name": "merge_annotations"}
    assert captured["model"] == "claude-opus-4-7"
    assert captured["tool_name"] == "merge_annotations"


def test_ask_merger_llm_returns_empty_matches_when_no_concepts(monkeypatch):
    """LLM legitimately returns empty concept_matches → matches={}, error=None (NOT an error)."""

    def fake_call(*a, **kw):
        return _FakeMessage([_FakeToolUseBlock({"concept_matches": {}})])

    monkeypatch.setattr("shared.anthropic_client.call_claude_with_tools", fake_call)

    result = mod._ask_merger_llm("any prompt")
    assert result.matches == {}
    assert result.error is None  # empty match ≠ failure (key UX distinction)


def test_ask_merger_llm_returns_error_on_no_tool_use(monkeypatch):
    """LLM 沒 invoke tool（只回 text） → error msg surface to caller."""

    def fake_call(*a, **kw):
        return _FakeMessage([_FakeTextBlock("I refuse")], stop_reason="end_turn")

    monkeypatch.setattr("shared.anthropic_client.call_claude_with_tools", fake_call)

    result = mod._ask_merger_llm("any prompt")
    assert result.matches == {}
    assert result.error is not None
    assert "merge_annotations" in result.error


def test_ask_merger_llm_returns_error_on_api_exception(monkeypatch):
    """API exception → caught, surfaced as error msg, not raised."""

    def fake_call(*a, **kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("shared.anthropic_client.call_claude_with_tools", fake_call)

    result = mod._ask_merger_llm("any prompt")
    assert result.matches == {}
    assert result.error is not None
    assert "RuntimeError" in result.error
    assert "connection reset" in result.error


def test_ask_merger_llm_filters_non_string_values(monkeypatch):
    """Defensive: even if SDK schema lets weird values through, we filter to dict[str, str]."""

    def fake_call(*a, **kw):
        return _FakeMessage(
            [_FakeToolUseBlock({"concept_matches": {"good-slug": "> ok", "bad-slug": 123}})]
        )

    monkeypatch.setattr("shared.anthropic_client.call_claude_with_tools", fake_call)

    result = mod._ask_merger_llm("any prompt")
    assert result.matches == {"good-slug": "> ok"}
    assert result.error is None


# ---------------------------------------------------------------------------
# sync_source_to_concepts: idempotency short-circuit + error propagation
# ---------------------------------------------------------------------------


def test_sync_short_circuits_when_unsynced_count_zero(vault, mock_llm, monkeypatch):
    """unsynced_count == 0 → no LLM call, returns short_circuited=True."""
    ann_set = AnnotationSet(
        slug="src",
        source_filename="src.md",
        base="inbox",
        items=[Annotation(ref="ref", note="note", created_at="2026-05-04T00:00:00Z")],
    )
    monkeypatch.setattr(mod, "get_annotation_store", lambda: _make_store(ann_set, unsynced=0))
    merger = ConceptPageAnnotationMerger()
    report = merger.sync_source_to_concepts("src")

    assert report.short_circuited is True
    assert report.errors == []
    assert report.concepts_updated == []
    assert report.annotations_merged == 0
    assert mock_llm == []  # LLM never called


def test_sync_propagates_llm_error_to_report(vault, monkeypatch):
    """LLM contract failure → SyncReport.errors non-empty, no concept page mutation."""
    ann_set = AnnotationSet(
        slug="src",
        source_filename="src.md",
        base="inbox",
        items=[Annotation(ref="ref", note="note", created_at="2026-05-04T00:00:00Z")],
    )
    monkeypatch.setattr(mod, "get_annotation_store", lambda: _make_store(ann_set))

    def fake_llm(prompt: str) -> MergerLLMResult:
        return MergerLLMResult(matches={}, error="LLM 未呼叫 merge_annotations tool")

    monkeypatch.setattr(mod, "_ask_merger_llm", fake_llm)

    merger = ConceptPageAnnotationMerger()
    report = merger.sync_source_to_concepts("src")

    assert report.errors == ["LLM 未呼叫 merge_annotations tool"]
    assert report.concepts_updated == []
    assert report.short_circuited is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(ann_set: AnnotationSet | None, *, unsynced: int | None = None):
    """Stub AnnotationStore that returns a fixed AnnotationSet (or None).

    ``unsynced`` overrides the default ``unsynced_count`` (default = ``len(items)``,
    so all items appear unsynced and short-circuit doesn't trigger).
    Pass ``unsynced=0`` to test the idempotency short-circuit path.
    """

    class _Stub:
        def load(self, slug: str) -> AnnotationSet | None:
            return ann_set

        def unsynced_count(self, slug: str) -> int:
            if unsynced is not None:
                return unsynced
            return len(ann_set.items) if ann_set else 0

        def mark_synced(self, slug: str) -> None:  # pragma: no cover — invoked by router only
            pass

    return _Stub()
