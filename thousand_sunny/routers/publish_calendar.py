"""Authenticated, read-only Stage 6 Publish Calendar surface."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from shared.publish_calendar import (
    PODCAST_YOUTUBE_CHANNEL_HANDLE,
    PODCAST_YOUTUBE_CHANNEL_ID,
    PODCAST_YOUTUBE_CHANNEL_NAME,
    CalendarItem,
    build_month_grid,
    build_publish_calendar,
    parse_month,
    shift_month,
)
from thousand_sunny.auth import check_auth

page_router = APIRouter(prefix="/bridge/publish", tags=["bridge-publish-calendar"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)
_WEEKDAY_LABELS = ("週日", "週一", "週二", "週三", "週四", "週五", "週六")


def _asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    digest = hashlib.sha1()
    for name in (
        "tokens.css",
        "bridge.css",
        "bridge-pages.css",
        "publish-calendar.css",
    ):
        asset = static_dir / name
        if asset.is_file():
            digest.update(asset.read_bytes())
    return digest.hexdigest()[:8]


def _calendar_url(month_start: date, episode: str) -> str:
    return "/bridge/publish/calendar?" + urlencode(
        {"month": month_start.strftime("%Y-%m"), "episode": episode}
    )


def _shift_url(month_start: date, episode: str, offset: int) -> str | None:
    try:
        return _calendar_url(shift_month(month_start, offset), episode)
    except (OverflowError, ValueError):
        return None


def _episode_is_safe(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 120
        and value not in {".", ".."}
        and not any(character in value for character in ("/", "\\", ":", "\x00"))
    )


def _items_for_episode(items: tuple[CalendarItem, ...], episode: str) -> list[CalendarItem]:
    return [item for item in items if episode == "all" or item.episode == episode]


@page_router.get("/calendar", response_class=HTMLResponse, response_model=None)
def publish_calendar_page(
    request: Request,
    month: str | None = None,
    episode: str = "all",
    nakama_auth: str | None = Cookie(None),
) -> HTMLResponse | RedirectResponse:
    if not check_auth(nakama_auth):
        relative_next = request.url.path
        if request.url.query:
            relative_next += f"?{request.url.query}"
        return RedirectResponse(f"/login?next={quote(relative_next, safe='')}", status_code=302)
    try:
        month_start = parse_month(month)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    root_value = os.environ.get("PODCAST_EPISODES_ROOT", "").strip()
    projection = build_publish_calendar(Path(root_value) if root_value else None)
    episode_options = projection.episodes
    if episode != "all" and (not _episode_is_safe(episode) or episode not in episode_options):
        raise HTTPException(status_code=400, detail="unknown episode filter")

    filtered = _items_for_episode(projection.items, episode)
    dated = [
        item
        for item in filtered
        if item.calendar_at is not None
        and item.calendar_at.year == month_start.year
        and item.calendar_at.month == month_start.month
    ]
    backlog = [item for item in filtered if item.calendar_at is None]
    by_date: dict[str, list[CalendarItem]] = defaultdict(list)
    for item in dated:
        assert item.local_date is not None
        by_date[item.local_date.isoformat()].append(item)
    agenda_days = [
        {"date": day, "items": by_date[day.isoformat()]}
        for day in sorted({item.local_date for item in dated if item.local_date is not None})
    ]
    diagnostics = [
        diagnostic
        for diagnostic in projection.diagnostics
        if episode == "all" or diagnostic.episode in {None, episode}
    ]
    return _templates.TemplateResponse(
        request,
        "publish_calendar.html",
        {
            "month_start": month_start,
            "month_value": month_start.strftime("%Y-%m"),
            "month_label": f"{month_start.year} 年 {month_start.month:02d} 月",
            "episode": episode,
            "episode_options": episode_options,
            "weeks": build_month_grid(month_start),
            "weekday_labels": _WEEKDAY_LABELS,
            "items_by_date": by_date,
            "agenda_days": agenda_days,
            "dated_count": len(dated),
            "backlog": backlog,
            "diagnostics": diagnostics,
            "podcast_youtube": {
                "name": PODCAST_YOUTUBE_CHANNEL_NAME,
                "handle": PODCAST_YOUTUBE_CHANNEL_HANDLE,
                "channel_id": PODCAST_YOUTUBE_CHANNEL_ID,
            },
            "previous_url": _shift_url(month_start, episode, -1),
            "next_url": _shift_url(month_start, episode, 1),
            "asset_version": _asset_version(),
        },
    )
