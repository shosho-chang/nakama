"""Tests for _patch_alias_map_to_staging in run_s8_preflight (issue #496)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


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
