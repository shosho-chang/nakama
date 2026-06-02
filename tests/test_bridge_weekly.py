"""Tests for thousand_sunny.routers.bridge_weekly plan mutations (ADR-039 Slice 1a).

Covers the GET dashboard render (plan-edit affordances) + the three POST routes
that wire ``shared.weekly_writer`` (add/update, remove, sync-scheduled). Mirrors
the bridge_projects test harness: no auth when WEB_PASSWORD/WEB_SECRET are unset,
VAULT_PATH → tmp_path, reload modules, drive with TestClient + 303 assertions.
"""

from __future__ import annotations

import importlib

import pytest
import yaml
from fastapi.testclient import TestClient

# A task scheduled inside the Sunday-start week 2026-05-31 .. 2026-06-06.
#   2026-06-03 = Wed (weekday)   2026-06-06 = Sat / 2026-06-07 = Sun (weekend)
WEEK_KEY = "2026-05-31"
SAMPLE_TASK = """---
title: 測試任務
status: to-do
預估🍅: 6
scheduled: 2026-06-03
plan:
  - date: 2026-06-03
    pomodoros: 2
timeEntries: []
tags:
  - task
---

任務內文（不可被改動）。
"""


def _task_path(tmp_path):
    return tmp_path / "TaskNotes" / "Tasks" / "測試任務.md"


def _plan(tmp_path) -> list[dict]:
    raw = _task_path(tmp_path).read_text(encoding="utf-8")
    fm = yaml.safe_load(raw.split("---", 2)[1])
    return [dict(e) for e in (fm.get("plan") or [])]


def _scheduled(tmp_path):
    raw = _task_path(tmp_path).read_text(encoding="utf-8")
    fm = yaml.safe_load(raw.split("---", 2)[1])
    return fm.get("scheduled")


def _time_entries(tmp_path) -> list[dict]:
    raw = _task_path(tmp_path).read_text(encoding="utf-8")
    fm = yaml.safe_load(raw.split("---", 2)[1])
    return [dict(e) for e in (fm.get("timeEntries") or [])]


def _weekly_file(tmp_path):
    return tmp_path / "Journals" / "Weekly" / f"{WEEK_KEY}.md"


def _weekly_fm(tmp_path) -> dict:
    raw = _weekly_file(tmp_path).read_text(encoding="utf-8")
    return yaml.safe_load(raw.split("---", 2)[1]) or {}


def _weekly_body(tmp_path) -> str:
    raw = _weekly_file(tmp_path).read_text(encoding="utf-8")
    return raw.split("---", 2)[2]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))
    monkeypatch.setenv("NAKAMA_RESEARCH_CACHE_DIR", str(tmp_path / "research_cache"))

    tasks_dir = tmp_path / "TaskNotes" / "Tasks"
    tasks_dir.mkdir(parents=True)
    _task_path(tmp_path).write_text(SAMPLE_TASK, encoding="utf-8")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge_weekly as bw_module

    importlib.reload(auth_module)
    importlib.reload(bw_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


class TestRender:
    def test_active_week_shows_plan_editor(self, client, monkeypatch):
        # Pin "today" so the requested week is the (active) current week.
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: wi.date(2026, 6, 1))
        r = client.get(f"/bridge/weekly?week={WEEK_KEY}")
        assert r.status_code == 200
        body = r.text
        assert "wk-plan-add" in body  # add/update form
        assert 'action="/bridge/weekly/plan"' in body
        assert 'action="/bridge/weekly/plan/remove"' in body
        assert 'action="/bridge/weekly/sync-scheduled"' in body
        assert "測試任務" in body

    def test_no_error_banner_by_default(self, client):
        r = client.get(f"/bridge/weekly?week={WEEK_KEY}")
        assert '<div class="wk-toast"' not in r.text

    def test_error_banner_renders_on_err_param(self, client):
        r = client.get(f"/bridge/weekly?week={WEEK_KEY}&err=weekend")
        assert '<div class="wk-toast"' in r.text
        assert "週末排程需填寫原因" in r.text


class TestAddPlan:
    def test_add_weekday_entry(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-04",
                "pomodoros": "3",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}"
        plan = _plan(tmp_path)
        match = [e for e in plan if str(e["date"]) == "2026-06-04"]
        assert len(match) == 1 and match[0]["pomodoros"] == 3

    def test_add_same_date_updates_not_duplicates(self, client, tmp_path):
        client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-03",
                "pomodoros": "5",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        plan = _plan(tmp_path)
        match = [e for e in plan if str(e["date"]) == "2026-06-03"]
        assert len(match) == 1 and match[0]["pomodoros"] == 5

    def test_weekend_without_reason_rejected(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-06",
                "pomodoros": "1",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=weekend"
        assert not [e for e in _plan(tmp_path) if str(e["date"]) == "2026-06-06"]

    def test_weekend_with_reason_accepted(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-06",
                "pomodoros": "1",
                "reason": "趕稿截止",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "err=" not in r.headers["location"]
        match = [e for e in _plan(tmp_path) if str(e["date"]) == "2026-06-06"]
        assert len(match) == 1 and match[0]["reason"] == "趕稿截止"

    @pytest.mark.parametrize("bad", ["0", "21", "-1"])
    def test_pomodoros_out_of_range_rejected(self, client, tmp_path, bad):
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-04",
                "pomodoros": bad,
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=pomodoros"
        assert not [e for e in _plan(tmp_path) if str(e["date"]) == "2026-06-04"]

    def test_bad_date_rejected(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "not-a-date",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=date"

    def test_unknown_task_redirects_with_err(self, client):
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "不存在的任務",
                "entry_date": "2026-06-04",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=task"


class TestRemovePlan:
    def test_remove_existing_entry(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/plan/remove",
            data={"task_slug": "測試任務", "entry_date": "2026-06-03", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}"
        assert not [e for e in _plan(tmp_path) if str(e["date"]) == "2026-06-03"]

    def test_remove_unknown_task_err(self, client):
        r = client.post(
            "/bridge/weekly/plan/remove",
            data={"task_slug": "不存在的任務", "entry_date": "2026-06-03", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=task"


class TestSyncScheduled:
    def test_sync_sets_scheduled_to_next_plan_date(self, client, tmp_path, monkeypatch):
        import shared.weekly_writer as ww

        # Pin today so "earliest plan date ≥ today" is deterministic across runs.
        monkeypatch.setattr(ww, "_today", lambda: ww.date(2026, 6, 1))
        # add a later weekday entry; earliest future is still 2026-06-03 (the seed)
        client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-04",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        r = client.post(
            "/bridge/weekly/sync-scheduled",
            data={"task_slug": "測試任務", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}"
        assert str(_scheduled(tmp_path)) == "2026-06-03"

    def test_sync_unknown_task_err(self, client):
        r = client.post(
            "/bridge/weekly/sync-scheduled",
            data={"task_slug": "不存在的任務", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=task"


class TestTaskDetail:
    def test_get_renders_task_page(self, client):
        r = client.get(f"/bridge/weekly/task/測試任務?week={WEEK_KEY}")
        assert r.status_code == 200
        body = r.text
        assert "測試任務" in body
        assert "tk-timerbox" in body  # the pomodoro timer block
        assert 'action="/bridge/weekly/task/測試任務/log"' in body or "%E6%B8%AC" in body
        assert "回週看板" in body

    def test_get_unknown_task_redirects(self, client):
        r = client.get(f"/bridge/weekly/task/不存在?week={WEEK_KEY}", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=task"

    def test_log_pomodoro_appends_time_entry(self, client, tmp_path):
        before = len(_time_entries(tmp_path))
        r = client.post(
            "/bridge/weekly/task/測試任務/log",
            data={"mode": "pomodoro", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"].endswith("&logged=1") or "logged=1" in r.headers["location"]
        entries = _time_entries(tmp_path)
        assert len(entries) == before + 1
        assert entries[-1]["mode"] == "pomodoro"
        assert entries[-1]["startTime"] and entries[-1]["endTime"]

    def test_log_deep_marks_mode(self, client, tmp_path):
        client.post(
            "/bridge/weekly/task/測試任務/log",
            data={"mode": "deep", "week": WEEK_KEY},
            follow_redirects=False,
        )
        entries = _time_entries(tmp_path)
        assert entries[-1]["mode"] == "deep"

    def test_log_actual_elapsed_from_timer(self, client, tmp_path):
        """ADR-040 A2: the live timer reports elapsed_seconds → the logged span is
        the real elapsed time (提早完成), not a fixed nominal block."""
        client.post(
            "/bridge/weekly/task/測試任務/log",
            data={"mode": "pomodoro", "elapsed_seconds": "600", "week": WEEK_KEY},  # 10 min
            follow_redirects=False,
        )
        e = _time_entries(tmp_path)[-1]
        assert e["actual_minutes"] == 10
        assert e["planned_minutes"] == 25
        assert e["completed"] is True
        assert "manual" not in e

    def test_log_manual_flags_claim_full_block(self, client, tmp_path):
        client.post(
            "/bridge/weekly/task/測試任務/log",
            data={"mode": "deep", "manual": "1", "week": WEEK_KEY},
            follow_redirects=False,
        )
        e = _time_entries(tmp_path)[-1]
        assert e["actual_minutes"] == 75  # full nominal block
        assert e["manual"] is True

    def test_log_unknown_mode_rejected(self, client, tmp_path):
        before = len(_time_entries(tmp_path))
        r = client.post(
            "/bridge/weekly/task/測試任務/log",
            data={"mode": "bogus", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "err=mode" in r.headers["location"]
        assert len(_time_entries(tmp_path)) == before

    def test_log_unknown_task_redirects_with_err(self, client):
        r = client.post(
            "/bridge/weekly/task/不存在/log",
            data={"mode": "pomodoro", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert "err=log" in r.headers["location"]


class TestWeeklyFileWrites:
    """ADR-040 Slice 2 — the Journals/Weekly 🟡 composable write routes."""

    def test_top3_creates_file_and_persists(self, client, tmp_path):
        # dropdowns submit repeated `top3` fields (a blank slot is skipped)
        r = client.post(
            "/bridge/weekly/top3",
            data={"week": WEEK_KEY, "expected_token": "", "top3": ["測試任務", "", "肌酸的妙用"]},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&saved=top3"
        fm = _weekly_fm(tmp_path)
        assert fm["top3"] == ["[[測試任務]]", "[[肌酸的妙用]]"]  # blank slot dropped, order kept
        assert fm["status"] == "planning"  # created from template

    def test_targets_persist_only_positive(self, client, tmp_path):
        client.post(
            "/bridge/weekly/targets",
            data={"week": WEEK_KEY, "expected_token": "", "pomodoro": "35", "ufo": "0"},
            follow_redirects=False,
        )
        assert _weekly_fm(tmp_path)["targets"] == {"pomodoro": 35}

    def test_review_writes_prose_and_next3(self, client, tmp_path):
        client.post(
            "/bridge/weekly/review",
            data={
                "week": WEEK_KEY,
                "expected_token": "",
                "highlight": "出片了 🎉",
                "next3": "[[下週要事]]",
                "mark_reviewed": "1",
            },
            follow_redirects=False,
        )
        fm = _weekly_fm(tmp_path)
        assert fm["status"] == "reviewed"
        assert fm["next3"] == ["[[下週要事]]"]
        assert "出片了 🎉" in _weekly_body(tmp_path)

    def test_notes_persist(self, client, tmp_path):
        client.post(
            "/bridge/weekly/notes",
            data={"week": WEEK_KEY, "expected_token": "", "notes": "下週試 75 分 block"},
            follow_redirects=False,
        )
        assert "下週試 75 分 block" in _weekly_body(tmp_path)

    def test_status_advances(self, client, tmp_path):
        client.post(
            "/bridge/weekly/status",
            data={"week": WEEK_KEY, "expected_token": "", "status": "active"},
            follow_redirects=False,
        )
        assert _weekly_fm(tmp_path)["status"] == "active"

    def test_stale_token_is_conflict(self, client, tmp_path):
        # create the file, then post with the now-stale empty token → conflict
        client.post(
            "/bridge/weekly/top3",
            data={"week": WEEK_KEY, "expected_token": "", "top3": "A"},
            follow_redirects=False,
        )
        r = client.post(
            "/bridge/weekly/top3",
            data={"week": WEEK_KEY, "expected_token": "", "top3": "B"},
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=conflict"
        assert _weekly_fm(tmp_path)["top3"] == ["[[A]]"]  # B rejected

    def test_active_week_renders_plan_and_review_forms(self, client, monkeypatch):
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: wi.date(2026, 6, 1))
        body = client.get(f"/bridge/weekly?week={WEEK_KEY}").text
        assert 'action="/bridge/weekly/top3"' in body
        assert 'action="/bridge/weekly/targets"' in body
        assert 'action="/bridge/weekly/review"' in body
        assert 'action="/bridge/weekly/notes"' in body
        assert 'action="/bridge/weekly/status"' in body
        assert 'name="expected_token"' in body
        # top3 is now a dropdown picker (not free text); the seed task is an option
        assert "wk-top3-sel" in body
        assert "<optgroup" in body
        assert "測試任務" in body


class TestAuthGate:
    def test_post_requires_auth_when_password_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WEB_PASSWORD", "secret")
        monkeypatch.setenv("WEB_SECRET", "topsecret")
        monkeypatch.setenv("DISABLE_ROBIN", "1")
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))
        monkeypatch.setenv("NAKAMA_RESEARCH_CACHE_DIR", str(tmp_path / "research_cache"))
        (tmp_path / "TaskNotes" / "Tasks").mkdir(parents=True)
        _task_path(tmp_path).write_text(SAMPLE_TASK, encoding="utf-8")

        import thousand_sunny.app as app_module
        import thousand_sunny.auth as auth_module
        import thousand_sunny.routers.bridge_weekly as bw_module

        importlib.reload(auth_module)
        importlib.reload(bw_module)
        importlib.reload(app_module)
        c = TestClient(app_module.app)
        r = c.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-04",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login")
        # the write must NOT have happened
        assert not [e for e in _plan(tmp_path) if str(e["date"]) == "2026-06-04"]
