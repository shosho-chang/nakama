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
