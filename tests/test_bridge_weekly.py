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


def _weekly_token(tmp_path) -> str:
    from shared.weekly_writer import weekly_file_token

    return weekly_file_token(tmp_path, WEEK_KEY)


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


class TestNewTask:
    """v3-H Slice 2: 新增任務 from the dashboard (optional project, category)."""

    def _files(self, tmp_path):
        return {p.name for p in (tmp_path / "TaskNotes" / "Tasks").iterdir()}

    def _fm(self, tmp_path, name):
        raw = (tmp_path / "TaskNotes" / "Tasks" / name).read_text(encoding="utf-8")
        return yaml.safe_load(raw.split("---", 2)[1])

    def test_standalone_task(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/task/new",
            data={
                "title": "讀論文",
                "project": "",
                "category": "growth",
                "est_pomodoros": "3",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith(f"/bridge/weekly?week={WEEK_KEY}&saved=task_new")
        # v3-I: jump to the new row + focus its 排入 chip (?focus + #task- anchor).
        assert "focus=%E8%AE%80%E8%AB%96%E6%96%87" in loc  # quote("讀論文")
        assert loc.endswith("#task-%E8%AE%80%E8%AB%96%E6%96%87")
        assert "讀論文.md" in self._files(tmp_path)
        fm = self._fm(tmp_path, "讀論文.md")
        assert "projects" not in fm
        assert fm["category"] == "growth"
        assert fm["預估🍅"] == 3

    def test_project_linked_task(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/task/new",
            data={"title": "腳本", "project": "肌酸的妙用", "category": "work", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "肌酸的妙用 - 腳本.md" in self._files(tmp_path)
        assert self._fm(tmp_path, "肌酸的妙用 - 腳本.md")["projects"] == ["[[肌酸的妙用]]"]

    def test_blank_title_rejected(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/task/new",
            data={"title": "   ", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "err=title" in r.headers["location"]

    def test_duplicate_rejected(self, client, tmp_path):
        # SAMPLE_TASK already wrote 測試任務.md
        r = client.post(
            "/bridge/weekly/task/new",
            data={"title": "測試任務", "project": "", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert "err=task_exists" in r.headers["location"]

    def test_bad_pomodoros_rejected(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/task/new",
            data={"title": "x", "est_pomodoros": "99", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert "err=pomodoros" in r.headers["location"]


class TestFromProjectRedirect:
    """v3-H Slice 3: plan actions fired from a Project Brief tab redirect back there."""

    def test_remove_with_from_project(self, client):
        r = client.post(
            "/bridge/weekly/plan/remove",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-03",
                "week": WEEK_KEY,
                "from_project": "肌酸的妙用",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("/bridge/projects/")
        assert "tab=brief" in loc
        assert "/bridge/weekly" not in loc

    def test_plan_add_without_from_project_still_dashboard(self, client):
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-04",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.headers["location"].startswith("/bridge/weekly?")


class TestRender:
    def test_active_week_shows_plan_editor(self, client, monkeypatch):
        # Pin "today" so the requested week is the (active) current week.
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: wi.date(2026, 6, 1))
        r = client.get(f"/bridge/weekly?week={WEEK_KEY}")
        assert r.status_code == 200
        body = r.text
        # v3-B merged 「排入」: one form (date + optional time + 🍅) → plan + Google
        assert "wk-plan-merged" in body
        assert 'action="/bridge/weekly/plan"' in body
        assert 'action="/bridge/weekly/plan/remove"' in body
        assert 'name="entry_time"' in body  # the merged form carries an optional time
        assert 'action="/bridge/weekly/sync-scheduled"' not in body  # retired in v3-B
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
        # v3-B: lands back on the row (#task-<slug> anchor) — stay-in-place
        assert r.headers["location"].startswith(f"/bridge/weekly?week={WEEK_KEY}")
        assert "#task-" in r.headers["location"]
        plan = _plan(tmp_path)
        match = [e for e in plan if str(e["date"]) == "2026-06-04"]
        assert len(match) == 1 and match[0]["pomodoros"] == 3

    def test_cross_week_schedule_jumps_to_that_week(self, client, tmp_path):
        # v3-G: viewing WEEK_KEY (2026-05-31) but scheduling 2026-06-08 (Mon, a LATER
        # week, Sunday 2026-06-07) → the dashboard must land on the scheduled date's
        # week so the new chip is visible, not stay on the viewed week (修修: "disappears").
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-08",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"].startswith("/bridge/weekly?week=2026-06-07")
        assert f"week={WEEK_KEY}" not in r.headers["location"]
        assert "#task-" in r.headers["location"]

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
        assert r.headers["location"].startswith(f"/bridge/weekly?week={WEEK_KEY}&err=weekend")
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
        # the weekend gate passed (the point of this test); the calendar step is
        # best-effort and unmocked here, so a cal_* banner is irrelevant — assert the
        # vault entry + reason landed (v3-E: blank time = all-day, still vault-first).
        assert "err=weekend" not in r.headers["location"]
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
        assert r.headers["location"].startswith(f"/bridge/weekly?week={WEEK_KEY}&err=pomodoros")
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
        # v3-C: like the pomodoros error, the row anchor rides along (stay-in-place)
        loc = r.headers["location"]
        assert loc.startswith(f"/bridge/weekly?week={WEEK_KEY}&err=date#task-")

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
        assert r.headers["location"].startswith(f"/bridge/weekly?week={WEEK_KEY}")
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
        # the unified pomodoro footer dock (same form as the Project page) + 25/75 toggle
        assert "tk-pomodoro-dock" in body and "pj-timer-btn" in body
        assert 'data-minutes="75"' in body  # 75-min 🤩 super-focus option present
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

    # ── A7 / Slice W — scoped note-body editor ──────────────────────────────
    def test_get_renders_note_editor(self, client):
        body = client.get(f"/bridge/weekly/task/測試任務?week={WEEK_KEY}").text
        assert 'action="/bridge/weekly/task/測試任務/body"' in body or "%E6%B8%AC" in body
        assert 'name="body"' in body and 'name="expected_token"' in body
        assert "在 Obsidian 繼續編輯" in body  # prominent deep-link (A7)
        assert "任務內文" in body  # existing body pre-filled into the textarea

    def test_body_save_persists_and_preserves_frontmatter(self, client, tmp_path):
        from shared.weekly_writer import task_file_token

        tok = task_file_token(tmp_path, "測試任務")
        r = client.post(
            "/bridge/weekly/task/測試任務/body",
            data={"week": WEEK_KEY, "expected_token": tok, "body": "# 本週電子報\r\n\r\n草稿。"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "saved=body" in r.headers["location"]  # slug is URL-encoded in the path
        raw = _task_path(tmp_path).read_text(encoding="utf-8")
        assert "# 本週電子報" in raw
        assert "\r\n" not in raw  # CRLF normalised to LF
        fm = yaml.safe_load(raw.split("---", 2)[1])
        assert fm["title"] == "測試任務" and fm["預估🍅"] == 6  # frontmatter untouched

    def test_body_save_stale_token_conflict(self, client, tmp_path):
        from shared.weekly_writer import task_file_token

        tok = task_file_token(tmp_path, "測試任務")
        client.post(
            "/bridge/weekly/task/測試任務/body",
            data={"week": WEEK_KEY, "expected_token": tok, "body": "first"},
            follow_redirects=False,
        )
        r = client.post(
            "/bridge/weekly/task/測試任務/body",
            data={"week": WEEK_KEY, "expected_token": tok, "body": "second"},
            follow_redirects=False,
        )
        assert "err=conflict" in r.headers["location"]
        assert "second" not in _task_path(tmp_path).read_text(encoding="utf-8")

    def test_body_save_unknown_task_err(self, client):
        r = client.post(
            "/bridge/weekly/task/不存在/body",
            data={"week": WEEK_KEY, "expected_token": "", "body": "x"},
            follow_redirects=False,
        )
        # renamed/removed → bounce to the dashboard with err=task (PR#812 panel F4),
        # not the generic err=write
        assert "err=task" in r.headers["location"]
        assert "/bridge/weekly?" in r.headers["location"]


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
        # the 本週計畫 panel is one consolidated form (top3 + ufo + advance)
        assert 'action="/bridge/weekly/plan-save"' in body
        assert 'action="/bridge/weekly/review"' in body
        assert 'action="/bridge/weekly/notes"' in body
        assert 'name="expected_token"' in body
        # top3 is a dropdown picker (not free text); the seed task is an option
        assert "wk-top3-sel" in body
        assert "<optgroup" in body
        assert "測試任務" in body

    def test_plan_save_writes_top3_ufo_and_status(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/plan-save",
            data={
                "week": WEEK_KEY,
                "expected_token": "",
                "top3": ["測試任務", "", ""],
                "ufo": "4",
                "advance": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&saved=plan"
        fm = _weekly_fm(tmp_path)
        assert fm["top3"] == ["[[測試任務]]"]
        assert fm["targets"] == {"ufo": 4}
        assert fm["status"] == "active"  # advance checkbox flipped the FSM

    def test_plan_save_without_advance_keeps_planning(self, client, tmp_path):
        client.post(
            "/bridge/weekly/plan-save",
            data={"week": WEEK_KEY, "expected_token": "", "top3": ["測試任務"], "ufo": "0"},
            follow_redirects=False,
        )
        fm = _weekly_fm(tmp_path)
        assert fm["status"] == "planning"  # created from template, not advanced
        assert fm["targets"] == {}  # ufo 0 → empty

    # ── PR#811 audit regressions ────────────────────────────────────────────
    def test_review_save_preserves_existing_notes(self, client, tmp_path):
        """B1: 隨手筆記 has its own form; a review save must NOT blank it."""
        client.post(
            "/bridge/weekly/notes",
            data={"week": WEEK_KEY, "expected_token": "", "notes": "別把我清掉"},
            follow_redirects=False,
        )
        tok = _weekly_token(tmp_path)
        client.post(
            "/bridge/weekly/review",
            data={"week": WEEK_KEY, "expected_token": tok, "highlight": "好的一刻"},
            follow_redirects=False,
        )
        body = _weekly_body(tmp_path)
        assert "別把我清掉" in body  # notes survived the review save
        assert "好的一刻" in body

    def test_plan_save_blank_ufo_does_not_persist_default(self, client, tmp_path):
        """B2: a blank UFO field must not write the machine default as intent."""
        client.post(
            "/bridge/weekly/plan-save",
            data={"week": WEEK_KEY, "expected_token": "", "top3": ["測試任務"], "ufo": ""},
            follow_redirects=False,
        )
        assert _weekly_fm(tmp_path)["targets"] == {}  # no targets.ufo:5 leaked

    def test_plan_save_ufo_merges_keeps_pomodoro(self, client, tmp_path):
        """B2: saving the 🤩 goal merges — it must not wipe a stored 🍅 goal."""
        client.post(
            "/bridge/weekly/targets",
            data={"week": WEEK_KEY, "expected_token": "", "pomodoro": "30", "ufo": "0"},
            follow_redirects=False,
        )
        tok = _weekly_token(tmp_path)
        client.post(
            "/bridge/weekly/plan-save",
            data={"week": WEEK_KEY, "expected_token": tok, "top3": ["測試任務"], "ufo": "4"},
            follow_redirects=False,
        )
        assert _weekly_fm(tmp_path)["targets"] == {"pomodoro": 30, "ufo": 4}

    def test_token_is_content_hash_stable_across_reads(self, client, tmp_path):
        """F3: the If-Match token is a content hash — same bytes → same token
        (immune to mtime resets), different bytes → different token."""
        client.post(
            "/bridge/weekly/top3",
            data={"week": WEEK_KEY, "expected_token": "", "top3": "A"},
            follow_redirects=False,
        )
        t1 = _weekly_token(tmp_path)
        assert t1 and t1 == _weekly_token(tmp_path)  # stable, content-derived
        client.post(
            "/bridge/weekly/notes",
            data={"week": WEEK_KEY, "expected_token": t1, "notes": "changed"},
            follow_redirects=False,
        )
        assert _weekly_token(tmp_path) != t1  # content changed → token changed


class TestCalendarSchedule:
    """ADR-041 41c — the backlog calendar scheduler route. Google Calendar is
    fully mocked (no live API); the vault write is real (D1 authoritative)."""

    def _ev(self, eid="evt_1"):
        from shared.google_calendar import CalendarEvent

        return CalendarEvent(id=eid, title="測試任務", start="x", end="y", html_link="http://h")

    def test_backlog_zone_renders_picker(self, client, monkeypatch):
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: wi.date(2026, 6, 1))
        body = client.get(f"/bridge/weekly?week={WEEK_KEY}").text
        # v3-B: the picker is the merged 「排入」 in the unified row (date + optional
        # time + 🍅 → /weekly/plan); the separate /weekly/schedule row form is gone
        assert 'data-pane="all"' in body
        assert 'action="/bridge/weekly/plan"' in body
        assert 'type="date"' in body and 'type="time"' in body
        # v3-F: the 「強制」 checkbox is gone — a clash now escalates to Nami in Slack
        assert 'name="force"' not in body
        assert "測試任務" in body

    def test_schedule_creates_event_and_links(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        seen = {}

        def fake_create(**kw):
            seen.update(kw)
            return self._ev("evt_42")

        monkeypatch.setattr(gc, "create_event", fake_create)
        r = client.post(
            "/bridge/weekly/schedule",
            data={
                "task_slug": "測試任務",
                # a stale/forged client title must be IGNORED — the event title is
                # derived from the vault (Codex audit §2). Server uses the real title.
                "title": "STALE-FORGED-TITLE",
                "entry_date": "2026-06-03",  # Wed
                "entry_time": "09:00",
                "pomodoros": "4",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&saved=scheduled"
        raw = _task_path(tmp_path).read_text(encoding="utf-8")
        fm = yaml.safe_load(raw.split("---", 2)[1])
        assert str(fm["scheduled"]).startswith("2026-06-03T09:00")
        assert str(fm["scheduled_end"]).startswith("2026-06-03T11:00")  # 4🍅 × 30 min
        assert fm["calendar_event_id"] == "evt_42"
        assert seen["idempotency_key"] == "測試任務@2026-06-03"
        assert seen["title"] == "測試任務"  # vault title, NOT the forged form value

    def test_schedule_conflict_writes_plan_no_event(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        monkeypatch.setattr(gc, "create_event", lambda **kw: [self._ev("a"), self._ev("b")])
        r = client.post(
            "/bridge/weekly/schedule",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-03",
                "entry_time": "09:00",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=cal_conflict&n=2"
        fm = yaml.safe_load(_task_path(tmp_path).read_text(encoding="utf-8").split("---", 2)[1])
        assert str(fm["scheduled"]).startswith("2026-06-03T09:00")  # plan still written (D2)
        assert "calendar_event_id" not in fm

    def test_schedule_calendar_outage_degrades(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        def boom(**kw):
            raise RuntimeError("API down")

        monkeypatch.setattr(gc, "create_event", boom)
        r = client.post(
            "/bridge/weekly/schedule",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-03",
                "entry_time": "09:00",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=cal_unavailable"
        fm = yaml.safe_load(_task_path(tmp_path).read_text(encoding="utf-8").split("---", 2)[1])
        assert str(fm["scheduled"]).startswith("2026-06-03T09:00")  # plan stands

    def test_schedule_force_skips_conflict_check(self, client, monkeypatch):
        import shared.google_calendar as gc

        seen = {}
        monkeypatch.setattr(gc, "create_event", lambda **kw: (seen.update(kw), self._ev())[1])
        client.post(
            "/bridge/weekly/schedule",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-03",
                "entry_time": "09:00",
                "pomodoros": "2",
                "force": "1",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert seen["check_conflict"] is False

    def test_schedule_weekend_requires_reason(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        called = {"n": 0}
        monkeypatch.setattr(
            gc, "create_event", lambda **kw: (called.__setitem__("n", 1), self._ev())[1]
        )
        r = client.post(
            "/bridge/weekly/schedule",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-06",  # Sat
                "entry_time": "09:00",
                "pomodoros": "1",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=weekend"
        assert called["n"] == 0  # never reached the calendar (vault write raised first)

    def test_schedule_bad_time_rejected(self, client):
        r = client.post(
            "/bridge/weekly/schedule",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-03",
                "entry_time": "25:99",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=time"

    def test_schedule_unknown_task_err(self, client):
        r = client.post(
            "/bridge/weekly/schedule",
            data={
                "task_slug": "不存在",
                "entry_date": "2026-06-03",
                "entry_time": "09:00",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=task"

    @pytest.mark.parametrize("bad_time", ["09:00:00", "09:00:00+08:00", "9:00", "0900", "24:00"])
    def test_schedule_rejects_non_naive_hhmm(self, client, bad_time):
        """Codex §1: only a strict naive HH:MM is accepted — seconds or a tz offset
        would make datetime.combine aware and break the D4 naive contract."""
        r = client.post(
            "/bridge/weekly/schedule",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-03",
                "entry_time": bad_time,
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=time"

    def test_linked_task_renders_editable_chip_not_locked_note(self, client, tmp_path, monkeypatch):
        """v3-B: a linked task is now editable inline — its plan chip is the link,
        with a confirm-gated 🗑 (/weekly/plan/cancel). The old 41d 'locked, go to the
        task page' note is gone; multi-block makes the row the home of scheduling."""
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: wi.date(2026, 6, 1))
        linked = (
            "---\ntitle: 已連動任務\nstatus: to-do\n預估🍅: 2\n"
            "scheduled: 2026-06-03T09:00:00\ncalendar_event_id: evt_live\n"
            "tags:\n  - task\n---\n\nbody\n"
        )
        (tmp_path / "TaskNotes" / "Tasks" / "已連動任務.md").write_text(linked, encoding="utf-8")
        body = client.get(f"/bridge/weekly?week={WEEK_KEY}").text
        assert "已連動任務" in body
        assert "改期或取消請到" not in body  # the old locked note is retired
        assert 'action="/bridge/weekly/plan/cancel"' in body  # the confirm-gated 🗑
        assert "is-linked" in body  # the chip reads as linked (accent)

    def test_schedule_rejects_already_linked_task(self, client, tmp_path, monkeypatch):
        """Server-side orphan guard (41d panel — Codex §2): a task that already
        carries calendar_event_id must NOT re-enter the create path (would orphan
        the first event). create_event must never be called."""
        import shared.google_calendar as gc

        called = {"n": 0}
        monkeypatch.setattr(
            gc, "create_event", lambda **kw: (called.__setitem__("n", 1), self._ev())[1]
        )
        linked = (
            "---\ntitle: 已連動任務\nstatus: to-do\n預估🍅: 2\n"
            "scheduled: 2026-06-03T09:00:00\ncalendar_event_id: evt_live\n"
            "tags:\n  - task\n---\n\nbody\n"
        )
        (tmp_path / "TaskNotes" / "Tasks" / "已連動任務.md").write_text(linked, encoding="utf-8")
        r = client.post(
            "/bridge/weekly/schedule",
            data={
                "task_slug": "已連動任務",
                "entry_date": "2026-06-04",
                "entry_time": "09:00",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=already_linked"
        assert called["n"] == 0  # create never reached


def _make_linked(tmp_path, slug="已連動任務", eid="evt_live"):
    """Write a task already projected onto the calendar (scheduled Wed 6/3 09:00)."""
    linked = (
        f"---\ntitle: {slug}\nstatus: to-do\n預估🍅: 4\n"
        f"scheduled: 2026-06-03T09:00:00\nscheduled_end: 2026-06-03T10:00:00\n"
        f"calendar_event_id: {eid}\nplan:\n  - date: 2026-06-03\n    pomodoros: 2\n"
        "tags:\n  - task\n---\n\nbody\n"
    )
    (tmp_path / "TaskNotes" / "Tasks" / f"{slug}.md").write_text(linked, encoding="utf-8")


def _linked_fm(tmp_path, slug="已連動任務"):
    raw = (tmp_path / "TaskNotes" / "Tasks" / f"{slug}.md").read_text(encoding="utf-8")
    return yaml.safe_load(raw.split("---", 2)[1])


def _task_token(tmp_path, slug="已連動任務") -> str:
    from shared.weekly_writer import task_file_token

    return task_file_token(tmp_path, slug)


class TestCalendarReschedule:
    """ADR-041 41d D8 — the task-page reschedule route (Google fully mocked)."""

    def test_reschedule_patches_event_and_moves_plan(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        _make_linked(tmp_path)
        seen = {}
        monkeypatch.setattr(gc, "find_conflicts", lambda s, e: [])
        monkeypatch.setattr(
            gc,
            "update_event",
            lambda eid, **kw: (seen.update({"eid": eid, **kw}), self._ok(eid))[1],
        )
        r = client.post(
            "/bridge/weekly/task/已連動任務/reschedule",
            data={
                "entry_date": "2026-06-04",
                "entry_time": "14:00",
                "pomodoros": "3",
                "expected_token": _task_token(tmp_path),
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "saved=rescheduled" in r.headers["location"]
        assert seen["eid"] == "evt_live"  # same event PATCHed
        fm = _linked_fm(tmp_path)
        assert [e["date"] for e in fm["plan"]] == ["2026-06-04"]
        assert fm["calendar_event_id"] == "evt_live"

    def test_reschedule_conflict_banner(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        _make_linked(tmp_path)
        monkeypatch.setattr(gc, "find_conflicts", lambda s, e: [self._ok("other")])
        monkeypatch.setattr(gc, "update_event", lambda eid, **kw: self._ok(eid))
        r = client.post(
            "/bridge/weekly/task/已連動任務/reschedule",
            data={
                "entry_date": "2026-06-04",
                "entry_time": "09:00",
                "pomodoros": "2",
                "expected_token": _task_token(tmp_path),
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert "err=cal_conflict" in r.headers["location"]

    def test_reschedule_bad_time_rejected(self, client, tmp_path):
        _make_linked(tmp_path)
        r = client.post(
            "/bridge/weekly/task/已連動任務/reschedule",
            data={
                "entry_date": "2026-06-04",
                "entry_time": "9am",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert "err=sched_time" in r.headers["location"]

    def test_reschedule_weekend_requires_reason(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        _make_linked(tmp_path)
        called = {"n": 0}
        monkeypatch.setattr(gc, "find_conflicts", lambda s, e: (called.__setitem__("n", 1), [])[1])
        monkeypatch.setattr(gc, "update_event", lambda eid, **kw: self._ok(eid))
        r = client.post(
            "/bridge/weekly/task/已連動任務/reschedule",
            data={
                "entry_date": "2026-06-06",
                "entry_time": "09:00",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert "err=weekend" in r.headers["location"]
        assert called["n"] == 0  # vault write raised before any calendar call

    def test_reschedule_stale_token_conflict(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        _make_linked(tmp_path)
        monkeypatch.setattr(gc, "find_conflicts", lambda s, e: [])
        monkeypatch.setattr(gc, "update_event", lambda eid, **kw: self._ok(eid))
        r = client.post(
            "/bridge/weekly/task/已連動任務/reschedule",
            data={
                "entry_date": "2026-06-04",
                "entry_time": "09:00",
                "pomodoros": "2",
                "expected_token": "STALE",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert "err=conflict" in r.headers["location"]

    def test_reschedule_missing_token_rejected(self, client, tmp_path, monkeypatch):
        """An omitted/empty token must NOT silently disable the lock (41d panel —
        Codex §1/§2): it conflicts against the existing file."""
        import shared.google_calendar as gc

        _make_linked(tmp_path)
        moved = {"n": 0}
        monkeypatch.setattr(gc, "find_conflicts", lambda s, e: [])
        monkeypatch.setattr(
            gc, "update_event", lambda eid, **kw: (moved.__setitem__("n", 1), self._ok(eid))[1]
        )
        r = client.post(
            "/bridge/weekly/task/已連動任務/reschedule",
            data={
                "entry_date": "2026-06-04",
                "entry_time": "09:00",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert "err=conflict" in r.headers["location"]
        assert moved["n"] == 0  # never reached the calendar

    def _ok(self, eid="evt_live"):
        from shared.google_calendar import CalendarEvent

        return CalendarEvent(id=eid, title="已連動任務", start="x", end="y", html_link="http://h")


class TestCalendarCancel:
    """ADR-041 41d D9 — 移出行事曆 / 取消排程 task-page routes."""

    def test_unlink_keeps_plan_and_deletes_event(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        _make_linked(tmp_path)
        deleted = []
        monkeypatch.setattr(gc, "delete_event", lambda eid: deleted.append(eid))
        r = client.post(
            "/bridge/weekly/task/已連動任務/unlink",
            data={"expected_token": _task_token(tmp_path), "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert "saved=unlinked" in r.headers["location"]
        assert deleted == ["evt_live"]
        fm = _linked_fm(tmp_path)
        assert "calendar_event_id" not in fm
        assert len(fm["plan"]) == 1  # plan KEPT (D9 — only the calendar link removed)
        assert str(fm["plan"][0]["date"]) == "2026-06-03" and fm["plan"][0]["pomodoros"] == 2

    def test_unschedule_drops_plan_and_deletes_event(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        _make_linked(tmp_path)
        deleted = []
        monkeypatch.setattr(gc, "delete_event", lambda eid: deleted.append(eid))
        r = client.post(
            "/bridge/weekly/task/已連動任務/unschedule",
            data={"expected_token": _task_token(tmp_path), "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert "saved=unscheduled" in r.headers["location"]
        assert deleted == ["evt_live"]
        fm = _linked_fm(tmp_path)
        assert not fm.get("plan")  # 6/3 entry dropped
        assert "calendar_event_id" not in fm

    def test_cancel_delete_failure_degrades(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        _make_linked(tmp_path)

        def boom(eid):
            raise RuntimeError("API down")

        monkeypatch.setattr(gc, "delete_event", boom)
        r = client.post(
            "/bridge/weekly/task/已連動任務/unlink",
            data={"expected_token": _task_token(tmp_path), "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert "err=cancel_cal_failed" in r.headers["location"]
        assert "calendar_event_id" not in _linked_fm(tmp_path)  # vault still cleared

    def test_task_page_shows_per_entry_scheduling_for_linked(self, client, tmp_path, monkeypatch):
        """v3-C: the task page schedules per-entry, identical to the dashboard row.
        The 6/3 linked plan entry renders as a chip with a confirm-gated 🗑
        (/weekly/plan/cancel); the merged 「排入」 form posts /weekly/plan with
        from_task so the action lands back on the task page. The old single-event
        reschedule/unlink/unschedule panel is retired."""
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: wi.date(2026, 6, 1))
        _make_linked(tmp_path)
        body = client.get(f"/bridge/weekly/task/已連動任務?week={WEEK_KEY}").text
        assert "行事曆排程" in body
        assert "wk-plan-merged" in body  # the merged 排入 form (date + optional time + 🍅)
        assert 'action="/bridge/weekly/plan"' in body
        assert 'name="from_task"' in body  # lands back on the task page, not the dashboard
        assert 'action="/bridge/weekly/plan/cancel"' in body  # the linked chip's 🗑
        assert "is-linked" in body  # the 6/3 entry reads linked (dual-read fold)
        # the old single-event panel is gone — scheduling is per-entry everywhere now
        assert "/reschedule" not in body
        assert "/unlink" not in body and "/unschedule" not in body


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


class TestUnifiedTaskViewsAndDone:
    """#7 unified row + 3 views (今日/整週/全部) + #2 done checkbox + #4 bullet 🍅."""

    def _pin(self, monkeypatch):
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: wi.date(2026, 6, 1))

    def test_all_view_replaces_backlog_zone(self, client, monkeypatch):
        self._pin(monkeypatch)
        body = client.get(f"/bridge/weekly?week={WEEK_KEY}").text
        # the 3rd task view is now 全部 (folds in the old 待排程 backlog)
        assert 'data-tab="all"' in body
        assert 'data-pane="all"' in body
        assert 'data-tab="project"' not in body  # 按專案 retired
        # the separate backlog <details> zone is gone
        assert "wk-backlog" not in body

    def test_done_checkbox_is_a_form_button(self, client, monkeypatch):
        self._pin(monkeypatch)
        body = client.get(f"/bridge/weekly?week={WEEK_KEY}").text
        assert "wk-box-form" in body
        assert "/done" in body  # the toggle route action

    def test_unified_row_has_merged_plan_form(self, client, monkeypatch):
        self._pin(monkeypatch)
        body = client.get(f"/bridge/weekly?week={WEEK_KEY}").text
        # v3-B: ONE merged 「排入」 form (date + optional time + 🍅 → /weekly/plan)
        assert "wk-plan-merged" in body
        assert 'action="/bridge/weekly/plan"' in body
        assert 'name="entry_time"' in body

    def test_done_route_marks_and_reopens(self, client, tmp_path):
        # mark done
        r = client.post(
            "/bridge/weekly/task/測試任務/done",
            data={"done": "1", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.status_code == 303
        # v3-I follow-up: done redirect now anchors on the row so the dashboard stays in place.
        loc = r.headers["location"]
        assert loc.startswith(f"/bridge/weekly?week={WEEK_KEY}")
        assert loc.endswith("#task-%E6%B8%AC%E8%A9%A6%E4%BB%BB%E5%8B%99")
        fm = yaml.safe_load(_task_path(tmp_path).read_text(encoding="utf-8").split("---", 2)[1])
        assert fm["status"] == "done"
        # re-open
        client.post(
            "/bridge/weekly/task/測試任務/done",
            data={"done": "0", "week": WEEK_KEY},
            follow_redirects=False,
        )
        fm = yaml.safe_load(_task_path(tmp_path).read_text(encoding="utf-8").split("---", 2)[1])
        assert fm["status"] == "to-do"

    def test_done_route_unknown_task(self, client):
        r = client.post(
            "/bridge/weekly/task/不存在/done",
            data={"done": "1", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.headers["location"] == f"/bridge/weekly?week={WEEK_KEY}&err=task"

    def test_daily_bullet_shows_actual_planned_pom_for_work(self, client, tmp_path, monkeypatch):
        # a work task scheduled Wed 06/03 with a logged session that day → 實際/預計
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: wi.date(2026, 6, 1))
        work = (
            "---\n"
            "title: 工作任務\n"
            "status: to-do\n"
            "category: work\n"
            "預估🍅: 4\n"
            "plan:\n"
            "  - date: 2026-06-03\n"
            "    pomodoros: 3\n"
            "timeEntries:\n"
            "  - startTime: '2026-06-03T09:00:00'\n"
            "    endTime: '2026-06-03T09:25:00'\n"
            "    mode: pomodoro\n"
            "---\n"
        )
        (tmp_path / "TaskNotes" / "Tasks" / "工作任務.md").write_text(work, encoding="utf-8")
        body = client.get(f"/bridge/weekly?week={WEEK_KEY}").text
        assert "wk-ci-pom" in body
        assert "1/3🍅" in body  # actual 1 / planned 3


class TestTaskPageCalendarConsistency:
    """修修: the task-page calendar UI reads IDENTICALLY to the dashboard row — v3-C
    unifies both on the per-entry merged 「排入」 (posts /weekly/plan)."""

    def test_task_uses_merged_plan_form_not_legacy_schedule(self, client, monkeypatch):
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: wi.date(2026, 6, 1))
        # SAMPLE_TASK has no calendar_event_id → not linked
        body = client.get(f"/bridge/weekly/task/測試任務?week={WEEK_KEY}").text
        assert 'action="/bridge/weekly/plan"' in body  # same merged route as the dashboard
        assert "wk-plan-merged" in body
        assert 'name="from_task"' in body
        # legacy single-event surfaces are gone from the task page
        assert 'action="/bridge/weekly/schedule"' not in body
        assert "/reschedule" not in body

    def test_accuracy_stat_tomato_only_for_work(self, client, tmp_path):
        # v3-I.5: the 🍅 準確率/實際·預估 stats render only for 工作 tasks (修修 — 🍅 is a
        # work-only metric). SAMPLE_TASK has no category ⇒ misc ⇒ the stats are hidden.
        misc_body = client.get(f"/bridge/weekly/task/測試任務?week={WEEK_KEY}").text
        assert "準確率" not in misc_body

        work = (
            "---\ntitle: 工作任務\nstatus: to-do\ncategory: work\n預估🍅: 4\ntimeEntries: []\n---\n"
        )
        (tmp_path / "TaskNotes" / "Tasks" / "工作任務.md").write_text(work, encoding="utf-8")
        work_body = client.get(f"/bridge/weekly/task/工作任務?week={WEEK_KEY}").text
        assert "🍅</span> 準確率" in work_body


class TestScheduleBadge:
    """修修: no icon at all on the schedule badge — the colour alone marks linked
    (accent .wk-bl-linked) vs vault-only (muted .wk-bl-when). Bare date."""

    def test_linked_row_uses_colour_class_no_icon(self, client, tmp_path, monkeypatch):
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: wi.date(2026, 6, 1))
        linked = (
            "---\ntitle: 已連動任務\nstatus: to-do\n預估🍅: 2\n"
            "scheduled: 2026-06-03T09:00:00\ncalendar_event_id: evt_live\n"
            "tags:\n  - task\n---\n\nbody\n"
        )
        (tmp_path / "TaskNotes" / "Tasks" / "已連動任務.md").write_text(linked, encoding="utf-8")
        body = client.get(f"/bridge/weekly?week={WEEK_KEY}").text
        # linked → accent colour class + bare date, no 🔗 / 📅 glyphs on the badge
        assert 'wk-bl-linked" title="已連動 Google 行事曆事件">06/03' in body
        assert "🔗" not in body
        assert "📅🔗" not in body


class TestMergedScheduleRoute:
    """v3-B: /weekly/plan is the merged 「排入」 — timed → vault + Google in one;
    blank time → plan-only; /weekly/plan/cancel drops a linked entry + its event."""

    def _ev(self, eid="evt_m"):
        from shared.google_calendar import CalendarEvent

        return CalendarEvent(id=eid, title="測試任務", start="x", end="y", html_link="http://h")

    def test_timed_writes_plan_and_links_event(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        seen = {}
        monkeypatch.setattr(gc, "create_event", lambda **kw: (seen.update(kw), self._ev())[1])
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-04",
                "entry_time": "10:00",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "saved=scheduled" in r.headers["location"] and "#task-" in r.headers["location"]
        entry = next(e for e in _plan(tmp_path) if str(e["date"]) == "2026-06-04")
        assert entry["start"] == "2026-06-04T10:00:00+08:00"
        assert entry["end"] == "2026-06-04T11:00:00+08:00"  # 2×30 = 60 min
        assert entry["calendar_event_id"] == "evt_m"
        assert seen["idempotency_key"] == "測試任務@2026-06-04"

    def test_blank_time_creates_all_day_event(self, client, tmp_path, monkeypatch):
        """v3-E: blank time is no longer plan-only — it projects an ALL-DAY Google
        event (date-only start/end) and links it, in one action."""
        import shared.google_calendar as gc

        seen = {}
        monkeypatch.setattr(gc, "create_event", lambda **kw: (seen.update(kw), self._ev())[1])
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-04",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303 and "saved=scheduled" in r.headers["location"]
        # create_event was called with a date-only (all-day) start/end
        assert seen["start"] == "2026-06-04" and seen["end"] == "2026-06-05"
        entry = next(e for e in _plan(tmp_path) if str(e["date"]) == "2026-06-04")
        assert entry["start"] == "2026-06-04"  # date-only ⇒ all-day
        assert entry["end"] == "2026-06-05"
        assert entry["calendar_event_id"] == "evt_m"

    def test_cancel_drops_entry_and_deletes_event(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        # link the 06-03 entry first
        monkeypatch.setattr(gc, "create_event", lambda **kw: self._ev("evt_c"))
        client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-03",
                "entry_time": "09:00",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        deleted = []
        monkeypatch.setattr(gc, "delete_event", lambda eid: deleted.append(eid))
        r = client.post(
            "/bridge/weekly/plan/cancel",
            data={"task_slug": "測試任務", "entry_date": "2026-06-03", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.status_code == 303 and "saved=unscheduled" in r.headers["location"]
        assert deleted == ["evt_c"]
        assert not [e for e in _plan(tmp_path) if str(e["date"]) == "2026-06-03"]

    def test_timed_conflict_opens_web_modal_not_slack(self, client, tmp_path, monkeypatch):
        """v3-I.2/4: a timed clash no longer DMs Slack — it redirects with the
        ?err=cal_conflict&cf_* params that drive the Web UI modal (強排/改時間/取消排程).
        The clash is pre-checked BEFORE the vault write, so nothing is written; no Slack."""
        import shared.google_calendar as gc
        from agents.franky import slack_bot

        # v3-I.4: the pre-check uses find_conflicts (before any vault write)
        monkeypatch.setattr(gc, "find_conflicts", lambda s, e: [self._ev("c1")])  # → CONFLICT
        monkeypatch.setattr(
            gc,
            "find_free_slots",
            lambda d, dur, **kw: [("2026-06-04T15:00:00+08:00", "2026-06-04T16:00:00+08:00")],
        )

        def _boom(cls):  # the web path must NOT post to Slack any more
            raise AssertionError("web conflict must not escalate to Slack")

        monkeypatch.setattr(slack_bot.FrankySlackBot, "from_env", classmethod(_boom))
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-04",
                "entry_time": "15:00",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        loc = r.headers["location"]
        assert r.status_code == 303
        assert "err=cal_conflict" in loc and "cal_conflict_nami" not in loc
        # modal params: which row + the attempted slot + the clash + nearby slots
        assert "cf_slug=" in loc and "cf_date=2026-06-04" in loc
        assert "cf_time=15%3A00" in loc and "cf_pom=2" in loc
        assert "cf_with=" in loc and "cf_slots=15%3A00" in loc
        # v3-I.4: the clash is pre-checked BEFORE the vault write, so NOTHING is written
        # for 06-04 (修修: 取消排程 must leave no orphan plan chip).
        assert not [e for e in _plan(tmp_path) if str(e["date"]) == "2026-06-04"]


class TestPlanRouteFromTask:
    """v3-C: the SAME /weekly/plan{,/remove,/cancel} routes serve the task page; when
    posted with from_task=1 they redirect BACK to the task page (stay-in-place), not
    the dashboard — so the two surfaces never diverge (修修)."""

    def _ev(self, eid="evt_ft"):
        from shared.google_calendar import CalendarEvent

        return CalendarEvent(id=eid, title="測試任務", start="x", end="y", html_link="http://h")

    def test_plan_only_redirects_to_task_page(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-04",
                "pomodoros": "2",
                "week": WEEK_KEY,
                "from_task": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("/bridge/weekly/task/")  # back on the task page, not dashboard
        assert "/bridge/weekly?week" not in loc

    def test_timed_redirects_to_task_page(self, client, tmp_path, monkeypatch):
        import shared.google_calendar as gc

        monkeypatch.setattr(gc, "create_event", lambda **kw: self._ev())
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-04",
                "entry_time": "10:00",
                "pomodoros": "2",
                "week": WEEK_KEY,
                "from_task": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("/bridge/weekly/task/") and "saved=scheduled" in loc

    def test_remove_redirects_to_task_page(self, client, tmp_path):
        # seed a plan-only entry, then remove it from the task page
        client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-05",
                "pomodoros": "1",
                "week": WEEK_KEY,
                "from_task": "1",
            },
            follow_redirects=False,
        )
        r = client.post(
            "/bridge/weekly/plan/remove",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-05",
                "week": WEEK_KEY,
                "from_task": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303 and r.headers["location"].startswith("/bridge/weekly/task/")

    def test_no_from_task_still_redirects_to_dashboard(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/plan",
            data={
                "task_slug": "測試任務",
                "entry_date": "2026-06-04",
                "pomodoros": "2",
                "week": WEEK_KEY,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("/bridge/weekly?week") and "#task-" in loc


class TestRenameRoute:
    """v3-I.3: POST /weekly/task/{slug}/rename — true rename (title + filename + linked
    events). Slug changes ⇒ success lands on the NEW row (focused); collisions banner."""

    def _files(self, tmp_path):
        return {p.name for p in (tmp_path / "TaskNotes" / "Tasks").iterdir()}

    def test_rename_success_redirects_to_new_row(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/task/測試任務/rename",
            data={"new_title": "正式名稱", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        # v3-I.4: stay-in-place — anchor to the renamed row, NO focus jump (focus= is for 新增任務).
        assert "saved=renamed" in loc and "focus=" not in loc
        assert loc.endswith("#task-%E6%AD%A3%E5%BC%8F%E5%90%8D%E7%A8%B1")  # quote("正式名稱")
        files = self._files(tmp_path)
        assert "正式名稱.md" in files and "測試任務.md" not in files

    def test_rename_blank_title_rejected(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/task/測試任務/rename",
            data={"new_title": "   ", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.status_code == 303 and "err=rename_invalid" in r.headers["location"]
        assert "測試任務.md" in self._files(tmp_path)  # unchanged

    def test_rename_duplicate_rejected(self, client, tmp_path):
        (tmp_path / "TaskNotes" / "Tasks" / "佔位.md").write_text(SAMPLE_TASK, encoding="utf-8")
        r = client.post(
            "/bridge/weekly/task/測試任務/rename",
            data={"new_title": "佔位", "week": WEEK_KEY},
            follow_redirects=False,
        )
        assert r.status_code == 303 and "err=rename_exists" in r.headers["location"]
        assert "測試任務.md" in self._files(tmp_path)  # unchanged

    def test_rename_from_task_redirects_to_task_page(self, client, tmp_path):
        r = client.post(
            "/bridge/weekly/task/測試任務/rename",
            data={"new_title": "任務頁改名", "week": WEEK_KEY, "from_task": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("/bridge/weekly/task/") and "saved=renamed" in loc
        assert "任務頁改名.md" in self._files(tmp_path)


class TestRowChips:
    """v3-I follow-up (修修): task rows carry category (outlined) + priority (solid) chips."""

    def test_dashboard_renders_category_and_priority_chips(self, client, monkeypatch):
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: wi.date(2026, 6, 1))
        body = client.get(f"/bridge/weekly?week={WEEK_KEY}").text
        # SAMPLE_TASK has no category/priority → misc + normal(Medium)
        assert "wk-cat-misc" in body
        assert "wk-pri-normal" in body and "Medium" in body
        assert "wk-chips" in body
