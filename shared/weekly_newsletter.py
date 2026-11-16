"""Standing weekly task: 「WXX 電子報」 on Monday 06:00, 2 🍅 (修修 2026-11-12).

Bridge has no recurring-task engine, so the smallest correct shape is a lazy
ensure: when 修修 opens a week (current or future) on the Weekly page, the
week's newsletter task is created and scheduled once. Two guards keep it from
being pushy:

* **Already there** — any task (TaskNotes/Tasks *or* /Archive) whose title names
  this week's number plus 電子報 and whose dates fall in/near this week counts as
  the week's newsletter. 修修's past titles vary (「W46 電子報」「26W35電子報」
  「W43 寫本週電子報」), so the match is on the week number, not the exact title.
* **Only once** — a state.db marker per week. Once ensured, a deleted or moved
  newsletter is never resurrected. The marker is per machine (local Bridge and
  VPS Bridge each keep their own); the vault check above is what stops the two
  from duplicating each other.

Never raises into the page: the caller renders the week either way.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

import yaml

from shared.log import get_logger
from shared.weekly_indexer import WeekRef, week_for_date

logger = get_logger("nakama.weekly_newsletter")

NEWSLETTER_POMODOROS = 2
NEWSLETTER_START = time(6, 0)  # Monday 06:00 (Asia/Taipei)
MARKER_KIND = "newsletter"

# Tests flip this off in conftest (opt back in with @pytest.mark.weekly_autotask):
# the GET dashboard is exercised by hundreds of tests that must not grow a task.
ENABLED = True

_TASK_DIRS = ("TaskNotes/Tasks", "TaskNotes/Archive")
_FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---", re.DOTALL)


def newsletter_title(wk: WeekRef) -> str:
    return f"{wk.label} 電子報"


def newsletter_start(wk: WeekRef) -> datetime:
    monday = wk.start + timedelta(days=1)  # Sunday-start week → +1 = Monday
    return datetime.combine(monday, NEWSLETTER_START)  # naive Asia/Taipei (ADR-041 D4)


# ── marker (state.db) ────────────────────────────────────────────────────────


def _conn():
    from shared import state

    conn = state._get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS weekly_autotasks (
               week_key   TEXT NOT NULL,
               kind       TEXT NOT NULL,
               outcome    TEXT NOT NULL,
               created_at TEXT NOT NULL,
               PRIMARY KEY (week_key, kind)
           )"""
    )
    return conn


def marker_outcome(week_key: str, kind: str = MARKER_KIND) -> Optional[str]:
    row = (
        _conn()
        .execute(
            "SELECT outcome FROM weekly_autotasks WHERE week_key = ? AND kind = ?",
            (week_key, kind),
        )
        .fetchone()
    )
    return row[0] if row else None


def _set_marker(week_key: str, outcome: str, kind: str = MARKER_KIND) -> None:
    conn = _conn()
    conn.execute(
        "INSERT OR IGNORE INTO weekly_autotasks (week_key, kind, outcome, created_at)"
        " VALUES (?, ?, ?, ?)",
        (week_key, kind, outcome, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


# ── "already there" check ────────────────────────────────────────────────────


def _as_date(v: object) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and len(v.strip()) >= 10:
        try:
            return date.fromisoformat(v.strip()[:10])
        except ValueError:
            return None
    return None


def _task_dates(fm: dict) -> list[date]:
    out: list[date] = []
    for key in ("scheduled", "created", "dateCreated"):
        d = _as_date(fm.get(key))
        if d:
            out.append(d)
    for e in fm.get("plan") or []:
        if isinstance(e, dict):
            d = _as_date(e.get("date"))
            if d:
                out.append(d)
    return out


def _names_this_week(title: str, wk: WeekRef) -> bool:
    if "電子報" not in title:
        return False
    return re.search(rf"W0?{wk.week}(?!\d)", title, re.IGNORECASE) is not None


def find_existing(vault: Path, wk: WeekRef) -> Optional[Path]:
    """The task file that already is this week's newsletter, or None.

    A title match alone isn't enough — Archive keeps last year's 「W46 電子報」 —
    so the file must also carry a date within two weeks before this week's end.
    A matching title with no dates at all is treated as a match (don't duplicate)."""
    window_start = wk.start - timedelta(days=14)
    for rel in _TASK_DIRS:
        folder = vault / rel
        if not folder.is_dir():
            continue
        for path in folder.glob("*.md"):
            stem = unicodedata.normalize("NFC", path.stem)
            try:
                m = _FM_RE.match(path.read_text(encoding="utf-8"))
                fm = (yaml.safe_load(m.group(1)) if m else {}) or {}
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                fm = {}
            if not isinstance(fm, dict):
                fm = {}
            title = unicodedata.normalize("NFC", str(fm.get("title") or stem))
            if not (_names_this_week(title, wk) or _names_this_week(stem, wk)):
                continue
            dates = _task_dates(fm)
            if not dates or any(window_start <= d <= wk.end for d in dates):
                return path
    return None


# ── ensure ───────────────────────────────────────────────────────────────────


def ensure_weekly_newsletter(vault: Path, wk: WeekRef, *, today: date) -> Optional[str]:
    """Create + schedule this week's newsletter task if it should exist and doesn't.

    Returns the created task slug, or None when nothing was created (past week,
    already ensured, already present, disabled, or any failure — logged)."""
    if not ENABLED:
        return None
    if wk.start < week_for_date(today).start:
        return None  # past weeks are history — never back-fill
    try:
        if marker_outcome(wk.file_key) is not None:
            return None
        if find_existing(vault, wk) is not None:
            _set_marker(wk.file_key, "existing")
            return None
    except Exception:  # noqa: BLE001 — a marker/scan hiccup must not break the page
        logger.exception("newsletter ensure pre-check failed for %s", wk.file_key)
        return None

    from shared import calendar_scheduler
    from shared.project_writer import ProjectWriteError, create_task

    title = newsletter_title(wk)
    try:
        created = create_task(
            vault_root=vault,
            project_slug=None,
            task_name=title,
            estimated_pomodoros=NEWSLETTER_POMODOROS,
            priority="normal",
            category="work",
        )
    except ProjectWriteError:
        # Same basename already in Tasks/ (e.g. an undated leftover the scan skipped
        # over) — 修修 has one; don't fight it.
        _set_marker(wk.file_key, "name_taken")
        return None
    except Exception:  # noqa: BLE001
        logger.exception("newsletter create_task failed for %s", wk.file_key)
        return None
    # Marker before the calendar step: the task now exists, so even if scheduling
    # fails this week is "ensured" — the next page load must not create another.
    _set_marker(wk.file_key, "created")
    slug = unicodedata.normalize("NFC", created.stem)
    try:
        outcome = calendar_scheduler.schedule_entry(
            vault,
            slug,
            start=newsletter_start(wk),
            pomodoros=NEWSLETTER_POMODOROS,
            title=title,
            all_day=False,
            reason=None,
            # 修修's standing slot — book it even if something else sits there.
            force=True,
        )
        logger.info("newsletter %s scheduled: %s", slug, outcome.calendar_status)
    except Exception:  # noqa: BLE001 — task exists; 修修 can 排入 it by hand
        logger.exception("newsletter schedule_entry failed for %s", slug)
    return slug
