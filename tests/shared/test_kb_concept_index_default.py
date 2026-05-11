"""Tests for ``shared.kb_concept_index_default.VaultKBConceptIndex``
(ADR-024 Slice 10 / N518a).

Brief §5 AT13-AT15:

- AT13 ``KB/Wiki/Concepts/`` with 3 valid concept md files → 3 entries.
- AT14 One malformed frontmatter → skipped + warned; valid 2 returned.
- AT15 Missing dir → returns ``[]`` cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.kb_concept_index_default import VaultKBConceptIndex


def _write_concept(
    root: Path,
    name: str,
    *,
    aliases: list[str] | None = None,
    languages: list[str] | None = None,
) -> Path:
    """Write a minimal concept page under ``root`` with frontmatter."""
    fm_lines = ["---", f"name: {name}"]
    if aliases is not None:
        fm_lines.append("aliases:")
        for alias in aliases:
            fm_lines.append(f"  - {alias}")
    if languages is not None:
        fm_lines.append("languages:")
        for lang in languages:
            fm_lines.append(f"  - {lang}")
    fm_lines.append("---")
    fm_lines.append(f"# {name}")
    fm_lines.append("body")
    path = root / f"{name}.md"
    path.write_text("\n".join(fm_lines), encoding="utf-8")
    return path


@pytest.fixture
def concepts_root(tmp_path: Path) -> Path:
    root = tmp_path / "KB" / "Wiki" / "Concepts"
    root.mkdir(parents=True)
    return root


# ── AT13 — happy path: 3 valid concepts ─────────────────────────────────────


def test_at13_vault_kb_concept_index_lists_concepts(concepts_root: Path):
    _write_concept(concepts_root, "HRV", aliases=["heart rate variability"], languages=["en"])
    _write_concept(concepts_root, "RMSSD", aliases=["root mean square SD"])
    _write_concept(concepts_root, "Sleep", languages=["en", "zh-Hant"])

    idx = VaultKBConceptIndex(concepts_root=concepts_root)
    entries = idx.list_entries()

    assert len(entries) == 3
    labels = {e.canonical_label for e in entries}
    assert labels == {"HRV", "RMSSD", "Sleep"}
    # ``aliases`` round-trips through frontmatter parse.
    hrv = next(e for e in entries if e.canonical_label == "HRV")
    assert "heart rate variability" in hrv.aliases
    assert hrv.languages == ["en"]


def test_lookup_finds_by_alias(concepts_root: Path):
    _write_concept(
        concepts_root,
        "HRV",
        aliases=["heart rate variability", "RMSSD"],
        languages=["en"],
    )
    idx = VaultKBConceptIndex(concepts_root=concepts_root)

    # Lookup by alias.
    found = idx.lookup("heart rate variability")
    assert found is not None
    assert found.canonical_label == "HRV"

    # Lookup is case-insensitive.
    assert idx.lookup("HEART RATE VARIABILITY") is not None
    assert idx.lookup("Heart Rate Variability") is not None

    # Lookup by canonical label also works.
    assert idx.lookup("HRV") is not None
    assert idx.lookup("hrv") is not None


def test_lookup_returns_none_for_unknown_alias(concepts_root: Path):
    _write_concept(concepts_root, "HRV")
    idx = VaultKBConceptIndex(concepts_root=concepts_root)
    assert idx.lookup("unknown-concept") is None
    assert idx.lookup("") is None
    assert idx.lookup("   ") is None


def test_aliases_starting_with_returns_matching(concepts_root: Path):
    _write_concept(concepts_root, "HRV", aliases=["heart rate variability"])
    _write_concept(concepts_root, "RMSSD", aliases=["root mean square SD"])
    _write_concept(concepts_root, "Sleep")

    idx = VaultKBConceptIndex(concepts_root=concepts_root)

    # Prefix "hr" matches the canonical_label "HRV" (case-insensitive).
    out_hr = idx.aliases_starting_with("hr")
    lowered_hr = [a.casefold() for a in out_hr]
    assert "hrv" in lowered_hr
    assert not any("rmssd" in a for a in lowered_hr)

    # Prefix "heart" matches the alias "heart rate variability".
    out_heart = idx.aliases_starting_with("heart")
    lowered_heart = [a.casefold() for a in out_heart]
    assert any("heart rate variability" in a for a in lowered_heart)

    # Empty prefix returns ALL aliases (including canonical labels).
    out_all = idx.aliases_starting_with("")
    assert len(out_all) >= 4  # 3 names + 1+ aliases


# ── AT14 — malformed frontmatter skipped + warned ───────────────────────────


def test_at14_vault_kb_concept_index_skips_malformed(
    concepts_root: Path, caplog: pytest.LogCaptureFixture
):
    _write_concept(concepts_root, "HRV", aliases=["heart rate variability"])
    _write_concept(concepts_root, "Sleep", languages=["en"])

    # Write a malformed frontmatter — broken YAML.
    bad = concepts_root / "Broken.md"
    bad.write_text(
        "---\nname: Broken\naliases: [unbalanced\n---\nbody\n",
        encoding="utf-8",
    )

    idx = VaultKBConceptIndex(concepts_root=concepts_root)
    entries = idx.list_entries()

    # Malformed entry skipped; the two valid ones surface.
    assert len(entries) == 2
    labels = {e.canonical_label for e in entries}
    assert labels == {"HRV", "Sleep"}


# ── AT15 — empty / missing dir ──────────────────────────────────────────────


def test_at15_vault_kb_concept_index_empty_dir(tmp_path: Path):
    """Missing concepts root → empty list; no exception."""
    nonexistent = tmp_path / "KB" / "Wiki" / "Concepts"
    idx = VaultKBConceptIndex(concepts_root=nonexistent)
    assert idx.list_entries() == []
    assert idx.lookup("anything") is None
    assert idx.aliases_starting_with("a") == []


def test_empty_dir_returns_empty_list(concepts_root: Path):
    """Existing but empty dir → empty list."""
    idx = VaultKBConceptIndex(concepts_root=concepts_root)
    assert idx.list_entries() == []


# ── Edge cases ──────────────────────────────────────────────────────────────


def test_skips_non_md_files(concepts_root: Path):
    _write_concept(concepts_root, "HRV")
    (concepts_root / "notes.txt").write_text("not a concept", encoding="utf-8")
    (concepts_root / ".hidden.md").write_text("---\nname: hidden\n---", encoding="utf-8")

    idx = VaultKBConceptIndex(concepts_root=concepts_root)
    entries = idx.list_entries()
    labels = {e.canonical_label for e in entries}
    assert labels == {"HRV"}


def test_concept_without_frontmatter_falls_back_to_stem(concepts_root: Path):
    """A page with no frontmatter still surfaces, using the file stem
    as canonical_label so the index is still queryable."""
    plain = concepts_root / "PlainConcept.md"
    plain.write_text("# Just a heading\nbody only", encoding="utf-8")

    idx = VaultKBConceptIndex(concepts_root=concepts_root)
    entries = idx.list_entries()
    assert len(entries) == 1
    assert entries[0].canonical_label == "PlainConcept"


def test_concept_with_missing_name_uses_stem(concepts_root: Path, caplog: pytest.LogCaptureFixture):
    """Frontmatter without ``name`` → fall back to file stem + warn."""
    path = concepts_root / "Stemmed.md"
    path.write_text("---\naliases:\n  - alt\n---\nbody", encoding="utf-8")

    idx = VaultKBConceptIndex(concepts_root=concepts_root)
    entries = idx.list_entries()
    assert len(entries) == 1
    assert entries[0].canonical_label == "Stemmed"


def test_index_caches_scan_when_directory_unchanged(concepts_root: Path, monkeypatch):
    """Repeat lookups don't re-walk the filesystem when nothing changed.

    Updated for N518b C1: cache invalidation is now mtime-based on
    ``concepts_root``. We monkeypatch ``VaultKBConceptIndex._current_mtime_ns``
    to return a stable value so the cache is honoured even though the
    filesystem actually changed underneath. This proves the cache path
    executes; the ``test_index_invalidates_cache_when_directory_mtime_changes``
    test below proves the invalidation path.
    """
    _write_concept(concepts_root, "HRV")
    idx = VaultKBConceptIndex(concepts_root=concepts_root)

    # Prime the cache.
    first = idx.list_entries()
    stable_mtime = idx._current_mtime_ns()

    # Force the index to see a stable mtime (same as during prime).
    monkeypatch.setattr(VaultKBConceptIndex, "_current_mtime_ns", lambda self: stable_mtime)

    # Add a new file — the index should NOT pick it up because we faked
    # the mtime as unchanged.
    _write_concept(concepts_root, "ColdBrew")
    second = idx.list_entries()

    assert len(first) == len(second) == 1


def test_index_invalidates_cache_when_directory_mtime_changes(concepts_root: Path):
    """N518b C1: adding a new concept file changes the directory mtime,
    which invalidates the cache so the new entry is picked up on the next
    call. This is the regression test for the carry-over from PR #541
    review (long-running uvicorn would never see new concepts otherwise)."""
    import os
    import time

    _write_concept(concepts_root, "HRV")
    idx = VaultKBConceptIndex(concepts_root=concepts_root)

    # Prime the cache.
    first = idx.list_entries()
    assert len(first) == 1

    # Bump the directory mtime by writing a new concept. On most platforms
    # adding a directory entry updates the parent dir's mtime; we also
    # explicitly bump it to handle filesystems with low-resolution mtime
    # (some CI runners on FAT-style filesystems have 2s granularity).
    _write_concept(concepts_root, "ColdBrew")
    # Sleep briefly + explicit utime so even low-res filesystems see a change.
    time.sleep(0.01)
    new_time = time.time() + 1.0
    os.utime(concepts_root, (new_time, new_time))

    second = idx.list_entries()

    # Cache invalidated — new file picked up.
    assert len(second) == 2
    labels = {e.canonical_label for e in second}
    assert labels == {"HRV", "ColdBrew"}
