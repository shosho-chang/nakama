"""Stage 6 social publishing orchestration contracts.

This module owns platform-neutral eligibility, fan-out and durable target
checkpoints.  HTTP payloads and credentials belong to concrete adapters (for
example ``meta_graph.py``), never here.

Release targets are deliberately independent retry units: a successful target
is never rolled back or called again because another platform failed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence, runtime_checkable

from shared.release_store import (
    TARGET_CLAIM_STALE_AFTER,
    claim_target,
    ensure_target,
    get_release,
    update_target,
)

SHORT_PLATFORMS = ("youtube", "instagram_reels", "facebook_reels")
_ADAPTER_BY_PLATFORM = {
    "youtube": "youtube_data",
    "instagram_reels": "meta_graph",
    "facebook_reels": "meta_graph",
}
FACEBOOK_REEL_MAX_DURATION_SEC = 60.0
FACEBOOK_REEL_DURATION_REASON = (
    "Facebook Page Reels are fail-closed at 60 seconds; create a separate "
    "Stage 5 variant instead of trimming or transcoding this release."
)

CheckpointCallback = Callable[[Mapping[str, Any]], None]
AdapterStatus = Literal["uploaded", "published", "failed", "handoff_pending"]


@dataclass(frozen=True)
class AdapterResult:
    """Normalized result returned by a platform adapter."""

    status: AdapterStatus
    external_id: str | None = None
    url: str | None = None
    receipt_id: str | None = None
    error: str | None = None
    checkpoint: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status in {"uploaded", "published"} and self.error:
            raise ValueError("successful adapter result cannot contain an error")
        if self.status == "failed" and not self.error:
            raise ValueError("failed adapter result requires an error")
        if self.status == "handoff_pending" and self.error:
            raise ValueError("pending handoff cannot contain an error")


@runtime_checkable
class SocialPublishAdapter(Protocol):
    """Anti-corruption boundary implemented by YouTube/Meta/browser workers."""

    platform: str

    def publish(
        self,
        *,
        release: Mapping[str, Any],
        target: Mapping[str, Any],
        idempotency_key: str,
        checkpoint: CheckpointCallback,
    ) -> AdapterResult | Mapping[str, Any]: ...


@dataclass(frozen=True)
class YouTubeCommunityHandoff:
    """Browser handoff payload; creating this is not proof of publication."""

    caption: str
    asset_paths: tuple[str, ...]
    target_url: str

    def __post_init__(self) -> None:
        if not self.caption.strip():
            raise ValueError("YouTube Community handoff requires a caption")
        if not self.asset_paths or len(self.asset_paths) > 10:
            raise ValueError("YouTube Community handoff requires 1 to 10 images")
        if not self.target_url.startswith(("https://", "http://")):
            raise ValueError("YouTube Community handoff target must be HTTP(S)")

    def checkpoint(self) -> dict[str, Any]:
        return {
            "kind": "browser_handoff",
            "platform": "youtube_community",
            "state": "awaiting_receipt",
            "caption": self.caption,
            "asset_paths": list(self.asset_paths),
            "target_url": self.target_url,
        }


def _stable_idempotency_key(release: Mapping[str, Any], platform: str) -> str:
    identity = {
        "episode": str(release.get("episode", "")),
        "cut_id": str(release.get("cut_id", "")),
        "platform": platform,
    }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fresh_release(release: Mapping[str, Any]) -> dict[str, Any]:
    episode = str(release.get("episode", ""))
    cut_id = str(release.get("cut_id", ""))
    if not episode or not cut_id:
        raise ValueError("release requires episode and cut_id")
    fresh = get_release(episode, cut_id)
    if fresh is None:
        raise ValueError(f"release {episode}/{cut_id} does not exist")
    return fresh


def _target_map(release: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(target["platform"]): target for target in release.get("targets", [])}


def ensure_short_targets(release: dict) -> list[dict]:
    """Ensure the three Short targets and persist eligibility/adapter metadata.

    Facebook Page Reels longer than 60 seconds are represented explicitly as
    ``ineligible``.  The other platform targets remain executable.
    """

    fresh = _fresh_release(release)
    if fresh.get("format") != "short":
        raise ValueError("ensure_short_targets only accepts format=short releases")
    duration = float(fresh.get("duration_sec") or 0.0)

    for platform in SHORT_PLATFORMS:
        target_id = ensure_target(int(fresh["id"]), platform)
        fresh = _fresh_release(fresh)
        target = _target_map(fresh)[platform]
        expected_key = _stable_idempotency_key(fresh, platform)
        existing_key = target.get("idempotency_key")
        if existing_key and existing_key != expected_key:
            raise ValueError(f"{platform} target has a conflicting idempotency key")

        fields: dict[str, Any] = {
            "adapter": _ADAPTER_BY_PLATFORM[platform],
            "idempotency_key": expected_key,
        }
        is_ineligible = platform == "facebook_reels" and duration > FACEBOOK_REEL_MAX_DURATION_SEC
        if is_ineligible and target["status"] != "published":
            fields.update(
                status="ineligible",
                ineligibility_reason=FACEBOOK_REEL_DURATION_REASON,
                error=None,
            )
        elif not is_ineligible:
            fields["ineligibility_reason"] = None
            if target["status"] == "ineligible":
                fields["status"] = "draft"
        update_target(target_id, **fields)

    refreshed = _fresh_release(fresh)
    targets = _target_map(refreshed)
    return [targets[platform] for platform in SHORT_PLATFORMS]


def approve_short_targets(release: dict, source_target: dict) -> list[dict]:
    """Copy reviewed copy/schedule to every eligible, mutable Short target."""

    targets = ensure_short_targets(release)
    reviewed = {
        "title": source_target.get("title"),
        "description": source_target.get("description"),
        "publish_at": source_target.get("publish_at"),
    }
    if not reviewed["title"] or not reviewed["description"]:
        raise ValueError("reviewed source target requires title and description")

    for target in targets:
        if target["status"] in {"ineligible", "uploading", "uploaded", "published"}:
            continue
        update_target(target["id"], **reviewed, status="approved", error=None)

    refreshed = _fresh_release(release)
    by_platform = _target_map(refreshed)
    return [by_platform[platform] for platform in SHORT_PLATFORMS]


def _adapter_map(
    adapters: Mapping[str, SocialPublishAdapter] | Sequence[SocialPublishAdapter],
) -> dict[str, SocialPublishAdapter]:
    if isinstance(adapters, Mapping):
        return dict(adapters)
    return {adapter.platform: adapter for adapter in adapters}


def _canonical_checkpoint(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("adapter checkpoint must be JSON serializable") from exc


def _normalize_result(value: AdapterResult | Mapping[str, Any]) -> AdapterResult:
    if isinstance(value, AdapterResult):
        return value
    return AdapterResult(
        status=value.get("status", "failed"),
        external_id=value.get("external_id"),
        url=value.get("url") or value.get("permalink"),
        receipt_id=value.get("receipt_id"),
        error=value.get("error"),
        checkpoint=value.get("checkpoint") or {},
    )


def dispatch_release(
    release: dict,
    adapters: Mapping[str, SocialPublishAdapter] | Sequence[SocialPublishAdapter],
    only_platforms: Sequence[str] | set[str] | None = None,
    *,
    claim_now: datetime | None = None,
    claim_stale_after: timedelta = TARGET_CLAIM_STALE_AFTER,
    expected_publish_at_by_platform: Mapping[str, str] | None = None,
    adapter_setup_errors: Mapping[str, str] | None = None,
) -> list[dict]:
    """Dispatch eligible targets independently and persist each outcome.

    Only atomically claimed approved or stale-uploading targets are called.
    Explicit operator retry must first reset one failed target to approved.
    Adapter checkpoint callbacks are durable immediately, including when the
    adapter later raises, and refresh the stale-recovery lease timestamp.  A
    due dispatcher can additionally bind its claim to a previously re-read
    publish time; ordinary immediate dispatchers omit that optional guard.
    """

    adapter_by_platform = _adapter_map(adapters)
    selected = set(only_platforms) if only_platforms is not None else None
    fresh = _fresh_release(release)
    results: list[dict] = []

    for original_target in fresh.get("targets", []):
        platform = str(original_target["platform"])
        if selected is not None and platform not in selected:
            continue
        target = _target_map(_fresh_release(fresh))[platform]
        current_status = str(target["status"])

        if current_status in {"published", "ineligible"}:
            results.append({"platform": platform, "status": current_status, "called": False})
            continue
        if current_status not in {"approved", "uploading"}:
            results.append({"platform": platform, "status": current_status, "called": False})
            continue

        adapter = adapter_by_platform.get(platform)
        if adapter is None:
            error = (
                adapter_setup_errors.get(platform) if adapter_setup_errors is not None else None
            ) or f"no adapter configured for {platform}"
            update_target(target["id"], status="failed", error=error)
            results.append(
                {"platform": platform, "status": "failed", "called": False, "error": error}
            )
            continue

        key = target.get("idempotency_key") or _stable_idempotency_key(fresh, platform)
        if not target.get("idempotency_key"):
            update_target(target["id"], idempotency_key=key)
        claimed = claim_target(
            target["id"],
            now=claim_now,
            stale_after=claim_stale_after,
            expected_publish_at=(
                expected_publish_at_by_platform.get(platform)
                if expected_publish_at_by_platform is not None
                else None
            ),
        )
        if claimed is None:
            current = _target_map(_fresh_release(fresh))[platform]
            results.append(
                {
                    "platform": platform,
                    "status": current["status"],
                    "called": False,
                }
            )
            continue
        target = claimed

        def save_checkpoint(data: Mapping[str, Any], *, target_id: int = target["id"]) -> None:
            update_target(target_id, checkpoint_json=_canonical_checkpoint(data))

        try:
            current_target = _target_map(_fresh_release(fresh))[platform]
            outcome = _normalize_result(
                adapter.publish(
                    release=fresh,
                    target=current_target,
                    idempotency_key=key,
                    checkpoint=save_checkpoint,
                )
            )
            if outcome.checkpoint:
                save_checkpoint(outcome.checkpoint)

            if outcome.status == "handoff_pending":
                if platform != "youtube_community":
                    raise ValueError("handoff_pending is reserved for youtube_community")
                update_target(target["id"], status="approved", error=None)
            elif outcome.status in {"uploaded", "published"}:
                if platform == "youtube_community" and not (
                    outcome.receipt_id or outcome.external_id or outcome.url
                ):
                    raise ValueError(
                        "YouTube Community cannot be published without a handoff receipt"
                    )
                update_target(
                    target["id"],
                    status=outcome.status,
                    video_id=outcome.external_id or outcome.receipt_id,
                    url=outcome.url,
                    error=None,
                )
            else:
                update_target(target["id"], status="failed", error=outcome.error)
            results.append(
                {
                    "platform": platform,
                    "status": outcome.status,
                    "called": True,
                    "external_id": outcome.external_id or outcome.receipt_id,
                    "url": outcome.url,
                    "error": outcome.error,
                }
            )
        except Exception as exc:  # adapters are an isolation boundary
            error = str(exc)[:4000]
            update_target(target["id"], status="failed", error=error)
            results.append(
                {"platform": platform, "status": "failed", "called": True, "error": error}
            )

    return results


__all__ = [
    "AdapterResult",
    "CheckpointCallback",
    "FACEBOOK_REEL_DURATION_REASON",
    "SHORT_PLATFORMS",
    "SocialPublishAdapter",
    "YouTubeCommunityHandoff",
    "approve_short_targets",
    "dispatch_release",
    "ensure_short_targets",
]
