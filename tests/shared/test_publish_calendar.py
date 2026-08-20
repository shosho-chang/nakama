from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from scripts.podcast_carousel_publish_job import (
    _target_idempotency_key,
    carousel_campaign_anchor_token,
)
from shared.heartbeat import Heartbeat
from shared.publish_calendar import (
    OUTCOME_RECONCILER_STALE_AFTER,
    PODCAST_YOUTUBE_CHANNEL,
    PODCAST_YOUTUBE_CHANNEL_HANDLE,
    PODCAST_YOUTUBE_CHANNEL_ID,
    PODCAST_YOUTUBE_CHANNEL_NAME,
    SHORT_DUE_WORKER_STALE_AFTER,
    build_month_grid,
    build_publish_calendar,
    future_short_requires_due_worker,
    outcome_reconciler_health,
    parse_month,
    short_due_worker_health,
    short_execution_readiness,
)
from shared.release_store import (
    ensure_target,
    get_release_campaign_anchor,
    register_release,
    set_release_campaign_anchor,
    update_target,
)
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
    campaign_anchor_at: datetime | None = None,
) -> None:
    targets = [_target(state.platform) for state in states]
    results = [state.checkpoint for state in states if state.checkpoint is not None]
    claim = _claim(created_at) if job_status in {"in_progress", "completed", "failed"} else None
    job = CarouselPublishJobV1(
        job_id=f"pj-{job_hex * 32}",
        episode_id="episode-alpha",
        source_revision="r026",
        source_manifest_sha256=SHA,
        source_publish_compatibility="api_compatible",
        approval_revision_number=1,
        approved_at=created_at,
        request_fingerprint=fingerprint,
        campaign_anchor_at=campaign_anchor_at,
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
    assert item.targets[0].platform_label == "Podcast YouTube"
    assert item.phase == "scheduled"
    assert PODCAST_YOUTUBE_CHANNEL == (
        f"{PODCAST_YOUTUBE_CHANNEL_NAME} {PODCAST_YOUTUBE_CHANNEL_HANDLE}"
    )
    assert PODCAST_YOUTUBE_CHANNEL_ID == "UCvipegP35x3-OcAs--PgAig"


def test_release_targets_are_grouped_into_one_publication_with_platform_states(
    tmp_path: Path,
) -> None:
    release_id = register_release(
        "backlog-only", "S02", "short", "C:/exports/S02.mp4", work_title="無日期短片"
    )
    youtube = ensure_target(release_id, "youtube")
    instagram = ensure_target(release_id, "instagram_reels")
    facebook = ensure_target(release_id, "facebook_reels")
    update_target(youtube, status="uploaded", title="無日期短片")
    update_target(instagram, status="failed", title="無日期短片", error="transport")
    update_target(facebook, status="approved", title="無日期短片")

    projection = build_publish_calendar(tmp_path)

    assert len(projection.items) == 1
    group = projection.items[0]
    assert [(target.platform, target.status) for target in group.targets] == [
        ("facebook_reels", "approved"),
        ("instagram_reels", "failed"),
        ("youtube", "uploaded"),
    ]
    assert group.calendar_at is None
    assert group.date_basis is None
    assert group.local_time_label == "日期未定"
    assert group.phase == "attention"
    assert projection.episodes == ("backlog-only",)


def test_short_with_canonical_file_and_reviewed_copy_is_ready_for_explicit_execution(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "S-ready.mp4"
    canonical.write_bytes(b"short")
    release_id = register_release(
        "episode-ready", "S-ready", "short", str(canonical), work_title="可投遞 Short"
    )
    youtube = ensure_target(release_id, "youtube")
    update_target(youtube, status="draft", title="主要標題", description="主要描述")

    item = build_publish_calendar(tmp_path).items[0]

    assert item.execution_ready is True
    assert item.execution_reason == "素材與主要文案已齊，可核准並投遞。"


def test_failed_short_exposes_only_target_scoped_retry_evidence(tmp_path: Path) -> None:
    canonical = tmp_path / "S-failed.mp4"
    canonical.write_bytes(b"short")
    release_id = register_release("episode-failed", "S-failed", "short", str(canonical))
    youtube = ensure_target(release_id, "youtube")
    update_target(
        youtube,
        status="failed",
        title="主要標題",
        description="主要描述",
        error="YouTube transport failed",
        url="https://youtube.example/receipt",
        video_id="yt-receipt-1",
    )

    item = build_publish_calendar(tmp_path).items[0]
    target = item.targets[0]

    assert item.execution_ready is False
    assert item.execution_reason == "有平台投遞失敗；請只重試失敗平台。"
    assert target.retryable is True
    assert target.error == "YouTube transport failed"
    assert target.permalink == "https://youtube.example/receipt"
    assert target.receipt_id == "yt-receipt-1"


def test_release_projection_drops_non_http_platform_permalink(tmp_path: Path) -> None:
    release_id = register_release("episode-url", "S-url", "short", str(tmp_path / "S-url.mp4"))
    youtube = ensure_target(release_id, "youtube")
    update_target(
        youtube,
        status="published",
        title="已發布",
        description="描述",
        url="javascript:alert(1)",
    )

    target = build_publish_calendar(tmp_path).items[0].targets[0]

    assert target.permalink is None


@pytest.mark.parametrize(
    ("release", "reason"),
    [
        (
            {
                "format": "short",
                "file_path": "C:/does-not-exist/S01.mp4",
                "targets": [
                    {
                        "platform": "youtube",
                        "status": "draft",
                        "title": "標題",
                        "description": "描述",
                    }
                ],
            },
            "成品檔不存在；請重新產出或檢查素材路徑。",
        ),
        (
            {"format": "short", "file_path": __file__, "targets": []},
            "缺少主要 YouTube Target；請重新執行 publish_prep。",
        ),
        (
            {
                "format": "short",
                "file_path": __file__,
                "targets": [{"platform": "youtube", "status": "draft", "description": "描述"}],
            },
            "缺少主要標題；請先檢查素材與文案。",
        ),
        (
            {
                "format": "short",
                "file_path": __file__,
                "targets": [{"platform": "youtube", "status": "draft", "title": "標題"}],
            },
            "缺少主要描述；請先檢查素材與文案。",
        ),
        (
            {
                "format": "short",
                "file_path": __file__,
                "targets": [
                    {
                        "platform": "youtube",
                        "status": "uploading",
                        "title": "標題",
                        "description": "描述",
                    }
                ],
            },
            "此 Short 已開始或完成投遞；不可再次整組核准。",
        ),
        (
            {
                "format": "short",
                "file_path": __file__,
                "targets": [
                    {
                        "platform": "youtube",
                        "status": "approved",
                        "title": "標題",
                        "description": "描述",
                    },
                    {"platform": "instagram_reels", "status": "approved"},
                ],
            },
            "已核准；等待 worker 認領或投遞啟動中。",
        ),
        (
            {
                "format": "short",
                "file_path": __file__,
                "targets": [
                    {
                        "platform": "youtube",
                        "status": "approved",
                        "title": "標題",
                        "description": "描述",
                    },
                    {"platform": "instagram_reels", "status": "draft"},
                ],
            },
            "Target 核准狀態不一致；請等待 worker 認領或檢查發布紀錄。",
        ),
        (
            {
                "format": "short",
                "file_path": __file__,
                "targets": [
                    {
                        "platform": "youtube",
                        "status": "unknown",
                        "title": "標題",
                        "description": "描述",
                    }
                ],
            },
            "Release Target 狀態無法執行；請先檢查發布紀錄。",
        ),
    ],
)
def test_short_execution_readiness_fails_closed_with_actionable_reason(
    release: dict, reason: str
) -> None:
    readiness = short_execution_readiness(release)

    assert readiness.ready is False
    assert readiness.reason == reason


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
    group = projection.items[0]
    assert group.targets[0].status == "failed"
    assert group.targets[0].error == "facebook_page failed"
    assert group.phase == "attention"
    assert group.calendar_at is None
    assert group.detail_url == "/bridge/ig-cards/episode-alpha/publish"
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

    assert len(projection.items) == 1
    group = projection.items[0]
    by_platform = {target.platform: target for target in group.targets}
    assert by_platform["instagram"].status == "published"
    assert by_platform["facebook_page"].status == "pending"
    assert group.phase == "attention"
    assert group.calendar_at is None
    assert group.progress_label == "1/2 published"


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
    assert item.targets[0].platform_label == "Podcast YouTube · Community handoff"
    assert item.targets[0].receipt_id == "receipt-youtube_community"
    assert item.phase == "published"
    assert item.date_basis == "published"


def test_carousel_published_checkpoint_without_timezone_is_diagnostic_no_date(
    tmp_path: Path,
) -> None:
    fingerprint = "0" * 64
    created_at = datetime(2026, 8, 31, 16, 30, tzinfo=UTC)
    _write_job(
        tmp_path,
        job_hex="5",
        fingerprint=fingerprint,
        created_at=created_at,
        states=[
            _state(
                "youtube_community",
                "published",
                datetime(2026, 8, 31, 16, 30),
                fingerprint,
            )
        ],
        job_status="completed",
    )

    projection = build_publish_calendar(tmp_path)

    assert projection.items[0].phase == "published"
    assert projection.items[0].calendar_at is None
    assert [diagnostic.code for diagnostic in projection.diagnostics] == [
        "carousel_completed_at_invalid"
    ]


def test_release_pipeline_phase_mapping_and_one_anchor_one_placement(tmp_path: Path) -> None:
    cases = [
        ("draft", "draft", None, "needs_review"),
        ("ready", "approved", None, "ready_to_schedule"),
        ("scheduled", "approved", datetime(2026, 8, 25, tzinfo=UTC), "scheduled"),
        ("running", "uploading", None, "in_progress"),
        ("failed", "failed", None, "attention"),
        ("done", "published", None, "published"),
    ]
    for cut_id, status, anchor, _phase in cases:
        release_id = register_release("episode-phases", cut_id, "short", f"{cut_id}.mp4")
        for platform in ("youtube", "instagram_reels", "facebook_reels"):
            target_id = ensure_target(release_id, platform)
            update_target(target_id, status=status)
        if anchor is not None:
            current = get_release_campaign_anchor("episode-phases", cut_id)
            set_release_campaign_anchor(
                "episode-phases",
                cut_id,
                anchor,
                expected_anchor_token=current.expected_token,
            )

    projection = build_publish_calendar(tmp_path)
    by_cut = {item.content_id: item for item in projection.items}

    assert {cut_id: by_cut[cut_id].phase for cut_id, *_ in cases} == {
        cut_id: phase for cut_id, _status, _anchor, phase in cases
    }
    assert len(by_cut["scheduled"].targets) == 3
    assert by_cut["scheduled"].calendar_at.isoformat() == "2026-08-25T08:00:00+08:00"
    assert (
        by_cut["scheduled"].expected_anchor_token
        == get_release_campaign_anchor("episode-phases", "scheduled").expected_token
    )


def test_release_partial_failure_takes_precedence_over_in_progress(tmp_path: Path) -> None:
    release_id = register_release("episode-partial", "S01", "short", "S01.mp4")
    statuses = {
        "youtube": "uploading",
        "instagram_reels": "failed",
        "facebook_reels": "approved",
    }
    for platform, status in statuses.items():
        target_id = ensure_target(release_id, platform)
        update_target(target_id, status=status)

    group = build_publish_calendar(tmp_path).items[0]

    assert group.phase == "attention"


def test_ineligible_target_is_visible_but_excluded_from_release_completion(tmp_path: Path) -> None:
    release_id = register_release("episode-coverage", "S74", "short", "S74.mp4")
    statuses = {
        "youtube": "published",
        "instagram_reels": "published",
        "facebook_reels": "ineligible",
    }
    for platform, status in statuses.items():
        target_id = ensure_target(release_id, platform)
        update_target(target_id, status=status)

    group = build_publish_calendar(tmp_path).items[0]

    assert group.phase == "published"
    assert group.progress_label == "2/2 published"
    assert {target.platform: target.status for target in group.targets} == statuses


def test_carousel_partial_failure_takes_precedence_over_in_progress(tmp_path: Path) -> None:
    fingerprint = "1" * 64
    updated_at = datetime(2026, 8, 25, tzinfo=UTC)
    in_progress = CarouselPublishTargetState(
        platform="instagram",
        strategy="meta_api",
        idempotency_key=_target_idempotency_key(fingerprint, "instagram"),
        status="in_progress",
        attempt_count=1,
        attempt_id=f"pa-{'c' * 32}",
        updated_at=updated_at,
    )
    _write_job(
        tmp_path,
        job_hex="6",
        fingerprint=fingerprint,
        created_at=updated_at,
        states=[_state("facebook_page", "failed", updated_at, fingerprint), in_progress],
        job_status="in_progress",
    )

    group = build_publish_calendar(tmp_path).items[0]

    assert group.phase == "attention"


def test_divergent_release_anchors_are_attention_and_never_choose_a_date(tmp_path: Path) -> None:
    release_id = register_release("episode-divergent", "S01", "short", "S01.mp4")
    youtube = ensure_target(release_id, "youtube")
    instagram = ensure_target(release_id, "instagram_reels")
    update_target(youtube, status="approved", publish_at="2026-08-25T01:00:00+00:00")
    update_target(instagram, status="approved", publish_at="2026-08-26T01:00:00+00:00")

    projection = build_publish_calendar(tmp_path)

    assert projection.items[0].calendar_at is None
    assert projection.items[0].phase == "attention"
    assert any(item.code == "release_campaign_anchor_divergent" for item in projection.diagnostics)


def test_queued_carousel_campaign_anchor_creates_one_scheduled_group(tmp_path: Path) -> None:
    fingerprint = "f" * 64
    anchor = datetime(2026, 8, 25, 1, tzinfo=UTC)
    _write_job(
        tmp_path,
        job_hex="5",
        fingerprint=fingerprint,
        created_at=datetime(2026, 8, 19, tzinfo=UTC),
        states=[
            _state("facebook_page", "pending", anchor, fingerprint),
            _state("instagram", "pending", anchor, fingerprint),
        ],
        job_status="queued",
        campaign_anchor_at=anchor,
    )

    group = build_publish_calendar(tmp_path).items[0]

    assert group.phase == "scheduled"
    assert group.date_basis == "scheduled"
    assert group.calendar_at.isoformat() == "2026-08-25T09:00:00+08:00"
    assert len(group.targets) == 2
    assert group.expected_anchor_token == carousel_campaign_anchor_token(anchor)


def test_future_native_armed_short_remains_scheduled_and_depends_on_due_worker(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    anchor = now + timedelta(days=1)
    canonical = tmp_path / "S-native.mp4"
    canonical.write_bytes(b"short")
    release_id = register_release("episode", "S-native", "short", str(canonical))
    target_ids = {
        platform: ensure_target(release_id, platform)
        for platform in ("youtube", "instagram_reels", "facebook_reels")
    }
    for target_id in target_ids.values():
        update_target(target_id, status="approved")
    update_target(target_ids["youtube"], title="Future Short", description="已審文案")
    current = get_release_campaign_anchor("episode", "S-native")
    set_release_campaign_anchor(
        "episode",
        "S-native",
        anchor,
        expected_anchor_token=current.expected_token,
    )
    update_target(target_ids["youtube"], status="uploaded")
    update_target(target_ids["facebook_reels"], status="uploaded")

    projection = build_publish_calendar(tmp_path, now=now)

    assert projection.items[0].phase == "scheduled"
    assert projection.items[0].execution_ready is False
    assert projection.items[0].execution_reason == "已核准；等待 worker 認領或投遞啟動中。"
    assert future_short_requires_due_worker(projection.items, now=now) is True


def test_due_unfinished_native_armed_short_becomes_in_progress(tmp_path: Path) -> None:
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    release_id = register_release("episode", "S-due", "short", "S-due.mp4")
    target_ids = [
        ensure_target(release_id, platform)
        for platform in ("youtube", "instagram_reels", "facebook_reels")
    ]
    for target_id in target_ids:
        update_target(target_id, status="approved")
    current = get_release_campaign_anchor("episode", "S-due")
    set_release_campaign_anchor(
        "episode", "S-due", now, expected_anchor_token=current.expected_token
    )
    update_target(target_ids[0], status="uploaded")
    update_target(target_ids[2], status="uploaded")

    assert build_publish_calendar(tmp_path, now=now).items[0].phase == "in_progress"


def test_due_uploaded_native_targets_wait_for_outcome_confirmation(tmp_path: Path) -> None:
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    anchor = now - timedelta(minutes=1)
    release_id = register_release("episode", "S-native", "short", str(tmp_path / "S.mp4"))
    for platform in ("youtube", "facebook_reels"):
        target_id = ensure_target(release_id, platform)
        update_target(
            target_id,
            status="uploaded",
            video_id=f"{platform}-1",
            publish_at=anchor.isoformat(),
        )

    item = build_publish_calendar(tmp_path, now=now).items[0]

    assert item.phase == "in_progress"
    assert item.progress_label == "0/2 published"
    assert all(target.confirmation_overdue for target in item.targets)
    assert all(target.status == "uploaded" for target in item.targets)


def test_short_due_worker_health_maps_never_online_stale_and_failing():
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    online_row = Heartbeat(
        job_name="usopp-short-due-dispatcher",
        last_success_at=now - timedelta(minutes=1),
        last_run_at=now - timedelta(minutes=1),
        last_status="success",
        last_error=None,
        consecutive_failures=0,
        updated_at=now - timedelta(minutes=1),
    )
    stale_row = Heartbeat(
        **{
            **online_row.__dict__,
            "last_success_at": now - SHORT_DUE_WORKER_STALE_AFTER - timedelta(seconds=1),
            "last_run_at": now - SHORT_DUE_WORKER_STALE_AFTER - timedelta(seconds=1),
        }
    )
    failing_row = Heartbeat(
        **{
            **online_row.__dict__,
            "last_status": "fail",
            "last_error": "one target failed",
            "consecutive_failures": 2,
        }
    )

    assert short_due_worker_health(None, now=now).state == "never_seen"
    assert short_due_worker_health(online_row, now=now).state == "online"
    assert short_due_worker_health(stale_row, now=now).state == "stale"
    assert short_due_worker_health(failing_row, now=now).state == "failing"


def test_outcome_reconciler_health_has_independent_identity_and_states():
    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    online = Heartbeat(
        job_name="usopp-release-outcome-reconciler",
        last_success_at=now,
        last_run_at=now,
        last_status="success",
        last_error=None,
        consecutive_failures=0,
        updated_at=now,
    )
    stale = Heartbeat(
        **{
            **online.__dict__,
            "last_run_at": now - OUTCOME_RECONCILER_STALE_AFTER - timedelta(seconds=1),
        }
    )
    jitter = Heartbeat(
        **{
            **online.__dict__,
            "last_run_at": now - timedelta(minutes=5, seconds=1),
        }
    )
    failing = Heartbeat(
        **{
            **online.__dict__,
            "last_status": "fail",
            "last_error": "one observation uncertain",
            "consecutive_failures": 1,
        }
    )

    assert outcome_reconciler_health(None, now=now).state == "never_seen"
    assert outcome_reconciler_health(online, now=now).state == "online"
    assert outcome_reconciler_health(jitter, now=now).state == "online"
    assert outcome_reconciler_health(stale, now=now).state == "stale"
    assert outcome_reconciler_health(failing, now=now).state == "failing"
