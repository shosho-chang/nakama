"""Google Calendar client — OAuth token 管理 + 事件 CRUD + freebusy 衝突偵測。

使用 OAuth 2.0 Desktop app flow（user-consent，不用 service account）。
Token 持久化於 ``data/google_calendar_token.json``，refresh 時用 filelock 避免併發寫入。

所有事件強制套用 ``Asia/Taipei`` 時區。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from filelock import FileLock
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from shared.log import get_logger

logger = get_logger("nakama.google_calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TIMEZONE = "Asia/Taipei"

_DATA_DIR = Path(os.environ.get("NAKAMA_DATA_DIR", "data"))
_TOKEN_PATH = _DATA_DIR / "google_calendar_token.json"
_CREDS_PATH = _DATA_DIR / "google_oauth_credentials.json"
_LOCK_PATH = _DATA_DIR / "google_calendar_token.lock"


class GoogleCalendarAuthError(Exception):
    """授權失效，需使用者重跑 consent 流程。"""


@dataclass
class CalendarEvent:
    """Calendar event 的精簡表示。"""

    id: str
    title: str
    start: str  # ISO 8601 含 TZ offset
    end: str
    html_link: str
    description: str = ""


# ── Credentials / Service ─────────────────────────────────────────


def _get_credentials() -> Credentials:
    """讀 token.json，expired 時自動 refresh + 寫回，回傳可用的 Credentials。"""
    if not _TOKEN_PATH.exists():
        raise GoogleCalendarAuthError(
            f"Token 不存在於 {_TOKEN_PATH}。"
            " 請在本機執行 `python scripts/google_calendar_auth.py` 後把 token 搬到此位置。"
        )

    lock = FileLock(str(_LOCK_PATH), timeout=10)
    with lock:
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), SCOPES)
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                raise GoogleCalendarAuthError(
                    f"Token refresh 失敗：{e}。"
                    " 請重跑 `python scripts/google_calendar_auth.py` 重新授權。"
                ) from e
            _TOKEN_PATH.write_text(creds.to_json())
            logger.info("Google Calendar token refreshed & persisted")
        elif not creds.valid:
            raise GoogleCalendarAuthError("Token 無效且無 refresh_token。請重新跑 consent 流程。")
    return creds


def _get_service():
    """取得 Google Calendar API service client（每次呼叫新建，不 cache）。"""
    creds = _get_credentials()
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


# ── Public API ────────────────────────────────────────────────────


# Private extended-property key linking a calendar event back to a LifeOS task
# block (ADR-041 D7). Value is f"{task_slug}@{YYYY-MM-DD}" — stable per task+day, so
# a double-submit/retry finds the existing event instead of duplicating it, and
# rollback/reschedule can locate it without storing the id elsewhere.
IDEMPOTENCY_PROP = "lifeos_task"


def _day_window_from_key(key: str) -> tuple[datetime, datetime] | None:
    """Derive a whole-day [00:00, +1d) search window from a ``{slug}@{YYYY-MM-DD}``
    idempotency key (ADR-041 v3 V3b — the task-day, not just one block's time span,
    so the key works as a per-task-day uniqueness lookup). None if the key has no
    parseable trailing date."""
    from datetime import time as _time
    from datetime import timedelta as _td

    _, _, datepart = key.rpartition("@")
    try:
        d = date.fromisoformat(datepart.strip()[:10])
    except ValueError:
        return None
    tz = ZoneInfo(TIMEZONE)
    start = datetime.combine(d, _time(0, 0), tzinfo=tz)
    return start, start + _td(days=1)


def find_event_by_idempotency_key(
    key: str,
    *,
    time_min: datetime | None = None,
    time_max: datetime | None = None,
) -> CalendarEvent | None:
    """The event tagged ``extendedProperties.private.lifeos_task == key`` in the
    window, or None. Used to dedupe create + locate for reschedule/rollback (D7).

    ADR-041 v3 (V3b): when ``time_min``/``time_max`` are omitted, the window
    defaults to the **whole day** encoded in the key (``{slug}@{date}``) so the
    lookup is a true per-task-day uniqueness check — not just the proposed block's
    span (which would miss an existing same-day block at a different time)."""
    if time_min is None or time_max is None:
        win = _day_window_from_key(key)
        if win is not None:
            time_min, time_max = win
        elif time_min is None or time_max is None:
            raise ValueError(f"find_event_by_idempotency_key: undatable key {key!r} needs a window")
    service = _get_service()
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=_dt_to_rfc3339(time_min),
            timeMax=_dt_to_rfc3339(time_max),
            privateExtendedProperty=f"{IDEMPOTENCY_PROP}={key}",
            singleEvents=True,
            maxResults=1,
        )
        .execute()
    )
    items = result.get("items", [])
    return _parse_event(items[0]) if items else None


def create_event(
    *,
    title: str,
    start: str,
    end: str,
    description: str = "",
    check_conflict: bool = True,
    idempotency_key: str | None = None,
) -> CalendarEvent | list[CalendarEvent]:
    """建立事件。

    若 ``check_conflict=True`` 且時段有精確重疊的既有事件，**不建立**，改回傳
    衝突事件列表供呼叫端決定（問使用者或 force=True 重試）。

    **All-day 事件 (ADR-041 v3-E):** 當 ``start``/``end`` 是 date-only（``YYYY-MM-DD``，
    無 ``T``）時建立整天事件（Google ``start.date``/``end.date``，``end`` 為 exclusive
    的隔天）。整天事件不做衝突偵測（不佔特定時段）。

    ``idempotency_key`` (ADR-041 D7)：給定時先查同 key 的既有事件，有就直接回傳該
    事件（不重複建立 — 防雙送/retry），並把 key 寫進 ``extendedProperties.private``
    供日後 reschedule/rollback 定位。其值不參與衝突判定（自己不算自己衝突）。
    """
    all_day = _is_date_only(start)
    if idempotency_key is not None:
        if all_day:
            day = _parse_iso(_ensure_tz_iso(start))
            existing = find_event_by_idempotency_key(
                idempotency_key, time_min=day, time_max=day + timedelta(days=1)
            )
        else:
            existing = find_event_by_idempotency_key(
                idempotency_key,
                time_min=_parse_iso(_ensure_tz_iso(start)),
                time_max=_parse_iso(_ensure_tz_iso(end)),
            )
        if existing is not None:
            return existing

    if check_conflict and not all_day:  # all-day events don't occupy a time slot
        conflicts = find_conflicts(start, end)
        if conflicts:
            return conflicts

    service = _get_service()
    body: dict = {"summary": title, "description": description}
    if all_day:  # date-only → Google all-day event (end.date is exclusive)
        body["start"] = {"date": start}
        body["end"] = {"date": end}
    else:
        body["start"] = {"dateTime": _ensure_tz_iso(start), "timeZone": TIMEZONE}
        body["end"] = {"dateTime": _ensure_tz_iso(end), "timeZone": TIMEZONE}
    if idempotency_key is not None:
        body["extendedProperties"] = {"private": {IDEMPOTENCY_PROP: idempotency_key}}
    created = service.events().insert(calendarId="primary", body=body).execute()
    return _parse_event(created)


def list_events(
    *,
    time_min: datetime,
    time_max: datetime,
    max_results: int = 20,
    query: str | None = None,
) -> list[CalendarEvent]:
    """列出時段內的事件，依開始時間排序。"""
    service = _get_service()
    kwargs: dict = {
        "calendarId": "primary",
        "timeMin": _dt_to_rfc3339(time_min),
        "timeMax": _dt_to_rfc3339(time_max),
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if query:
        kwargs["q"] = query
    result = service.events().list(**kwargs).execute()
    return [_parse_event(e) for e in result.get("items", [])]


def update_event(
    event_id: str,
    *,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    idempotency_key: str | None = None,
) -> CalendarEvent:
    """patch 事件。未給的欄位不變。

    ``idempotency_key`` (ADR-041 v3 V3a): re-stamp ``extendedProperties.private.
    lifeos_task`` — used when a **date-change reschedule** moves the block to a new
    day, so the date-scoped key (``{slug}@{date}``) tracks the event's new date
    instead of going stale. PATCHing the SAME event preserves its identity / Meet
    link / attendees / RSVPs (vs delete+recreate, which the panel rejected)."""
    service = _get_service()
    body: dict = {}
    if title is not None:
        body["summary"] = title
    if description is not None:
        body["description"] = description
    if start is not None:
        body["start"] = (
            {"date": start}
            if _is_date_only(start)
            else {"dateTime": _ensure_tz_iso(start), "timeZone": TIMEZONE}
        )
    if end is not None:
        body["end"] = (
            {"date": end}
            if _is_date_only(end)
            else {"dateTime": _ensure_tz_iso(end), "timeZone": TIMEZONE}
        )
    if idempotency_key is not None:
        body["extendedProperties"] = {"private": {IDEMPOTENCY_PROP: idempotency_key}}
    updated = service.events().patch(calendarId="primary", eventId=event_id, body=body).execute()
    return _parse_event(updated)


def delete_event(event_id: str) -> None:
    """刪除事件。**idempotent**：事件已不存在（404 / 410 已刪除）視為成功 —
    取消動作重送或事件已在 Google 端被刪不該回報失敗（ADR-041 41d panel）。"""
    service = _get_service()
    try:
        service.events().delete(calendarId="primary", eventId=event_id).execute()
    except HttpError as exc:
        if exc.resp is not None and exc.resp.status in (404, 410):
            logger.info(
                "delete_event(%s): already gone (%s) — treating as deleted",
                event_id,
                exc.resp.status,
            )
            return
        raise


def find_conflicts(start: str, end: str) -> list[CalendarEvent]:
    """回傳與 [start, end) 精確重疊的事件（不含剛好首尾相接的情況）。

    不用 ``freebusy.query`` — 它需要額外的 ``calendar.freebusy`` scope。改用
    ``events.list`` 直接撈時段內事件，再用 ``_overlaps`` 精確過濾。只吃 ``calendar.events``
    scope 就夠。

    **整天事件 (all-day) 一律略過**：它們不佔特定時段（與 ``create_event`` line ~189、
    ``find_free_slots`` 同一語意），不該擋有時間的事件。且整天事件的 ``start`` 是 date-only
    （``YYYY-MM-DD``，naive），若丟進 ``_overlaps`` 與帶 TZ 的 aware datetime 比對會拋
    ``can't compare offset-naive and offset-aware datetimes``。
    """
    start_iso = _ensure_tz_iso(start)
    end_iso = _ensure_tz_iso(end)
    events = list_events(
        time_min=_parse_iso(start_iso),
        time_max=_parse_iso(end_iso),
        max_results=20,
    )
    # events.list 會把首尾相接的事件、以及整天事件也抓進來；整天事件不佔時段直接略過
    # （也避開 naive date-only vs aware datetime 的比對 crash），再用精確 overlap 過濾。
    return [
        e
        for e in events
        if not _is_date_only(e.start) and _overlaps(e.start, e.end, start_iso, end_iso)
    ]


def find_free_slots(
    day: date,
    duration_minutes: int,
    *,
    near: str | None = None,
    day_start_hour: int = 8,
    day_end_hour: int = 22,
    max_slots: int = 3,
) -> list[tuple[str, str]]:
    """Suggest up to ``max_slots`` free ``[start, end]`` windows of ``duration_minutes``
    within ``day``'s working hours (``day_start_hour``..``day_end_hour`` Asia/Taipei),
    computed from existing **timed** events (all-day events don't occupy a slot).
    Ordered by proximity to ``near`` (an ISO datetime; default = chronological).
    ADR-041 v3-F — the free-slot suggestions Nami offers on a clash."""
    tz = ZoneInfo(TIMEZONE)
    win_start = datetime(day.year, day.month, day.day, day_start_hour, 0, tzinfo=tz)
    win_end = datetime(day.year, day.month, day.day, day_end_hour, 0, tzinfo=tz)
    dur = timedelta(minutes=duration_minutes)

    busy: list[tuple[datetime, datetime]] = []
    for e in list_events(time_min=win_start, time_max=win_end, max_results=50):
        if _is_date_only(e.start):  # all-day → doesn't block a time slot
            continue
        try:
            s = _parse_iso(_ensure_tz_iso(e.start))
            en = _parse_iso(_ensure_tz_iso(e.end))
        except ValueError:
            continue
        busy.append((max(s, win_start), min(en, win_end)))
    busy.sort()

    merged: list[tuple[datetime, datetime]] = []
    for s, en in busy:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], en))
        else:
            merged.append((s, en))

    gaps: list[datetime] = []  # free-window START times big enough for dur
    cursor = win_start
    for s, en in merged:
        if s - cursor >= dur:
            gaps.append(cursor)
        cursor = max(cursor, en)
    if win_end - cursor >= dur:
        gaps.append(cursor)

    if near:
        try:
            target = _parse_iso(_ensure_tz_iso(near))
            gaps.sort(key=lambda g: abs((g - target).total_seconds()))
        except ValueError:
            pass
    return [
        (g.isoformat(timespec="seconds"), (g + dur).isoformat(timespec="seconds"))
        for g in gaps[:max_slots]
    ]


def find_events_by_title(
    query: str,
    *,
    time_min: datetime,
    time_max: datetime,
) -> list[CalendarEvent]:
    """在時段內 by title 模糊搜尋事件。Google 的 q param 會搜 summary/description。"""
    return list_events(time_min=time_min, time_max=time_max, query=query, max_results=50)


# ── Helpers ───────────────────────────────────────────────────────


def _parse_event(g: dict) -> CalendarEvent:
    start = g.get("start", {})
    end = g.get("end", {})
    return CalendarEvent(
        id=g["id"],
        title=g.get("summary", ""),
        start=start.get("dateTime") or start.get("date", ""),
        end=end.get("dateTime") or end.get("date", ""),
        html_link=g.get("htmlLink", ""),
        description=g.get("description", ""),
    )


def _is_date_only(s: str) -> bool:
    """True for an all-day date string (``YYYY-MM-DD``, no ``T``) — ADR-041 v3-E."""
    return bool(s) and "T" not in s


def _ensure_tz_iso(s: str) -> str:
    """保證 ISO 8601 字串帶時區資訊；naive datetime 補 +08:00。"""
    if not s:
        return s
    # Z 或 ±HH:MM 結尾代表已有時區
    if s.endswith("Z"):
        return s
    tail = s[-6:]
    if len(tail) >= 6 and (tail[0] in "+-") and tail[3] == ":":
        return s
    return f"{s}+08:00"


def _dt_to_rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(TIMEZONE))
    return dt.isoformat()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """兩個時段是否重疊（不含剛好接邊）。全部已是 ISO 帶 TZ。"""
    a1 = _parse_iso(a_start)
    a2 = _parse_iso(a_end)
    b1 = _parse_iso(b_start)
    b2 = _parse_iso(b_end)
    return a1 < b2 and b1 < a2
