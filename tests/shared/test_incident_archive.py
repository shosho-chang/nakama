"""Tests for shared/incident_archive.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from shared import incident_archive
from shared.incident_archive import (
    archive_incident,
    list_pending_incidents,
)

_TPE = ZoneInfo("Asia/Taipei")


def _fake_fired_at(year: int = 2026, month: int = 4, day: int = 26, hour: int = 14) -> datetime:
    return datetime(year, month, day, hour, 30, 0, tzinfo=_TPE).astimezone(timezone.utc)


def test_archive_creates_new_stub_with_frontmatter(tmp_path: Path):
    result = archive_incident(
        rule_id="backup-r2-fail",
        severity="error",
        title="R2 backup mirror failed",
        message="upload to bucket B2 timed out after 30s",
        fired_at=_fake_fired_at(),
        context={"endpoint": "b2.example.com", "attempt": 3},
        pending_dir=tmp_path,
    )

    assert result is not None
    assert result.is_new is True
    assert result.path == tmp_path / "2026-04-26-backup-r2-fail.md"
    body = result.path.read_text(encoding="utf-8")
    # frontmatter
    assert "id: 2026-04-26-backup-r2-fail" in body
    assert 'title: "R2 backup mirror failed"' in body
    assert "severity: SEV-2" in body  # error → SEV-2
    assert "trigger: backup-r2-fail" in body
    assert "status: detected" in body
    # body
    assert "upload to bucket B2 timed out after 30s" in body
    assert "## Repeat fires" not in body  # not until 2nd fire
    # context rendered alphabetically
    assert "- `attempt`: 3" in body
    assert "- `endpoint`: b2.example.com" in body


def test_archive_dedupes_same_day_rule_appends_repeat(tmp_path: Path):
    fired_1 = _fake_fired_at(hour=10)
    fired_2 = fired_1 + timedelta(hours=2)
    fired_3 = fired_1 + timedelta(hours=4)

    r1 = archive_incident(
        rule_id="cron-stuck",
        severity="critical",
        title="cron franky-probe stalled",
        message="no run in 12 min",
        fired_at=fired_1,
        pending_dir=tmp_path,
    )
    r2 = archive_incident(
        rule_id="cron-stuck",
        severity="critical",
        title="cron franky-probe stalled",
        message="still stuck after 14 min",
        fired_at=fired_2,
        pending_dir=tmp_path,
    )
    r3 = archive_incident(
        rule_id="cron-stuck",
        severity="critical",
        title="cron franky-probe stalled",
        message="still stuck after 16 min",
        fired_at=fired_3,
        pending_dir=tmp_path,
    )

    # Only one file
    assert sorted(p.name for p in tmp_path.glob("*.md")) == ["2026-04-26-cron-stuck.md"]

    assert r1.is_new is True
    assert r2.is_new is False
    assert r3.is_new is False

    body = r1.path.read_text(encoding="utf-8")
    assert "## Repeat fires" in body
    # First fire row stays in §Timeline; repeat fires has 2 rows (fire 2 + fire 3)
    repeat_section = body.split("## Repeat fires")[1]
    assert "still stuck after 14 min" in repeat_section
    assert "still stuck after 16 min" in repeat_section
    # First fire message NOT in repeat section (it's in Timeline)
    assert "no run in 12 min" not in repeat_section


def test_archive_different_days_create_separate_files(tmp_path: Path):
    archive_incident(
        rule_id="r2-mirror",
        severity="error",
        title="X",
        message="day 1",
        fired_at=_fake_fired_at(day=26),
        pending_dir=tmp_path,
    )
    archive_incident(
        rule_id="r2-mirror",
        severity="error",
        title="X",
        message="day 2",
        fired_at=_fake_fired_at(day=27),
        pending_dir=tmp_path,
    )

    files = sorted(p.name for p in tmp_path.glob("*.md"))
    assert files == ["2026-04-26-r2-mirror.md", "2026-04-27-r2-mirror.md"]


@pytest.mark.parametrize(
    "severity,expected",
    [
        ("critical", "SEV-1"),
        ("error", "SEV-2"),
        ("warn", "SEV-3"),
        ("warning", "SEV-3"),
        ("info", "SEV-4"),
        ("CRITICAL", "SEV-1"),
        ("unknown_made_up", "SEV-3"),
    ],
)
def test_severity_to_tier_mapping(tmp_path: Path, severity: str, expected: str):
    result = archive_incident(
        rule_id=f"rule-{severity}",
        severity=severity,
        title="t",
        message="m",
        fired_at=_fake_fired_at(),
        pending_dir=tmp_path,
    )
    assert result is not None
    assert f"severity: {expected}" in result.path.read_text(encoding="utf-8")


def test_slugify_handles_special_chars(tmp_path: Path):
    result = archive_incident(
        rule_id="alert/category with spaces & symbols!",
        severity="error",
        title="t",
        message="m",
        fired_at=_fake_fired_at(),
        pending_dir=tmp_path,
    )
    assert result is not None
    # Special chars collapsed to single dashes, lowercased
    assert result.path.name == "2026-04-26-alert-category-with-spaces-symbols.md"


def test_archive_naive_datetime_treated_as_utc(tmp_path: Path):
    """Naive datetime passed → assume UTC, convert to Asia/Taipei for filename."""
    naive_utc = datetime(2026, 4, 26, 22, 30, 0)  # 22:30 UTC = 06:30 next day Taipei
    result = archive_incident(
        rule_id="probe-fail",
        severity="error",
        title="t",
        message="m",
        fired_at=naive_utc,
        pending_dir=tmp_path,
    )
    assert result is not None
    # 22:30 UTC = 06:30 Asia/Taipei next day
    assert result.path.name == "2026-04-27-probe-fail.md"


def test_archive_uses_env_dir_when_no_pending_dir_passed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NAKAMA_INCIDENTS_PENDING_DIR", str(tmp_path))
    result = archive_incident(
        rule_id="env-test",
        severity="error",
        title="t",
        message="m",
        fired_at=_fake_fired_at(),
    )
    assert result is not None
    assert result.path.parent == tmp_path


def test_archive_returns_none_when_dir_unwritable(tmp_path: Path, caplog):
    # Make pending_dir a *file* — mkdir(parents=True, exist_ok=True) won't help
    bogus = tmp_path / "blocker"
    bogus.write_text("i am not a directory")
    pending = bogus / "subdir"  # mkdir will fail because parent is a file

    caplog.set_level("ERROR", logger="nakama.incident_archive")
    result = archive_incident(
        rule_id="unwritable",
        severity="error",
        title="t",
        message="m",
        fired_at=_fake_fired_at(),
        pending_dir=pending,
    )
    assert result is None
    assert any("mkdir" in r.message for r in caplog.records)


def test_list_pending_incidents_empty_dir(tmp_path: Path):
    rollup = list_pending_incidents(
        since=_fake_fired_at() - timedelta(days=30), pending_dir=tmp_path
    )
    assert rollup.total == 0
    assert rollup.by_severity == {}
    assert rollup.open_count == 0
    assert rollup.top_recurring == []


def test_list_pending_incidents_counts_severity_and_open(tmp_path: Path):
    archive_incident(
        rule_id="r1",
        severity="critical",
        title="t",
        message="m1",
        fired_at=_fake_fired_at(day=26),
        pending_dir=tmp_path,
    )
    archive_incident(
        rule_id="r2",
        severity="error",
        title="t",
        message="m2",
        fired_at=_fake_fired_at(day=27),
        pending_dir=tmp_path,
    )
    archive_incident(
        rule_id="r3",
        severity="error",
        title="t",
        message="m3",
        fired_at=_fake_fired_at(day=28),
        pending_dir=tmp_path,
    )
    # Mark r3 closed by editing its frontmatter
    r3_path = next(tmp_path.glob("*r3.md"))
    text = r3_path.read_text(encoding="utf-8")
    r3_path.write_text(text.replace("status: detected", "status: closed"), encoding="utf-8")

    rollup = list_pending_incidents(
        since=_fake_fired_at() - timedelta(days=30), pending_dir=tmp_path
    )
    assert rollup.total == 3
    assert rollup.by_severity == {"SEV-1": 1, "SEV-2": 2}
    assert rollup.open_count == 2  # r1 + r2 are detected, r3 is closed


def test_list_pending_incidents_top_recurring_counts_repeat_fires(tmp_path: Path):
    # rule-A fires 3 times same day (1 timeline + 2 repeats)
    fired = _fake_fired_at(day=26)
    archive_incident(
        rule_id="A", severity="error", title="t", message="m", fired_at=fired, pending_dir=tmp_path
    )
    archive_incident(
        rule_id="A",
        severity="error",
        title="t",
        message="m",
        fired_at=fired + timedelta(hours=1),
        pending_dir=tmp_path,
    )
    archive_incident(
        rule_id="A",
        severity="error",
        title="t",
        message="m",
        fired_at=fired + timedelta(hours=2),
        pending_dir=tmp_path,
    )
    # rule-B fires once
    archive_incident(
        rule_id="B", severity="error", title="t", message="m", fired_at=fired, pending_dir=tmp_path
    )

    rollup = list_pending_incidents(since=fired - timedelta(days=1), pending_dir=tmp_path)
    assert rollup.total == 2  # 2 files
    # Top recurring: A appeared 3 times (1 initial + 2 repeats), B appeared 1 time
    rules = dict(rollup.top_recurring)
    assert rules["A"] == 3
    assert rules["B"] == 1
    assert rollup.top_recurring[0][0] == "A"  # A first (highest count)


def test_list_pending_incidents_skips_files_older_than_since(tmp_path: Path):
    import os

    archive_incident(
        rule_id="old",
        severity="error",
        title="t",
        message="m",
        fired_at=_fake_fired_at(day=26),
        pending_dir=tmp_path,
    )
    old_path = next(tmp_path.glob("*old.md"))
    # Backdate mtime to 60 days ago
    sixty_days_ago = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
    os.utime(old_path, (sixty_days_ago, sixty_days_ago))

    archive_incident(
        rule_id="new",
        severity="error",
        title="t",
        message="m",
        fired_at=_fake_fired_at(day=26),
        pending_dir=tmp_path,
    )

    rollup = list_pending_incidents(
        since=datetime.now(timezone.utc) - timedelta(days=30),
        pending_dir=tmp_path,
    )
    assert rollup.total == 1  # only "new" file is within window
    assert rollup.top_recurring[0][0] == "new"


def test_list_pending_incidents_nonexistent_dir(tmp_path: Path):
    rollup = list_pending_incidents(
        since=_fake_fired_at() - timedelta(days=30),
        pending_dir=tmp_path / "does-not-exist",
    )
    assert rollup.total == 0


def test_repeat_fire_message_with_pipe_chars_escaped(tmp_path: Path):
    fired = _fake_fired_at(hour=10)
    archive_incident(
        rule_id="pipe-test",
        severity="error",
        title="t",
        message="first",
        fired_at=fired,
        pending_dir=tmp_path,
    )
    # Message containing a literal `|` — would break Markdown table if not escaped
    archive_incident(
        rule_id="pipe-test",
        severity="error",
        title="t",
        message="error: A | B failed",
        fired_at=fired + timedelta(hours=1),
        pending_dir=tmp_path,
    )

    body = (tmp_path / "2026-04-26-pipe-test.md").read_text(encoding="utf-8")
    repeat = body.split("## Repeat fires")[1]
    # `|` inside message is escaped so the table stays well-formed
    assert "A \\| B failed" in repeat


def test_default_pending_dir_resolves_env_or_repo_default(monkeypatch, tmp_path):
    monkeypatch.delenv("NAKAMA_INCIDENTS_PENDING_DIR", raising=False)
    default = incident_archive.default_pending_dir()
    assert default.name == "incidents-pending"
    assert default.parent.name == "data"

    monkeypatch.setenv("NAKAMA_INCIDENTS_PENDING_DIR", str(tmp_path / "custom"))
    overridden = incident_archive.default_pending_dir()
    assert overridden == tmp_path / "custom"
