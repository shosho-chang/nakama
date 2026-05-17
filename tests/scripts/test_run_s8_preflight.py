"""Tests for _patch_alias_map_to_staging in run_s8_preflight (issue #496)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def test_dedup_concepts_singular_plural_collapsed():
    """Singular + plural forms of same concept collapse to one entry."""
    from scripts.run_s8_preflight import _dedup_concepts_by_canonical

    deduped, dropped = _dedup_concepts_by_canonical(
        ["transcription factor", "ATP", "transcription factors"]
    )
    assert deduped == ["transcription factor", "ATP"]
    assert dropped == [("transcription factor", "transcription factors")]


def test_dedup_concepts_literal_duplicate_collapsed():
    """Literal duplicates (same surface form twice) collapse to one entry."""
    from scripts.run_s8_preflight import _dedup_concepts_by_canonical

    deduped, dropped = _dedup_concepts_by_canonical(
        ["electron transfer chain", "ATP", "electron transfer chain"]
    )
    assert deduped == ["electron transfer chain", "ATP"]
    assert dropped == [("electron transfer chain", "electron transfer chain")]


def test_dedup_concepts_preserves_order_no_dups():
    """No duplicates → list unchanged, dropped is empty."""
    from scripts.run_s8_preflight import _dedup_concepts_by_canonical

    deduped, dropped = _dedup_concepts_by_canonical(["ATP", "lactate", "pyruvate"])
    assert deduped == ["ATP", "lactate", "pyruvate"]
    assert dropped == []


def test_dedup_concepts_seed_alias_merged():
    """Seed-alias variants (Lactic Acid → lactate) collapse to canonical."""
    from scripts.run_s8_preflight import _dedup_concepts_by_canonical

    deduped, dropped = _dedup_concepts_by_canonical(["lactate", "Lactic Acid"])
    assert deduped == ["lactate"]
    assert dropped == [("lactate", "Lactic Acid")]


def test_patch_alias_map_to_staging_importable_from_preflight():
    """_patch_alias_map_to_staging must be importable from run_s8_preflight."""
    from scripts.run_s8_preflight import _patch_alias_map_to_staging  # noqa: F401

    assert callable(_patch_alias_map_to_staging)


def test_patch_alias_map_redirects_to_staging_not_live(tmp_path):
    """After _patch_alias_map_to_staging(), append_alias_entry writes to staging."""
    from scripts.run_s8_preflight import _patch_alias_map_to_staging
    from shared import concept_classifier as cc

    original_fn = cc.append_alias_entry
    original_patched = getattr(cc, "_s8_staging_patched", False)
    try:
        cc._s8_staging_patched = False
        _patch_alias_map_to_staging()

        vault = tmp_path / "vault"
        vault.mkdir()
        cc.append_alias_entry("TestTerm", "[[source]]", vault)

        staging_file = vault / "KB" / "Wiki.staging" / "_alias_map.md"
        live_file = vault / "KB" / "Wiki" / "_alias_map.md"

        assert staging_file.exists(), "alias must be written to Wiki.staging"
        assert not live_file.exists(), "alias must NOT be written to live Wiki"
        assert "TestTerm" in staging_file.read_text(encoding="utf-8")
    finally:
        cc.append_alias_entry = original_fn
        cc._s8_staging_patched = original_patched


# -----------------------------------------------------------------------------
# Phase 1 JSON extractor — tolerates LLM prose preambles around the JSON body
# (regression: MEP ch8/9/10 ERROR'd because Sonnet prepended
# "Outputting the complete JSON to avoid truncation.\n\n{...}").
# -----------------------------------------------------------------------------


def test_extract_json_object_strips_prose_preamble():
    from scripts.run_s8_preflight import _extract_json_object

    blob = 'Outputting the complete JSON to avoid truncation.\n\n{"a": 1, "b": [2, 3]}\n'
    assert _extract_json_object(blob) == '{"a": 1, "b": [2, 3]}'


def test_extract_json_object_handles_nested_braces_in_strings():
    from scripts.run_s8_preflight import _extract_json_object

    blob = 'prose\n{"k": "value with } inside", "n": {"inner": true}}\ntail'
    # raw_decode walks balanced braces correctly — must include the inner object.
    extracted = _extract_json_object(blob)
    import json as _json

    parsed = _json.loads(extracted)
    assert parsed == {"k": "value with } inside", "n": {"inner": True}}


def test_extract_json_object_returns_text_when_no_brace():
    from scripts.run_s8_preflight import _extract_json_object

    blob = "just prose, no JSON here"
    assert _extract_json_object(blob) == blob


def test_extract_json_object_picks_schema_match_over_first_object():
    """LLM sometimes emits a figure / example object before the real payload.

    Regression: MEP ch8/ch10 re-run, where the first ``{...}`` was a figure dict
    with keys [vault_path, alt_text, vision_class, vision_status] and the real
    ``{frontmatter, sections}`` came later in the response.
    """
    import json as _json

    from scripts.run_s8_preflight import _extract_json_object

    figure_obj = (
        '{"vault_path": "Attachments/f1.png", "alt_text": "x", '
        '"vision_class": "diagram", "vision_status": "caption_only"}'
    )
    blob = (
        "Here is an example figure:\n"
        + figure_obj
        + "\n\nAnd the full output:\n"
        + '{"frontmatter": {"title": "Ch 8"}, "sections": [{"anchor": "8.1"}]}\n'
    )
    extracted = _extract_json_object(blob, required_keys=("frontmatter", "sections"))
    parsed = _json.loads(extracted)
    assert parsed == {"frontmatter": {"title": "Ch 8"}, "sections": [{"anchor": "8.1"}]}


def test_extract_json_object_falls_back_to_largest_when_no_match():
    """Without required_keys (or when none match), pick the largest parseable block."""
    from scripts.run_s8_preflight import _extract_json_object

    blob = 'prose\n{"a":1}\nmore\n{"b":2, "c":3, "d": [4,5,6]}\n'
    # Both parse; the second is larger and should win.
    extracted = _extract_json_object(blob)
    assert extracted == '{"b":2, "c":3, "d": [4,5,6]}'
