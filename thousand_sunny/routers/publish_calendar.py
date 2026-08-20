"""Authenticated Stage 6 Pipeline and Campaign Anchor planning surface."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Cookie, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from scripts.podcast_carousel_publish_job import (
    PublishJobTransitionError,
    publish_job_path,
    set_publish_job_campaign_anchor,
)
from shared.heartbeat import get_heartbeat
from shared.publish_calendar import (
    PODCAST_YOUTUBE_CHANNEL_HANDLE,
    PODCAST_YOUTUBE_CHANNEL_ID,
    PODCAST_YOUTUBE_CHANNEL_NAME,
    TAIPEI,
    CalendarItem,
    build_month_grid,
    build_publish_calendar,
    future_short_requires_due_worker,
    parse_month,
    shift_month,
    short_due_worker_health,
)
from shared.release_store import set_release_campaign_anchor
from thousand_sunny.auth import check_auth

page_router = APIRouter(prefix="/bridge/publish", tags=["bridge-publish-calendar"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)
_WEEKDAY_LABELS = ("週日", "週一", "週二", "週三", "週四", "週五", "週六")
_PHASE_LABELS = {
    "needs_review": "待審核",
    "ready_to_schedule": "待排程",
    "scheduled": "已排程",
    "in_progress": "發布中",
    "attention": "需處理",
    "published": "已發布",
}
_SHORT_DUE_WORKER_JOB = "usopp-short-due-dispatcher"
_WORKER_HEALTH_LABELS = {
    "never_seen": "尚未執行",
    "online": "在線",
    "stale": "逾時",
    "failing": "失敗",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


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


def _identity_is_safe(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 160
        and value not in {".", ".."}
        and not any(character in value for character in ("/", "\\", ":", "\x00"))
    )


def _campaign_anchor_from_form(operation: str, value: str) -> datetime | None:
    if operation == "clear":
        return None
    if operation != "set" or not value.strip():
        raise HTTPException(status_code=400, detail="Campaign Anchor is required")
    try:
        local = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise HTTPException(status_code=400, detail="invalid Campaign Anchor") from error
    if local.tzinfo is not None:
        raise HTTPException(status_code=400, detail="Campaign Anchor must be Taipei local time")
    return local.replace(tzinfo=TAIPEI).astimezone(UTC)


def _return_url(month: str, episode: str) -> str:
    try:
        month_start = parse_month(month)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if episode != "all" and not _episode_is_safe(episode):
        raise HTTPException(status_code=400, detail="unsafe episode filter")
    return _calendar_url(month_start, episode)


def _carousel_package_root(episode: str) -> Path:
    if not _episode_is_safe(episode):
        raise HTTPException(status_code=400, detail="unsafe episode identity")
    root_value = os.environ.get("PODCAST_EPISODES_ROOT", "").strip()
    if not root_value:
        raise HTTPException(status_code=503, detail="PODCAST_EPISODES_ROOT is not configured")
    root = Path(root_value)
    candidate = root / episode / "ig-carousel"
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise HTTPException(status_code=400, detail="unsafe episode identity") from error
    if not candidate.is_dir():
        raise HTTPException(status_code=404, detail="Carousel package not found")
    return candidate


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
    phase_counts = tuple(
        {
            "phase": phase,
            "label": label,
            "count": sum(item.phase == phase for item in filtered),
        }
        for phase, label in _PHASE_LABELS.items()
    )
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
    observed_now = _utc_now()
    worker_health = short_due_worker_health(
        get_heartbeat(_SHORT_DUE_WORKER_JOB),
        now=observed_now,
    )
    due_worker_required = future_short_requires_due_worker(projection.items, now=observed_now)
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
            "phase_counts": phase_counts,
            "phase_labels": _PHASE_LABELS,
            "short_due_worker": worker_health,
            "short_due_worker_label": _WORKER_HEALTH_LABELS[worker_health.state],
            "short_due_worker_warning": (due_worker_required and worker_health.state != "online"),
            "taipei": TAIPEI,
            "podcast_youtube": {
                "name": PODCAST_YOUTUBE_CHANNEL_NAME,
                "handle": PODCAST_YOUTUBE_CHANNEL_HANDLE,
                "channel_id": PODCAST_YOUTUBE_CHANNEL_ID,
            },
            "previous_url": _shift_url(month_start, episode, -1),
            "next_url": _shift_url(month_start, episode, 1),
            "calendar_return_url": _calendar_url(month_start, episode),
            "asset_version": _asset_version(),
        },
    )


@page_router.post("/calendar/release/{episode}/{cut_id}/schedule")
def schedule_release_campaign_anchor(
    episode: str,
    cut_id: str,
    operation: str = Form("set"),
    campaign_anchor_local: str = Form(""),
    expected_anchor_token: str = Form(...),
    month: str = Form(...),
    return_episode: str = Form("all"),
    nakama_auth: str | None = Cookie(None),
) -> RedirectResponse:
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    if not _episode_is_safe(episode) or not _identity_is_safe(cut_id):
        raise HTTPException(status_code=400, detail="unsafe Release identity")
    return_url = _return_url(month, return_episode)
    anchor = _campaign_anchor_from_form(operation, campaign_anchor_local)
    try:
        set_release_campaign_anchor(
            episode,
            cut_id,
            anchor,
            expected_anchor_token=expected_anchor_token,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RedirectResponse(return_url, status_code=303)


@page_router.post("/calendar/carousel/{episode}/{job_id}/schedule")
def schedule_carousel_campaign_anchor(
    episode: str,
    job_id: str,
    operation: str = Form("set"),
    campaign_anchor_local: str = Form(""),
    expected_anchor_token: str = Form(...),
    month: str = Form(...),
    return_episode: str = Form("all"),
    nakama_auth: str | None = Cookie(None),
) -> RedirectResponse:
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    if not _episode_is_safe(episode) or not _identity_is_safe(job_id):
        raise HTTPException(status_code=400, detail="unsafe Carousel identity")
    return_url = _return_url(month, return_episode)
    anchor = _campaign_anchor_from_form(operation, campaign_anchor_local)
    try:
        path = publish_job_path(_carousel_package_root(episode), job_id)
        set_publish_job_campaign_anchor(
            path,
            campaign_anchor_at=anchor,
            expected_anchor_token=expected_anchor_token,
        )
    except (OSError, ValueError, PublishJobTransitionError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RedirectResponse(return_url, status_code=303)
