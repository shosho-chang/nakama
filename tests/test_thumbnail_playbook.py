"""Tests for shared/thumbnail_playbook.py — playbook archetype index loader.

ADR-033 D4 + v1.1 playbook integration. Validates that the loader correctly
reads playbook_data_v1.json + extracts brand-fit grades from playbook_v1.md
§5.2 matrix, and that format_playbook_index_for_prompt produces a compact
LLM-injectable text block.
"""

from __future__ import annotations

import pytest

from shared.thumbnail_playbook import (
    PlaybookIndex,
    _parse_grade,
    format_playbook_index_for_prompt,
    load_playbook_index,
    valid_archetype_ids,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset lru_cache between tests so on-disk edits during dev are picked up."""
    load_playbook_index.cache_clear()
    valid_archetype_ids.cache_clear()


def test_parse_grade_strips_markdown_bold_and_avoid_suffix():
    assert _parse_grade("A") == "A"
    assert _parse_grade("**A**") == "A"
    assert _parse_grade("**D / Avoid**") == "D"
    assert _parse_grade(" S ") == "S"


def test_load_playbook_index_returns_expected_archetype_counts():
    idx = load_playbook_index()
    assert isinstance(idx, PlaybookIndex)
    # v1 corpus produced 10 title + 10 thumb + 8 pairings
    assert len(idx.title_archetypes) == 10
    assert len(idx.thumbnail_archetypes) == 10
    assert len(idx.joint_pairings) == 8


def test_load_playbook_index_assigns_brand_fit_grades_from_md():
    idx = load_playbook_index()
    grades_by_id = {a.id: a.brand_fit_grade for a in idx.title_archetypes}
    grades_by_id.update({a.id: a.brand_fit_grade for a in idx.thumbnail_archetypes})

    # Spot-check known grades from playbook_v1.md §5.2 matrix:
    assert grades_by_id["T-A8"] == "S"  # Authority-Research — sole S grade
    assert grades_by_id["T-V6"] == "D"  # Question Overlay — v1.1 downgrade
    assert grades_by_id["T-A1"] == "A"  # Numbered listicle
    assert grades_by_id["T-V8"] == "C"  # High-saturation command text


def test_load_playbook_index_caches_result():
    a = load_playbook_index()
    b = load_playbook_index()
    assert a is b  # same object thanks to @lru_cache


def test_format_playbook_index_for_prompt_includes_all_archetypes():
    text = format_playbook_index_for_prompt()
    assert "## Playbook archetype index" in text
    assert "### Title archetypes" in text
    assert "### Thumbnail archetypes" in text
    assert "### Common joint pairings" in text
    assert "T-A1" in text and "T-A10" in text
    assert "T-V1" in text and "T-V10" in text
    assert "JP-1" in text and "JP-8" in text


def test_format_playbook_index_includes_grade_legend_and_tag_instruction():
    text = format_playbook_index_for_prompt()
    # Grade legend
    assert "S" in text and "A" in text and "B" in text
    assert "D/F-grade DO NOT USE" in text
    # Tag instruction
    assert "archetype: [T-AN, T-VN, JP-N]" in text


def test_format_playbook_index_size_is_compact():
    """Inject-size budget: target ≤2K tokens (~8000 chars at ~4 chars/token)."""
    text = format_playbook_index_for_prompt()
    assert len(text) < 8000, f"playbook index grew too large: {len(text)} chars"


def test_valid_archetype_ids_includes_all_types():
    ids = valid_archetype_ids()
    assert "T-A1" in ids
    assert "T-V6" in ids
    assert "JP-3" in ids
    assert len(ids) == 28  # 10 + 10 + 8
