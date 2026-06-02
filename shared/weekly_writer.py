"""Task writer — plan[] add/remove + timeEntries[] logging on TaskNotes tasks
(ADR-039 D4/D9 + E slice).

Writes only to ``TaskNotes/Tasks/{slug}.md``; touches only the ``plan``,
``scheduled`` and ``timeEntries`` frontmatter keys (allowlist). All other keys
+ the file body are preserved verbatim. None of these is the ``Journals/Weekly``
red line — they are the already-approved TaskNotes write surface.

All writes are **atomic** via tmp-file + ``os.replace`` (same pattern as
``project_writer._atomic_write``).
"""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import yaml

TASKS_DIR = "TaskNotes/Tasks"

# timeEntries mode (ADR-039 E): a 25-min focus block (``pomodoro`` → +1🍅) or a
# 75-min super-focus / UFO block (``deep`` → +3🍅 by duration, counted as 1 UFO).
# 🍅 itself is derived from duration by the aggregator (minutes ÷ 25); ``mode``
# only distinguishes UFO sessions for the 🤩 weekly count.
TIME_ENTRY_MODES = ("pomodoro", "deep")
_MODE_NOMINAL_MINUTES = {"pomodoro": 25, "deep": 75}  # the block 修修 started
_MAX_ENTRY_SECONDS = 6 * 60 * 60  # guard against a runaway timer logging garbage

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


class WeeklyWriteError(RuntimeError):
    pass


class WeekendReasonRequired(WeeklyWriteError):
    """Raised when a plan entry on Sat/Sun is submitted without a reason (D9)."""


def _today() -> date:
    return date.today()


def _is_weekend(d: date) -> bool:
    # weekday(): Mon=0 … Fri=4, Sat=5, Sun=6
    return d.weekday() in (5, 6)


def _task_path(vault_root: Path, task_slug: str) -> Path:
    task_slug = unicodedata.normalize("NFC", task_slug)
    return vault_root / TASKS_DIR / f"{task_slug}.md"


class _BlankNoneDumper(yaml.SafeDumper):
    """Renders None as blank scalar — matches LifeOS handwritten files."""


_BlankNoneDumper.add_representer(
    type(None),
    lambda dumper, _: dumper.represent_scalar("tag:yaml.org,2002:null", ""),
)


def _dump_frontmatter(fm: dict[str, Any]) -> str:
    return yaml.dump(
        fm,
        Dumper=_BlankNoneDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        width=10**9,
    ).rstrip()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.stem}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp_path_str, path)
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def _read_task(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        raise WeeklyWriteError(f"task not found: {path}")
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise WeeklyWriteError(f"invalid frontmatter in {path}: {exc}") from exc
    if not isinstance(fm, dict):
        raise WeeklyWriteError(f"frontmatter is not a mapping in {path}")
    return fm, m.group(2)


def _write_task(path: Path, fm: dict[str, Any], body: str) -> None:
    fm_str = _dump_frontmatter(fm)
    if body and not body.startswith("\n"):
        body = "\n" + body
    content = f"---\n{fm_str}\n---{body}"
    if not content.endswith("\n"):
        content += "\n"
    _atomic_write(path, content)


def _plan_list(fm: dict[str, Any]) -> list[dict[str, Any]]:
    raw = fm.get("plan")
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def _entry_date(entry: dict[str, Any]) -> Optional[date]:
    v = entry.get("date")
    if isinstance(v, date):  # yaml.safe_load parses YYYY-MM-DD as date
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v)
        except ValueError:
            return None
    return None


# ── Public API ────────────────────────────────────────────────────────────────


def add_plan_entry(
    vault_root: Path,
    task_slug: str,
    entry_date: date,
    pomodoros: int,
    reason: Optional[str] = None,
) -> None:
    """Add or update the plan entry for ``task_slug`` on ``entry_date``.

    Weekend (Sat/Sun) entries require ``reason`` — raises
    :class:`WeekendReasonRequired` before touching the file if missing.

    Only ``plan`` is mutated; ``scheduled`` and all other keys are untouched.
    Preserves an existing ``done`` field on the entry being updated.
    """
    if _is_weekend(entry_date) and not reason:
        raise WeekendReasonRequired(f"週末 ({entry_date.isoformat()}) 排程需填寫 reason（D9）")

    path = _task_path(vault_root, task_slug)
    fm, body = _read_task(path)

    entries = _plan_list(fm)
    new_entry: dict[str, Any] = {
        "date": entry_date.isoformat(),
        "pomodoros": pomodoros,
    }
    if reason:
        new_entry["reason"] = reason

    replaced = False
    for i, e in enumerate(entries):
        if _entry_date(e) == entry_date:
            if "done" in e:
                new_entry["done"] = e["done"]
            entries[i] = new_entry
            replaced = True
            break
    if not replaced:
        entries.append(new_entry)

    fm["plan"] = entries
    _write_task(path, fm, body)


def remove_plan_entry(
    vault_root: Path,
    task_slug: str,
    entry_date: date,
) -> bool:
    """Remove the plan entry for ``task_slug`` on ``entry_date``.

    Returns True if an entry was removed, False if none existed (file
    is not written in the False case). Only ``plan`` is mutated.
    """
    path = _task_path(vault_root, task_slug)
    fm, body = _read_task(path)

    entries = _plan_list(fm)
    new_entries = [e for e in entries if _entry_date(e) != entry_date]
    if len(new_entries) == len(entries):
        return False

    fm["plan"] = new_entries
    _write_task(path, fm, body)
    return True


def sync_scheduled_to_next_plan(
    vault_root: Path,
    task_slug: str,
    *,
    today: Optional[date] = None,
) -> Optional[date]:
    """Explicitly set ``scheduled`` to the earliest plan[] date ≥ today.

    This is the ONLY write path that touches ``scheduled``; ``add_plan_entry``
    and ``remove_plan_entry`` never rewrite it (D4 — no auto-rewrite).

    Returns the new ``scheduled`` value, or None if plan[] is empty / all
    entries are in the past (file is not written in the None case).
    """
    if today is None:
        today = _today()

    path = _task_path(vault_root, task_slug)
    fm, body = _read_task(path)

    entries = _plan_list(fm)
    future_dates = sorted(d for e in entries if (d := _entry_date(e)) is not None and d >= today)
    if not future_dates:
        return None

    next_date = future_dates[0]
    fm["scheduled"] = next_date.isoformat()
    _write_task(path, fm, body)
    return next_date


def _time_entry_list(fm: dict[str, Any]) -> list[dict[str, Any]]:
    raw = fm.get("timeEntries")
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def log_time_entry(
    vault_root: Path,
    task_slug: str,
    start: datetime,
    end: datetime,
    mode: str = "pomodoro",
    *,
    planned_minutes: Optional[int] = None,
    completed: bool = True,
    manual: bool = False,
) -> None:
    """Append one focus session as **evidence** to a task's ``timeEntries[]``
    (ADR-039 E / ADR-040 A2).

    Each entry records what was *intended* and what *actually happened*::

        {startTime, endTime, mode, planned_minutes, actual_minutes, completed}

    - ``start``/``end`` are the real span (the live timer passes its *actual
      elapsed* time, not a fixed nominal block — fixes the v1 bug where 提早完成
      logged a full block). Stored ISO-8601 (offset preserved when tz-aware).
    - ``actual_minutes`` is the rounded span; the aggregator still derives 🍅
      from the start→end interval (minutes ÷ 25), so this field is audit
      evidence, not the source of truth.
    - ``planned_minutes`` defaults to the mode's nominal block (25 / 75).
    - ``completed`` marks whether 修修 saw the block through; the UFO 🤩 metric
      is *derived* (``mode==deep`` & actual ≥ 70 min & completed) — never a bare
      tag. ``manual=True`` flags the ``+1`` backup button's asserted block,
      distinguishing it from real timer evidence.

    Only ``timeEntries`` is mutated; all other keys + the body are preserved.
    Raises :class:`WeeklyWriteError` on a non-positive or absurdly long span.
    """
    if mode not in TIME_ENTRY_MODES:
        raise WeeklyWriteError(f"unknown timeEntries mode: {mode!r}")
    span = (end - start).total_seconds()
    if span <= 0:
        raise WeeklyWriteError("timeEntries endTime must be after startTime")
    if span > _MAX_ENTRY_SECONDS:
        raise WeeklyWriteError(f"timeEntries span too long ({span:.0f}s)")

    path = _task_path(vault_root, task_slug)
    fm, body = _read_task(path)

    entry: dict[str, Any] = {
        "startTime": start.isoformat(),
        "endTime": end.isoformat(),
        "mode": mode,
        "planned_minutes": (
            planned_minutes if planned_minutes is not None else _MODE_NOMINAL_MINUTES[mode]
        ),
        "actual_minutes": round(span / 60),
        "completed": bool(completed),
    }
    if manual:
        entry["manual"] = True

    entries = _time_entry_list(fm)
    entries.append(entry)
    fm["timeEntries"] = entries
    _write_task(path, fm, body)
