"""Tests for shared.weekly_indexer — week math, task parsing, view assembly,
plus a /bridge/weekly route smoke test.
"""

from __future__ import annotations

from datetime import date

import pytest
import yaml

from shared import weekly_indexer as wi
from shared.weekly_indexer import WeeklyIndexer, week_for_date, week_from_key

# ── week math (ADR-039 D2) ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "d,week,year,start",
    [
        ("2026-05-31", 23, 2026, "2026-05-31"),  # Sun → W23 first day (修修's anchor)
        ("2026-06-01", 23, 2026, "2026-05-31"),  # Mon → same week
        ("2026-05-30", 22, 2026, "2026-05-24"),  # Sat → prior week
        ("2026-12-27", 1, 2027, "2026-12-27"),  # Sun whose week holds Jan 1 2027 → W1/2027
        ("2027-01-01", 1, 2027, "2026-12-27"),
        ("2026-01-01", 1, 2026, "2025-12-28"),
    ],
)
def test_week_for_date(d, week, year, start):
    wr = week_for_date(date.fromisoformat(d))
    assert (wr.week, wr.year, wr.start.isoformat()) == (week, year, start)
    assert (wr.end - wr.start).days == 6  # Sun..Sat
    assert wr.label == f"W{week}"
    assert wr.file_key == start


def test_week_from_key_accepts_only_sunday_starts():
    assert week_from_key("2026-05-31").week == 23  # a real week-start Sunday
    assert week_from_key("2026-06-01") is None  # mid-week (Mon) → rejected
    assert week_from_key("garbage") is None


def test_week_shift():
    w23 = week_for_date(date(2026, 5, 31))
    assert w23.shift(-1).file_key == "2026-05-24"
    assert w23.shift(1).file_key == "2026-06-07"


# ── fixture vault ────────────────────────────────────────────────────────────


def _task(vault, fname: str, fm: dict) -> None:
    d = vault / "TaskNotes" / "Tasks"
    d.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
    (d / fname).write_text(f"---\n{body}---\n\n", encoding="utf-8")


def _daily(vault, day: str, fm: dict) -> None:
    d = vault / "Journals" / "Daily"
    d.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
    (d / f"{day}.md").write_text(f"---\n{body}---\n\n", encoding="utf-8")


def _weekly(vault, file_key: str, fm: dict, body: str = "") -> None:
    d = vault / "Journals" / "Weekly"
    d.mkdir(parents=True, exist_ok=True)
    fms = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
    (d / f"{file_key}.md").write_text(f"---\n{fms}---\n{body}", encoding="utf-8")


@pytest.fixture
def vault(tmp_path):
    _task(
        tmp_path,
        "肌酸的妙用 - Pre-production.md",
        {
            "title": "肌酸的妙用 - Pre-production",
            "projects": ["[[肌酸的妙用]]"],
            "category": "work",
            "預估🍅": 2,
            "scheduled": "2026-06-01T10:00:00",
            "status": "to-do",
            "weekly_priority": "2026-05-31",  # top-3 for W23
            "tags": ["task"],
        },
    )
    _task(
        tmp_path,
        "蛋白質攝取量 - Synthesis.md",
        {
            "title": "蛋白質攝取量 - Synthesis",
            "projects": ["[[蛋白質攝取量]]"],
            "category": "work",
            "預估🍅": 5,
            "scheduled": "2026-04-01T10:00:00",
            "status": "to-do",
            "tags": ["task"],
        },
    )
    _task(
        tmp_path,
        "倒垃圾.md",
        {
            "title": "倒垃圾",
            "預估🍅": 1,
            "scheduled": "2026-06-02T10:00:00",
            "status": "done",
            "✅": True,
            "tags": ["task"],
        },
    )
    _daily(
        tmp_path,
        "2026-06-01",
        {
            "date": "2026-06-01",
            "exercise_duration": 30,
            "meditation_minutes": 10,
            "pomodoros": [
                {
                    "startTime": "2026-06-01T09:00:00+08:00",
                    "endTime": "2026-06-01T09:25:00+08:00",
                    "type": "work",
                    "completed": True,
                    "taskPath": "TaskNotes/Tasks/肌酸的妙用 - Pre-production.md",
                }
            ],
        },
    )
    return tmp_path


def test_view_assembles_real_counts(vault, monkeypatch):
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))  # inside W23 → current
    v = WeeklyIndexer(vault).view(week_for_date(date(2026, 6, 1)))

    assert v.week.label == "W23"
    assert v.is_current is True
    assert v.mode == "active"
    # planned = work-only: 肌酸(2, scheduled in week). 倒垃圾 is misc (no 🍅);
    # 蛋白質(4/01) out of week.
    assert v.planned == 2
    assert v.actual.total_pomodoros == 1  # one 25-min daily work session
    assert v.rate_pct == 50  # round(100 * 1/2)
    assert {t.slug for t in v.tasks} == {"肌酸的妙用 - Pre-production", "倒垃圾"}
    # incomplete (not done, scheduled <= week end): 肌酸 + 蛋白質 (倒垃圾 is done)
    assert {t.slug for t in v.incomplete} == {
        "肌酸的妙用 - Pre-production",
        "蛋白質攝取量 - Synthesis",
    }
    # category from frontmatter: 肌酸 → work; 倒垃圾 (no category) → misc default
    work = next(t for t in v.tasks if t.slug.startswith("肌酸"))
    misc = next(t for t in v.tasks if t.slug == "倒垃圾")
    assert (work.category, misc.category) == ("work", "misc")
    assert (work.is_work, misc.is_work) == (True, False)
    # ADR-039 E hero extras: no deep sessions in fixture → 0 UFO, target 5;
    # 肌酸 flagged weekly_priority for W23 → the sole top-3 entry.
    assert v.ufo_count == 0
    assert v.ufo_target == 5
    assert {t.slug for t in v.top3} == {"肌酸的妙用 - Pre-production"}


def test_days_place_tasks_by_category(vault, monkeypatch):
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    v = WeeklyIndexer(vault).view(week_for_date(date(2026, 6, 1)))
    # daily cards = Mon..Fri (5), not the 7-day grid
    assert len(v.days) == 5
    mon, tue = v.days[0], v.days[1]
    assert (mon["date"], tue["date"]) == ("2026-06-01", "2026-06-02")
    # 肌酸 (work, 2🍅) lands on Mon under the work category
    assert mon["work_pomodoros"] == 2
    work_cat = next(c for c in mon["categories"] if c["slug"] == "work")
    assert any(it["slug"].startswith("肌酸") and it["pomodoros"] == 2 for it in work_cat["items"])
    # 倒垃圾 (misc) lands on Tue under misc, carries no 🍅
    misc_cat = next(c for c in tue["categories"] if c["slug"] == "misc")
    assert any(it["slug"] == "倒垃圾" and it["pomodoros"] == 0 for it in misc_cat["items"])
    assert tue["work_pomodoros"] == 0
    # editor day-select still spans all 7 days incl weekends
    assert v.day_headers[0]["is_weekend"] is True  # Sun
    assert v.day_headers[6]["is_weekend"] is True  # Sat


def test_actual_and_ufo_from_time_entries(tmp_path, monkeypatch):
    """ADR-039 E: per-task actual 🍅 + accuracy come from web-logged timeEntries[];
    week UFO count = 75-min ``deep`` sessions ending in the week."""
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    _task(
        tmp_path,
        "深度工作.md",
        {
            "title": "深度工作",
            "category": "work",
            "預估🍅": 6,
            "scheduled": "2026-06-01T10:00:00",
            "status": "to-do",
            "timeEntries": [
                # 75-min UFO (06-01) + 25-min pomodoro (06-02) = 100min → 4🍅, 1 UFO
                {
                    "startTime": "2026-06-01T13:00:00+08:00",
                    "endTime": "2026-06-01T14:15:00+08:00",
                    "mode": "deep",
                },
                {
                    "startTime": "2026-06-02T09:00:00+08:00",
                    "endTime": "2026-06-02T09:25:00+08:00",
                    "mode": "pomodoro",
                },
            ],
        },
    )
    idx = WeeklyIndexer(tmp_path)
    t = idx.find_task("深度工作")
    assert t is not None
    assert t.actual_pomodoros == 4  # 100 min // 25
    assert t.ufo_total == 1
    assert t.accuracy_pct == 67  # round(100 * 4/6)

    v = idx.view(week_for_date(date(2026, 6, 1)))
    assert v.ufo_count == 1  # one deep session this week
    assert v.actual.total_pomodoros == 4  # aggregator union over timeEntries


def test_ufo_derived_requires_min_minutes_and_completed(tmp_path):
    """ADR-040 A2: UFO is derived, not a bare mode:deep tag. A short deep block
    (<70 min) or one explicitly not completed does NOT count as a UFO."""
    _task(
        tmp_path,
        "深度.md",
        {
            "title": "深度",
            "category": "work",
            "預估🍅": 6,
            "timeEntries": [
                # 50-min deep — too short for UFO, but still 2🍅 by duration
                {
                    "startTime": "2026-06-01T13:00:00+08:00",
                    "endTime": "2026-06-01T13:50:00+08:00",
                    "mode": "deep",
                    "completed": True,
                },
                # 75-min deep but explicitly not completed → not a UFO
                {
                    "startTime": "2026-06-02T13:00:00+08:00",
                    "endTime": "2026-06-02T14:15:00+08:00",
                    "mode": "deep",
                    "completed": False,
                },
                # 72-min deep, completed → the one true UFO
                {
                    "startTime": "2026-06-03T13:00:00+08:00",
                    "endTime": "2026-06-03T14:12:00+08:00",
                    "mode": "deep",
                    "completed": True,
                },
            ],
        },
    )
    t = WeeklyIndexer(tmp_path).find_task("深度")
    assert t.ufo_total == 1  # only the 72-min completed block
    # 50 + 0(not-counted? no, completed False still counts 🍅 by duration) ...
    # 🍅 is duration-based via aggregator, independent of the UFO gate.


def test_find_task_missing_returns_none(tmp_path):
    assert WeeklyIndexer(tmp_path).find_task("nope") is None


def test_top3_resolved_from_weekly_file(vault, monkeypatch):
    """ADR-040 A4: top3 reads from the weekly file as wikilinks → task | project
    | unresolved. Project done-ratio = done tasks / total."""
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    _weekly(
        vault,
        "2026-05-31",
        {
            "status": "active",
            "top3": [
                "[[肌酸的妙用 - Pre-production]]",  # → task (not done)
                "[[肌酸的妙用]]",  # → project (1 task, 0 done)
                "[[不存在的東西]]",  # → unresolved
            ],
        },
    )
    v = WeeklyIndexer(vault).view(week_for_date(date(2026, 6, 1)))
    kinds = [it.kind for it in v.top3]
    assert kinds == ["task", "project", "unresolved"]
    task_item, proj_item, unres = v.top3
    assert task_item.slug == "肌酸的妙用 - Pre-production" and task_item.done is False
    assert proj_item.ratio_total == 1 and proj_item.ratio_done == 0 and proj_item.done is False
    assert unres.is_unresolved


def test_top3_falls_back_to_weekly_priority_without_file(vault, monkeypatch):
    """A5: with no weekly file, top3 falls back to weekly_priority task flags."""
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    v = WeeklyIndexer(vault).view(week_for_date(date(2026, 6, 1)))
    assert [it.kind for it in v.top3] == ["task"]
    assert v.top3[0].slug == "肌酸的妙用 - Pre-production"


def test_full_width_colon_project_resolves(tmp_path, monkeypatch):
    """Audit §4: the file uses the full-width colon (the only form legal in a
    Windows filename, so the real synced form); BOTH the full-width and the
    ASCII-colon wikilink spellings resolve to it via _link_key normalisation."""
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    (tmp_path / "Projects").mkdir(parents=True)
    (tmp_path / "Projects" / "專案：子題.md").write_text("---\n---\n", encoding="utf-8")
    for link in ("[[專案：子題]]", "[[專案:子題]]"):
        _weekly(tmp_path, "2026-05-31", {"status": "active", "top3": [link]})
        v = WeeklyIndexer(tmp_path).view(week_for_date(date(2026, 6, 1)))
        assert v.top3[0].kind == "project", f"{link!r} should resolve as a project"


def test_path_style_wikilink_resolves(tmp_path, monkeypatch):
    """Audit F2: [[Projects/專案]] (path-style, common for disambiguation)
    resolves to the bare 專案 project rather than rendering unresolved."""
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    (tmp_path / "Projects").mkdir(parents=True)
    (tmp_path / "Projects" / "專案.md").write_text("---\n---\n", encoding="utf-8")
    _weekly(tmp_path, "2026-05-31", {"status": "active", "top3": ["[[Projects/專案]]"]})
    v = WeeklyIndexer(tmp_path).view(week_for_date(date(2026, 6, 1)))
    assert v.top3[0].kind == "project"
    assert v.top3[0].title == "專案"


def test_targets_read_from_weekly_file_drive_hero_and_rate(vault, monkeypatch):
    """A3: targets.{pomodoro,ufo} from the weekly file set the hero goals + rate."""
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    _weekly(
        vault,
        "2026-05-31",
        {"status": "active", "targets": {"pomodoro": 10, "ufo": 3}},
    )
    v = WeeklyIndexer(vault).view(week_for_date(date(2026, 6, 1)))
    assert v.pomodoro_target == 10
    assert v.pomodoro_goal == 10  # target overrides planned-sum
    assert v.ufo_target == 3
    assert v.rate_pct == 10  # actual 1 / goal 10


def test_targets_default_when_absent(vault, monkeypatch):
    """No targets → ufo_target falls back to the constant, 🍅 goal to planned-sum."""
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    v = WeeklyIndexer(vault).view(week_for_date(date(2026, 6, 1)))
    assert v.pomodoro_target == 0
    assert v.pomodoro_goal == v.planned  # 2 (work scheduled in week)
    assert v.ufo_target == wi.UFO_WEEKLY_TARGET


def test_past_week_without_review_is_review_mode(vault, monkeypatch):
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 10))  # W24 → W23 is past
    v = WeeklyIndexer(vault).view(week_for_date(date(2026, 6, 1)))
    assert v.is_current is False
    assert v.mode == "review"


# ── route smoke ──────────────────────────────────────────────────────────────


def test_bridge_weekly_route_renders(vault, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from thousand_sunny.routers import bridge_weekly

    monkeypatch.setattr(bridge_weekly, "get_vault_path", lambda: vault)
    monkeypatch.setattr(bridge_weekly, "check_auth", lambda *_a, **_k: True)
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))

    app = FastAPI()
    app.include_router(bridge_weekly.page_router)
    client = TestClient(app)

    r = client.get("/bridge/weekly?week=2026-05-31")
    assert r.status_code == 200
    assert "W23" in r.text
    assert "wk-days" in r.text  # daily cards container rendered
    assert "nav_active" not in r.text  # macro expanded, not literal


def test_priority_surfaced_with_label(tmp_path, monkeypatch):
    # v3-I follow-up (修修): WeeklyTask now reads `priority`; label maps low/normal/high.
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    for fn, pri in (("高.md", "high"), ("低.md", "low"), ("未.md", None)):
        fm = {
            "title": fn[:-3],
            "category": "work",
            "預估🍅": 1,
            "scheduled": "2026-06-01",
            "status": "to-do",
            "tags": ["task"],
        }
        if pri:
            fm["priority"] = pri
        _task(tmp_path, fn, fm)
    by = {t.name: t for t in WeeklyIndexer(tmp_path).read_tasks()}
    assert by["高"].priority == "high" and by["高"].priority_label == "High"
    assert by["低"].priority == "low" and by["低"].priority_label == "Low"
    assert by["未"].priority == "normal" and by["未"].priority_label == "Medium"  # default


def test_important_tasks_uncapped(tmp_path, monkeypatch):
    # 修修: 本週重要任務 no longer capped at 3 — 4 weekly_priority tasks all surface.
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    for i in range(4):
        _task(
            tmp_path,
            f"要事{i}.md",
            {
                "title": f"要事{i}",
                "category": "work",
                "預估🍅": 1,
                "scheduled": "2026-06-01",
                "weekly_priority": "2026-05-31",
                "status": "to-do",
                "tags": ["task"],
            },
        )
    v = WeeklyIndexer(tmp_path).view(week_for_date(date(2026, 6, 1)))
    assert len(v.top3) == 4
    assert len(v.important_slugs) == 4


def test_daily_merges_growth_into_other_and_carries_meta(tmp_path, monkeypatch):
    # 修修: daily card has 3 columns work/health/其他; 自我進修(growth) folds into 其他(misc);
    # items carry priority + project.
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    _task(
        tmp_path,
        "讀書.md",
        {
            "title": "讀書",
            "category": "growth",
            "預估🍅": 1,
            "scheduled": "2026-06-01",
            "status": "to-do",
            "tags": ["task"],
        },
    )
    _task(
        tmp_path,
        "專案A - 寫稿.md",
        {
            "title": "專案A - 寫稿",
            "projects": ["[[專案A]]"],
            "category": "work",
            "預估🍅": 2,
            "scheduled": "2026-06-01",
            "priority": "high",
            "status": "to-do",
            "tags": ["task"],
        },
    )
    v = WeeklyIndexer(tmp_path).view(week_for_date(date(2026, 6, 1)))
    mon = next(d for d in v.days if d["date"] == "2026-06-01")
    cats = {c["slug"]: c for c in mon["categories"]}
    assert set(cats) == {"work", "health", "misc"}  # no standalone growth column
    other = cats["misc"]
    assert other["label"] == "其他"  # misc column relabelled
    assert "讀書" in [it["name"] for it in other["items"]]  # growth task folded in
    item = cats["work"]["items"][0]
    assert item["priority"] == "high" and item["priority_label"] == "High"
    assert item["project"] == "專案A"


def test_est_pomodoros_falls_back_to_plan_sum(tmp_path):
    """A calendar-linked task created without 預估🍅 carries its estimate only on
    plan[] — find_task should sum those so 預估 shows a real number, not 0/"-"
    (regression: 知識衛星試錄課程影片 had plan pomodoros 12 but no 預估🍅 → 預估 "-")."""
    _task(
        tmp_path,
        "知識衛星試錄課程影片.md",
        {
            "title": "知識衛星試錄課程影片",
            "status": "to-do",
            "category": "work",
            "plan": [
                {
                    "date": "2026-06-16",
                    "pomodoros": 12,
                    "start": "2026-06-16T10:30:00+08:00",
                    "end": "2026-06-16T16:30:00+08:00",
                    "calendar_event_id": "evt123",
                }
            ],
            "tags": ["task"],
        },
    )
    t = WeeklyIndexer(tmp_path).find_task("知識衛星試錄課程影片")
    assert t.est_pomodoros == 12


def test_explicit_estimate_wins_over_plan_sum(tmp_path):
    """An explicit 預估🍅 must not be overridden by the plan-sum fallback."""
    _task(
        tmp_path,
        "estimate-task.md",
        {
            "title": "estimate-task",
            "status": "to-do",
            "category": "work",
            "預估🍅": 3,
            "plan": [{"date": "2026-06-16", "pomodoros": 12}],
            "tags": ["task"],
        },
    )
    t = WeeklyIndexer(tmp_path).find_task("estimate-task")
    assert t.est_pomodoros == 3


def test_plan_entry_done_drives_daily_crossout(tmp_path, monkeypatch):
    # Regression (修修): a plan entry's `done: true` must surface as done_on(d) / the
    # daily item's done — _as_int treats bool as 0, so `done` is parsed truthily instead.
    monkeypatch.setattr(wi, "today_taipei", lambda: date(2026, 6, 3))
    _task(
        tmp_path,
        "肌酸的妙用 - Film.md",
        {
            "title": "肌酸的妙用 - Film",
            "projects": ["[[肌酸的妙用]]"],
            "category": "work",
            "預估🍅": 1,
            "status": "to-do",
            "plan": [{"date": "2026-06-01", "pomodoros": 1, "done": True}],
            "tags": ["task"],
        },
    )
    t = WeeklyIndexer(tmp_path).find_task("肌酸的妙用 - Film")
    assert t.done_on(date(2026, 6, 1)) is True  # per-day done
    assert t.done is False  # whole task still open
    v = WeeklyIndexer(tmp_path).view(week_for_date(date(2026, 6, 1)))
    mon = next(d for d in v.days if d["date"] == "2026-06-01")
    item = next(c for c in mon["categories"] if c["slug"] == "work")["items"][0]
    assert item["done"] is True  # daily item crosses out


def test_all_view_buckets_by_schedule(tmp_path):
    """N541: 全部 splits not-done tasks into this-week / other-scheduled / unscheduled,
    with 1+2 sorted nearest→furthest by date. Week of 2026-06-01..06-07."""
    base = {"status": "to-do", "category": "work", "tags": ["task"]}

    def mk(fname: str, title: str, **extra):
        _task(tmp_path, fname, {**base, "title": title, **extra})

    mk("A 本週.md", "A 本週", plan=[{"date": "2026-06-05", "pomodoros": 2}])
    mk("B 本週早.md", "B 本週早", plan=[{"date": "2026-06-02", "pomodoros": 1}])
    mk("C 未來.md", "C 未來", plan=[{"date": "2026-06-20", "pomodoros": 1}])
    mk("D 過去.md", "D 過去", scheduled="2026-05-20")
    mk("E 未排.md", "E 未排")
    mk("F 完成.md", "F 完成", status="done")  # excluded

    v = WeeklyIndexer(tmp_path).view(week_for_date(date(2026, 6, 1)))
    assert [t.name for t in v.all_this_week] == ["B 本週早", "A 本週"]  # nearest date first
    # other-scheduled sorts by earliest date: 過去(05-20) before 未來(06-20)
    assert [t.name for t in v.all_other_scheduled] == ["D 過去", "C 未來"]
    assert [t.name for t in v.all_unscheduled] == ["E 未排"]
    assert v.backlog_count == 5  # all not-done; F excluded


# ── WeeklyReview.has_content (draft-vs-empty signal, #weekly-status fix) ──────


def test_has_content_true_when_a_review_section_filled():
    r = wi.WeeklyReview(
        exists=True,
        status="planning",
        sections={"✨ Highlight": "課程規劃收斂了", "😔 Lowlight": ""},
    )
    assert r.has_content is True


def test_has_content_false_when_all_review_sections_blank():
    r = wi.WeeklyReview(
        exists=True,
        status="planning",
        # only whitespace / the separate 隨手筆記 — not a written review
        sections={"✨ Highlight": "  ", "😔 Lowlight": "\n", "隨手筆記": "some note"},
    )
    assert r.has_content is False


def test_has_content_ignores_notes_section():
    r = wi.WeeklyReview(exists=True, status="active", sections={"隨手筆記": "隨手記一筆"})
    assert r.has_content is False
