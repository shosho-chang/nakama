"""shared.weekly_newsletter — 「WXX 電子報」 Monday 06:00 × 2 🍅 (修修 2026-11-12)."""

from __future__ import annotations

from datetime import date

import pytest
import yaml

from shared import weekly_newsletter as wn
from shared.weekly_indexer import week_for_date

pytestmark = pytest.mark.weekly_autotask

TODAY = date(2026, 11, 12)  # Thu of W46 (11/8 – 11/14)
W46 = week_for_date(TODAY)
W47 = W46.shift(1)


def _tasks(vault):
    d = vault / "TaskNotes" / "Tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fm(path):
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])


def test_week_label_matches_shosho_numbering():
    assert W46.label == "W46" and W46.start == date(2026, 11, 8)
    assert wn.newsletter_title(W47) == "W47 電子報"
    assert wn.newsletter_start(W47).isoformat() == "2026-11-16T06:00:00"  # Monday


def test_creates_and_schedules_monday_0600_two_pomodoros(tmp_path):
    _tasks(tmp_path)
    slug = wn.ensure_weekly_newsletter(tmp_path, W47, today=TODAY)
    assert slug == "W47 電子報"
    fm = _fm(tmp_path / "TaskNotes" / "Tasks" / "W47 電子報.md")
    assert fm["category"] == "work"
    assert "projects" not in fm
    (entry,) = fm["plan"]
    assert str(entry["date"]) == "2026-11-16"
    assert entry["pomodoros"] == 2
    assert str(entry["start"]).replace(" ", "T").startswith("2026-11-16T06:00:00")
    assert wn.marker_outcome(W47.file_key) == "created"


def test_only_once_even_after_the_task_is_deleted(tmp_path):
    _tasks(tmp_path)
    assert wn.ensure_weekly_newsletter(tmp_path, W47, today=TODAY)
    (tmp_path / "TaskNotes" / "Tasks" / "W47 電子報.md").unlink()
    assert wn.ensure_weekly_newsletter(tmp_path, W47, today=TODAY) is None
    assert not (tmp_path / "TaskNotes" / "Tasks" / "W47 電子報.md").exists()


def test_existing_variant_title_counts(tmp_path):
    archive = tmp_path / "TaskNotes" / "Archive"
    archive.mkdir(parents=True)
    (archive / "26W47電子報.md").write_text(
        "---\ntitle: 26W47電子報\ncreated: 2026-11-15\n---\n", encoding="utf-8"
    )
    _tasks(tmp_path)
    assert wn.ensure_weekly_newsletter(tmp_path, W47, today=TODAY) is None
    assert wn.marker_outcome(W47.file_key) == "existing"
    assert not (tmp_path / "TaskNotes" / "Tasks" / "W47 電子報.md").exists()


def test_last_years_same_number_does_not_count(tmp_path):
    archive = tmp_path / "TaskNotes" / "Archive"
    archive.mkdir(parents=True)
    (archive / "W47 寫本週電子報.md").write_text(
        "---\ntitle: W47 寫本週電子報\ncreated: 2025-11-17\n---\n", encoding="utf-8"
    )
    _tasks(tmp_path)
    assert wn.ensure_weekly_newsletter(tmp_path, W47, today=TODAY) == "W47 電子報"


def test_other_week_number_does_not_count(tmp_path):
    t = _tasks(tmp_path)
    (t / "W4 電子報.md").write_text("---\ntitle: W4 電子報\n---\n", encoding="utf-8")
    (t / "W470 電子報.md").write_text("---\ntitle: W470 電子報\n---\n", encoding="utf-8")
    assert wn.ensure_weekly_newsletter(tmp_path, W47, today=TODAY) == "W47 電子報"


def test_past_weeks_are_never_backfilled(tmp_path):
    _tasks(tmp_path)
    assert wn.ensure_weekly_newsletter(tmp_path, W46.shift(-1), today=TODAY) is None
    assert wn.marker_outcome(W46.shift(-1).file_key) is None


def test_name_taken_is_left_alone(tmp_path):
    t = _tasks(tmp_path)
    # same basename, but dated last year so the scan does not treat it as this week's
    (t / "W47 電子報.md").write_text(
        "---\ntitle: W47 電子報\ncreated: 2025-11-17\n---\nold\n", encoding="utf-8"
    )
    assert wn.ensure_weekly_newsletter(tmp_path, W47, today=TODAY) is None
    assert wn.marker_outcome(W47.file_key) == "name_taken"
    assert (t / "W47 電子報.md").read_text(encoding="utf-8").endswith("old\n")


def test_disabled_flag_short_circuits(tmp_path, monkeypatch):
    _tasks(tmp_path)
    monkeypatch.setattr(wn, "ENABLED", False)
    assert wn.ensure_weekly_newsletter(tmp_path, W47, today=TODAY) is None
    assert wn.marker_outcome(W47.file_key) is None
