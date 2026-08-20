"""Read-only Stage 6 projection for the Bridge Publish Calendar.

The projection deliberately owns no scheduling or publishing state. Video and
Short truth comes from :mod:`shared.release_store`; Carousel truth comes from
episode-local ``ig-carousel/publish_jobs`` receipts.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from shared.heartbeat import Heartbeat
from shared.log import get_logger
from shared.release_store import get_release, get_release_campaign_anchor, list_releases
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
SHORT_DUE_WORKER_STALE_AFTER = timedelta(minutes=5)
OUTCOME_RECONCILER_STALE_AFTER = timedelta(minutes=15)
_NATIVE_OUTCOME_PLATFORMS = frozenset({"youtube", "facebook_reels"})

ContentType = Literal["long", "short", "carousel"]
DateBasis = Literal["scheduled", "published"]
PipelinePhase = Literal[
    "needs_review",
    "ready_to_schedule",
    "scheduled",
    "in_progress",
    "attention",
    "published",
]

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
class PlatformTargetView:
    platform: str
    platform_label: str
    status: str
    error: str | None = None
    permalink: str | None = None
    receipt_id: str | None = None
    retryable: bool = False
    confirmation_overdue: bool = False


@dataclass(frozen=True, slots=True)
class CalendarItem:
    """One Release or deduplicated Carousel lineage in the Calendar Projection."""

    item_id: str
    episode: str
    content_id: str
    title: str
    content_type: ContentType
    targets: tuple[PlatformTargetView, ...]
    phase: PipelinePhase
    calendar_at: datetime | None
    date_basis: DateBasis | None
    detail_url: str
    schedule_kind: Literal["release", "carousel"]
    schedule_id: str
    schedule_editable: bool
    expected_anchor_token: str
    schedule_disabled_reason: str | None = None
    execution_ready: bool = False
    execution_reason: str = "請先開啟發布審核頁確認。"

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

    @property
    def campaign_anchor_local_value(self) -> str:
        if self.date_basis != "scheduled" or self.calendar_at is None:
            return ""
        return self.calendar_at.strftime("%Y-%m-%dT%H:%M")

    @property
    def published_count(self) -> int:
        return sum(
            target.status == "published" for target in self.targets if target.status != "ineligible"
        )

    @property
    def eligible_target_count(self) -> int:
        return sum(target.status != "ineligible" for target in self.targets)

    @property
    def progress_label(self) -> str:
        return f"{self.published_count}/{self.eligible_target_count} published"


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


@dataclass(frozen=True, slots=True)
class ShortDueWorkerHealth:
    state: Literal["never_seen", "online", "stale", "failing"]
    last_run_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int
    last_error: str | None


@dataclass(frozen=True, slots=True)
class OutcomeReconcilerHealth:
    state: Literal["never_seen", "online", "stale", "failing"]
    last_run_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int
    last_error: str | None


@dataclass(frozen=True, slots=True)
class ShortExecutionReadiness:
    """Server-renderable readiness for the existing Short approval route."""

    ready: bool
    reason: str


def short_execution_readiness(release: dict) -> ShortExecutionReadiness:
    """Check local artifacts and reviewed copy without touching platform adapters."""

    if release.get("format") != "short":
        return ShortExecutionReadiness(False, "只有 Short 可從月曆直接核准並投遞。")
    if not Path(str(release.get("file_path") or "")).is_file():
        return ShortExecutionReadiness(False, "成品檔不存在；請重新產出或檢查素材路徑。")
    targets = release.get("targets") or []
    primary = next((target for target in targets if target.get("platform") == "youtube"), None)
    if primary is None:
        return ShortExecutionReadiness(False, "缺少主要 YouTube Target；請重新執行 publish_prep。")
    if not str(primary.get("title") or "").strip():
        return ShortExecutionReadiness(False, "缺少主要標題；請先檢查素材與文案。")
    if not str(primary.get("description") or "").strip():
        return ShortExecutionReadiness(False, "缺少主要描述；請先檢查素材與文案。")
    statuses = {str(target.get("status") or "draft") for target in targets}
    known_statuses = {
        "draft",
        "approved",
        "uploading",
        "uploaded",
        "published",
        "failed",
        "ineligible",
    }
    if statuses - known_statuses:
        return ShortExecutionReadiness(False, "Release Target 狀態無法執行；請先檢查發布紀錄。")
    if "failed" in statuses:
        return ShortExecutionReadiness(False, "有平台投遞失敗；請只重試失敗平台。")
    eligible_statuses = statuses - {"ineligible"}
    if "approved" in eligible_statuses:
        if "draft" in eligible_statuses:
            return ShortExecutionReadiness(
                False, "Target 核准狀態不一致；請等待 worker 認領或檢查發布紀錄。"
            )
        return ShortExecutionReadiness(False, "已核准；等待 worker 認領或投遞啟動中。")
    if eligible_statuses != {"draft"}:
        return ShortExecutionReadiness(False, "此 Short 已開始或完成投遞；不可再次整組核准。")
    return ShortExecutionReadiness(True, "素材與主要文案已齊，可核准並投遞。")


def short_due_worker_health(
    row: Heartbeat | None,
    *,
    now: datetime | None = None,
    stale_after: timedelta = SHORT_DUE_WORKER_STALE_AFTER,
) -> ShortDueWorkerHealth:
    """Project one durable heartbeat into explicit due-worker readiness."""

    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("worker health clock must be timezone-aware")
    if stale_after <= timedelta(0):
        raise ValueError("worker stale threshold must be positive")
    if row is None:
        return ShortDueWorkerHealth("never_seen", None, None, 0, None)
    if row.last_status == "fail" or row.consecutive_failures > 0:
        state = "failing"
    elif current.astimezone(UTC) - row.last_run_at.astimezone(UTC) > stale_after:
        state = "stale"
    else:
        state = "online"
    return ShortDueWorkerHealth(
        state,
        row.last_run_at,
        row.last_success_at,
        row.consecutive_failures,
        row.last_error,
    )


def outcome_reconciler_health(
    row: Heartbeat | None,
    *,
    now: datetime | None = None,
    stale_after: timedelta = OUTCOME_RECONCILER_STALE_AFTER,
) -> OutcomeReconcilerHealth:
    """Project the Outcome Reconciler heartbeat without merging worker identities."""

    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("reconciler health clock must be timezone-aware")
    if stale_after <= timedelta(0):
        raise ValueError("reconciler stale threshold must be positive")
    if row is None:
        return OutcomeReconcilerHealth("never_seen", None, None, 0, None)
    if row.last_status == "fail" or row.consecutive_failures > 0:
        state = "failing"
    elif current.astimezone(UTC) - row.last_run_at.astimezone(UTC) > stale_after:
        state = "stale"
    else:
        state = "online"
    return OutcomeReconcilerHealth(
        state,
        row.last_run_at,
        row.last_success_at,
        row.consecutive_failures,
        row.last_error,
    )


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


def _safe_external_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if "\\" in candidate or any(
        ord(character) < 32 or ord(character) == 127 for character in candidate
    ):
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _platform_view(
    platform: str,
    status: str,
    *,
    error: object = None,
    permalink: object = None,
    receipt_id: object = None,
    retryable: bool = False,
    confirmation_overdue: bool = False,
) -> PlatformTargetView:
    return PlatformTargetView(
        platform=platform,
        platform_label=_PLATFORM_LABELS.get(platform, platform.replace("_", " ").title()),
        status=status,
        error=str(error).strip() if error else None,
        permalink=_safe_external_url(permalink),
        receipt_id=str(receipt_id).strip() if receipt_id else None,
        retryable=retryable,
        confirmation_overdue=confirmation_overdue,
    )


def _carousel_anchor_token(campaign_anchor_at: datetime | None) -> str:
    if campaign_anchor_at is None:
        return "carousel-anchor-v1:none"
    return "carousel-anchor-v1:utc:" + campaign_anchor_at.astimezone(UTC).isoformat()


def _release_phase(
    statuses: list[str],
    *,
    anchor_state: str,
    anchor_at: datetime | None,
    now: datetime,
) -> PipelinePhase:
    eligible_statuses = [status for status in statuses if status != "ineligible"]
    if eligible_statuses and all(status == "published" for status in eligible_statuses):
        return "published"
    if anchor_state == "divergent" or any(status == "failed" for status in eligible_statuses):
        return "attention"
    if (
        anchor_state == "shared"
        and anchor_at is not None
        and anchor_at > now
        and eligible_statuses
        and all(status in {"approved", "uploaded"} for status in eligible_statuses)
    ):
        return "scheduled"
    if any(status in {"uploading", "uploaded"} for status in eligible_statuses):
        return "in_progress"
    if any(status == "draft" for status in eligible_statuses):
        return "needs_review"
    if anchor_state == "shared" and anchor_at is not None and anchor_at > now:
        return "scheduled"
    if (
        anchor_state == "shared"
        and eligible_statuses
        and all(status == "approved" for status in eligible_statuses)
    ):
        return "in_progress"
    if eligible_statuses and all(status == "approved" for status in eligible_statuses):
        return "ready_to_schedule"
    return "attention"


def _release_items(*, now: datetime) -> tuple[list[CalendarItem], list[CalendarDiagnostic]]:
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
        targets = release.get("targets", [])
        if not targets:
            diagnostics.append(
                CalendarDiagnostic(
                    code="release_targets_missing",
                    message=f"{episode}/{cut_id} 沒有 Release Targets。",
                    episode=episode,
                )
            )
            continue
        anchor = get_release_campaign_anchor(episode, cut_id)
        calendar_at = _aware_taipei(anchor.anchor_at) if anchor.state == "shared" else None
        if anchor.state == "divergent":
            diagnostics.append(
                CalendarDiagnostic(
                    code="release_campaign_anchor_divergent",
                    message=f"{episode}/{cut_id} 的 Release Targets 排程時間不一致，未列入月曆。",
                    episode=episode,
                )
            )
        statuses = [str(target.get("status") or "draft") for target in targets]
        target_views = tuple(
            _platform_view(
                str(target.get("platform", "unknown")),
                status,
                error=target.get("error") or target.get("ineligibility_reason"),
                permalink=target.get("url"),
                receipt_id=target.get("video_id"),
                retryable=content_type == "short" and status == "failed",
                confirmation_overdue=(
                    anchor.state == "shared"
                    and anchor.anchor_at is not None
                    and anchor.anchor_at.astimezone(UTC) <= now
                    and target.get("platform") in _NATIVE_OUTCOME_PLATFORMS
                    and status == "uploaded"
                ),
            )
            for target, status in zip(targets, statuses, strict=True)
        )
        youtube = next(
            (target for target in targets if target.get("platform") == "youtube"), targets[0]
        )
        editable = all(status in {"draft", "approved", "failed"} for status in statuses)
        execution = short_execution_readiness(release)
        items.append(
            CalendarItem(
                item_id=f"release:{release.get('id', cut_id)}",
                episode=episode,
                content_id=cut_id,
                title=str(youtube.get("title") or release.get("work_title") or cut_id),
                content_type=content_type,
                targets=target_views,
                phase=_release_phase(
                    statuses,
                    anchor_state=anchor.state,
                    anchor_at=anchor.anchor_at,
                    now=now,
                ),
                calendar_at=calendar_at,
                date_basis="scheduled" if calendar_at else None,
                detail_url=_detail_url("/bridge/publish", episode, cut_id),
                schedule_kind="release",
                schedule_id=cut_id,
                schedule_editable=editable,
                expected_anchor_token=anchor.expected_token,
                schedule_disabled_reason=(
                    None if editable else "上傳或發布已開始，Campaign Anchor 已鎖定。"
                ),
                execution_ready=execution.ready,
                execution_reason=execution.reason,
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


def _current_platform_result(
    job: CarouselPublishJobV1, platform: str
) -> CarouselPublishPlatformResult | None:
    state = _target_state(job, platform)
    if state is not None and state.checkpoint is not None:
        return state.checkpoint
    return next((result for result in job.results if result.platform == platform), None)


def _carousel_title(job: CarouselPublishJobV1) -> str:
    first_line = next((line.strip() for line in job.caption.splitlines() if line.strip()), "")
    return first_line[:120] or f"Carousel {job.source_revision}"


def _carousel_phase(job: CarouselPublishJobV1, statuses: list[str]) -> PipelinePhase:
    if statuses and all(status == "published" for status in statuses):
        return "published"
    if (
        job.status in {"failed", "superseded"}
        or any(status == "failed" for status in statuses)
        or any(status == "published" for status in statuses)
    ):
        return "attention"
    if job.status in {"claimed", "in_progress"} or any(
        status == "in_progress" for status in statuses
    ):
        return "in_progress"
    if job.campaign_anchor_at is not None:
        return "scheduled"
    return "ready_to_schedule"


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
    grouped: dict[tuple[str, str], list[CarouselPublishJobV1]] = {}
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
            grouped.setdefault((episode_dir.name, job.request_fingerprint), []).append(job)

    items: list[CalendarItem] = []
    for (episode, fingerprint), jobs in grouped.items():
        latest = max(jobs, key=_job_order)
        successful_by_platform = {
            target.platform: next(
                (
                    result
                    for job in sorted(jobs, key=_job_order, reverse=True)
                    if (result := _successful_result(job, target.platform)) is not None
                ),
                None,
            )
            for target in latest.targets
        }
        statuses = [
            "published"
            if successful_by_platform[target.platform] is not None
            else _current_platform_status(latest, target.platform)
            for target in latest.targets
        ]
        fully_published = statuses and all(status == "published" for status in statuses)
        calendar_at = _aware_taipei(latest.campaign_anchor_at)
        date_basis: DateBasis | None = "scheduled" if calendar_at else None
        if calendar_at is None and fully_published:
            completed = []
            invalid_completed_platforms = []
            for platform, result in successful_by_platform.items():
                parsed_completed_at = (
                    _aware_taipei(result.completed_at) if result is not None else None
                )
                if parsed_completed_at is None:
                    invalid_completed_platforms.append(platform)
                else:
                    completed.append(parsed_completed_at)
            if len(completed) == len(latest.targets):
                calendar_at = max(completed)
                date_basis = "published"
            elif invalid_completed_platforms:
                diagnostics.append(
                    CalendarDiagnostic(
                        code="carousel_completed_at_invalid",
                        message=(
                            f"{episode} 的發布 checkpoint 不含可信時區（"
                            + ", ".join(sorted(invalid_completed_platforms))
                            + "），已改列日期未定。"
                        ),
                        episode=episode,
                    )
                )
        if latest.status == "superseded" and not fully_published:
            continue
        editable = latest.status == "queued"
        target_views = []
        for target, status in zip(latest.targets, statuses, strict=True):
            result = successful_by_platform[target.platform] or _current_platform_result(
                latest, target.platform
            )
            target_views.append(
                _platform_view(
                    target.platform,
                    status,
                    error=result.error if result is not None else None,
                    permalink=result.permalink if result is not None else None,
                    receipt_id=result.receipt_id if result is not None else None,
                )
            )
        items.append(
            CalendarItem(
                item_id=f"carousel:{episode}:{fingerprint}",
                episode=episode,
                content_id=latest.source_revision,
                title=_carousel_title(latest),
                content_type="carousel",
                targets=tuple(target_views),
                phase=_carousel_phase(latest, statuses),
                calendar_at=calendar_at,
                date_basis=date_basis,
                detail_url=_detail_url("/bridge/ig-cards", episode),
                schedule_kind="carousel",
                schedule_id=latest.job_id,
                schedule_editable=editable,
                expected_anchor_token=_carousel_anchor_token(latest.campaign_anchor_at),
                schedule_disabled_reason=(
                    None if editable else "發布執行已開始，Campaign Anchor 已鎖定。"
                ),
                execution_reason="從既有 Publish page 建立或接續三平台發布工作。",
            )
        )
    return items, diagnostics


def build_publish_calendar(
    episodes_root: Path | None,
    *,
    now: datetime | None = None,
) -> CalendarProjection:
    """Load the complete, read-only Stage 6 projection without external APIs."""

    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        raise ValueError("Calendar clock must be timezone-aware")
    observed_now = observed_now.astimezone(UTC)
    release_items, release_diagnostics = _release_items(now=observed_now)
    carousel_items, carousel_diagnostics = _carousel_items(episodes_root)
    items = sorted(
        [*release_items, *carousel_items],
        key=lambda item: (
            item.calendar_at is None,
            item.calendar_at or datetime.max.replace(tzinfo=TAIPEI),
            item.episode,
            item.content_id,
            item.item_id,
        ),
    )
    return CalendarProjection(
        items=tuple(items),
        diagnostics=tuple([*release_diagnostics, *carousel_diagnostics]),
    )


def future_short_requires_due_worker(
    items: tuple[CalendarItem, ...] | list[CalendarItem],
    *,
    now: datetime | None = None,
) -> bool:
    """Whether a future Short still relies on Instagram due-time execution."""

    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("due dependency clock must be timezone-aware")
    current = current.astimezone(UTC)
    return any(
        item.content_type == "short"
        and item.calendar_at is not None
        and item.calendar_at.astimezone(UTC) > current
        and any(
            target.platform == "instagram_reels" and target.status in {"approved", "uploading"}
            for target in item.targets
        )
        for item in items
    )


def overdue_native_confirmation_exists(
    items: tuple[CalendarItem, ...] | list[CalendarItem],
) -> bool:
    """Whether any projected native target awaits explicit public evidence."""

    return any(target.confirmation_overdue for item in items for target in item.targets)
