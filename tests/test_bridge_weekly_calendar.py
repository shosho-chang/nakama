"""行事曆 tab routes (修修 2026-11-12): the GET render and drag-to-another-day on
/bridge/weekly/plan (``from_date``). Harness mirrors tests/test_bridge_weekly.py."""

from __future__ import annotations

import importlib
from datetime import date

import pytest
import yaml
from fastapi.testclient import TestClient

WEEK_KEY = "2026-05-31"  # Sun 5/31 .. Sat 6/6
TASK = """---
title: 測試任務
status: to-do
預估🍅: 2
plan:
  - date: 2026-06-03
    pomodoros: 2
    start: '2026-06-03T09:00:00+08:00'
    end: '2026-06-03T10:00:00+08:00'
{extra}timeEntries: []
tags:
  - task
---

本文。
"""


def _path(tmp_path):
    return tmp_path / "TaskNotes" / "Tasks" / "測試任務.md"


def _plan(tmp_path):
    fm = yaml.safe_load(_path(tmp_path).read_text(encoding="utf-8").split("---", 2)[1])
    return {str(e["date"]): dict(e) for e in fm.get("plan") or []}


def _client(monkeypatch, tmp_path, extra=""):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))
    monkeypatch.setenv("NAKAMA_RESEARCH_CACHE_DIR", str(tmp_path / "research_cache"))
    (tmp_path / "TaskNotes" / "Tasks").mkdir(parents=True)
    _path(tmp_path).write_text(TASK.format(extra=extra), encoding="utf-8")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge_weekly as bw_module

    importlib.reload(auth_module)
    importlib.reload(bw_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


def _move(client, **kw):
    data = {
        "task_slug": "測試任務",
        "entry_date": "2026-06-04",
        "entry_time": "14:00",
        "pomodoros": "3",
        "from_date": "2026-06-03",
        "week": WEEK_KEY,
    }
    data.update(kw)
    return client.post("/bridge/weekly/plan", data=data, follow_redirects=False)


class TestRender:
    def test_calendar_tab_and_block(self, monkeypatch, tmp_path):
        import shared.weekly_indexer as wi

        monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
        c = _client(monkeypatch, tmp_path)
        html = c.get(f"/bridge/weekly?week={WEEK_KEY}").text
        assert 'data-tab="cal"' in html and 'data-pane="cal"' in html
        assert 'class="wk-cal"' in html
        assert 'data-start="540"' in html and 'data-end="600"' in html  # 09:00–10:00
        assert 'class="wk-cal-form"' in html and 'name="from_date"' in html
        assert "bridge-calendar.js" in html and "bridge-calendar.css" in html


class TestMoveToAnotherDay:
    def test_moves_entry_and_drops_old_day(self, monkeypatch, tmp_path):
        c = _client(monkeypatch, tmp_path)
        r = _move(c)
        assert r.status_code == 303
        plan = _plan(tmp_path)
        assert set(plan) == {"2026-06-04"}
        assert str(plan["2026-06-04"]["start"]).replace(" ", "T").startswith("2026-06-04T14:00")
        assert plan["2026-06-04"]["pomodoros"] == 3
        # calendar is disabled in tests → plan written, Google unavailable
        assert "err=cal_unavailable" in r.headers["location"]

    def test_same_day_is_an_in_place_upsert(self, monkeypatch, tmp_path):
        c = _client(monkeypatch, tmp_path)
        _move(c, entry_date="2026-06-03", entry_time="11:30", pomodoros="1")
        plan = _plan(tmp_path)
        assert set(plan) == {"2026-06-03"}
        assert str(plan["2026-06-03"]["start"]).replace(" ", "T").startswith("2026-06-03T11:30")
        assert plan["2026-06-03"]["pomodoros"] == 1

    def test_occupied_target_day_is_refused(self, monkeypatch, tmp_path):
        c = _client(
            monkeypatch,
            tmp_path,
            extra="  - date: 2026-06-04\n    pomodoros: 1\n",
        )
        r = _move(c)
        assert "err=move_occupied" in r.headers["location"]
        assert set(_plan(tmp_path)) == {"2026-06-03", "2026-06-04"}
        assert _plan(tmp_path)["2026-06-04"]["pomodoros"] == 1

    def test_done_entry_cannot_move(self, monkeypatch, tmp_path):
        c = _client(monkeypatch, tmp_path)
        p = _path(tmp_path)
        p.write_text(
            p.read_text(encoding="utf-8").replace(
                "    end: '2026-06-03T10:00:00+08:00'\n",
                "    end: '2026-06-03T10:00:00+08:00'\n    done: true\n",
            ),
            encoding="utf-8",
        )
        r = _move(c)
        assert "err=move_done" in r.headers["location"]
        assert set(_plan(tmp_path)) == {"2026-06-03"}

    def test_conflict_writes_nothing_and_carries_cf_from(self, monkeypatch, tmp_path):
        from shared import google_calendar
        from shared.google_calendar import CalendarEvent

        c = _client(monkeypatch, tmp_path)
        clash = CalendarEvent(
            id="other",
            title="看牙醫",
            start="2026-06-04T14:00:00+08:00",
            end="2026-06-04T15:00:00+08:00",
            html_link="",
        )
        monkeypatch.setattr(google_calendar, "find_conflicts", lambda s, e: [clash])
        monkeypatch.setattr(google_calendar, "find_free_slots", lambda *a, **k: [])
        r = _move(c)
        loc = r.headers["location"]
        assert "err=cal_conflict" in loc and "cf_from=2026-06-03" in loc
        assert set(_plan(tmp_path)) == {"2026-06-03"}  # old day intact, new day not written

    def test_weekend_target_needs_reason(self, monkeypatch, tmp_path):
        c = _client(monkeypatch, tmp_path)
        r = _move(c, entry_date="2026-06-06")
        assert "err=weekend" in r.headers["location"]
        assert set(_plan(tmp_path)) == {"2026-06-03"}
        r = _move(c, entry_date="2026-06-06", reason="趕稿")
        assert set(_plan(tmp_path)) == {"2026-06-06"}


@pytest.mark.weekly_autotask
def test_opening_a_future_week_creates_its_newsletter(monkeypatch, tmp_path):
    import shared.weekly_indexer as wi

    c = _client(monkeypatch, tmp_path)
    import thousand_sunny.routers.bridge_weekly as bw

    monkeypatch.setattr(bw, "today_taipei", lambda: date(2026, 6, 1))
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 1))
    wk = wi.week_for_date(date(2026, 6, 8))
    html = c.get(f"/bridge/weekly?week={wk.file_key}").text
    name = f"{wk.label} 電子報"
    assert (tmp_path / "TaskNotes" / "Tasks" / f"{name}.md").exists()
    assert name in html
