"""Read-only Stage 6 projection for the Bridge Publish Calendar.

The projection deliberately owns no scheduling or publishing state. Video and
Short truth comes from :mod:`shared.release_store`; Carousel truth comes from
episode-local ``ig-carousel/publish_jobs`` receipts.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from shared.log import get_logger
from shared.release_store import get_release, list_releases
from shared.schemas.carousel_publish import (
    CarouselPublishJobV1,
    CarouselPublishPlatformResult,
    CarouselPublishTargetState,
)

TAIPEI = ZoneInfo("Asia/Taipei")
PODCAST_YOUTUBE_CHANNEL_NAME = "《張修修的不正常人類研究所》"
PODCAST_YOUTUBE_CHANNEL_HANDLE = "@abnormal-human-research"
PODCAST_YOUTUBE_CHANNEL_ID = "UCvipegP35x3-OcAs--PgAig"
PODCAST_YOUTUBE_CHANNEL = f"{PODCAST_YOUTUBE_CHANNEL_NAME} {PODCAST_YOUTUBE_CHANNEL_HANDLE}"

ContentType = Literal["long", "short", "carousel"]
DateBasis = Literal["scheduled", "published"]

_logger = get_logger("nakama.publish_calendar")
_PLATFORM_LABELS = {
    "youtube": "Podcast YouTube",
    "youtube_community": "Podcast YouTube · Community handoff",
    "instagram_reels": "Instagram Reels",
    "instagram": "Instagram Carousel",
    "facebook_reels": "Facebook Page Reels",
    "facebook_page": "Facebook Page Carousel",
}


@dataclass(frozen=True, slots=True)
class CalendarItem:
    """One platform target/result shown in either a dated month or backlog."""

    item_id: str
    episode: str
    content_id: str
    title: str
    content_type: ContentType
    platform: str
    platform_label: str
    status: str
    calendar_at: datetime | None
    date_basis: DateBasis | None
    detail_url: str

    @property
    def local_date(self) -> date | None:
        return self.calendar_at.date() if self.calendar_at else None

    @property
    def local_time_label(self) -> str:
        return self.calendar_at.strftime("%H:%M") if self.calendar_at else "日期未定"

    @property
    def date_basis_label(self) -> str:
        return {
            "scheduled": "排程時間",
            "published": "實際發布時間",
            None: "日期未定",
        }[self.date_basis]


@dataclass(frozen=True, slots=True)
class CalendarDiagnostic:
    code: str
    message: str
    episode: str | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class CalendarProjection:
    items: tuple[CalendarItem, ...]
    diagnostics: tuple[CalendarDiagnostic, ...]

    @property
    def episodes(self) -> tuple[str, ...]:
        return tuple(sorted({item.episode for item in self.items}))


@dataclass(frozen=True, slots=True)
class CalendarDay:
    value: date
    in_month: bool


def parse_month(value: str | None, *, now: datetime | None = None) -> date:
    """Parse a strict ``YYYY-MM`` query, defaulting to the Taipei current month."""

    if value is None or value == "":
        local_now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
        return date(local_now.year, local_now.month, 1)
    if len(value) != 7 or value[4] != "-" or not value[:4].isdigit() or not value[5:].isdigit():
        raise ValueError("month must use YYYY-MM")
    year = int(value[:4])
    month = int(value[5:])
    if year < 1 or not 1 <= month <= 12:
        raise ValueError("month is outside the supported range")
    return date(year, month, 1)


def shift_month(month_start: date, offset: int) -> date:
    ordinal = month_start.year * 12 + month_start.month - 1 + offset
    year, zero_month = divmod(ordinal, 12)
    if year < 1:
        raise ValueError("month is outside the supported range")
    return date(year, zero_month + 1, 1)


def build_month_grid(month_start: date) -> tuple[tuple[CalendarDay, ...], ...]:
    """Return a complete Sunday-first grid, including adjacent-month dates."""

    weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(
        month_start.year, month_start.month
    )
    return tuple(
        tuple(CalendarDay(value=day, in_month=day.month == month_start.month) for day in week)
        for week in weeks
    )


def _aware_taipei(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(TAIPEI)


def _detail_url(prefix: str, episode: str, content_id: str | None = None) -> str:
    encoded_episode = quote(episode, safe="")
    if content_id is None:
        return f"{prefix}/{encoded_episode}/publish"
    return f"{prefix}/{encoded_episode}/{quote(content_id, safe='')}"


def _release_items() -> tuple[list[CalendarItem], list[CalendarDiagnostic]]:
    items: list[CalendarItem] = []
    diagnostics: list[CalendarDiagnostic] = []
    for summary in list_releases():
        episode = str(summary.get("episode", ""))
        cut_id = str(summary.get("cut_id", ""))
        release = get_release(episode, cut_id)
        if release is None:
            diagnostics.append(
                CalendarDiagnostic(
                    code="release_disappeared",
                    message=f"{episode}/{cut_id} 在讀取期間已不存在。",
                    episode=episode or None,
                )
            )
            continue
        content_type = release.get("format")
        if content_type not in {"long", "short"}:
            diagnostics.append(
                CalendarDiagnostic(
                    code="release_format_invalid",
                    message=f"{episode}/{cut_id} 的內容型別無法辨識。",
                    episode=episode or None,
                )
            )
            continue
        for target in release.get("targets", []):
            platform = str(target.get("platform", "unknown"))
            raw_publish_at = target.get("publish_at")
            calendar_at = _aware_taipei(raw_publish_at)
            if raw_publish_at and calendar_at is None:
                diagnostics.append(
                    CalendarDiagnostic(
                        code="release_publish_at_invalid",
                        message=(
                            f"{episode}/{cut_id} 的 {platform} 排程時間"
                            + "不含可信時區，已改列日期未定。"
                        ),
                        episode=episode,
                    )
                )
            target_id = target.get("id", platform)
            title = str(target.get("title") or release.get("work_title") or cut_id)
            items.append(
                CalendarItem(
                    item_id=f"release:{release.get('id', cut_id)}:{target_id}",
                    episode=episode,
                    content_id=cut_id,
                    title=title,
                    content_type=content_type,
                    platform=platform,
                    platform_label=_PLATFORM_LABELS.get(
                        platform, platform.replace("_", " ").title()
                    ),
                    status=str(target.get("status") or "draft"),
                    calendar_at=calendar_at,
                    date_basis="scheduled" if calendar_at else None,
                    detail_url=_detail_url("/bridge/publish", episode, cut_id),
                )
            )
    return items, diagnostics


def _job_order(job: CarouselPublishJobV1) -> tuple[float, float, str]:
    def epoch(value: datetime) -> float:
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.timestamp()

    return epoch(job.updated_at), epoch(job.created_at), job.job_id


def _target_state(job: CarouselPublishJobV1, platform: str) -> CarouselPublishTargetState | None:
    return next((state for state in job.target_states if state.platform == platform), None)


def _successful_result(
    job: CarouselPublishJobV1, platform: str
) -> CarouselPublishPlatformResult | None:
    state = _target_state(job, platform)
    if state and state.checkpoint and state.checkpoint.status == "published":
        return state.checkpoint
    return next(
        (
            result
            for result in job.results
            if result.platform == platform and result.status == "published"
        ),
        None,
    )


def _current_platform_status(job: CarouselPublishJobV1, platform: str) -> str:
    state = _target_state(job, platform)
    if state is not None:
        return state.status
    result = next((result for result in job.results if result.platform == platform), None)
    return result.status if result is not None else job.status


def _carousel_title(job: CarouselPublishJobV1) -> str:
    first_line = next((line.strip() for line in job.caption.splitlines() if line.strip()), "")
    return first_line[:120] or f"Carousel {job.source_revision}"


def _carousel_items(
    episodes_root: Path | None,
) -> tuple[list[CalendarItem], list[CalendarDiagnostic]]:
    if episodes_root is None:
        return [], [
            CalendarDiagnostic(
                code="carousel_root_unconfigured",
                message="PODCAST_EPISODES_ROOT 尚未設定；目前只顯示影片與 Short 資料。",
            )
        ]
    if not episodes_root.is_dir():
        return [], [
            CalendarDiagnostic(
                code="carousel_root_unreadable",
                message="Podcast episode root 無法讀取；目前只顯示影片與 Short 資料。",
                source=str(episodes_root),
            )
        ]

    diagnostics: list[CalendarDiagnostic] = []
    grouped: dict[tuple[str, str, str], list[CarouselPublishJobV1]] = {}
    for episode_dir in sorted(
        (path for path in episodes_root.iterdir() if path.is_dir()), key=lambda p: p.name
    ):
        jobs_dir = episode_dir / "ig-carousel" / "publish_jobs"
        if not jobs_dir.is_dir():
            continue
        for path in sorted(jobs_dir.glob("pj-*.json")):
            try:
                job = CarouselPublishJobV1.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, ValueError) as error:
                _logger.warning(
                    "Publish Calendar skipped unreadable Carousel job %s: %s", path, error
                )
                diagnostics.append(
                    CalendarDiagnostic(
                        code="carousel_job_invalid",
                        message=(
                            f"{episode_dir.name} 有一筆 Carousel 發布紀錄無法讀取，"
                            "其他資料仍可使用。"
                        ),
                        episode=episode_dir.name,
                        source=path.name,
                    )
                )
                continue
            for target in job.targets:
                grouped.setdefault(
                    (episode_dir.name, job.request_fingerprint, target.platform), []
                ).append(job)

    items: list[CalendarItem] = []
    for (episode, fingerprint, platform), jobs in grouped.items():
        latest = max(jobs, key=_job_order)
        successful = [result for job in jobs if (result := _successful_result(job, platform))]
        published = max(
            (result for result in successful if _aware_taipei(result.completed_at)),
            key=lambda result: _aware_taipei(result.completed_at),
            default=None,
        )
        calendar_at = _aware_taipei(published.completed_at) if published else None
        if successful and published is None:
            diagnostics.append(
                CalendarDiagnostic(
                    code="carousel_completed_at_invalid",
                    message=(
                        f"{episode} 的 {platform} 發布 checkpoint 不含可信時區，"
                        + "已改列日期未定。"
                    ),
                    episode=episode,
                )
            )
        if latest.status == "superseded" and published is None:
            continue
        status = "published" if published else _current_platform_status(latest, platform)
        items.append(
            CalendarItem(
                item_id=f"carousel:{episode}:{fingerprint}:{platform}",
                episode=episode,
                content_id=latest.source_revision,
                title=_carousel_title(latest),
                content_type="carousel",
                platform=platform,
                platform_label=_PLATFORM_LABELS.get(platform, platform.replace("_", " ").title()),
                status=status,
                calendar_at=calendar_at,
                date_basis="published" if calendar_at else None,
                detail_url=_detail_url("/bridge/ig-cards", episode),
            )
        )
    return items, diagnostics


def build_publish_calendar(episodes_root: Path | None) -> CalendarProjection:
    """Load the complete, read-only Stage 6 projection without external APIs."""

    release_items, release_diagnostics = _release_items()
    carousel_items, carousel_diagnostics = _carousel_items(episodes_root)
    items = sorted(
        [*release_items, *carousel_items],
        key=lambda item: (
            item.calendar_at is None,
            item.calendar_at or datetime.max.replace(tzinfo=TAIPEI),
            item.episode,
            item.content_id,
            item.platform,
        ),
    )
    return CalendarProjection(
        items=tuple(items),
        diagnostics=tuple([*release_diagnostics, *carousel_diagnostics]),
    )
