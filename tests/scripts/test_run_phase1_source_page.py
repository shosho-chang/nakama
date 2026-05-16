"""Unit tests for Stage 1b: run_phase1_source_page JSON wiring + dry-run + filename.

Each test builds a small in-memory ChapterPayload — no real files, no LLM calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.run_s8_preflight import run_phase1_source_page
from shared.source_ingest import ChapterPayload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payload(
    chapter_index: int = 3,
    section_anchors: list[str] | None = None,
    book_id: str = "test-book",
) -> ChapterPayload:
    anchors = section_anchors if section_anchors is not None else ["Sec A"]
    body = "\n\n".join(f"## {a}\n\nText for {a}." for a in anchors)
    return ChapterPayload(
        book_id=book_id,
        raw_path="test.md",
        chapter_index=chapter_index,
        chapter_title=f"{chapter_index} Test Chapter",
        verbatim_body=body,
        section_anchors=anchors,
        figures=[],
        tables=[],
    )


def _valid_json_for(payload: ChapterPayload) -> str:
    return json.dumps(
        {
            "frontmatter": {
                "title": payload.chapter_title,
                "chapter_index": payload.chapter_index,
            },
            "sections": [
                {
                    "anchor": a,
                    "concept_map_md": "```mermaid\nflowchart LR\n  A --> B\n```",
                    "wikilinks": [],
                }
                for a in payload.section_anchors
            ],
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dry_run_writes_real_file(tmp_path: Path) -> None:
    """--dry-run: file exists, has frontmatter, body present, section count matches walker."""
    payload = _payload(chapter_index=3, section_anchors=["Sec A", "Sec B"])

    out_path = run_phase1_source_page(
        payload,
        vault_root=tmp_path,
        book_title="Test Book",
        dry_run=True,
    )

    assert out_path.exists()
    content = out_path.read_text()
    assert content.startswith("---\n")
    assert "Sec A" in content
    # Patch 3 (2026-05-08): metadata moved to single chapter-end appendix.
    assert content.count("## Section Concept Maps") == 1
    assert "### Sec A" in content
    assert "### Sec B" in content


def test_json_parse_retry_then_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First LLM call: bad JSON. Second: valid. Retry triggered, body assembled."""
    # Force MAX_PLAN routing so the patch on `_ask_llm` is taken (otherwise
    # run_phase1_source_page branches to `_ask_llm_streaming` for SDK path).
    monkeypatch.setenv("NAKAMA_REQUIRE_MAX_PLAN", "1")
    payload = _payload()
    valid = _valid_json_for(payload)

    with patch("scripts.run_s8_preflight._ask_llm", side_effect=["not valid json {{{", valid]):
        out_path = run_phase1_source_page(payload, vault_root=tmp_path, book_title="Test")

    assert out_path.exists()
    content = out_path.read_text()
    assert content.startswith("---\n")


def test_json_parse_double_fail_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both LLM calls return malformed JSON → raises (not silent)."""
    monkeypatch.setenv("NAKAMA_REQUIRE_MAX_PLAN", "1")
    payload = _payload()

    with patch("scripts.run_s8_preflight._ask_llm", return_value="not json at all"):
        with pytest.raises(json.JSONDecodeError):
            run_phase1_source_page(payload, vault_root=tmp_path, book_title="Test")


def test_schema_section_count_mismatch_fail_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM JSON has wrong len(sections) → ValueError with no retry (schema error ≠ parse error)."""
    monkeypatch.setenv("NAKAMA_REQUIRE_MAX_PLAN", "1")
    payload = _payload(section_anchors=["Sec A", "Sec B"])

    wrong_json = json.dumps(
        {
            "frontmatter": {"title": "Test"},
            "sections": [
                {"anchor": "Sec A", "concept_map_md": "map", "wikilinks": []},
            ],
        }
    )

    with patch("scripts.run_s8_preflight._ask_llm", return_value=wrong_json) as mock_llm:
        with pytest.raises(ValueError, match="sections count mismatch"):
            run_phase1_source_page(payload, vault_root=tmp_path, book_title="Test")
        assert mock_llm.call_count == 1


def test_filename_uses_real_chapter_index(tmp_path: Path) -> None:
    """payload.chapter_index=7 → file is ch7.md, not ch2.md (walker chunk index)."""
    payload = _payload(chapter_index=7)

    out_path = run_phase1_source_page(payload, vault_root=tmp_path, book_title="Test", dry_run=True)

    assert out_path.name == "ch7.md"


def test_phase1_prompt_contains_prioritized_concepts_instruction() -> None:
    """Phase 1 prompt template demands prioritized_concepts (issue #498).

    Checks that _build_phase1_prompt emits 'prioritized_concepts' and '5' or '7'
    as part of the runtime prompt, so the LLM sees the constraint on every call.
    No LLM call made — only checks the built prompt string.
    """
    from scripts.run_s8_preflight import _build_phase1_prompt

    payload = _payload(chapter_index=1, section_anchors=["Sec A"])
    prompt = _build_phase1_prompt(payload, book_title="Test Book", ingest_date="2026-05-08")
    assert "prioritized_concepts" in prompt, "prompt must reference prioritized_concepts field"
    assert "5" in prompt and "7" in prompt, "prompt must state the 5-7 concept count constraint"
    assert "justification" in prompt, "prompt must demand per-concept justification"
