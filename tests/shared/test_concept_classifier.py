"""Tests for shared.concept_classifier (ADR-020 S3).

Tests are purely deterministic (no LLM calls) except for detect_scope_conflict
which accepts a callable to avoid real API calls.
"""

from __future__ import annotations

from pathlib import Path

from shared.concept_classifier import (
    append_alias_entry,
    classify_high_value,
    detect_scope_conflict,
    route_concept,
)

# ---------------------------------------------------------------------------
# Rule 1: section_heading
# ---------------------------------------------------------------------------


def test_h2_heading_triggers_section_heading():
    _, signals = classify_high_value("Carbohydrates", "## Carbohydrates\n\nText here.")
    assert "section_heading" in signals


def test_h1_heading_triggers_section_heading():
    _, signals = classify_high_value("Lipids", "# Lipids\n\nDefinition follows.")
    assert "section_heading" in signals


def test_h3_heading_triggers_section_heading():
    _, signals = classify_high_value("ATP synthesis", "### ATP synthesis\n\nDetail.")
    assert "section_heading" in signals


def test_term_in_body_not_heading():
    _, signals = classify_high_value("ATP", "Some text mentions ATP briefly.")
    assert "section_heading" not in signals


def test_heading_case_insensitive():
    _, signals = classify_high_value("carbohydrates", "## Carbohydrates\n\nText.")
    assert "section_heading" in signals


# ---------------------------------------------------------------------------
# Rule 2: bolded_define
# ---------------------------------------------------------------------------


def test_double_bold_triggers_bolded_define():
    _, signals = classify_high_value("glycogen", "The **glycogen** is the primary storage form.")
    assert "bolded_define" in signals


def test_italic_triggers_bolded_define():
    _, signals = classify_high_value("glycogen", "The *glycogen* is synthesized in the liver.")
    assert "bolded_define" in signals


def test_plain_term_no_bolded_define():
    _, signals = classify_high_value("glycogen", "Glycogen is stored in liver.")
    assert "bolded_define" not in signals


def test_bold_case_insensitive():
    _, signals = classify_high_value("Glycogen", "The **glycogen** is stored.")
    assert "bolded_define" in signals


# ---------------------------------------------------------------------------
# Rule 3: freq_multi_section
# ---------------------------------------------------------------------------


def test_term_in_two_sections_three_total():
    context = (
        "## Section 1\nATP is used. ATP provides energy.\n\n## Section 2\nATP is produced here."
    )
    _, signals = classify_high_value("ATP", context)
    assert "freq_multi_section" in signals


def test_term_in_one_section_below_threshold():
    context = "## Section 1\nATP appears once.\n\n## Section 2\nNo relevant content."
    _, signals = classify_high_value("ATP", context)
    assert "freq_multi_section" not in signals


def test_term_three_times_but_one_section():
    context = "## Section 1\nATP here. ATP again. ATP a third time."
    _, signals = classify_high_value("ATP", context)
    assert "freq_multi_section" not in signals


def test_term_in_two_sections_only_two_total():
    context = "## Section 1\nATP here.\n\n## Section 2\nATP here."
    _, signals = classify_high_value("ATP", context)
    assert "freq_multi_section" not in signals


# ---------------------------------------------------------------------------
# Rule 4: definition_phrase
# ---------------------------------------------------------------------------


def test_is_defined_as_triggers():
    _, signals = classify_high_value("homeostasis", "Homeostasis is defined as the process of...")
    assert "definition_phrase" in signals


def test_is_referred_to_as_triggers():
    _, signals = classify_high_value(
        "lactate", "Lactate is referred to as the primary fuel during..."
    )
    assert "definition_phrase" in signals


def test_chinese_稱為_triggers():
    _, signals = classify_high_value("乳酸", "乳酸 稱為無氧代謝的副產品。")
    assert "definition_phrase" in signals


def test_chinese_定義為_triggers():
    _, signals = classify_high_value("乳酸", "乳酸 定義為有機酸。")
    assert "definition_phrase" in signals


def test_no_definition_phrase():
    _, signals = classify_high_value("running", "Running is a great exercise.")
    assert "definition_phrase" not in signals


# ---------------------------------------------------------------------------
# classify_high_value — composite
# ---------------------------------------------------------------------------


def test_high_value_true_when_heading():
    is_hv, _ = classify_high_value("ATP", "## ATP\n\nText.")
    assert is_hv is True


def test_low_value_empty_signals():
    is_hv, signals = classify_high_value("exercise", "Exercise is a commonly used term.")
    assert is_hv is False
    assert signals == []


def test_multiple_signals_accumulated():
    context = (
        "## Creatine\n\nCreatine is defined as an organic compound. **Creatine** stores phosphate."
    )
    _, signals = classify_high_value("Creatine", context)
    assert "section_heading" in signals
    assert "definition_phrase" in signals
    assert "bolded_define" in signals


# ---------------------------------------------------------------------------
# route_concept
# ---------------------------------------------------------------------------


def test_route_l1_low_value_single_source():
    level, _ = route_concept("exercise", "Exercise is beneficial.", source_count=1)
    assert level == "L1"


def test_route_l2_single_source_high_value():
    level, signals = route_concept("ATP", "## ATP\n\nText.", source_count=1)
    assert level == "L2"
    assert signals


def test_route_l3_multi_source_high_value():
    level, _ = route_concept("ATP", "## ATP\n\nText.", source_count=2)
    assert level == "L3"


def test_route_l1_stays_l1_even_multi_source():
    level, _ = route_concept("exercise", "Exercise is beneficial.", source_count=2)
    assert level == "L1"


def test_route_l3_requires_source_count_ge_2():
    level, _ = route_concept("ATP", "## ATP\n\nText.", source_count=1)
    assert level == "L2"
    level2, _ = route_concept("ATP", "## ATP\n\nText.", source_count=2)
    assert level2 == "L3"


def test_route_returns_signals_for_l2():
    _, signals = route_concept(
        "homeostasis",
        "Homeostasis is defined as the internal balance mechanism.",
        source_count=1,
    )
    assert "definition_phrase" in signals


def test_route_l1_empty_signals():
    _, signals = route_concept("word", "Just a word mentioned.", source_count=1)
    assert signals == []


# ---------------------------------------------------------------------------
# detect_scope_conflict
# ---------------------------------------------------------------------------


def test_detect_same_facet():
    result = detect_scope_conflict(
        "Body about ATP energy currency.",
        "More about ATP and energy transfer.",
        _ask_llm=lambda prompt: "same_facet",
    )
    assert result == "same_facet"


def test_detect_different_facet():
    result = detect_scope_conflict(
        "ATP in muscle contraction.",
        "ATP in signal transduction.",
        _ask_llm=lambda prompt: "different_facet",
    )
    assert result == "different_facet"


def test_detect_different_concept():
    result = detect_scope_conflict(
        "Stress refers to mechanical load on bone.",
        "Stress refers to psychological pressure.",
        _ask_llm=lambda prompt: "different_concept",
    )
    assert result == "different_concept"


def test_detect_invalid_llm_response_defaults_same_facet():
    result = detect_scope_conflict(
        "Body A.",
        "Body B.",
        _ask_llm=lambda prompt: "UNKNOWN garbage response xyz",
    )
    assert result == "same_facet"


def test_detect_llm_response_substring_match():
    result = detect_scope_conflict(
        "Body A.",
        "Body B.",
        _ask_llm=lambda prompt: "I think this is different_facet because...",
    )
    assert result == "different_facet"


# ---------------------------------------------------------------------------
# append_alias_entry
# ---------------------------------------------------------------------------


def test_append_alias_creates_file(tmp_path: Path):
    (tmp_path / "KB" / "Wiki").mkdir(parents=True)
    append_alias_entry("exercise", "[[Sources/Books/bse-2024/ch1]]", tmp_path)
    alias_file = tmp_path / "KB" / "Wiki" / "_alias_map.md"
    assert alias_file.exists()
    assert "exercise" in alias_file.read_text(encoding="utf-8")


def test_append_alias_entry_is_deduped(tmp_path: Path):
    (tmp_path / "KB" / "Wiki").mkdir(parents=True)
    append_alias_entry("exercise", "[[Sources/Books/bse-2024/ch1]]", tmp_path)
    append_alias_entry("exercise", "[[Sources/Books/bse-2024/ch1]]", tmp_path)
    content = (tmp_path / "KB" / "Wiki" / "_alias_map.md").read_text(encoding="utf-8")
    assert content.count("exercise | [[Sources/Books/bse-2024/ch1]]") == 1


def test_append_alias_multiple_terms(tmp_path: Path):
    (tmp_path / "KB" / "Wiki").mkdir(parents=True)
    append_alias_entry("ATP", "[[Sources/Books/bse-2024/ch1]]", tmp_path)
    append_alias_entry("lactate", "[[Sources/Books/bse-2024/ch2]]", tmp_path)
    content = (tmp_path / "KB" / "Wiki" / "_alias_map.md").read_text(encoding="utf-8")
    assert "ATP" in content
    assert "lactate" in content


def test_append_alias_idempotent_different_source(tmp_path: Path):
    (tmp_path / "KB" / "Wiki").mkdir(parents=True)
    append_alias_entry("ATP", "[[Sources/Books/bse-2024/ch1]]", tmp_path)
    append_alias_entry("ATP", "[[Sources/Books/bse-2024/ch2]]", tmp_path)
    content = (tmp_path / "KB" / "Wiki" / "_alias_map.md").read_text(encoding="utf-8")
    assert content.count("ATP") >= 2
