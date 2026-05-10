"""Behaviour tests for ``shared.writing_assist_surface`` (ADR-024 Slice 9 / #517).

12 tests covering Brief §5 WT1-WT12:

- WT1  surface renders one ``SectionBlock`` per ``IdeaCluster``.
- WT2  pointer_index includes every ``EvidenceItem`` locator.
- WT3  W2: heading must not end with terminal punctuation (raises).
- WT4  W3: parametrized first-person token sweep (raises).
- WT5  W1: heading + question + missing_piece must not end with sentence-terminal.
- WT6  W4: parametrized "I think" / "我認為" sweep (raises).
- WT7  W5: question prompts end with '?' or '？'.
- WT8  W7: total non-excerpt char count ≤ 5000 for fixture package.
- WT9  Negative: directly construct a violating SectionBlock and assert
       surface render raises.
- WT10 Excerpt containing "I think" passes (it's quoted source content).
- WT11 subprocess: importing surface module does NOT pull shared.book_storage.
- WT12 Round-trip: model_dump + model_validate identity.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from shared.schemas.reading_context_package import (
    EvidenceItem,
    IdeaCluster,
    MissingPiecePrompt,
    OutlineSkeleton,
    Question,
    ReadingContextPackage,
    SectionBlock,
    WritingAssistOutput,
)
from shared.writing_assist_surface import WritingAssistSurface

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Fixture builders ──────────────────────────────────────────────────────────


def _hrv_evidence_pointer() -> EvidenceItem:
    return EvidenceItem(
        item_kind="annotation",
        locator="anno:ebook:alpha-book:cfi-001",
        excerpt="RMSSD short-window response distinguishes acute fatigue",
        source="annotation · ch-1",
    )


def _basic_package() -> ReadingContextPackage:
    """Minimal valid package with one cluster, one question, one
    missing-piece prompt, and one annotation evidence item — exercises
    every surface code path."""
    annotation = _hrv_evidence_pointer()
    cluster = IdeaCluster(
        cluster_id="clu_ch-1",
        label="ch-1",
        annotation_refs=["anno:ebook:alpha-book:cfi-001"],
        claim_refs=[],
    )
    question = Question(
        question_id="q_1",
        text="how does RMSSD respond to overtraining?",
        related_clusters=["clu_ch-1"],
    )
    missing = MissingPiecePrompt(
        prompt_id="miss_clu_ch-1",
        text="ch-1: 需要更多 evidence",
    )
    return ReadingContextPackage(
        source_id="ebook:alpha-book",
        annotations=[annotation],
        idea_clusters=[cluster],
        questions=[question],
        outline_skeleton=OutlineSkeleton(
            skeleton_id="outline_v1",
            section_labels=["ch-1"],
        ),
        missing_piece_prompts=[missing],
    )


def _two_cluster_package() -> ReadingContextPackage:
    annotation_a = EvidenceItem(
        item_kind="annotation",
        locator="anno:cfi-a",
        excerpt="evidence a",
        source="annotation · ch-1",
    )
    annotation_b = EvidenceItem(
        item_kind="annotation",
        locator="anno:cfi-b",
        excerpt="evidence b",
        source="annotation · ch-3",
    )
    return ReadingContextPackage(
        source_id="ebook:alpha-book",
        annotations=[annotation_a, annotation_b],
        idea_clusters=[
            IdeaCluster(
                cluster_id="clu_ch-1",
                label="ch-1",
                annotation_refs=["anno:cfi-a"],
                claim_refs=[],
            ),
            IdeaCluster(
                cluster_id="clu_ch-3",
                label="ch-3",
                annotation_refs=["anno:cfi-b"],
                claim_refs=[],
            ),
        ],
        outline_skeleton=OutlineSkeleton(
            skeleton_id="outline_v1",
            section_labels=["ch-1", "ch-3"],
        ),
    )


# ── WT1 — section blocks count ───────────────────────────────────────────────


def test_wt1_surface_renders_section_blocks_from_clusters():
    surface = WritingAssistSurface()
    package = _two_cluster_package()
    output = surface.render(package)
    assert len(output.section_blocks) == 2
    assert [b.heading for b in output.section_blocks] == ["ch-1", "ch-3"]


# ── WT2 — pointer index ──────────────────────────────────────────────────────


def test_wt2_surface_pointer_index_built():
    surface = WritingAssistSurface()
    package = _basic_package()
    output = surface.render(package)
    # Single annotation reference; pointer_index must include its locator.
    assert "anno:ebook:alpha-book:cfi-001" in output.pointer_index
    # Identifier is URL-safe (no '/' / ':' / spaces).
    identifier = output.pointer_index["anno:ebook:alpha-book:cfi-001"]
    assert all(ch.isalnum() or ch in {"-", "_"} for ch in identifier)


# ── WT3 — W2 heading no terminal punct ──────────────────────────────────────


def test_wt3_section_block_heading_no_terminal_punctuation_at_schema_level():
    """The schema validator on SectionBlock rejects terminal punctuation
    headings outright (defense-in-depth — surface render also strips them)."""
    with pytest.raises(ValueError, match="W2 violation"):
        SectionBlock(heading="ch-1.")
    with pytest.raises(ValueError, match="W2 violation"):
        SectionBlock(heading="ch-1?")
    with pytest.raises(ValueError, match="W2 violation"):
        SectionBlock(heading="第一章。")


# ── WT4 — W3 first-person token sweep ───────────────────────────────────────


@pytest.mark.parametrize(
    "violating_text",
    [
        "我認為這個 section 很重要",
        "I think this is the important part",
        "we should explore this",
        "my interpretation",
        "我們的觀察",
        "I'll write this section about RMSSD",
        "we'll cover this later",
    ],
)
def test_wt4_no_first_person_or_opinion_in_question_prompts(violating_text: str):
    """Constructing a SectionBlock whose question_prompts contain a W3 / W4
    violation must fail at surface render time. We can't construct via the
    schema directly because question_prompts requires terminal '?'; so we
    add the '?' and let render() catch the W3/W4 sweep."""
    block = SectionBlock(
        heading="ch-1",
        question_prompts=[f"{violating_text}?"],
    )
    output = WritingAssistOutput(
        package_source_id="ebook:alpha-book",
        section_blocks=[block],
    )
    # The rendered output construction itself doesn't validate cross-field
    # W3/W4 (those sweeps live in render()), so we drive the render path.
    surface_with_package = WritingAssistSurface()
    package = ReadingContextPackage(
        source_id="ebook:alpha-book",
        idea_clusters=[
            IdeaCluster(cluster_id="clu_ch-1", label="ch-1", annotation_refs=[]),
        ],
        questions=[
            Question(
                question_id="q_v",
                text=f"{violating_text}?",
                related_clusters=["clu_ch-1"],
            )
        ],
    )
    with pytest.raises(ValueError, match=r"ghostwriting detected: W[34]"):
        surface_with_package.render(package)
    # Suppress unused-warning on auxiliary objects.
    assert output.section_blocks


# ── WT5 — W1 heading / prompt sentence-terminal sweep ───────────────────────


def test_wt5_no_completed_sentence_in_non_excerpt_fields():
    """The schema validator on SectionBlock blocks W2 violations (which is
    the heading-terminal subset of W1). For missing_piece_prompts the schema
    only enforces W6 (no '.' / '。'); surface render covers W1 (also no
    '!' / '！')."""
    surface = WritingAssistSurface()
    # A missing_piece_prompt ending with '!' is a W1 violation surfaced by
    # render(). Build a SectionBlock with such a prompt by going through
    # the package → cluster path so the surface render catches it.
    package = ReadingContextPackage(
        source_id="ebook:alpha-book",
        idea_clusters=[
            IdeaCluster(cluster_id="clu_ch-1", label="ch-1", annotation_refs=[]),
        ],
        missing_piece_prompts=[
            MissingPiecePrompt(
                prompt_id="miss_clu_ch-1",
                text="ch-1 needs evidence!",
            )
        ],
    )
    with pytest.raises(ValueError, match=r"ghostwriting detected: W1"):
        surface.render(package)


# ── WT6 — W4 "I think" / 我認為 sweep ───────────────────────────────────────


@pytest.mark.parametrize(
    "violating_text",
    [
        "I think this matters?",
        "I believe so?",
        "我認為應該這樣?",
        "我覺得 RMSSD 重要?",
        "我相信 HRV 是好指標?",
    ],
)
def test_wt6_no_i_think_patterns(violating_text: str):
    surface = WritingAssistSurface()
    package = ReadingContextPackage(
        source_id="ebook:alpha-book",
        idea_clusters=[
            IdeaCluster(cluster_id="clu_ch-1", label="ch-1", annotation_refs=[]),
        ],
        questions=[
            Question(
                question_id="q_v",
                text=violating_text,
                related_clusters=["clu_ch-1"],
            )
        ],
    )
    with pytest.raises(ValueError, match=r"ghostwriting detected: W[34]"):
        surface.render(package)


# ── WT7 — W5 question prompt terminal '?' ───────────────────────────────────


def test_wt7_question_prompt_ends_with_question_mark():
    """The Question schema validator enforces W5 at construct time."""
    with pytest.raises(ValueError, match="W5 violation"):
        Question(question_id="q_x", text="this is a statement.")
    # And '？' (full-width) is accepted.
    accepted = Question(question_id="q_y", text="這個如何？")
    assert accepted.text.endswith("？")


# ── WT8 — W7 size budget ─────────────────────────────────────────────────────


def test_wt8_size_budget_holds_for_fixture_package():
    surface = WritingAssistSurface()
    package = _basic_package()
    output = surface.render(package)
    # Size budget is 5000 chars; this minimal fixture is well below.
    from shared.schemas.reading_context_package import compute_non_excerpt_char_count

    total = compute_non_excerpt_char_count(output)
    assert total < 5000

    # A constructed output with a single 6000-char heading would explode the
    # budget; build a SectionBlock that bypasses the 80-char heading cap by
    # going through the schema (the cap is enforced via Field max_length).
    # Confirm directly: the Field max_length enforces a separate guard so
    # an out-of-budget construction is impossible without first violating
    # the heading cap.
    with pytest.raises(ValueError):
        SectionBlock(heading="x" * 100)


def test_wt8_size_budget_violation_raises_from_schema_layer():
    """Construct an output that ONLY violates W7 (not heading length).
    Use many short blocks so the per-field caps don't fire first."""
    blocks = [SectionBlock(heading=f"clu-{i:04d}") for i in range(700)]
    # 700 * 8 chars heading + boilerplate > 5000.
    with pytest.raises(ValueError, match="W7 violation"):
        WritingAssistOutput(
            package_source_id="ebook:alpha-book",
            section_blocks=blocks,
        )


# ── WT9 — direct violation raises at render time ────────────────────────────


def test_wt9_surface_render_raises_on_violation():
    """Inject a cluster whose label contains 'I think' → surface render must
    raise the ghostwriting error. The cluster.label is not validated at the
    IdeaCluster schema layer (clusters CAN carry user-derived labels), so
    the surface boundary is what enforces W3/W4 cross-field."""
    surface = WritingAssistSurface()
    package = ReadingContextPackage(
        source_id="ebook:alpha-book",
        idea_clusters=[
            IdeaCluster(
                cluster_id="clu_v",
                label="I think HRV matters",
                annotation_refs=[],
            )
        ],
    )
    with pytest.raises(ValueError, match=r"ghostwriting detected: W[34]"):
        surface.render(package)


# ── WT10 — excerpt containing "I think" passes ──────────────────────────────


def test_wt10_evidence_excerpt_unaffected_by_no_ghostwriting_rules():
    """The excerpt field is quoted source content, not authored. A pointer
    whose excerpt contains 'I think' must NOT trigger the W3/W4 sweep."""
    surface = WritingAssistSurface()
    annotation = EvidenceItem(
        item_kind="annotation",
        locator="anno:foo",
        # Excerpt contains "I think" — but it's a quote of the source's
        # author voice, which is legitimate for evidence display.
        excerpt="I think the chapter argues that HRV reflects readiness.",
        source="annotation · ch-1",
    )
    package = ReadingContextPackage(
        source_id="ebook:alpha-book",
        annotations=[annotation],
        idea_clusters=[
            IdeaCluster(
                cluster_id="clu_ch-1",
                label="ch-1",
                annotation_refs=["anno:foo"],
            )
        ],
    )
    # Render must succeed because the violating tokens live ONLY in the
    # excerpt field (which W3/W4 explicitly exclude).
    output = surface.render(package)
    assert output.section_blocks
    block = output.section_blocks[0]
    assert any("I think" in p.excerpt for p in block.evidence_pointers)


# ── WT11 — subprocess: no shared.book_storage ──────────────────────────────


def test_wt11_surface_no_book_storage_import():
    src = textwrap.dedent(
        """
        import sys
        import shared.writing_assist_surface  # noqa: F401

        offending = sorted(
            m for m in sys.modules if m.startswith("shared.book_storage")
        )
        if offending:
            print("OFFENDING:" + ",".join(offending))
            sys.exit(1)
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


def test_wt11b_surface_no_llm_or_fastapi_imports():
    """Defense-in-depth: surface must not pull LLM clients or FastAPI."""
    src = textwrap.dedent(
        """
        import sys
        import shared.writing_assist_surface  # noqa: F401

        offending = sorted(
            m
            for m in sys.modules
            if m.startswith((
                "anthropic",
                "openai",
                "google.generativeai",
                "google.genai",
                "fastapi",
                "thousand_sunny",
                "agents.",
            ))
        )
        if offending:
            print("OFFENDING:" + ",".join(offending))
            sys.exit(1)
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


# ── WT12 — round trip ────────────────────────────────────────────────────────


def test_wt12_round_trips():
    surface = WritingAssistSurface()
    output = surface.render(_basic_package())
    dumped = output.model_dump()
    reloaded = WritingAssistOutput.model_validate(dumped)
    assert reloaded.model_dump() == dumped
    json_payload = output.model_dump_json()
    reloaded_from_json = WritingAssistOutput.model_validate_json(json_payload)
    assert reloaded_from_json.model_dump() == dumped


# ── Sanity — pointer index keys are deterministic ────────────────────────────


def test_pointer_index_deterministic_across_runs():
    surface = WritingAssistSurface()
    package = _basic_package()
    out_a = surface.render(package)
    out_b = surface.render(package)
    assert out_a.pointer_index == out_b.pointer_index
    assert json.dumps(out_a.pointer_index, sort_keys=True) == json.dumps(
        out_b.pointer_index, sort_keys=True
    )
