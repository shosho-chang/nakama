"""YouTube platform reconciliation for one approved release target."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

VideoProcessingStatus = Literal["missing", "processing", "processed", "failed", "unknown"]
CaptionStatus = Literal["missing", "processing", "serving", "failed", "unknown"]


@dataclass(frozen=True, slots=True)
class YouTubeReconciliation:
    video_id: str
    release_status: Literal["uploaded", "published", "failed"]
    video_processing_status: VideoProcessingStatus
    platform_privacy_status: str | None
    platform_publish_at: str | None
    caption_status: CaptionStatus
    reconciliation_error: str | None
    last_reconciled_at: str

    def target_fields(self) -> dict[str, str | None]:
        return {
            "status": self.release_status,
            "video_processing_status": self.video_processing_status,
            "platform_privacy_status": self.platform_privacy_status,
            "platform_publish_at": self.platform_publish_at,
            "caption_status": self.caption_status,
            "reconciliation_error": self.reconciliation_error,
            "last_reconciled_at": self.last_reconciled_at,
        }


def _processing_status(raw: str | None) -> VideoProcessingStatus:
    if raw in ("succeeded", "processed"):
        return "processed"
    if raw in ("processing", "uploaded"):
        return "processing"
    if raw in ("failed", "terminated", "rejected", "deleted"):
        return "failed"
    return "unknown"


def _caption_status(items: list[dict]) -> CaptionStatus:
    item = next(
        (
            candidate
            for candidate in items
            if str(candidate.get("snippet", {}).get("language", "")).casefold() == "zh-tw"
        ),
        None,
    )
    if item is None:
        return "missing"
    raw = item.get("snippet", {}).get("status")
    if raw == "serving":
        return "serving"
    if raw == "syncing":
        return "processing"
    if raw == "failed":
        return "failed"
    return "unknown"


def reconcile_youtube_target(
    service,
    target: dict,
    *,
    now: datetime | None = None,
) -> YouTubeReconciliation:
    """Read authoritative video/caption state without mutating or publishing it."""

    video_id = str(target.get("video_id") or "").strip()
    if not video_id:
        raise ValueError("YouTube reconciliation requires video_id")
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    video_response = service.videos().list(
        part="status,processingDetails",
        id=video_id,
    ).execute()
    items = video_response.get("items") or []
    if not items:
        return YouTubeReconciliation(
            video_id=video_id,
            release_status="failed",
            video_processing_status="missing",
            platform_privacy_status=None,
            platform_publish_at=None,
            caption_status="unknown",
            reconciliation_error="video missing on YouTube",
            last_reconciled_at=checked_at,
        )

    video = items[0]
    status = video.get("status") or {}
    privacy = status.get("privacyStatus")
    publish_at = status.get("publishAt")
    processing = _processing_status(
        (video.get("processingDetails") or {}).get("processingStatus")
        or status.get("uploadStatus")
    )
    caption_response = service.captions().list(part="snippet", videoId=video_id).execute()
    caption = _caption_status(caption_response.get("items") or [])
    errors = []
    if processing == "failed":
        errors.append("video processing failed")
    if caption == "missing":
        errors.append("zh-TW caption missing")
    elif caption == "failed":
        errors.append("zh-TW caption failed")
    release_status = "published" if privacy == "public" else "uploaded"
    return YouTubeReconciliation(
        video_id=video_id,
        release_status=release_status,
        video_processing_status=processing,
        platform_privacy_status=privacy,
        platform_publish_at=publish_at,
        caption_status=caption,
        reconciliation_error="; ".join(errors) or None,
        last_reconciled_at=checked_at,
    )


def reconcile_and_persist(
    service,
    target: dict,
    *,
    update_target=None,
    now: datetime | None = None,
) -> YouTubeReconciliation:
    """Reconcile one target and atomically hand its typed fields to the release store."""

    if update_target is None:
        from shared.release_store import update_target as store_update_target

        update_target = store_update_target
    result = reconcile_youtube_target(service, target, now=now)
    update_target(target["id"], **result.target_fields())
    return result


__all__ = [
    "YouTubeReconciliation",
    "reconcile_and_persist",
    "reconcile_youtube_target",
]
