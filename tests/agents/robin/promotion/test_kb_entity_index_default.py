"""Tests for ``agents.robin.promotion.kb_entity_index_default.VaultKBEntityIndex``
(ADR-034 v2 PR3). Mirrors ``test_kb_concept_index_default``."""

from __future__ import annotations

from pathlib import Path

from agents.robin.promotion.kb_entity_index_default import VaultKBEntityIndex


def _write_entity(
    entities_root: Path,
    subdir: str,
    name: str,
    frontmatter: dict | None,
    body: str = "",
) -> Path:
    """Write a markdown entity page under entities_root/subdir/."""
    target = entities_root / subdir / f"{name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if frontmatter is None:
        target.write_text(body, encoding="utf-8")
        return target
    import yaml

    fm_block = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
    target.write_text(f"---\n{fm_block}---\n{body}", encoding="utf-8")
    return target


def test_missing_root_returns_empty_no_exception(tmp_path: Path) -> None:
    index = VaultKBEntityIndex(entities_root=tmp_path / "does-not-exist")
    assert index.list_entries() == []
    assert index.lookup("anything") is None
    assert index.aliases_starting_with("") == []


def test_empty_root_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "Entities").mkdir()
    index = VaultKBEntityIndex(entities_root=tmp_path / "Entities")
    assert index.list_entries() == []


def test_scans_people_and_organizations_subdirs(tmp_path: Path) -> None:
    entities_root = tmp_path / "Entities"
    _write_entity(
        entities_root,
        "People",
        "Andrew Huberman",
        {"entity_label": "Andrew Huberman", "aliases": ["Dr. Huberman"], "languages": ["en"]},
    )
    _write_entity(
        entities_root,
        "Organizations",
        "Stanford University",
        {"entity_label": "Stanford University", "languages": ["en"]},
    )

    index = VaultKBEntityIndex(entities_root=entities_root)
    entries = index.list_entries()
    assert len(entries) == 2
    by_label = {e.canonical_label: e for e in entries}
    assert by_label["Andrew Huberman"].entity_type == "person"
    assert by_label["Stanford University"].entity_type == "organization"


def test_lookup_by_alias_case_insensitive(tmp_path: Path) -> None:
    entities_root = tmp_path / "Entities"
    _write_entity(
        entities_root,
        "People",
        "Andrew Huberman",
        {"entity_label": "Andrew Huberman", "aliases": ["Dr. Huberman", "Andy"]},
    )
    index = VaultKBEntityIndex(entities_root=entities_root)
    entry = index.lookup("DR. HUBERMAN")
    assert entry is not None
    assert entry.canonical_label == "Andrew Huberman"


def test_lookup_returns_none_for_unknown_alias(tmp_path: Path) -> None:
    entities_root = tmp_path / "Entities"
    _write_entity(entities_root, "People", "X", {"entity_label": "X"})
    index = VaultKBEntityIndex(entities_root=entities_root)
    assert index.lookup("nobody") is None
    assert index.lookup("") is None
    assert index.lookup("   ") is None


def test_aliases_starting_with_returns_sorted(tmp_path: Path) -> None:
    entities_root = tmp_path / "Entities"
    _write_entity(
        entities_root,
        "People",
        "Andrew",
        {"entity_label": "Andrew Huberman", "aliases": ["Andy", "Andrew D."]},
    )
    out = index_with(entities_root).aliases_starting_with("Andr")
    assert "Andrew Huberman" in out
    assert "Andrew D." in out
    assert out == sorted(out, key=str.casefold)


def index_with(entities_root: Path) -> VaultKBEntityIndex:
    return VaultKBEntityIndex(entities_root=entities_root)


def test_entity_kind_mismatch_with_parent_dir_skipped(tmp_path: Path, caplog) -> None:
    """Frontmatter entity_kind='organization' under People/ → skipped."""
    entities_root = tmp_path / "Entities"
    _write_entity(
        entities_root,
        "People",
        "Misplaced",
        {"entity_label": "Misplaced", "entity_kind": "organization"},
    )
    index = VaultKBEntityIndex(entities_root=entities_root)
    assert index.list_entries() == []


def test_missing_entity_label_falls_back_to_filename_stem(tmp_path: Path) -> None:
    entities_root = tmp_path / "Entities"
    _write_entity(entities_root, "People", "Fallback Name", {"aliases": ["FN"]})
    index = VaultKBEntityIndex(entities_root=entities_root)
    entries = index.list_entries()
    assert len(entries) == 1
    assert entries[0].canonical_label == "Fallback Name"


def test_malformed_frontmatter_skipped_not_raised(tmp_path: Path) -> None:
    entities_root = tmp_path / "Entities" / "People"
    entities_root.mkdir(parents=True)
    (entities_root / "broken.md").write_text(
        "---\n: not: valid yaml: at: all:\n---\n", encoding="utf-8"
    )
    _write_entity(tmp_path / "Entities", "People", "Good", {"entity_label": "Good"})
    index = VaultKBEntityIndex(entities_root=tmp_path / "Entities")
    entries = index.list_entries()
    # Good entry survives; broken one logged + skipped.
    assert len(entries) == 1
    assert entries[0].canonical_label == "Good"


def test_dotfile_and_non_md_skipped(tmp_path: Path) -> None:
    entities_root = tmp_path / "Entities"
    _write_entity(entities_root, "People", ".hidden", {"entity_label": "Hidden"})
    (entities_root / "People" / "note.txt").write_text("ignore", encoding="utf-8")
    _write_entity(entities_root, "People", "Valid", {"entity_label": "Valid"})
    index = VaultKBEntityIndex(entities_root=entities_root)
    labels = [e.canonical_label for e in index.list_entries()]
    assert labels == ["Valid"]


def test_only_one_subdir_present_still_scans(tmp_path: Path) -> None:
    """Bootstrap: Organizations may not exist yet."""
    entities_root = tmp_path / "Entities"
    _write_entity(entities_root, "People", "Solo", {"entity_label": "Solo"})
    # No Organizations subdir at all
    index = VaultKBEntityIndex(entities_root=entities_root)
    entries = index.list_entries()
    assert len(entries) == 1
    assert entries[0].entity_type == "person"


def test_cache_invalidated_when_subdir_mtime_changes(tmp_path: Path) -> None:
    entities_root = tmp_path / "Entities"
    _write_entity(entities_root, "People", "First", {"entity_label": "First"})
    index = VaultKBEntityIndex(entities_root=entities_root)
    assert len(index.list_entries()) == 1

    # Add another file — directory mtime advances → cache invalidates.
    import os
    import time

    time.sleep(0.05)
    _write_entity(entities_root, "People", "Second", {"entity_label": "Second"})
    # Ensure mtime tick is observable even on coarse filesystems.
    os.utime(entities_root / "People", None)

    entries = index.list_entries()
    labels = sorted(e.canonical_label for e in entries)
    assert labels == ["First", "Second"]
