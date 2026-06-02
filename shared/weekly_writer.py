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

import hashlib
import os
import re
import tempfile
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

TASKS_DIR = "TaskNotes/Tasks"
WEEKLY_DIR = "Journals/Weekly"

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


class TaskNotFoundError(WeeklyWriteError):
    """The task file is gone (renamed/removed in Obsidian). Routed to an
    err=task dashboard bounce rather than a generic write error (PR#812 panel)."""


class WeeklyConflictError(WeeklyWriteError):
    """The weekly file changed between the page's read and this write (If-Match
    mtime mismatch). Surfaced to the route as a 409-style "reload and retry" —
    guards the `Journals/Weekly/` 🟡 file against a silent Obsidian/Syncthing
    overwrite (ADR-040 A1; mirrors project_writer's ProjectConcurrentEditError)."""


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
    """Write atomically via tmp + rename (intra-FS). Windows retry: ``os.replace``
    fails with WinError 5 when Obsidian / Syncthing / antivirus briefly holds the
    destination; three linear-backoff attempts cover the ~50–300ms window
    (mirrors ``project_writer._atomic_write``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.stem}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        for attempt in range(3):
            try:
                os.replace(tmp_path_str, path)
                return
            except PermissionError as e:
                if attempt == 2:
                    raise WeeklyWriteError(
                        f"無法寫入 {path.name}：被其他程式鎖定"
                        f"（可能是 Obsidian / Syncthing / 防毒在 sync）。"
                        f"關掉該檔或暫停 sync 再重試。Original: {e}"
                    ) from e
                # Short backoff (~50/100ms): this is on the synchronous save
                # request path, so a long hang reads as a broken UI — prefer a
                # fast failure + retry banner over a multi-hundred-ms freeze
                # (Gemini PR#811 audit §3).
                time.sleep(0.05 * (attempt + 1))
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


# ── Task note body (ADR-040 A7 — scoped writing surface, Slice W) ───────────────
# The task page is a scoped draft surface: 修修 writes the note BODY on the page
# (his ask: drafting 本週電子報 on the task). TaskNotes is already 🟡 — no red line —
# and there is NO LLM authorship (A1). This is a BODY-ONLY edit, so it must NOT go
# through ``_write_task`` (which re-serialises frontmatter via PyYAML and would drop
# the owner's comments / quoting / style — PR#812 panel, Codex+Gemini). Instead it
# **byte-splices**: the frontmatter prefix (incl. a BOM and CRLF endings) is kept
# verbatim and only the bytes after the closing ``---`` line are replaced. A file
# with no parseable frontmatter fails CLOSED (never clobbered).

# Frontmatter prefix = optional BOM + opening ``---`` line … closing ``---`` line,
# matching both LF and CRLF. Group 1 is preserved byte-for-byte; the rest is body.
_TASK_FM_PREFIX_RE = re.compile(r"﻿?---\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


def task_file_token(vault_root: Path, task_slug: str) -> str:
    """An If-Match token (content hash) for a task file — same primitive as
    :func:`weekly_file_token`, so a body save can't silently clobber an Obsidian
    edit made after the page loaded."""
    return _content_hash(_task_path(vault_root, task_slug))


def _split_task_frontmatter(text: str) -> tuple[str, str]:
    """``(frontmatter_prefix, body)`` for a task file. The prefix is verbatim
    (BOM / CRLF / comments / quoting all preserved). Raises if there is no
    parseable frontmatter — a body write must never reinterpret a whole file as
    body and so delete the frontmatter (PR#812 panel)."""
    m = _TASK_FM_PREFIX_RE.match(text)
    if not m:
        raise WeeklyWriteError(
            "task file has no parseable YAML frontmatter; refusing to rewrite the body"
        )
    return text[: m.end()], text[m.end() :]


def read_task_split(vault_root: Path, task_slug: str) -> tuple[str, str]:
    """One snapshot of a task file → ``(body, token)`` read from the SAME bytes, so
    the page can't show an old body paired with a fresher token (PR#812 panel).
    Raises :class:`WeeklyWriteError` (``.code == _TASK_NOT_FOUND``) if missing."""
    path = _task_path(vault_root, task_slug)
    if not path.exists():
        raise TaskNotFoundError(f"task not found: {path}")
    raw_bytes = path.read_bytes()  # bytes, so the token matches task_file_token and
    token = hashlib.sha1(raw_bytes).hexdigest()  # no newline translation skews it
    _, body = _split_task_frontmatter(raw_bytes.decode("utf-8"))
    return body, token


def read_task_body(vault_root: Path, task_slug: str) -> str:
    """The verbatim note body (everything after the frontmatter)."""
    body, _ = read_task_split(vault_root, task_slug)
    return body


def write_task_body(
    vault_root: Path,
    task_slug: str,
    body: str,
    *,
    expected_token: Optional[str] = None,
) -> str:
    """Replace a task's note **body**, preserving the frontmatter prefix byte-for-byte
    (A7 — body-only). The only normalisation is a single trailing newline; CRLF→LF on
    the body is the route's job. ``expected_token`` is the If-Match guard. Returns the
    new :func:`task_file_token`. Raises (``.code == _TASK_NOT_FOUND``) if the file is
    gone, :class:`WeeklyConflictError` on a stale token."""
    path = _task_path(vault_root, task_slug)
    if not path.exists():
        raise TaskNotFoundError(f"task not found: {path}")
    raw_bytes = path.read_bytes()  # bytes → token consistent with task_file_token
    if expected_token is not None:
        current = hashlib.sha1(raw_bytes).hexdigest()
        if current != expected_token:
            raise WeeklyConflictError(
                f"任務筆記 {path.name} 在你開啟頁面後被改動"
                "（Obsidian / Syncthing / 另一個分頁）。請重新整理頁面再存。"
            )
    prefix, _old_body = _split_task_frontmatter(raw_bytes.decode("utf-8"))  # validates fm
    content = prefix + body
    if not content.endswith("\n"):
        content += "\n"
    _atomic_write(path, content)
    return task_file_token(vault_root, task_slug)


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


# ── Calendar-block scheduling (ADR-041 — vault is the source of truth) ──────────
# A scheduled block is, authoritatively, a ``plan[]`` entry (effort intent — D1).
# The calendar event is a downstream projection; this writer only touches the VAULT:
# it upserts the plan[] entry AND writes the projection fields (scheduled/scheduled_end
# + optional calendar_event_id) in one byte-safe write. The Google Calendar event is
# created by the caller (Slice 41b) — keeping this layer pure-data + testable.
CALENDAR_BLOCK_MINUTES_PER_POMODORO = 30  # 25-min 🍅 focus + 5-min buffer (D2)


def schedule_task_block(
    vault_root: Path,
    task_slug: str,
    *,
    start: datetime,
    pomodoros: int,
    reason: Optional[str] = None,
    calendar_event_id: Optional[str] = None,
    expected_token: Optional[str] = None,
) -> tuple[str, str, str]:
    """Authoritatively schedule a task block in the vault (ADR-041 D1/D2).

    ``start`` is a **naive local** (Asia/Taipei, D4) datetime. In one coalesced,
    If-Match-guarded write this upserts the ``plan[]`` entry for ``start``'s date
    (the authoritative effort intent — preserving an existing ``done``) AND writes
    the calendar **projection** fields: ``scheduled`` = start, ``scheduled_end`` =
    start + ``pomodoros`` × 30 min, and ``calendar_event_id`` when the caller (41b)
    has created the event. No network — the event itself is the caller's job.

    Weekend (Sat/Sun) blocks require ``reason`` (ADR-039 D9). Returns
    ``(scheduled_iso, scheduled_end_iso, new_token)``. Raises
    :class:`TaskNotFoundError`, :class:`WeeklyConflictError`, or
    :class:`WeeklyWriteError` (bad ``pomodoros``)."""
    if pomodoros < 1:
        raise WeeklyWriteError("pomodoros must be >= 1")
    day = start.date()
    if _is_weekend(day) and not reason:
        raise WeekendReasonRequired(f"週末 ({day.isoformat()}) 排程需填寫 reason（D9）")

    end = start + timedelta(minutes=pomodoros * CALENDAR_BLOCK_MINUTES_PER_POMODORO)
    scheduled = start.isoformat(timespec="seconds")
    scheduled_end = end.isoformat(timespec="seconds")

    path = _task_path(vault_root, task_slug)
    if not path.exists():
        raise TaskNotFoundError(f"task not found: {path}")
    _check_token(path, expected_token)
    fm, body = _read_task(path)

    # authoritative plan[] upsert (D1) — same rules as add_plan_entry
    entries = _plan_list(fm)
    new_entry: dict[str, Any] = {"date": day.isoformat(), "pomodoros": pomodoros}
    if reason:
        new_entry["reason"] = reason
    replaced = False
    for i, e in enumerate(entries):
        if _entry_date(e) == day:
            if "done" in e:
                new_entry["done"] = e["done"]
            entries[i] = new_entry
            replaced = True
            break
    if not replaced:
        entries.append(new_entry)
    fm["plan"] = entries

    # calendar projection fields (D2/D3)
    fm["scheduled"] = scheduled
    fm["scheduled_end"] = scheduled_end
    if calendar_event_id is not None:
        fm["calendar_event_id"] = calendar_event_id

    _write_task(path, fm, body)
    return scheduled, scheduled_end, task_file_token(vault_root, task_slug)


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


# ── Weekly file writer (Journals/Weekly/ — the 🟡 red-line surface) ─────────────
# ADR-040 A1: the Bridge persists 修修's own INTENT (top3/next3/targets/status) +
# his verbatim PROSE (review answers, 隨手筆記); it never authors content, and
# observations (🍅/UFO/rate) are computed-on-read, never written here. Only the
# allowlisted frontmatter keys + named ## prose sections are touched; the body
# prose 修修 wrote is byte-preserved, and non-allowlisted frontmatter keys are
# preserved semantically (re-serialised by PyYAML — inline comments / quoting in
# the frontmatter block are not retained).

# The ONLY machine-maintained weekly-file frontmatter keys (ADR-040 A3; updates
# ADR-039 D5's allowlist).
WEEKLY_FRONTMATTER_KEYS = ("start_date", "end_date", "status", "top3", "next3", "targets")
# Honest FSM (ADR-040 A6): planning → active → reviewed. The writer validates the
# vocabulary; the route decides *when* a transition is truthful.
WEEKLY_STATUSES = ("planning", "active", "reviewed")
# Named prose sections the Bridge review form owns (verbatim human prose — A1).
WEEKLY_REVIEW_HEADINGS = ("✨ Highlight", "😔 Lowlight", "📚 學到的東西", "🙏 感恩")
WEEKLY_NOTES_HEADING = "隨手筆記"


def _weekly_path(vault_root: Path, file_key: str) -> Path:
    return Path(vault_root) / WEEKLY_DIR / f"{file_key}.md"


def _content_hash(path: Path) -> str:
    """SHA-1 of the file's bytes, or ``""`` when absent. Used as the If-Match
    token (Codex+Gemini PR#811 audit): a content hash is immune to mtime resets
    by git/backup/Syncthing and shrinks the TOCTOU window vs a bare mtime."""
    if not path.exists():
        return ""
    return hashlib.sha1(path.read_bytes()).hexdigest()


def weekly_file_token(vault_root: Path, file_key: str) -> str:
    """An If-Match token for the weekly file: a SHA-1 of its current bytes, or
    ``""`` when the file does not exist yet (a first Weekly Plan creates it). The
    page embeds this in its forms; :func:`write_weekly` rejects a stale token."""
    return _content_hash(_weekly_path(vault_root, file_key))


def _check_token(path: Path, expected_token: Optional[str]) -> None:
    """Raise :class:`WeeklyConflictError` if the file's content drifted since the
    page read it. ``expected_token is None`` → caller opted out of the check
    (tests / programmatic). ``""`` means "expected absent"; conflict if it now
    exists. NB: this is a guard, not a transaction — a write landing between this
    check and the rename is still possible, but the content hash makes the common
    Obsidian/Syncthing drift cases reliably detected."""
    if expected_token is None:
        return
    current = _content_hash(path)
    if current != expected_token:
        raise WeeklyConflictError(
            f"週檔 {path.name} 在你開啟頁面後被改動"
            "（Obsidian / Syncthing / 另一個分頁）。請重新整理頁面再存。"
        )


def _read_weekly(path: Path) -> tuple[dict[str, Any], str]:
    """Read a weekly file → (frontmatter, body). Missing file → ``({}, "")`` so
    the caller can create from template."""
    if not path.exists():
        return {}, ""
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


def _weekly_template(start_date: date, end_date: date) -> tuple[dict[str, Any], str]:
    """Frontmatter + body for a freshly-created weekly file. ``targets`` is left
    empty — 修修 sets it during the Weekly Plan (A3: never auto-filled). The prose
    sections are empty headings ready for the Friday/Sunday review."""
    fm: dict[str, Any] = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "status": "planning",
        "top3": [],
        "next3": [],
        "targets": {},
    }
    headings = list(WEEKLY_REVIEW_HEADINGS) + [WEEKLY_NOTES_HEADING]
    body = "\n" + "".join(f"## {h}\n\n" for h in headings)
    return fm, body


def _replace_section(body: str, heading: str, content: str) -> str:
    """Replace the body of a ``## {heading}`` section with ``content`` (the
    heading line is kept). Appends a new section if the heading is absent.
    Mirrors ``project_writer.update_body_section``."""
    # NB: match only the heading line + its own trailing newline. ``\s*`` would be
    # greedy across newlines and swallow the following blank line — landing on the
    # *next* heading and replacing it. ``[ \t]*`` keeps the match on one line.
    pat = re.compile(r"(^##[ \t]+" + re.escape(heading) + r"[ \t]*\n)", re.MULTILINE)
    m = pat.search(body)
    block = content.rstrip()
    if not m:
        sep = "" if body.endswith("\n\n") else ("\n" if body.endswith("\n") else "\n\n")
        return f"{body}{sep}## {heading}\n\n{block}\n"
    start = m.end()
    nxt = re.search(r"\n##\s+", body[start:])
    end = start + nxt.start() if nxt else len(body)
    tail = body[end:]
    return body[: m.end()] + "\n" + block + "\n" + tail


def _write_weekly(path: Path, fm: dict[str, Any], body: str) -> None:
    fm_str = _dump_frontmatter(fm)
    if body and not body.startswith("\n"):
        body = "\n" + body
    content = f"---\n{fm_str}\n---{body}"
    if not content.endswith("\n"):
        content += "\n"
    _atomic_write(path, content)


def write_weekly(
    vault_root: Path,
    file_key: str,
    *,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    frontmatter: Optional[dict[str, Any]] = None,
    sections: Optional[dict[str, str]] = None,
    expected_token: Optional[str] = None,
) -> str:
    """One coalesced, atomic write to the weekly file (one read → one rename, so
    the Windows Obsidian-regrab race is closed — same reason as
    ``project_writer.update_research_block``).

    - ``frontmatter`` — allowlisted keys (:data:`WEEKLY_FRONTMATTER_KEYS`) to
      set; any other key raises (the machine never *adds* keys outside the
      allowlist). ``targets`` is **merged** into the existing dict (so saving the
      🤩 goal never drops a stored 🍅 goal — PR#811 audit); other keys replace.
      A ``status`` value is checked against :data:`WEEKLY_STATUSES`. NB: keys
      outside the allowlist that 修修 hand-wrote are preserved *semantically* but
      re-serialised by PyYAML — inline comments / quoting / flow style in the
      frontmatter block are not retained (the body prose is byte-preserved).
    - ``sections`` — ``{heading: prose}`` to replace/insert as ``## heading``
      blocks (verbatim human prose — A1).
    - ``start_date``/``end_date`` — required only when the file does **not** exist
      yet: it is created from :func:`_weekly_template` first.
    - ``expected_token`` — If-Match guard (see :func:`_check_token`).

    Returns the new :func:`weekly_file_token`.
    """
    if frontmatter:
        bad = set(frontmatter) - set(WEEKLY_FRONTMATTER_KEYS)
        if bad:
            raise WeeklyWriteError(f"weekly-file frontmatter keys not in allowlist: {sorted(bad)}")
        status = frontmatter.get("status")
        if status is not None and status not in WEEKLY_STATUSES:
            raise WeeklyWriteError(f"unknown weekly status: {status!r}")

    path = _weekly_path(vault_root, file_key)
    _check_token(path, expected_token)

    if path.exists():
        fm, body = _read_weekly(path)
    else:
        if start_date is None or end_date is None:
            raise WeeklyWriteError("creating a weekly file requires start_date and end_date")
        fm, body = _weekly_template(start_date, end_date)

    if frontmatter:
        for k, v in frontmatter.items():
            if k == "targets" and isinstance(v, dict):
                # Merge, don't replace: a partial targets payload (e.g. just the
                # 🤩 goal from the plan panel) must not wipe a stored 🍅 goal
                # (PR#811 audit B2). To *clear* a target, callers omit it from
                # the payload and edit the file in Obsidian — there is no UI for
                # clearing, by design (machine never authors intent).
                cur = fm.get("targets")
                merged = dict(cur) if isinstance(cur, dict) else {}
                merged.update(v)
                fm["targets"] = merged
            else:
                fm[k] = v
    if sections:
        for heading, content in sections.items():
            body = _replace_section(body, heading, content)

    _write_weekly(path, fm, body)
    return weekly_file_token(vault_root, file_key)
