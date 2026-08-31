"""Bridge Weekly Dashboard — Tier B (vault-as-substrate, read-only Slice 0).

Surface:
  - ``GET /bridge/weekly`` — the focused-week dashboard (ADR-039). Defaults to
    the current Sunday-start week; ``?week=YYYY-MM-DD`` (a start-Sunday key)
    navigates. Read-only: aggregates real vault tasks + pomodoro session logs;
    no writes (plan[]/review writes land in Slice 1/2).

Auth: HMAC cookie (mirrors bridge_digests / bridge_projects).
"""

from __future__ import annotations

import contextvars
import hashlib
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared import calendar_scheduler
from shared.config import get_vault_path
from shared.log import get_logger
from shared.pomodoro_aggregator import TAIPEI, task_actual
from shared.project_index import list_projects
from shared.project_writer import (
    TASKS_DIR,
    ProjectWriteError,
    create_task,
    reassign_task_project,
    rename_task,
)
from shared.weekly_indexer import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
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
    log_time_entry,
    migrate_legacy_projection,
    read_task_split,
    remove_plan_entry,
    set_plan_entry_done,
    set_task_done,
    set_task_meta,
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
    for css in (
        "tokens.css",
        "bridge.css",
        "bridge-pages.css",
        "pomodoro-dock.css",
        "bridge-weekly.css",
        "theme.js",
        "bridge-weekly.js",  # the page links it with ?v= — a JS-only fix must cache-bust too
        "focus-music.js",  # task page pomodoro dock (修修 2026-08-25)
    ):
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
    "rename_invalid": "新名稱無效（空白或含路徑分隔符號）。",
    "rename_exists": "已有同名任務，請換一個名稱。",
    # calendar scheduling (ADR-041 41c) — plan is authoritative; calendar is best-effort.
    # v3-I.2: web conflicts pop a Web UI modal (強排 / 改時間 / 取消排程); this banner is the
    # no-JS fallback only and never mentions Slack (Slack is for Nami-initiated scheduling).
    "cal_conflict": (
        "此時段與 Google 行事曆既有事件衝突——尚未排入（不寫計畫、不建事件）。改個時間重送即可。"
    ),
    "cal_unavailable": (
        "已寫入本週計畫，但 Google 行事曆暫時無法連動（授權或網路）——稍後可重新排入以補建事件。"
    ),
    "cal_failed": "排程寫入後寫回失敗，行事曆事件已回滾——請重試。",
    "cancel_cal_failed": (
        "已從本週計畫移除，但刪除 Google 行事曆事件失敗——請到 Google 行事曆手動移除，或稍後重試。"
    ),
    # weekly-file (Journals/Weekly 🟡) write errors — ADR-040 Slice 2
    "conflict": (
        "週檔在你開啟頁面後被改動（Obsidian / Syncthing / 另一分頁），"
        "已擋下這次覆寫——請重新整理頁面再存。"
    ),
    "write": "寫入週檔失敗，可能被其他程式鎖定——請稍後重試。",
    # 新增任務 (v3-H Slice 2)
    "title": "任務標題不能空白。",
    "task_exists": "已有同名任務（同專案）——換個標題，或先到 Obsidian 確認。",
    "time_needs_date": "指定開始時間需要同時指定日期——任務尚未建立。",
}

_SAVED_MSGS = {
    "task_new": "✓ 已新增任務。",
    "task_new_scheduled": "✓ 已新增任務並排入行程。",
    "task_deleted": "✓ 已刪除任務。",
    "plan": "✓ 已儲存本週計畫。",
    "review": "✓ 已存週回顧。",
    "top3": "✓ 已更新本週重要任務。",
    "targets": "✓ 已更新本週目標。",
    "notes": "✓ 已存隨手筆記。",
    "status": "✓ 已更新本週狀態。",
    "scheduled": "✓ 已排入計畫並建立行事曆事件。",
    "unscheduled": "✓ 已取消這天的安排（含行事曆事件）。",
    "renamed": "✓ 已重新命名任務（已同步行事曆事件標題）。",
    "project": "✓ 已更新任務的專案歸屬（檔名與行事曆事件標題已同步）。",
    "project_partial": (
        "✓ 已更新專案歸屬，但部分行事曆事件標題未能同步——稍後可在 Google 行事曆手動調整。"
    ),
    "renamed_partial": (
        "✓ 已重新命名任務，但部分行事曆事件標題未能同步——稍後可在 Google 行事曆手動調整。"
    ),
}


def _group_by_project(tasks) -> list[tuple[str, list]]:
    """Cluster tasks by their 戰線 (ADR-068 auto-grouping — no toggle).

    Groups keep first-appearance order (so the tab's existing sort still decides
    which thread leads); rows keep their original order within a group; standalone
    tasks (``project == ""``) always come last. Returns ``[(project_name, rows)]``
    with ``""`` as the standalone bucket's key."""
    groups: dict[str, list] = {}
    for t in tasks:
        groups.setdefault(t.project, []).append(t)
    named = [(name, rows) for name, rows in groups.items() if name]
    standalone = [("", groups[""])] if "" in groups else []
    return named + standalone


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
        # No-JS fallback banner; with JS the bridge-weekly.js modal supersedes it.
        error_msg = (
            f"此時段與 Google 行事曆 {n} 個既有事件衝突——"
            "尚未排入（不寫計畫、不建事件）。改個時間重送即可。"
        )

    # N529 — 讀 5am 持久化的每日回顧 bundle（免重算）；缺/壞檔 → None（卡片不顯示）。
    from agents.robin.daily_review import load_review_bundle  # noqa: PLC0415

    _bundle = load_review_bundle(vault)
    review_summary = (
        {
            "candidate_count": len(_bundle.candidates),
            "fleeting_count": len(_bundle.fleeting),
            "review_date": _bundle.review_date,
            "weekly_sweep": _bundle.weekly_sweep,
        }
        if _bundle is not None
        else None
    )

    return _templates.TemplateResponse(
        request,
        "weekly.html",
        {
            "view": view,
            # ADR-068 (修修 2026-08-31): 今日/整週 tab 自動按戰線聚攏 — 同戰線
            # 任務相鄰、戰線名視覺區隔、獨立任務殿後；組內保留原排序。
            "today_groups": _group_by_project(view.today_tasks),
            "week_groups": _group_by_project(view.tasks),
            "daily_review": review_summary,
            # If-Match token for the weekly-file forms (ADR-040 Slice 2)
            "weekly_token": weekly_file_token(vault, wk.file_key),
            "asset_version": _SHOSHO_ASSET_VERSION,
            "error_msg": error_msg,
            "saved_msg": _SAVED_MSGS.get(saved) if saved else None,
            # 新增任務 form (v3-H Slice 2): project picker + category picker sources
            "all_projects": [e.name for e in list_projects(vault) if not e.archived],
            "categories": [(c, CATEGORY_LABELS[c]) for c in CATEGORY_ORDER],
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


# v3-H Slice 3: when a plan/done action is fired from a Project Brief tab's task row,
# the macro posts ``from_project={slug}``. This request-scoped var lets the shared
# redirect helpers land back on the project page instead of the dashboard, without
# threading the flag through ~20 call sites. ContextVar is request-task-isolated in
# async, and only the four macro routes set it — every other caller sees "".
_FROM_PROJECT: contextvars.ContextVar[str] = contextvars.ContextVar("from_project", default="")


def _project_back(
    slug: str,
    *,
    err: str | None = None,
    saved: str | None = None,
    n: int = 0,
    focus: str | None = None,
    extra: str | None = None,
) -> RedirectResponse:
    """Redirect back to a Project detail page (v3-H Slice 3; ADR-068 dropped the
    Brief tab — the target is now the read-only 戰線 detail, which ignores the
    query params). Dead in practice since the workspace retirement (nothing posts
    a non-empty ``from_project`` any more); kept until the from_project plumbing
    is ripped out with the Weekly grouping PR."""
    from urllib.parse import quote

    url = f"/bridge/projects/{quote(slug)}"
    params: list[str] = []
    if err:
        params.append(f"err={err}")
        if n:
            params.append(f"n={n}")
    elif saved:
        params.append(f"saved={saved}")
    if extra:
        params.append(extra)
    if focus:
        params.append(f"focus={quote(focus)}")
    if params:
        url += "?" + "&".join(params)
    if focus:
        url += f"#task-{quote(focus)}"
    return RedirectResponse(url, status_code=303)


def _back(
    week_key: str,
    err: str | None = None,
    *,
    slug: str | None = None,
    saved: str | None = None,
    n: int = 0,
    focus: bool = False,
    extra: str | None = None,
) -> RedirectResponse:
    """Redirect back to the weekly dashboard. ``slug`` appends a ``#task-<slug>``
    fragment so the merged 排入 action lands back ON the row it edited (v3-B
    stay-in-place — JS re-opens + scrolls + restores tab/scroll). ``focus`` (v3-I)
    additionally appends ``?focus=<slug>`` so the JS switches to the 全部 pane,
    opens that row and focuses its 排入 date field — used right after 新增任務 so 修修
    can add a time chip immediately. Mutually exclusive err / saved; ``n`` is the
    conflict count for the cal_conflict banner. When fired from a Project Brief tab
    (``_FROM_PROJECT`` set) lands there instead."""
    if _FROM_PROJECT.get():
        return _project_back(
            _FROM_PROJECT.get(),
            err=err,
            saved=saved,
            n=n,
            focus=slug if focus else None,
            extra=extra,
        )
    from urllib.parse import quote

    url = f"/bridge/weekly?week={week_key}"
    if err:
        url += f"&err={err}"
        if n:
            url += f"&n={n}"
    elif saved:
        url += f"&saved={saved}"
    if focus and slug:
        url += f"&focus={quote(slug)}"
    if extra:
        url += f"&{extra}"
    if slug:
        url += f"#task-{quote(slug)}"
    return RedirectResponse(url, status_code=303)


def _conflict_modal_params(task_slug, entry_date, entry_time, pomodoros, reason, conflicts) -> str:
    """v3-I.2: build the query string that drives the Web UI conflict modal
    (bridge-weekly.js). Carries the attempted slot (so 強排 can re-POST force=1 and 改時間
    can prefill), the clashing event titles, and best-effort nearby free slots. Replaces
    the old Slack escalation — web conflicts never touch Slack now (修修)."""
    from urllib.parse import quote

    slot_txt = ""
    try:  # best-effort — a Google hiccup just drops the suggestions, never blocks
        from shared import google_calendar

        near = f"{entry_date}T{entry_time or '09:00'}:00"
        slots = google_calendar.find_free_slots(
            date.fromisoformat(entry_date), pomodoros * 30, near=near
        )
        slot_txt = "、".join(s[11:16] for s, _ in slots[:4])
    except Exception as exc:  # noqa: BLE001
        logger.warning("conflict free-slot lookup failed for %s: %s", task_slug, exc)

    clash_txt = "、".join(c.title for c in conflicts[:3]) or "既有事件"
    parts = [
        f"cf_slug={quote(task_slug)}",
        f"cf_date={quote(entry_date)}",
        f"cf_time={quote(entry_time or '')}",
        f"cf_pom={pomodoros}",
        f"cf_with={quote(clash_txt)}",
    ]
    if reason:
        parts.append(f"cf_reason={quote(reason)}")
    if slot_txt:
        parts.append(f"cf_slots={quote(slot_txt)}")
    return "&".join(parts)


def _parse_entry_date(entry_date: str) -> date | None:
    try:
        return date.fromisoformat(entry_date.strip())
    except ValueError:
        return None


@page_router.post("/weekly/task/new")
async def weekly_task_new(
    title: str = Form(...),
    project: str = Form(""),
    category: str = Form("work"),
    priority: str = Form("normal"),
    est_pomodoros: int = Form(4),
    entry_date: str = Form(""),  # 建立時直接排程 — blank ⇒ bare create (排入 later)
    entry_time: str = Form(""),  # blank ⇒ all-day (mirrors the merged 排入 form)
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    """v3-H Slice 2: create a new task from the dashboard. Project optional (blank =
    standalone). Shares the dual-write creator (``create_task``) with Nami's Slack
    ``create_task`` so chat + web emit identical files.

    修修 (2026-08-23): the form now carries an optional 日期/時間 — when a date is
    given, the freshly-created task goes straight through ``schedule_entry`` (the
    same plan+calendar path as the row's merged 排入), so 建立+排程 is one submit.
    Blank date keeps the original bare-create + ?focus flow."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)
    name = title.strip()
    if not name:
        return _back(wk_key, "title")
    if not 1 <= est_pomodoros <= 20:
        return _back(wk_key, "pomodoros")
    if category not in CATEGORY_LABELS:
        category = "work"
    if priority not in {"low", "normal", "high"}:
        priority = "normal"
    # Validate the optional schedule fields BEFORE the vault write — a bad date/time
    # rejects the whole submit with nothing created (no half-made task to clean up).
    ed = None
    et = None
    if entry_date.strip():
        ed = _parse_entry_date(entry_date)
        if ed is None:
            return _back(wk_key, "date")
    if entry_time.strip():
        if ed is None:
            return _back(wk_key, "time_needs_date")
        et = _parse_entry_time(entry_time)
        if et is None:
            return _back(wk_key, "time")
    vault = get_vault_path()
    try:
        created = create_task(
            vault_root=vault,
            project_slug=project.strip() or None,
            task_name=name,
            estimated_pomodoros=est_pomodoros,
            priority=priority,
            category=category,
        )
    except ProjectWriteError:
        return _back(wk_key, "task_exists")
    new_slug = unicodedata.normalize("NFC", created.stem)
    if ed is None:
        # v3-I: land on the new row with its 排入 chip focused so 修修 adds a time
        # immediately (修修: bare-create is fine, just jump me to the chip).
        return _back(wk_key, saved="task_new", slug=new_slug, focus=True)
    # 排程 — identical semantics to /weekly/plan (v3-E: time ⇒ timed block, blank ⇒
    # all-day event; v3-G: land on the scheduled date's week so the chip is visible).
    dest_week = week_for_date(ed).file_key
    start = datetime.combine(ed, et or datetime.min.time())  # naive Asia/Taipei (D4)
    try:
        outcome = calendar_scheduler.schedule_entry(
            vault,
            new_slug,
            start=start,
            pomodoros=est_pomodoros,
            title=name,
            all_day=et is None,
            reason=None,
            force=False,
        )
    except WeekendReasonRequired:
        # The task exists; only the schedule was refused (weekend needs a reason and
        # this form carries none) — land on the row's 排入 chip, which has the field.
        return _back(wk_key, "weekend", slug=new_slug, focus=True)
    except (TaskNotFoundError, WeeklyWriteError) as exc:
        logger.warning("weekly_task_new schedule_entry: %s", exc)
        return _back(wk_key, "task", slug=new_slug, focus=True)
    st = outcome.calendar_status
    if st == calendar_scheduler.CREATED:
        return _back(dest_week, saved="task_new_scheduled", slug=new_slug)
    if st == calendar_scheduler.CONFLICT:
        # Task created, schedule pre-checked and refused — pop the same conflict modal
        # as 排入 (強排 / 改時間 / 取消排程 all operate on the now-existing row).
        extra = _conflict_modal_params(
            new_slug, entry_date.strip(), entry_time.strip(), est_pomodoros, None, outcome.conflicts
        )
        return _back(
            wk_key, "cal_conflict", slug=new_slug, focus=True, n=len(outcome.conflicts), extra=extra
        )
    if st == calendar_scheduler.UNAVAILABLE:
        return _back(dest_week, "cal_unavailable", slug=new_slug)
    return _back(dest_week, "cal_failed", slug=new_slug)  # FAILED — event rolled back


@page_router.post("/weekly/plan")
async def weekly_plan_add(
    task_slug: str = Form(..., min_length=1),
    entry_date: str = Form(..., min_length=1),
    entry_time: str = Form(""),  # v3 — optional; blank ⇒ plan-only (no calendar)
    pomodoros: int = Form(...),
    reason: str = Form(""),
    force: int = Form(0),
    week: str = Form(""),
    from_task: int = Form(0),  # v3-C — fired from the task page → land back there
    from_project: str = Form(""),  # v3-H — fired from a Project Brief tab → land back there
    nakama_auth: str | None = Cookie(None),
):
    """v3 merged 「排入」 (ADR-041 v3 V2): one action writes the ``plan[]`` entry AND,
    when a time is given, projects it to a Google event — for any date. Blank time
    ⇒ plan-only (today's behaviour). A per-task legacy fold runs first (V4 — so a v2
    single-event task migrates before its first multi-block write). Lands back on
    the row via ``#task-{slug}`` (stay-in-place), or on the task page when fired from
    there (``from_task`` — v3-C parity). Untokened, like the other dashboard plan
    routes — the per-task optimistic lock is the deferred #819 work."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    _FROM_PROJECT.set(from_project.strip())
    wk_key = _safe_week_key(week)
    ft = bool(from_task)

    ed = _parse_entry_date(entry_date)
    if ed is None:
        return _plan_back(ft, task_slug, wk_key, err="date")
    if not 1 <= pomodoros <= 20:
        return _plan_back(ft, task_slug, wk_key, err="pomodoros")
    # v3-G: the dashboard is week-scoped, so a 排入 onto a date in ANOTHER week left
    # the new chip invisible on the current week (修修: "entries disappear"). Land the
    # dashboard on the SCHEDULED date's week instead of the viewed one, so the chip is
    # right there. (The task page is week-agnostic; this just sets its back-context.)
    dest_week = week_for_date(ed).file_key
    rsn = reason.strip() or None
    vault = get_vault_path()

    try:
        migrate_legacy_projection(vault, task_slug)  # fold any v2 task-level link first (V4)
    except TaskNotFoundError:
        return _back(wk_key, "task")
    except WeeklyWriteError as exc:  # non-fatal — the write below will surface a real error
        logger.warning("weekly_plan migrate_legacy_projection: %s", exc)

    et = _parse_entry_time(entry_time) if entry_time.strip() else None
    if entry_time.strip() and et is None:
        return _plan_back(ft, task_slug, wk_key, err="time")

    # v3-E: ONE storage mode — every 排入 projects a Google event. Time given ⇒ a timed
    # block; blank ⇒ an all-day event (no conflict check). Plan-only is retired.
    all_day = et is None
    task = WeeklyIndexer(vault).find_task(task_slug)
    title = task.title if task is not None else task_slug
    start = datetime.combine(ed, et or datetime.min.time())  # naive Asia/Taipei (D4)
    try:
        outcome = calendar_scheduler.schedule_entry(
            vault,
            task_slug,
            start=start,
            pomodoros=pomodoros,
            title=title,
            all_day=all_day,
            reason=rsn,
            force=bool(force),
        )
    except WeekendReasonRequired:
        return _plan_back(ft, task_slug, wk_key, err="weekend")
    except TaskNotFoundError:
        return _back(wk_key, "task")
    except WeeklyWriteError as exc:
        logger.warning("weekly_plan schedule_entry: %s", exc)
        return _back(wk_key, "task")

    # v3-G: from here the plan entry is written (vault-first), so jump to its week
    # (dest_week) regardless of the calendar status — the chip/banner shows in context.
    st = outcome.calendar_status
    if st == calendar_scheduler.CREATED:
        return _plan_back(ft, task_slug, dest_week, saved="scheduled")
    if st == calendar_scheduler.CONFLICT:
        # v3-I.2: web-initiated conflicts resolve in a Web UI modal (NOT Slack — 修修:
        # only Slack-initiated scheduling via Nami should bounce back through Slack).
        # The plan entry already stands (vault-first); the modal (bridge-weekly.js, keyed
        # on ?err=cal_conflict&cf_*) offers 強排 (re-POST force=1) / 改時間 (reopen the
        # chip) / 取消排程 (keep the plan-only entry, no event). Pass the clash + nearby
        # free slots so the modal can guide the pick.
        extra = _conflict_modal_params(
            task_slug, entry_date, entry_time, pomodoros, rsn, outcome.conflicts
        )
        return _plan_back(
            ft, task_slug, dest_week, err="cal_conflict", n=len(outcome.conflicts), extra=extra
        )
    if st == calendar_scheduler.UNAVAILABLE:
        return _plan_back(ft, task_slug, dest_week, err="cal_unavailable")
    return _plan_back(ft, task_slug, dest_week, err="cal_failed")  # FAILED — event rolled back


@page_router.post("/weekly/plan/remove")
async def weekly_plan_remove(
    task_slug: str = Form(..., min_length=1),
    entry_date: str = Form(..., min_length=1),
    week: str = Form(""),
    from_task: int = Form(0),  # v3-C — fired from the task page → land back there
    from_project: str = Form(""),  # v3-H — fired from a Project Brief tab
    nakama_auth: str | None = Cookie(None),
):
    """The plain ✕ on an UNLINKED plan chip — plan-only removal, `done`-safe, never
    touches Google (ADR-041 v3 V3d). A linked chip uses /weekly/plan/cancel instead."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    _FROM_PROJECT.set(from_project.strip())
    wk_key = _safe_week_key(week)
    ft = bool(from_task)

    ed = _parse_entry_date(entry_date)
    if ed is None:
        return _plan_back(ft, task_slug, wk_key, err="date")

    try:
        remove_plan_entry(get_vault_path(), task_slug, ed)  # done-safe (v3)
    except WeeklyWriteError as exc:
        logger.warning("weekly_plan_remove: %s", exc)
        return _back(wk_key, "task")
    return _plan_back(ft, task_slug, wk_key)


@page_router.post("/weekly/plan/cancel")
async def weekly_plan_cancel(
    task_slug: str = Form(..., min_length=1),
    entry_date: str = Form(..., min_length=1),
    week: str = Form(""),
    from_task: int = Form(0),  # v3-C — fired from the task page → land back there
    from_project: str = Form(""),  # v3-H — fired from a Project Brief tab
    nakama_auth: str | None = Cookie(None),
):
    """The 🗑 on a LINKED plan chip (ADR-041 v3 V3d / confirm-gated in the UI): drop
    the entry (`done`-safe) AND delete its Google event, best-effort. Distinct from
    the plain ✕ so a calendar deletion is never silent."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    _FROM_PROJECT.set(from_project.strip())
    wk_key = _safe_week_key(week)
    ft = bool(from_task)

    ed = _parse_entry_date(entry_date)
    if ed is None:
        return _plan_back(ft, task_slug, wk_key, err="date")

    try:
        outcome = calendar_scheduler.cancel_entry(
            get_vault_path(), task_slug, day=ed, drop_plan=True
        )
    except TaskNotFoundError:
        return _back(wk_key, "task")
    except WeeklyWriteError as exc:
        logger.warning("weekly_plan_cancel: %s", exc)
        return _back(wk_key, "task")
    if outcome.calendar_status == calendar_scheduler.CANCEL_CAL_FAILED:
        return _plan_back(ft, task_slug, wk_key, err="cancel_cal_failed")
    return _plan_back(ft, task_slug, wk_key, saved="unscheduled")


@page_router.post("/weekly/task/{slug}/done")
async def weekly_task_done(
    slug: str = PathParam(..., min_length=1),
    done: int = Form(0),  # 1 = mark done, 0 = re-open
    week: str = Form(""),
    from_project: str = Form(""),  # v3-H — fired from a Project Brief tab
    nakama_auth: str | None = Cookie(None),
):
    """Toggle a task's done state from a dashboard / daily-bullet checkbox (#2).
    A one-click status flip (no If-Match — mirrors plan add/remove); bounces back
    to the dashboard (or the Project Brief tab via ``from_project``) so the
    struck-through row / 🍅 rollups re-render."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    _FROM_PROJECT.set(from_project.strip())
    wk_key = _safe_week_key(week)
    try:
        set_task_done(get_vault_path(), slug, bool(done))
    except TaskNotFoundError:
        return _back(wk_key, "task")
    except WeeklyWriteError as exc:
        logger.warning("weekly_task_done: %s", exc)
        return _back(wk_key, "task")
    # v3-I follow-up (修修): anchor on the toggled row so the dashboard lands back in
    # place (the JS save-state restores tab/scroll; the #task- anchor re-opens the row).
    return _back(wk_key, slug=slug)


@page_router.post("/weekly/task/{slug}/meta")
async def weekly_task_meta(
    slug: str = PathParam(..., min_length=1),
    category: str = Form(""),
    priority: str = Form(""),
    est_pomodoros: int = Form(-1),  # -1 = field absent (dashboard row form omits it)
    week: str = Form(""),
    from_task: int = Form(0),
    from_project: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    """v3-I follow-up (修修): edit a task's category + priority + 預估🍅 from the dashboard
    row / task page editors. Writes only the valid fields; stays in place (the row's
    #anchor + save-state, or the task page / Brief tab when fired from there). The task
    page carries ``est_pomodoros`` (0–40, 0 clears the estimate); the row omits it."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    _FROM_PROJECT.set(from_project.strip())
    wk_key = _safe_week_key(week)
    ft = bool(from_task)
    try:
        set_task_meta(
            get_vault_path(),
            slug,
            category=category if category in CATEGORY_LABELS else None,
            priority=priority if priority in {"low", "normal", "high"} else None,
            est_pomodoros=est_pomodoros if 0 <= est_pomodoros <= 40 else None,
        )
    except TaskNotFoundError:
        return _back(wk_key, "task")
    except WeeklyWriteError as exc:
        logger.warning("weekly_task_meta: %s", exc)
        return _back(wk_key, "task")
    return _plan_back(ft, slug, wk_key)


@page_router.post("/weekly/task/{slug}/day-done")
async def weekly_task_day_done(
    slug: str = PathParam(..., min_length=1),
    entry_date: str = Form(..., min_length=1),
    done: int = Form(0),
    week: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    """v3-I follow-up (修修): the DAILY bullet checkbox marks 'that day's work on this
    task is done' — it flips the plan[] entry's done flag for ``entry_date``, NOT the
    task's overall status (that's /done). The daily item crosses out but stays; the task
    remains open. Stay-in-place (the JS save-state on .wk-box-form restores tab/scroll)."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    wk_key = _safe_week_key(week)
    ed = _parse_entry_date(entry_date)
    if ed is None:
        return _back(wk_key, "date")
    try:
        set_plan_entry_done(get_vault_path(), slug, ed, bool(done))
    except TaskNotFoundError:
        return _back(wk_key, "task")
    except WeeklyWriteError as exc:
        logger.warning("weekly_task_day_done: %s", exc)
        return _back(wk_key, "task")
    return _back(wk_key)


@page_router.post("/weekly/task/{slug}/project")
async def weekly_task_project(
    slug: str = PathParam(..., min_length=1),
    project: str = Form(""),  # blank = 獨立任務（解除歸屬）
    week: str = Form(""),
    from_task: int = Form(0),
    from_project: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    """改派任務所屬專案（修修 2026-08-29 稽核：過去只能在建立當下決定）。

    歸屬是雙寫的（檔名前綴 + ``projects:``），所以這會搬檔＝換 slug，並同步每個
    已連動的 Google 事件標題 —— 全部交給 ``reassign_task_project``（內部複用
    ``rename_task``）。成功後落在新 slug 的列上（舊的已經不存在了）。"""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    _FROM_PROJECT.set(from_project.strip())
    wk_key = _safe_week_key(week)
    ft = bool(from_task)
    try:
        new_path, cal_errors = reassign_task_project(
            vault_root=get_vault_path(),
            task_slug=slug,
            project_slug=project.strip() or None,
        )
    except ProjectWriteError as exc:
        err = "project_exists" if "already exists" in str(exc) else "project_invalid"
        return _plan_back(ft, slug, wk_key, err=err)
    new_slug = unicodedata.normalize("NFC", new_path.stem)
    saved = "project_partial" if cal_errors else "project"
    if ft:  # 任務頁：slug 變了，跳到新的任務頁
        return _task_back(new_slug, wk_key, saved=saved)
    return _back(wk_key, saved=saved, slug=new_slug)


@page_router.post("/weekly/task/{slug}/rename")
async def weekly_task_rename(
    slug: str = PathParam(..., min_length=1),
    new_title: str = Form(...),
    week: str = Form(""),
    from_task: int = Form(0),  # v3-C parity — fired from the task page → land back there
    from_project: str = Form(""),  # v3-H — fired from a Project Brief tab
    nakama_auth: str | None = Cookie(None),
):
    """v3-I.3: rename a task (修修 typo'd a title and couldn't fix it). A true rename —
    frontmatter title + the filename (slug) + re-titled Google events — via the shared
    ``rename_task`` writer. The slug changes, so success lands on the NEW row (focused);
    a name collision / invalid name surfaces as a banner on the OLD row. Shared by the
    dashboard, the Project Brief tab and the task page (same three surfaces as 排入)."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    _FROM_PROJECT.set(from_project.strip())
    wk_key = _safe_week_key(week)
    ft = bool(from_task)
    title = new_title.strip()
    if not title:
        return _plan_back(ft, slug, wk_key, err="rename_invalid")
    try:
        new_path, cal_errors = rename_task(
            vault_root=get_vault_path(), old_slug=slug, new_title=title
        )
    except ProjectWriteError as exc:
        err = "rename_exists" if "already exists" in str(exc) else "rename_invalid"
        return _plan_back(ft, slug, wk_key, err=err)
    new_slug = unicodedata.normalize("NFC", new_path.stem)
    saved = "renamed_partial" if cal_errors else "renamed"
    if ft:  # task page: jump to the renamed task's own page (its slug changed)
        return _task_back(new_slug, wk_key, saved=saved)
    # dashboard / Brief: stay in place — land on the renamed row via #task-<new_slug>
    # (no focus: focus=True is the 新增任務 jump, which switches tab + scrolls; a rename
    # should keep 修修 exactly where he was — the row just re-renders with the new name).
    return _back(wk_key, saved=saved, slug=new_slug)


@page_router.post("/weekly/task/{slug}/delete")
async def weekly_task_delete(
    slug: str = PathParam(..., min_length=1),
    week: str = Form(""),
    from_project: str = Form(""),
    nakama_auth: str | None = Cookie(None),
):
    """Delete a task: cancel linked Google Calendar events best-effort, then send
    the vault file to the recycle bin. Shared by the dashboard task rows and the
    Project Brief tab (``from_project`` redirects back to the project page)."""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/weekly", status_code=302)
    _FROM_PROJECT.set(from_project.strip())
    wk_key = _safe_week_key(week)
    vault = get_vault_path()
    slug = unicodedata.normalize("NFC", slug)

    # Cancel any linked Google Calendar events before the file disappears.
    task = WeeklyIndexer(vault).find_task(slug)
    if task is not None:
        from shared import google_calendar

        for entry in task.plan:
            if entry.is_linked:
                try:
                    google_calendar.delete_event(entry.event_id)
                except Exception:  # noqa: BLE001 — vault delete proceeds regardless
                    logger.warning(
                        "weekly_task_delete: cal event %s not removed for task %s",
                        entry.event_id,
                        slug,
                    )

    task_path = vault / TASKS_DIR / f"{slug}.md"
    if task_path.exists():
        from shared.discard_service import _send_to_recycle_bin

        _send_to_recycle_bin(task_path)
    return _back(wk_key, saved="task_deleted")


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
    "rename_invalid": "新名稱無效（空白或含路徑分隔符號）。",
    "rename_exists": "已有同名任務，請換一個名稱。",
    # 改派專案 (修修 2026-08-29 稽核)
    "project_exists": "目標專案下已有同名任務——請先改名再改派。",
    "project_invalid": "專案名稱無效（含路徑分隔符號）。",
    # calendar reschedule / cancel (ADR-041 41d) — vault authoritative, calendar best-effort
    "sched_date": "排程日期格式無效。",
    "sched_time": "排程時間格式無效。",
    # merged 排入 (ADR-041 v3-C) — same keys the dashboard /weekly/plan uses
    "date": "排程日期格式無效。",
    "time": "排程時間格式無效。",
    "pomodoros": "番茄數需介於 1–20。",
    "weekend": "週末排程需填寫原因（D9）——已取消這次改期。",
    "cal_conflict": (  # v3-I.2/4: web conflicts never go to Slack; pre-checked → nothing written
        "此時段與 Google 行事曆既有事件衝突——尚未排入（不寫計畫、不建事件）。改個時間重送即可。"
    ),
    "cal_unavailable": (
        "已更新本週計畫，但 Google 行事曆暫時無法連動（授權或網路）——稍後可在此重試。"
    ),
    "cancel_cal_failed": (
        "已更新本週計畫，但刪除 Google 行事曆事件失敗——請到 Google 行事曆手動移除，或稍後重試。"
    ),
    "cal_failed": "寫入後寫回失敗，行事曆事件已回滾——請重試。",
}

_TASK_SAVED = {
    "body": "✓ 已儲存筆記。",
    "scheduled": "✓ 已排入並建立行事曆事件。",
    "rescheduled": "✓ 已改期並更新行事曆事件。",
    "unlinked": "✓ 已移出行事曆（保留本週計畫）。",
    "unscheduled": "✓ 已取消這天的安排。",
    "renamed": "✓ 已重新命名任務（已同步行事曆事件標題）。",
    "renamed_partial": (
        "✓ 已重新命名任務，但部分行事曆事件標題未能同步——稍後可在 Google 行事曆手動調整。"
    ),
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
    n: int = 0,
    extra: str | None = None,
):
    from urllib.parse import quote

    url = f"/bridge/weekly/task/{quote(slug)}?week={week_key}"
    if err:
        url += f"&err={err}"
        if n:
            url += f"&n={n}"
    elif saved:
        url += f"&saved={saved}"
    elif logged:
        url += "&logged=1"
    if extra:
        url += f"&{extra}"
    return RedirectResponse(url, status_code=303)


def _plan_back(
    from_task: bool,
    slug: str,
    week_key: str,
    *,
    err: str | None = None,
    saved: str | None = None,
    n: int = 0,
    extra: str | None = None,
) -> RedirectResponse:
    """Land a unified /weekly/plan{,/remove,/cancel} action back where it was fired:
    the task page (``from_task`` — v3-C parity), the Project Brief tab (``_FROM_PROJECT``
    — v3-H), or the dashboard row (``#task-{slug}`` anchor). Same routes serve all three
    surfaces (修修: stop the surfaces diverging). ``extra`` carries the cal-conflict
    modal params (v3-I.2)."""
    if _FROM_PROJECT.get():
        return _project_back(_FROM_PROJECT.get(), err=err, saved=saved, n=n, extra=extra)
    if from_task:
        return _task_back(slug, week_key, err=err, saved=saved, n=n, extra=extra)
    return _back(week_key, err, slug=slug, saved=saved, n=n, extra=extra)


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

    # 修修 (2026-08-24): 補記日期 defaults to the task's most recent scheduled day
    # (≤ today) — reviewing a past task then pressing +1 lands the 🍅 on the planned
    # session without touching the picker. No past schedule → today (原行為)。
    today = today_taipei()
    past_sched = [d for d in task.schedule_dates() if d <= today]
    backfill_default = max(past_sched) if past_sched else today

    # Calendar scheduling reads/writes per-entry here, identical to the dashboard row
    # (修修: stop the two surfaces diverging). The template iterates ``task.plan`` —
    # each entry carries its own ``is_linked`` / ``time_label`` (v3-A) — and posts the
    # SAME unified /weekly/plan{,/remove,/cancel} routes with ``from_task=1``. No
    # bespoke single-event panel state to compute.
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
            "today_iso": today.isoformat(),  # default date for the merged 排入 picker
            # 補記日期 default: latest scheduled day ≤ today (修修 2026-08-24)
            "backfill_default_iso": backfill_default.isoformat(),
            # 改派專案的下拉選單來源（修修 2026-08-29 稽核）
            "all_projects": [e.name for e in list_projects(vault) if not e.archived],
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
    entry_date: str = Form(""),  # 手動補記：這 block 是哪一天做的（YYYY-MM-DD）；空/今天 = 現在
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
    # 手動補記可指定「哪一天做的」(entry_date)：週儀表板用 endTime 歸屬週次，不給日期就一律記
    # 今天，補上週的工作會漏進本週 (修修回報的 bug)。給了過去的日期就把 block 錨在那天的 23:59，
    # 讓 log_time_entry 的「往回堆疊」把連續 N 顆疊在當天內、不跨到前一天。未來/今天 → 用現在。
    ed = _parse_entry_date(entry_date) if entry_date.strip() else None
    # 修修 (2026-08-24): 手動 +1 的時間戳要跟著任務「之前安排的時間」走 — review 舊任務補
    # 番茄時蓋 now 會灌進本週數字。目標日（entry_date，留空＝今天）若有 TIMED plan entry，
    # 錨在其排程結束時間（clamp 到 now，不產生未來戳記）；「往回堆疊」讓連續 N 顆由後往前
    # 填滿排程時窗。無 timed entry → 維持原行為（過去→23:59、今天→now）。
    planned_end: datetime | None = None
    if manual:
        target = ed if (ed is not None and ed <= end.date()) else end.date()
        t = WeeklyIndexer(get_vault_path()).find_task(slug)
        e = t.entry_on(target) if t is not None else None
        if e is not None and e.end and "T" in e.end:
            try:
                planned_end = datetime.fromisoformat(e.end)
            except ValueError:
                planned_end = None
            if planned_end is not None and planned_end.tzinfo is None:
                planned_end = planned_end.replace(tzinfo=TAIPEI)  # 舊資料可能無 offset
    if planned_end is not None:
        end = min(planned_end, end)
    elif ed is not None and ed < end.date():
        end = datetime.combine(ed, time(23, 59), tzinfo=TAIPEI)
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
