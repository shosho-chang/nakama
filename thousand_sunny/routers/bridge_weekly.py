"""Bridge Weekly Dashboard — Tier B (vault-as-substrate, read-only Slice 0).

Surface:
  - ``GET /bridge/weekly`` — the focused-week dashboard (ADR-039). Defaults to
    the current Sunday-start week; ``?week=YYYY-MM-DD`` (a start-Sunday key)
    navigates. Read-only: aggregates real vault tasks + pomodoro session logs;
    no writes (plan[]/review writes land in Slice 1/2).

Auth: HMAC cookie (mirrors bridge_digests / bridge_projects).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared import calendar_scheduler
from shared.config import get_vault_path
from shared.log import get_logger
from shared.pomodoro_aggregator import TAIPEI, task_actual
from shared.weekly_indexer import (
    WeeklyIndexer,
    today_taipei,
    week_for_date,
    week_from_key,
)
from shared.weekly_writer import (
    TaskNotFoundError,
    WeekendReasonRequired,
    WeeklyConflictError,
    WeeklyWriteError,
    add_plan_entry,
    log_time_entry,
    read_task_schedule,
    read_task_split,
    remove_plan_entry,
    sync_scheduled_to_next_plan,
    weekly_file_token,
    write_task_body,
    write_weekly,
)
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.bridge_weekly")

page_router = APIRouter(prefix="/bridge", tags=["bridge-weekly"])

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "bridge"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for css in ("tokens.css", "bridge.css", "bridge-pages.css", "bridge-weekly.css", "theme.js"):
        path = static_dir / css
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


_PLAN_ERRORS = {
    "weekend": "週末排程需填寫原因（D9）——已取消這次排入。",
    "pomodoros": "番茄數需介於 1–20。",
    "date": "排程日期格式無效。",
    "task": "找不到該任務檔，可能已在 Obsidian 改名或移除。",
    "time": "排程時間格式無效。",
    "already_linked": (
        "此任務已連動 Google 行事曆——要改期或取消，請到任務頁操作；重新排入會產生重複事件，已擋下。"
    ),
    # calendar scheduling (ADR-041 41c) — plan is authoritative; calendar is best-effort
    "cal_conflict": (
        "此時段與 Google 行事曆既有事件衝突——已寫入本週計畫但未建立行事曆事件。"
        "想忽略衝突，請勾選「強制排入」後重送。"
    ),
    "cal_unavailable": (
        "已寫入本週計畫，但 Google 行事曆暫時無法連動（授權或網路）——稍後可重新排入以補建事件。"
    ),
    "cal_failed": "排程寫入後寫回失敗，行事曆事件已回滾——請重試。",
    # weekly-file (Journals/Weekly 🟡) write errors — ADR-040 Slice 2
    "conflict": (
        "週檔在你開啟頁面後被改動（Obsidian / Syncthing / 另一分頁），"
        "已擋下這次覆寫——請重新整理頁面再存。"
    ),
    "write": "寫入週檔失敗，可能被其他程式鎖定——請稍後重試。",
}

_SAVED_MSGS = {
    "plan": "✓ 已儲存本週計畫。",
    "review": "✓ 已存週回顧。",
    "top3": "✓ 已更新本週三大要事。",
    "targets": "✓ 已更新本週目標。",
    "notes": "✓ 已存隨手筆記。",
    "status": "✓ 已更新本週狀態。",
    "scheduled": "✓ 已排入計畫並建立行事曆事件。",
}


@page_router.get("/weekly", response_class=HTMLResponse)
async def weekly_landing(
    request: Request,
    week: str | None = Query(None),
    err: str | None = Query(None),
    saved: str | None = Query(None),
    n: int | None = Query(None),  # conflict count, for the cal_conflict banner (41c)
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)

    wk = week_from_key(week) if week else None
    if wk is None:
        wk = week_for_date(today_taipei())

    vault = get_vault_path()
    idx = WeeklyIndexer(vault)
    try:
        view = idx.view(wk)
    except Exception:  # noqa: BLE001 — surface an empty-but-rendered page, never 500 the dashboard
        logger.exception("weekly view assembly failed for week=%s", wk.file_key)
        raise

    error_msg = _PLAN_ERRORS.get(err) if err else None
    if err == "cal_conflict" and n and n > 0:
        error_msg = (
            f"此時段與 Google 行事曆 {n} 個既有事件衝突——已寫入本週計畫但未建立行事曆事件。"
            "想忽略衝突，請勾選「強制排入」後重送。"
        )

    return _templates.TemplateResponse(
        request,
        "weekly.html",
        {
            "view": view,
            # If-Match token for the weekly-file forms (ADR-040 Slice 2)
            "weekly_token": weekly_file_token(vault, wk.file_key),
            "asset_version": _SHOSHO_ASSET_VERSION,
            "error_msg": error_msg,
            "saved_msg": _SAVED_MSGS.get(saved) if saved else None,
        },
    )


# ── Plan mutations (Slice 1a — ADR-039 D4/D9) ────────────────────────────────
# page_router (no blanket auth dep) → manual check_auth per endpoint; form POST →
# 303; failures that are 修修-recoverable redirect back with ?err=… (banner),
# genuine not-found is a 404. Mirrors bridge_projects mutation idiom.


def _safe_week_key(week: str | None) -> str:
    wk = week_from_key(week) if week else None
    if wk is None:
        wk = week_for_date(today_taipei())
    return wk.file_key


def _back(week_key: str, err: str | None = None) -> RedirectResponse:
    url = f"/bridge/weekly?week={week_key}"
    if err:
        url += f"&err={err}"
    return RedirectResponse(url, status_code=303)


def _parse_entry_date(entry_date: str) -> date | None:
    try:
        return date.fromisoformat(entry_date.strip())
    except ValueError:
        return None


@page_router.post("/weekly/plan")
async def weekly_plan_add(
    task_slug: str = Form(..., min_length=1),
    entry_date: str = Form(..., min_length=1),
    pomodoros: int = Form(...),
    reason: str = Form(""),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)

    ed = _parse_entry_date(entry_date)
    if ed is None:
        return _back(wk_key, "date")
    if not 1 <= pomodoros <= 20:
        return _back(wk_key, "pomodoros")

    try:
        add_plan_entry(get_vault_path(), task_slug, ed, pomodoros, reason.strip() or None)
    except WeekendReasonRequired:
        return _back(wk_key, "weekend")
    except WeeklyWriteError as exc:
        logger.warning("weekly_plan_add: %s", exc)
        return _back(wk_key, "task")
    return _back(wk_key)


@page_router.post("/weekly/plan/remove")
async def weekly_plan_remove(
    task_slug: str = Form(..., min_length=1),
    entry_date: str = Form(..., min_length=1),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)

    ed = _parse_entry_date(entry_date)
    if ed is None:
        return _back(wk_key, "date")

    try:
        remove_plan_entry(get_vault_path(), task_slug, ed)
    except WeeklyWriteError as exc:
        logger.warning("weekly_plan_remove: %s", exc)
        return _back(wk_key, "task")
    return _back(wk_key)


@page_router.post("/weekly/sync-scheduled")
async def weekly_sync_scheduled(
    task_slug: str = Form(..., min_length=1),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)

    try:
        sync_scheduled_to_next_plan(get_vault_path(), task_slug)
    except WeeklyWriteError as exc:
        logger.warning("weekly_sync_scheduled: %s", exc)
        return _back(wk_key, "task")
    return _back(wk_key)


_TIME_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _parse_entry_time(entry_time: str):
    """Parse a strict naive ``HH:MM`` (browser ``<input type=time>``) into a
    ``time``; None on anything else. Strict on purpose: ``time.fromisoformat``
    would also accept seconds and a ``+TZ`` offset, and ``datetime.combine`` with
    a tz-aware time yields an aware datetime — which violates the D4 naive
    Asia/Taipei contract (Codex audit §1)."""
    from datetime import time as _time

    m = _TIME_HHMM_RE.match(entry_time.strip())
    if not m:
        return None
    return _time(int(m.group(1)), int(m.group(2)))


@page_router.post("/weekly/schedule")
async def weekly_schedule(
    task_slug: str = Form(..., min_length=1),
    entry_date: str = Form(..., min_length=1),
    entry_time: str = Form(..., min_length=1),
    pomodoros: int = Form(...),
    reason: str = Form(""),
    force: int = Form(0),  # ignore a calendar clash and create anyway (D8)
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    """Schedule a task as a real clock-time block (ADR-041 41c): authoritatively
    write plan[] + scheduled/scheduled_end in the vault, then best-effort project
    it onto Google Calendar. The vault is the source of truth (D1); a calendar
    failure degrades to a banner, never blocks the plan (D2)."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)

    ed = _parse_entry_date(entry_date)
    if ed is None:
        return _back(wk_key, "date")
    et = _parse_entry_time(entry_time)
    if et is None:
        return _back(wk_key, "time")
    if not 1 <= pomodoros <= 20:
        return _back(wk_key, "pomodoros")

    vault = get_vault_path()
    # Derive the calendar event title from the vault (the source of truth, D1) —
    # never from a client-posted field that could be stale (Codex audit §2). A
    # missing task → the scheduler raises TaskNotFoundError below.
    task = WeeklyIndexer(vault).find_task(task_slug)
    # Server-enforce the 41c orphan guard (not just the hidden-picker UI): a task
    # already projected to the calendar must NOT re-enter the create path — a stale
    # dashboard / direct POST would create a SECOND event and orphan the first
    # (41d panel — Codex §2). Reschedule/cancel live on the task page.
    if task is not None and task.calendar_event_id:
        return _sched_back(wk_key, err="already_linked")
    event_title = task.title if task is not None else task_slug

    start = datetime.combine(ed, et)  # naive Asia/Taipei (D4)
    try:
        outcome = calendar_scheduler.schedule_block(
            vault,
            task_slug,
            start=start,
            pomodoros=pomodoros,
            title=event_title,
            reason=reason.strip() or None,
            force=bool(force),
        )
    except WeekendReasonRequired:
        return _back(wk_key, "weekend")
    except TaskNotFoundError:
        return _back(wk_key, "task")
    except WeeklyWriteError as exc:
        logger.warning("weekly_schedule plan write failed: %s", exc)
        return _back(wk_key, "task")

    status = outcome.calendar_status
    if status == calendar_scheduler.CREATED:
        return _sched_back(wk_key, saved="scheduled")
    if status == calendar_scheduler.CONFLICT:
        return _sched_back(wk_key, err="cal_conflict", n=len(outcome.conflicts))
    if status == calendar_scheduler.UNAVAILABLE:
        return _sched_back(wk_key, err="cal_unavailable")
    return _sched_back(wk_key, err="cal_failed")  # FAILED — event rolled back


def _sched_back(week_key: str, *, err: str | None = None, saved: str | None = None, n: int = 0):
    url = f"/bridge/weekly?week={week_key}"
    if err:
        url += f"&err={err}"
        if n:
            url += f"&n={n}"
    elif saved:
        url += f"&saved={saved}"
    return RedirectResponse(url, status_code=303)


# ── Weekly-file 週交接 writes (Journals/Weekly 🟡 — ADR-040 A6 composable steps) ─
# Each step persists ALONE (review / top3 / targets / notes / status); none blocks
# another. The Bridge only persists 修修's own intent + verbatim prose (A1). Every
# write carries an If-Match token; a stale token → conflict banner, never a silent
# overwrite of an Obsidian/Syncthing edit.

_REVIEW_FIELDS = {
    "highlight": "✨ Highlight",
    "lowlight": "😔 Lowlight",
    "learned": "📚 學到的東西",
    "gratitude": "🙏 感恩",
}


def _safe_week(week: str | None):
    wk = week_from_key(week) if week else None
    if wk is None:
        wk = week_for_date(today_taipei())
    return wk


def _weekly_back(week_key: str, *, err: str | None = None, saved: str | None = None):
    url = f"/bridge/weekly?week={week_key}"
    if err:
        url += f"&err={err}"
    elif saved:
        url += f"&saved={saved}"
    return RedirectResponse(url, status_code=303)


def _wrap_link(s: str) -> str:
    """Normalise one entry (bare name or ``[[wikilink]]``) to a ``[[wikilink]]``."""
    s = s.strip()
    inner = s[2:-2].strip() if s.startswith("[[") and s.endswith("]]") else s
    return f"[[{inner}]]"


def _links_from_list(values: list[str]) -> list[str]:
    """Dropdown selections → ordered ``[[wikilink]]`` list; blank slots dropped."""
    return [_wrap_link(v) for v in values if v.strip()]


def _write_weekly_or_back(wk, *, frontmatter=None, sections=None, expected_token, saved):
    """Run one composable weekly-file write; map writer errors to ?err banners."""
    try:
        write_weekly(
            get_vault_path(),
            wk.file_key,
            start_date=wk.start,
            end_date=wk.end,
            frontmatter=frontmatter,
            sections=sections,
            expected_token=expected_token,
        )
    except WeeklyConflictError:
        return _weekly_back(wk.file_key, err="conflict")
    except WeeklyWriteError as exc:
        logger.warning("weekly-file write failed (%s): %s", saved, exc)
        return _weekly_back(wk.file_key, err="write")
    return _weekly_back(wk.file_key, saved=saved)


@page_router.post("/weekly/review")
async def weekly_review_save(
    week: str = Form(""),
    expected_token: str = Form(""),
    highlight: str = Form(""),
    lowlight: str = Form(""),
    learned: str = Form(""),
    gratitude: str = Form(""),
    next3: list[str] = Form(default=[]),  # ≤3 dropdown selections for next week
    mark_reviewed: int = Form(0),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk = _safe_week(week)
    # NB: 隨手筆記 is owned by its OWN form (/weekly/notes), not this one — the
    # review form has no notes field. Writing it here would blank existing notes
    # on every review save (PR#811 audit B1).
    sections = {
        "✨ Highlight": highlight.strip(),
        "😔 Lowlight": lowlight.strip(),
        "📚 學到的東西": learned.strip(),
        "🙏 感恩": gratitude.strip(),
    }
    fm: dict = {"next3": _links_from_list(next3)}
    if mark_reviewed:  # honest FSM (A6): only flip on explicit completion
        fm["status"] = "reviewed"
    return _write_weekly_or_back(
        wk, frontmatter=fm, sections=sections, expected_token=expected_token, saved="review"
    )


@page_router.post("/weekly/notes")
async def weekly_notes_save(
    week: str = Form(""),
    expected_token: str = Form(""),
    notes: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk = _safe_week(week)
    return _write_weekly_or_back(
        wk, sections={"隨手筆記": notes.strip()}, expected_token=expected_token, saved="notes"
    )


@page_router.post("/weekly/top3")
async def weekly_top3_save(
    week: str = Form(""),
    expected_token: str = Form(""),
    top3: list[str] = Form(default=[]),  # ≤3 dropdown selections (task slug | project name)
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk = _safe_week(week)
    return _write_weekly_or_back(
        wk,
        frontmatter={"top3": _links_from_list(top3)},
        expected_token=expected_token,
        saved="top3",
    )


@page_router.post("/weekly/targets")
async def weekly_targets_save(
    week: str = Form(""),
    expected_token: str = Form(""),
    pomodoro: int = Form(0),
    ufo: int = Form(0),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk = _safe_week(week)
    targets: dict = {}
    if pomodoro > 0:
        targets["pomodoro"] = pomodoro
    if ufo > 0:
        targets["ufo"] = ufo
    return _write_weekly_or_back(
        wk, frontmatter={"targets": targets}, expected_token=expected_token, saved="targets"
    )


@page_router.post("/weekly/plan-save")
async def weekly_plan_save(
    week: str = Form(""),
    expected_token: str = Form(""),
    top3: list[str] = Form(default=[]),  # ≤3 dropdown selections
    ufo: str = Form(""),  # 🤩 weekly target; blank = leave as-is (🍅 goal auto-sums)
    advance: int = Form(0),  # checkbox: planning → active (honest FSM, A6)
    nakama_auth: str | None = Cookie(None),
):
    """One-button save for the 本週計畫 panel: top3 + UFO target (+ optional
    status advance) in a single coalesced write."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk = _safe_week(week)
    fm: dict = {"top3": _links_from_list(top3)}
    # Distinguish "field left blank" (don't touch the stored target — never write
    # the machine-default 5 as if it were 修修's intent) from an explicit number.
    # write_weekly merges targets, so this never wipes a stored 🍅 goal (audit B2).
    ufo_n = int(ufo) if ufo.strip().isdigit() else 0
    if ufo_n > 0:
        fm["targets"] = {"ufo": ufo_n}
    if advance:
        fm["status"] = "active"
    return _write_weekly_or_back(wk, frontmatter=fm, expected_token=expected_token, saved="plan")


@page_router.post("/weekly/status")
async def weekly_status_save(
    week: str = Form(""),
    expected_token: str = Form(""),
    status: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk = _safe_week(week)
    return _write_weekly_or_back(
        wk, frontmatter={"status": status}, expected_token=expected_token, saved="status"
    )


# ── Task detail page + pomodoro timer (ADR-039 E) ────────────────────────────
# A per-task page; the bottom timer logs a real focus session into the task's
# timeEntries[] (TaskNotes write surface — NOT the Journals/Weekly red line).

# Nominal block lengths: 25-min pomodoro (+1🍅) / 75-min deep super-focus (UFO if
# run ≥70 min). The live timer reports its **actual elapsed** seconds (ADR-040 A2
# — fixes the v1 bug where 提早完成 logged a full block); the manual "+1" backup
# omits elapsed and logs a full nominal block flagged ``manual``.
_MODE_MINUTES = {"pomodoro": 25, "deep": 75}

_TASK_ERRORS = {
    "mode": "未知的計時模式。",
    "log": "記錄番茄鐘失敗，任務檔可能已在 Obsidian 改名或移除。",
    # note-body save (A7 / Slice W)
    "conflict": (
        "任務筆記在你開啟頁面後被改動（Obsidian / Syncthing / 另一分頁），"
        "已擋下這次覆寫——請重新整理頁面再存。"
    ),
    "write": "寫入任務筆記失敗，可能被其他程式鎖定，或已在 Obsidian 改名／移除。",
    # calendar reschedule / cancel (ADR-041 41d) — vault authoritative, calendar best-effort
    "sched_date": "排程日期格式無效。",
    "sched_time": "排程時間格式無效。",
    "pomodoros": "番茄數需介於 1–20。",
    "weekend": "週末排程需填寫原因（D9）——已取消這次改期。",
    "cal_conflict": (
        "新時段與 Google 行事曆既有事件衝突——已更新本週計畫，但行事曆事件未移動。"
        "想忽略衝突，請勾選「強制」後重送。"
    ),
    "cal_unavailable": (
        "已更新本週計畫，但 Google 行事曆暫時無法連動（授權或網路）——稍後可在此重試。"
    ),
    "cancel_cal_failed": (
        "已更新本週計畫，但刪除 Google 行事曆事件失敗——請到 Google 行事曆手動移除，或稍後重試。"
    ),
    "cal_failed": "改期寫入後寫回失敗，行事曆事件已回滾——請重試。",
}

_TASK_SAVED = {
    "body": "✓ 已儲存筆記。",
    "rescheduled": "✓ 已改期並更新行事曆事件。",
    "unlinked": "✓ 已移出行事曆（保留本週計畫）。",
    "unscheduled": "✓ 已取消排程。",
}


def _now_taipei() -> datetime:
    return datetime.now(TAIPEI)


def _obsidian_uri(vault: Path, relative_path: str) -> str:
    """Best-effort ``obsidian://open`` deeplink. Vault name defaults to the
    vault folder's basename (Obsidian's default); ``file`` is the vault-relative
    path. Resolves on 修修's machine where the LifeOS vault is registered."""
    from urllib.parse import quote

    return f"obsidian://open?vault={quote(vault.name)}&file={quote(relative_path)}"


def _task_back(
    slug: str,
    week_key: str,
    *,
    err: str | None = None,
    logged: bool = False,
    saved: str | None = None,
):
    from urllib.parse import quote

    url = f"/bridge/weekly/task/{quote(slug)}?week={week_key}"
    if err:
        url += f"&err={err}"
    elif saved:
        url += f"&saved={saved}"
    elif logged:
        url += "&logged=1"
    return RedirectResponse(url, status_code=303)


@page_router.get("/weekly/task/{slug}", response_class=HTMLResponse)
async def weekly_task_detail(
    request: Request,
    slug: str = PathParam(..., min_length=1),
    week: str | None = Query(None),
    err: str | None = Query(None),
    saved: str | None = Query(None),
    logged: int | None = Query(None),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)

    wk_key = _safe_week_key(week)
    vault = get_vault_path()
    idx = WeeklyIndexer(vault)
    task = idx.find_task(slug)
    if task is None:
        # renamed/removed in Obsidian — bounce back to the dashboard with a banner
        return _back(wk_key, "task")

    # 實際🍅 / 準確率 use the D3 union (daily pomodoros[] ∪ this task's timeEntries[]),
    # not timeEntries alone (ADR-040 A2 / Codex §3).
    actual = task_actual(vault, task.slug, task.time_entries)
    actual_pom = actual.total_pomodoros
    accuracy_pct = int(round(100 * actual_pom / task.est_pomodoros)) if task.est_pomodoros else 0

    # A7 (Slice W): the note body is an editable draft surface. Read body + If-Match
    # token from ONE snapshot (PR#812 panel) so the page can't pair a stale body with
    # a fresher token. Body is passed verbatim — the template handles the textarea
    # leading-newline gotcha; no strip().
    try:
        task_body, task_token = read_task_split(vault, task.slug)
    except TaskNotFoundError:
        return _back(wk_key, "task")

    # Calendar-block reschedule/cancel panel (41d) — only for a task already
    # projected to Google Calendar. The indexer's task.scheduled is date-only, so
    # read the raw clock time for the form defaults; the block's 🍅 default is its
    # plan entry on the scheduled date (else the estimate).
    sched = read_task_schedule(vault, task.slug) or {}
    is_linked = bool(sched.get("calendar_event_id"))
    sched_date, sched_time = "", "09:00"
    if is_linked:
        raw = sched.get("scheduled", "")
        try:
            dt = datetime.fromisoformat(raw)
            sched_date, sched_time = dt.date().isoformat(), dt.strftime("%H:%M")
        except ValueError:
            sched_date = raw[:10]
    block_pom = next(
        (a.pomodoros for a in task.plan if task.scheduled and a.date == task.scheduled),
        task.est_pomodoros or 1,
    )
    return _templates.TemplateResponse(
        request,
        "task.html",
        {
            "task": task,
            "actual_pom": actual_pom,
            "accuracy_pct": accuracy_pct,
            "week_key": wk_key,
            "obsidian_uri": _obsidian_uri(vault, task.relative_path),
            "task_body": task_body,
            "task_token": task_token,
            "is_linked": is_linked,
            "sched_date": sched_date,
            "sched_time": sched_time,
            "block_pom": min(block_pom, 20) if block_pom else 1,
            "asset_version": _SHOSHO_ASSET_VERSION,
            "error_msg": _TASK_ERRORS.get(err) if err else None,
            "saved_msg": _TASK_SAVED.get(saved) if saved else None,
            "logged": bool(logged),
        },
    )


@page_router.post("/weekly/task/{slug}/body")
async def weekly_task_body_save(
    slug: str = PathParam(..., min_length=1),
    body: str = Form(""),
    expected_token: str = Form(""),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    """Save the task note body verbatim (A7 / Slice W). Body-only — frontmatter is
    untouched; no LLM authorship (A1). If-Match guarded → conflict banner."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)
    # Normalise the browser's CRLF to the vault's LF so the file stays LF-clean.
    clean = body.replace("\r\n", "\n").replace("\r", "\n")
    try:
        write_task_body(get_vault_path(), slug, clean, expected_token=expected_token)
    except WeeklyConflictError:
        return _task_back(slug, wk_key, err="conflict")
    except TaskNotFoundError:
        # renamed/removed in Obsidian — bounce to the dashboard, mirror the GET path
        return _back(wk_key, "task")
    except WeeklyWriteError as exc:
        logger.warning("weekly_task_body_save: %s", exc)
        return _task_back(slug, wk_key, err="write")
    return _task_back(slug, wk_key, saved="body")


@page_router.post("/weekly/task/{slug}/log")
async def weekly_task_log(
    slug: str = PathParam(..., min_length=1),
    mode: str = Form("pomodoro"),
    # actual elapsed from the live timer; 0/absent → manual full block
    elapsed_seconds: int = Form(0),
    manual: int = Form(0),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)

    nominal = _MODE_MINUTES.get(mode)
    if nominal is None:
        return _task_back(slug, wk_key, err="mode")

    end = _now_taipei()
    # Live timer → actual elapsed (capped at the nominal block, floored at 1 min so
    # a stray 0 doesn't make a non-positive span); manual button → full nominal block.
    if elapsed_seconds and elapsed_seconds > 0:
        seconds = max(60, min(int(elapsed_seconds), nominal * 60))
        start = end - timedelta(seconds=seconds)
    else:
        start = end - timedelta(minutes=nominal)
    try:
        log_time_entry(
            get_vault_path(),
            slug,
            start,
            end,
            mode=mode,
            planned_minutes=nominal,
            completed=True,
            manual=bool(manual),
        )
    except WeeklyWriteError as exc:
        logger.warning("weekly_task_log: %s", exc)
        return _task_back(slug, wk_key, err="log")
    return _task_back(slug, wk_key, logged=True)


# ── Calendar-block reschedule + cancel (ADR-041 41d — D8/D9) ─────────────────
# These act on a task ALREADY projected to Google Calendar (the 41c backlog row
# routes such a task here via its locked note). All three carry the page's
# If-Match ``expected_token`` so a stale action can't clobber an edit made after
# load; the vault write is authoritative and the Google call is best-effort (D2).


@page_router.post("/weekly/task/{slug}/reschedule")
async def weekly_task_reschedule(
    slug: str = PathParam(..., min_length=1),
    entry_date: str = Form(..., min_length=1),
    entry_time: str = Form(..., min_length=1),
    pomodoros: int = Form(...),
    reason: str = Form(""),
    force: int = Form(0),  # ignore a calendar clash and move anyway (D8)
    expected_token: str = Form(""),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    """Move a linked block to a new date/time (ADR-041 D8): relocate plan[] +
    scheduled in the vault, then PATCH the SAME Google event (no orphan)."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)

    ed = _parse_entry_date(entry_date)
    if ed is None:
        return _task_back(slug, wk_key, err="sched_date")
    et = _parse_entry_time(entry_time)
    if et is None:
        return _task_back(slug, wk_key, err="sched_time")
    if not 1 <= pomodoros <= 20:
        return _task_back(slug, wk_key, err="pomodoros")

    vault = get_vault_path()
    task = WeeklyIndexer(vault).find_task(slug)
    if task is None:
        return _back(wk_key, "task")
    start = datetime.combine(ed, et)  # naive Asia/Taipei (D4)
    try:
        outcome = calendar_scheduler.reschedule_block(
            vault,
            slug,
            start=start,
            pomodoros=pomodoros,
            title=task.title,  # derived from the vault, not a client field (Codex §2)
            reason=reason.strip() or None,
            expected_token=expected_token,
            force=bool(force),
        )
    except WeekendReasonRequired:
        return _task_back(slug, wk_key, err="weekend")
    except WeeklyConflictError:
        return _task_back(slug, wk_key, err="conflict")
    except TaskNotFoundError:
        return _back(wk_key, "task")
    except WeeklyWriteError as exc:
        logger.warning("weekly_task_reschedule plan write failed: %s", exc)
        return _task_back(slug, wk_key, err="write")

    status = outcome.calendar_status
    if status == calendar_scheduler.CREATED:
        return _task_back(slug, wk_key, saved="rescheduled")
    if status == calendar_scheduler.CONFLICT:
        return _task_back(slug, wk_key, err="cal_conflict")
    if status == calendar_scheduler.FAILED:  # unlinked-fallback rollback (event deleted)
        return _task_back(slug, wk_key, err="cal_failed")
    return _task_back(slug, wk_key, err="cal_unavailable")  # UNAVAILABLE


@page_router.post("/weekly/task/{slug}/unlink")
async def weekly_task_unlink(
    slug: str = PathParam(..., min_length=1),
    expected_token: str = Form(""),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    """「移出行事曆」 (D9): clear scheduled/calendar_event_id, KEEP plan[], delete
    the Google event (best-effort)."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)
    try:
        outcome = calendar_scheduler.unlink_calendar(
            get_vault_path(), slug, expected_token=expected_token
        )
    except WeeklyConflictError:
        return _task_back(slug, wk_key, err="conflict")
    except TaskNotFoundError:
        return _back(wk_key, "task")
    except WeeklyWriteError as exc:
        logger.warning("weekly_task_unlink: %s", exc)
        return _task_back(slug, wk_key, err="write")
    if outcome.calendar_status == calendar_scheduler.CANCEL_CAL_FAILED:
        return _task_back(slug, wk_key, err="cancel_cal_failed")
    return _task_back(slug, wk_key, saved="unlinked")


@page_router.post("/weekly/task/{slug}/unschedule")
async def weekly_task_unschedule(
    slug: str = PathParam(..., min_length=1),
    expected_token: str = Form(""),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    """「取消排程」 (D9): drop the scheduled-date plan[] entry (unless done) AND
    clear the projection fields, then delete the Google event (best-effort)."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)
    try:
        outcome = calendar_scheduler.cancel_schedule(
            get_vault_path(), slug, expected_token=expected_token
        )
    except WeeklyConflictError:
        return _task_back(slug, wk_key, err="conflict")
    except TaskNotFoundError:
        return _back(wk_key, "task")
    except WeeklyWriteError as exc:
        logger.warning("weekly_task_unschedule: %s", exc)
        return _task_back(slug, wk_key, err="write")
    if outcome.calendar_status == calendar_scheduler.CANCEL_CAL_FAILED:
        return _task_back(slug, wk_key, err="cancel_cal_failed")
    return _task_back(slug, wk_key, saved="unscheduled")
