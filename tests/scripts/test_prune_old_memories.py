"""Tests for scripts/prune_old_memories.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import prune_old_memories as prune


@pytest.fixture
def memory_dir(tmp_path, monkeypatch):
    """Create a fake memory/claude/ structure for tests."""
    mem = tmp_path / "memory" / "claude"
    mem.mkdir(parents=True)
    monkeypatch.setattr(prune, "_MEMORY_DIR", mem)
    monkeypatch.setattr(prune, "_INDEX", mem / "MEMORY.md")
    monkeypatch.setattr(prune, "_ARCHIVE_DIR", mem / "_archive")
    return mem


def _make_memory(dir: Path, name: str, *, created: datetime | None = None, body: str = "") -> Path:
    """Write a fake memory file with optional `created:` frontmatter."""
    fm_lines = ["---", f"name: {name.replace('.md', '')}"]
    if created:
        fm_lines.append(f"created: {created.strftime('%Y-%m-%d')}")
    fm_lines.append("---")
    text = "\n".join(fm_lines) + "\n\n" + body
    path = dir / name
    path.write_text(text, encoding="utf-8")
    return path


def _index(dir: Path, referenced_files: list[str]) -> None:
    lines = ["# Memory Index", ""]
    for f in referenced_files:
        lines.append(f"- [{f.replace('.md', '')}]({f}) — placeholder")
    (dir / "MEMORY.md").write_text("\n".join(lines), encoding="utf-8")


# ---- decide() ---------------------------------------------------------------


def test_feedback_files_never_archived(memory_dir):
    p = _make_memory(
        memory_dir, "feedback_old_thing.md", created=datetime(2024, 1, 1, tzinfo=timezone.utc)
    )
    _index(memory_dir, [])  # empty index — but feedback is protected anyway
    decision = prune.decide(p, p.read_text(), set(), 90, datetime(2026, 4, 25, tzinfo=timezone.utc))
    assert decision.action == "skip-protected"


def test_reference_files_never_archived(memory_dir):
    p = _make_memory(
        memory_dir, "reference_vps_paths.md", created=datetime(2020, 1, 1, tzinfo=timezone.utc)
    )
    decision = prune.decide(p, p.read_text(), set(), 90, datetime(2026, 4, 25, tzinfo=timezone.utc))
    assert decision.action == "skip-protected"


def test_user_files_never_archived(memory_dir):
    p = _make_memory(
        memory_dir, "user_profile.md", created=datetime(2020, 1, 1, tzinfo=timezone.utc)
    )
    decision = prune.decide(p, p.read_text(), set(), 90, datetime(2026, 4, 25, tzinfo=timezone.utc))
    assert decision.action == "skip-protected"


def test_project_files_old_and_unreferenced_archived(memory_dir):
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    p = _make_memory(memory_dir, "project_pr42_done.md", created=old)
    decision = prune.decide(p, p.read_text(), set(), 90, datetime(2026, 4, 25, tzinfo=timezone.utc))
    assert decision.action == "archive"


def test_project_files_referenced_in_index_kept(memory_dir):
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    p = _make_memory(memory_dir, "project_active_thing.md", created=old)
    decision = prune.decide(
        p,
        p.read_text(),
        {"project_active_thing.md"},
        90,
        datetime(2026, 4, 25, tzinfo=timezone.utc),
    )
    assert decision.action == "skip-referenced"


def test_project_files_fresh_kept(memory_dir):
    fresh = datetime.now(timezone.utc) - timedelta(days=30)
    p = _make_memory(memory_dir, "project_recent.md", created=fresh)
    decision = prune.decide(p, p.read_text(), set(), 90, datetime.now(timezone.utc))
    assert decision.action == "skip-fresh"


def test_non_project_non_protected_files_skipped(memory_dir):
    """Files that don't match any prefix (e.g. hand-written notes) get left alone."""
    p = memory_dir / "scratchpad.md"
    p.write_text("---\nname: scratch\n---\nrandom notes", encoding="utf-8")
    decision = prune.decide(p, p.read_text(), set(), 90, datetime(2026, 4, 25, tzinfo=timezone.utc))
    assert decision.action == "skip-other"


# ---- scan() + apply() -----------------------------------------------------


def test_scan_classifies_each_file(memory_dir):
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    fresh = datetime.now(timezone.utc) - timedelta(days=10)

    _make_memory(memory_dir, "feedback_old.md", created=old)
    _make_memory(memory_dir, "project_old_unref.md", created=old)
    _make_memory(memory_dir, "project_old_referenced.md", created=old)
    _make_memory(memory_dir, "project_recent.md", created=fresh)
    _index(memory_dir, ["project_old_referenced.md"])

    decisions = prune.scan(min_age_days=90)
    actions = {d.path.name: d.action for d in decisions}

    assert actions["feedback_old.md"] == "skip-protected"
    assert actions["project_old_unref.md"] == "archive"
    assert actions["project_old_referenced.md"] == "skip-referenced"
    assert actions["project_recent.md"] == "skip-fresh"


def test_apply_moves_archive_candidates(memory_dir):
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    p = _make_memory(memory_dir, "project_obsolete.md", created=old)
    _index(memory_dir, [])

    decisions = prune.scan(min_age_days=90)
    moved = prune.apply(decisions)

    assert moved == 1
    assert not p.exists()
    assert (memory_dir / "_archive" / "project_obsolete.md").exists()


def test_apply_idempotent_when_target_exists(memory_dir):
    """Re-running on a file already archived shouldn't re-move or crash."""
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    _make_memory(memory_dir, "project_dup.md", created=old)
    _index(memory_dir, [])

    # First run
    decisions = prune.scan(min_age_days=90)
    assert prune.apply(decisions) == 1

    # Re-create source (simulating cron tick that found the file again somehow)
    _make_memory(memory_dir, "project_dup.md", created=old)
    decisions = prune.scan(min_age_days=90)
    moved = prune.apply(decisions)
    # Target exists → skip rather than overwrite
    assert moved == 0
    # Source still exists (didn't get clobbered)
    assert (memory_dir / "project_dup.md").exists()


def test_main_dry_run_does_not_move_files(memory_dir, capsys):
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    p = _make_memory(memory_dir, "project_old.md", created=old)
    _index(memory_dir, [])

    rc = prune.main([])  # no --apply
    assert rc == 0
    assert p.exists()
    assert not (memory_dir / "_archive" / "project_old.md").exists()
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_main_apply_moves_files(memory_dir, capsys):
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    p = _make_memory(memory_dir, "project_done.md", created=old)
    _index(memory_dir, [])

    rc = prune.main(["--apply"])
    assert rc == 0
    assert not p.exists()
    assert (memory_dir / "_archive" / "project_done.md").exists()


def test_main_returns_1_when_index_missing(memory_dir):
    # Explicitly don't create MEMORY.md
    with pytest.raises(SystemExit):
        prune.main([])


def test_min_age_days_env_override(memory_dir, monkeypatch, capsys):
    """`NAKAMA_MEMORY_PRUNE_MIN_AGE_DAYS` env should override default."""
    semi_recent = datetime.now(timezone.utc) - timedelta(days=20)
    p = _make_memory(memory_dir, "project_x.md", created=semi_recent)
    _index(memory_dir, [])

    monkeypatch.setenv("NAKAMA_MEMORY_PRUNE_MIN_AGE_DAYS", "10")
    rc = prune.main(["--apply"])
    assert rc == 0
    # 20-day-old file > 10-day threshold → archived
    assert not p.exists()
    assert (memory_dir / "_archive" / "project_x.md").exists()
