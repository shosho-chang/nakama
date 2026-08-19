from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from scripts.podcast_carousel_publish_job import _target_idempotency_key
from shared.publish_calendar import (
    PODCAST_YOUTUBE_CHANNEL,
    PODCAST_YOUTUBE_CHANNEL_HANDLE,
    PODCAST_YOUTUBE_CHANNEL_ID,
    PODCAST_YOUTUBE_CHANNEL_NAME,
    build_month_grid,
    build_publish_calendar,
    parse_month,
)
from shared.release_store import ensure_target, register_release, update_target
from shared.schemas.carousel_publish import (
    CarouselPublishClaim,
    CarouselPublishJobV1,
    CarouselPublishPlatformResult,
    CarouselPublishTarget,
    CarouselPublishTargetState,
)
from shared.schemas.podcast_carousel import ArtifactReceipt

SHA = "a" * 64


def _target(platform: str) -> CarouselPublishTarget:
    if platform == "youtube_community":
        return CarouselPublishTarget(
            platform=platform,
            strategy="agent_browser_manual",
            configuration_state="manual_only",
            required_executor_capabilities=["browser_session"],
            note="manual handoff",
        )
    return CarouselPublishTarget(
        platform=platform,
        strategy="meta_api",
        configuration_state="configured",
        required_executor_capabilities=["meta_api"],
        note="configured",
    )


def _result(platform: str, status: str, completed_at: datetime, fingerprint: str):
    strategy = "agent_browser_manual" if platform == "youtube_community" else "meta_api"
    attempt_id = f"pa-{'b' * 32}"
    common = {
        "platform": platform,
        "strategy": strategy,
        "status": status,
        "idempotency_key": _target_idempotency_key(fingerprint, platform),
        "attempt_id": attempt_id,
        "completed_at": completed_at,
    }
    if status == "published":
        return CarouselPublishPlatformResult(**common, receipt_id=f"receipt-{platform}")
    return CarouselPublishPlatformResult(**common, error=f"{platform} failed")


def _state(platform: str, status: str, completed_at: datetime, fingerprint: str):
    strategy = "agent_browser_manual" if platform == "youtube_community" else "meta_api"
    if status == "pending":
        return CarouselPublishTargetState(
            platform=platform,
            strategy=strategy,
            idempotency_key=_target_idempotency_key(fingerprint, platform),
            updated_at=completed_at,
        )
    result = _result(platform, status, completed_at, fingerprint)
    return CarouselPublishTargetState(
        platform=platform,
        strategy=strategy,
        idempotency_key=_target_idempotency_key(fingerprint, platform),
        status=status,
        attempt_count=1,
        attempt_id=result.attempt_id,
        checkpoint=result,
        updated_at=completed_at,
    )


def _claim(at: datetime) -> CarouselPublishClaim:
    return CarouselPublishClaim(
        executor="codex",
        executor_id="test-executor",
        executor_capabilities=["meta_api"],
        claim_token="claim-test-token",
        claimed_at=at,
        lease_seconds=1800,
        lease_expires_at=at + timedelta(seconds=1800),
    )


def _write_job(
    root: Path,
    *,
    job_hex: str,
    fingerprint: str,
    created_at: datetime,
    states: list[CarouselPublishTargetState],
    job_status: str,
) -> None:
    targets = [_target(state.platform) for state in states]
    results = [state.checkpoint for state in states if state.checkpoint is not None]
    claim = _claim(created_at) if job_status in {"completed", "failed"} else None
    job = CarouselPublishJobV1(
        job_id=f"pj-{job_hex * 32}",
        episode_id="episode-alpha",
        source_revision="r026",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=1,
        approved_at=created_at,
        request_fingerprint=fingerprint,
        caption="Carousel 測試標題\n第二行",
        assets=[
            {
                "page_id": "cover",
                "page_number": 1,
                "image": ArtifactReceipt(path="C:/fixture/01.png", bytes=1, sha256=SHA),
            }
        ],
        targets=targets,
        target_states=states,
        results=results,
        status=job_status,
        created_at=created_at,
        updated_at=created_at,
        claim=claim,
        error="one or more publish targets failed" if job_status == "failed" else None,
    )
    directory = root / "episode-alpha" / "ig-carousel" / "publish_jobs"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{job.job_id}.json").write_text(job.model_dump_json(indent=2), encoding="utf-8")


def test_taipei_release_projection_crosses_utc_day_and_month(tmp_path: Path) -> None:
    release_id = register_release(
        "episode-alpha", "S01", "short", "C:/exports/S01.mp4", work_title="短片"
    )
    target_id = ensure_target(release_id, "youtube")
    update_target(
        target_id,
        status="approved",
        title="可信排程",
        publish_at="2026-08-31T16:30:00+00:00",
    )

    projection = build_publish_calendar(tmp_path)
    item = projection.items[0]

    assert item.calendar_at.isoformat() == "2026-09-01T00:30:00+08:00"
    assert item.date_basis == "scheduled"
    assert item.date_basis_label == "排程時間"
    assert item.platform_label == "Podcast YouTube"
    assert PODCAST_YOUTUBE_CHANNEL == (
        f"{PODCAST_YOUTUBE_CHANNEL_NAME} {PODCAST_YOUTUBE_CHANNEL_HANDLE}"
    )
    assert PODCAST_YOUTUBE_CHANNEL_ID == "UCvipegP35x3-OcAs--PgAig"


def test_release_targets_are_platform_grained_and_missing_date_stays_backlog(
    tmp_path: Path,
) -> None:
    release_id = register_release(
        "backlog-only", "S02", "short", "C:/exports/S02.mp4", work_title="無日期短片"
    )
    youtube = ensure_target(release_id, "youtube")
    instagram = ensure_target(release_id, "instagram_reels")
    update_target(youtube, status="uploaded", title="無日期短片")
    update_target(instagram, status="failed", title="無日期短片", error="transport")

    projection = build_publish_calendar(tmp_path)

    assert len(projection.items) == 2
    assert {item.platform for item in projection.items} == {"youtube", "instagram_reels"}
    assert all(item.calendar_at is None and item.date_basis is None for item in projection.items)
    assert all(item.local_time_label == "日期未定" for item in projection.items)
    assert all(item.date_basis_label == "日期未定" for item in projection.items)
    assert projection.episodes == ("backlog-only",)


def test_month_parser_and_grid_are_strict_and_sunday_first() -> None:
    assert parse_month("2026-08") == date(2026, 8, 1)
    grid = build_month_grid(date(2026, 8, 1))
    assert grid[0][0].value.weekday() == 6
    assert grid[0][0].value == date(2026, 7, 26)
    assert grid[-1][-1].value == date(2026, 9, 5)

    for invalid in ("2026-8", "2026-13", "2026-00", "not-a-month"):
        try:
            parse_month(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{invalid} should fail closed")


def test_carousel_failed_completion_is_backlog_and_malformed_job_fails_soft(
    tmp_path: Path,
) -> None:
    fingerprint = "c" * 64
    completed_at = datetime(2026, 8, 19, 1, tzinfo=UTC)
    _write_job(
        tmp_path,
        job_hex="1",
        fingerprint=fingerprint,
        created_at=completed_at,
        states=[_state("facebook_page", "failed", completed_at, fingerprint)],
        job_status="failed",
    )
    jobs_dir = tmp_path / "episode-alpha" / "ig-carousel" / "publish_jobs"
    (jobs_dir / f"pj-{'f' * 32}.json").write_text("{broken", encoding="utf-8")

    projection = build_publish_calendar(tmp_path)

    assert len(projection.items) == 1
    assert projection.items[0].status == "failed"
    assert projection.items[0].calendar_at is None
    assert projection.items[0].detail_url == "/bridge/ig-cards/episode-alpha/publish"
    assert [item.code for item in projection.diagnostics] == ["carousel_job_invalid"]


def test_carousel_retry_deduplicates_and_preserves_carried_published_checkpoint(
    tmp_path: Path,
) -> None:
    fingerprint = "d" * 64
    first = datetime(2026, 8, 19, 1, tzinfo=UTC)
    carried = _state("instagram", "published", first, fingerprint)
    failed = _state("facebook_page", "failed", first, fingerprint)
    _write_job(
        tmp_path,
        job_hex="2",
        fingerprint=fingerprint,
        created_at=first,
        states=[failed, carried],
        job_status="failed",
    )
    _write_job(
        tmp_path,
        job_hex="3",
        fingerprint=fingerprint,
        created_at=first + timedelta(minutes=5),
        states=[
            _state("facebook_page", "pending", first + timedelta(minutes=5), fingerprint),
            carried,
        ],
        job_status="queued",
    )

    projection = build_publish_calendar(tmp_path)

    assert len(projection.items) == 2
    by_platform = {item.platform: item for item in projection.items}
    assert by_platform["instagram"].status == "published"
    assert by_platform["instagram"].calendar_at.isoformat() == "2026-08-19T09:00:00+08:00"
    assert by_platform["instagram"].date_basis == "published"
    assert by_platform["instagram"].date_basis_label == "實際發布時間"
    assert by_platform["facebook_page"].status == "pending"
    assert by_platform["facebook_page"].calendar_at is None


def test_carousel_published_checkpoint_crosses_into_taipei_next_month(tmp_path: Path) -> None:
    fingerprint = "e" * 64
    completed_at = datetime(2026, 8, 31, 16, 30, tzinfo=UTC)
    _write_job(
        tmp_path,
        job_hex="4",
        fingerprint=fingerprint,
        created_at=completed_at,
        states=[_state("youtube_community", "published", completed_at, fingerprint)],
        job_status="completed",
    )

    item = build_publish_calendar(tmp_path).items[0]

    assert item.calendar_at.isoformat() == "2026-09-01T00:30:00+08:00"
    assert item.platform_label == "Podcast YouTube · Community handoff"
