from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.heartbeat import Heartbeat
from shared.publish_calendar import (
    CalendarDiagnostic,
    CalendarItem,
    CalendarProjection,
    PlatformTargetView,
)

TAIPEI = ZoneInfo("Asia/Taipei")


def _projection() -> CalendarProjection:
    return CalendarProjection(
        items=(
            CalendarItem(
                item_id="release:1:1",
                episode="episode-alpha",
                content_id="S01",
                title="Short 已排程",
                content_type="short",
                targets=(PlatformTargetView("youtube", "Podcast YouTube", "approved"),),
                phase="scheduled",
                calendar_at=datetime(2026, 9, 1, 9, 0, tzinfo=TAIPEI),
                date_basis="scheduled",
                detail_url="/bridge/publish/episode-alpha/S01",
                schedule_kind="release",
                schedule_id="S01",
                schedule_editable=True,
                expected_anchor_token="release-anchor-v1:test",
            ),
            CalendarItem(
                item_id="carousel:episode-beta:x:instagram",
                episode="episode-beta",
                content_id="r026",
                title="Carousel 已發布但日期未知",
                content_type="carousel",
                targets=(PlatformTargetView("instagram", "Instagram Carousel", "published"),),
                phase="published",
                calendar_at=None,
                date_basis=None,
                detail_url="/bridge/ig-cards/episode-beta/publish",
                schedule_kind="carousel",
                schedule_id=f"pj-{'a' * 32}",
                schedule_editable=False,
                expected_anchor_token="carousel-anchor-v1:none",
                schedule_disabled_reason="發布執行已開始，Campaign Anchor 已鎖定。",
            ),
        ),
        diagnostics=(
            CalendarDiagnostic(
                code="carousel_job_invalid",
                message="episode-beta 有一筆 Carousel 發布紀錄無法讀取，其他資料仍可使用。",
                episode="episode-beta",
                source="pj-broken.json",
            ),
        ),
    )


@pytest.fixture
def calendar_client(monkeypatch) -> TestClient:
    import thousand_sunny.routers.publish_calendar as calendar_router

    monkeypatch.setattr(calendar_router, "check_auth", lambda _cookie: True)
    monkeypatch.setattr(calendar_router, "build_publish_calendar", lambda _root: _projection())
    app = FastAPI()
    app.include_router(calendar_router.page_router)
    return TestClient(app, follow_redirects=False)


def test_calendar_route_requires_login_and_preserves_safe_relative_query(monkeypatch) -> None:
    import thousand_sunny.routers.publish_calendar as calendar_router

    monkeypatch.setattr(calendar_router, "check_auth", lambda _cookie: False)
    app = FastAPI()
    app.include_router(calendar_router.page_router)
    client = TestClient(app, follow_redirects=False)

    response = client.get("/bridge/publish/calendar?month=2026-09&episode=episode-alpha")

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("/login?next=")
    next_url = parse_qs(urlparse(location).query)["next"][0]
    assert next_url == "/bridge/publish/calendar?month=2026-09&episode=episode-alpha"

    from thousand_sunny.routers.auth import _safe_next

    assert _safe_next(next_url) == next_url
    assert _safe_next("https://evil.example/steal") == "/"


def test_calendar_route_renders_dated_and_backlog_with_existing_detail_links(
    calendar_client: TestClient,
) -> None:
    response = calendar_client.get("/bridge/publish/calendar?month=2026-09&episode=all")

    assert response.status_code == 200
    assert "Short 已排程" in response.text
    assert "Carousel 已發布但日期未知" in response.text
    assert 'href="/bridge/publish/episode-alpha/S01"' in response.text
    assert 'href="/bridge/ig-cards/episode-beta/publish"' in response.text
    assert "Podcast YouTube" in response.text
    assert "@abnormal-human-research" in response.text
    assert "UCvipegP35x3-OcAs--PgAig" in response.text
    assert "未列入月曆／日期未定" in response.text
    assert "已發布 · 1/1 published · 日期未定" in response.text
    assert "待排程" in response.text
    assert 'data-phase="scheduled"' in response.text
    assert "一組內容 · 一個 Campaign Anchor · 各平台狀態獨立" in response.text
    assert "Instagram Carousel · published" in response.text
    assert 'name="campaign_anchor_local" value="2026-09-01T09:00"' in response.text
    assert 'name="operation" value="clear"' in response.text
    assert "發布執行已開始，Campaign Anchor 已鎖定。" in response.text
    assert 'type="button" disabled' in response.text


def test_future_instagram_dependency_warns_until_due_worker_is_online(monkeypatch) -> None:
    import thousand_sunny.routers.publish_calendar as calendar_router

    now = datetime(2026, 8, 20, 1, tzinfo=UTC)
    future = replace(
        _projection().items[0],
        targets=(
            PlatformTargetView("youtube", "Podcast YouTube", "uploaded"),
            PlatformTargetView("instagram_reels", "Instagram Reels", "approved"),
            PlatformTargetView("facebook_reels", "Facebook Page Reels", "uploaded"),
        ),
        calendar_at=datetime(2026, 8, 25, 9, tzinfo=TAIPEI),
    )
    monkeypatch.setattr(calendar_router, "check_auth", lambda _cookie: True)
    monkeypatch.setattr(
        calendar_router,
        "build_publish_calendar",
        lambda _root: CalendarProjection(items=(future,), diagnostics=()),
    )
    monkeypatch.setattr(calendar_router, "_utc_now", lambda: now)
    monkeypatch.setattr(calendar_router, "get_heartbeat", lambda _job: None)
    app = FastAPI()
    app.include_router(calendar_router.page_router)
    client = TestClient(app)

    missing = client.get("/bridge/publish/calendar?month=2026-08")
    assert missing.status_code == 200
    assert 'data-worker-health="never_seen"' in missing.text
    assert 'role="alert"' in missing.text
    assert "publish_due.py --watch --execute" in missing.text

    online = Heartbeat(
        job_name="usopp-short-due-dispatcher",
        last_success_at=now,
        last_run_at=now,
        last_status="success",
        last_error=None,
        consecutive_failures=0,
        updated_at=now,
    )
    monkeypatch.setattr(calendar_router, "get_heartbeat", lambda _job: online)
    healthy = client.get("/bridge/publish/calendar?month=2026-08")
    assert 'data-worker-health="online"' in healthy.text
    assert 'role="alert"' not in healthy.text
    assert "LAST RUN" in healthy.text
    assert "LAST SUCCESS" in healthy.text
    assert "FAILURE STREAK" in healthy.text


def test_episode_filter_applies_to_dated_items_and_backlog_and_preserves_month(
    calendar_client: TestClient,
) -> None:
    response = calendar_client.get("/bridge/publish/calendar?month=2026-09&episode=episode-beta")

    assert response.status_code == 200
    assert "Carousel 已發布但日期未知" in response.text
    assert "Short 已排程" not in response.text
    assert 'name="month" value="2026-09"' in response.text
    assert '<option value="episode-beta" selected>' in response.text
    assert '<option value="episode-alpha">' in response.text
    assert "部分資料無法讀取" in response.text
    assert "pj-broken.json" in response.text


@pytest.mark.parametrize("month", ["2026-9", "2026-00", "2026-13", "garbage"])
def test_invalid_month_fails_closed(calendar_client: TestClient, month: str) -> None:
    response = calendar_client.get(f"/bridge/publish/calendar?month={month}")

    assert response.status_code == 400


def test_unknown_or_unsafe_episode_filter_fails_closed(calendar_client: TestClient) -> None:
    assert (
        calendar_client.get("/bridge/publish/calendar?month=2026-09&episode=missing").status_code
        == 400
    )
    assert (
        calendar_client.get(
            "/bridge/publish/calendar?month=2026-09&episode=..%2Fsecret"
        ).status_code
        == 400
    )


def test_calendar_render_path_needs_no_platform_credentials_or_external_api(
    tmp_path: Path, monkeypatch
) -> None:
    import thousand_sunny.routers.publish_calendar as calendar_router

    for name in (
        "META_PAGE_ACCESS_TOKEN",
        "META_MEDIA_R2_ACCESS_KEY_ID",
        "YOUTUBE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(tmp_path))
    monkeypatch.setattr(calendar_router, "check_auth", lambda _cookie: True)
    app = FastAPI()
    app.include_router(calendar_router.page_router)

    response = TestClient(app).get("/bridge/publish/calendar?month=2026-09")

    assert response.status_code == 200
    assert "這個月份沒有可信日期的發布項目" in response.text


def test_release_schedule_converts_taipei_local_to_shared_utc_and_preserves_view(
    calendar_client: TestClient, monkeypatch
) -> None:
    import thousand_sunny.routers.publish_calendar as calendar_router

    captured: list[tuple[str, str, datetime | None, str]] = []
    monkeypatch.setattr(
        calendar_router,
        "set_release_campaign_anchor",
        lambda episode, cut_id, anchor, *, expected_anchor_token: captured.append(
            (episode, cut_id, anchor, expected_anchor_token)
        ),
    )

    response = calendar_client.post(
        "/bridge/publish/calendar/release/episode-alpha/S01/schedule",
        data={
            "operation": "set",
            "campaign_anchor_local": "2026-09-02T09:30",
            "expected_anchor_token": "release-anchor-v1:test",
            "month": "2026-09",
            "return_episode": "episode-alpha",
        },
    )

    assert response.status_code == 303
    assert captured == [
        (
            "episode-alpha",
            "S01",
            datetime(2026, 9, 2, 1, 30, tzinfo=UTC),
            "release-anchor-v1:test",
        )
    ]
    assert response.headers["location"] == (
        "/bridge/publish/calendar?month=2026-09&episode=episode-alpha"
    )


def test_release_unschedule_clears_shared_anchor(calendar_client: TestClient, monkeypatch) -> None:
    import thousand_sunny.routers.publish_calendar as calendar_router

    captured: list[datetime | None] = []
    monkeypatch.setattr(
        calendar_router,
        "set_release_campaign_anchor",
        lambda _episode, _cut_id, anchor, *, expected_anchor_token: captured.append(anchor),
    )

    response = calendar_client.post(
        "/bridge/publish/calendar/release/episode-alpha/S01/schedule",
        data={
            "operation": "clear",
            "expected_anchor_token": "release-anchor-v1:test",
            "month": "2026-09",
            "return_episode": "all",
        },
    )

    assert response.status_code == 303
    assert captured == [None]


def test_carousel_schedule_updates_queued_job_and_preserves_view(
    calendar_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    import thousand_sunny.routers.publish_calendar as calendar_router

    package_root = tmp_path / "episode-alpha" / "ig-carousel"
    package_root.mkdir(parents=True)
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(tmp_path))
    captured: list[tuple[Path, datetime | None]] = []
    monkeypatch.setattr(
        calendar_router,
        "set_publish_job_campaign_anchor",
        lambda path, *, campaign_anchor_at, expected_anchor_token: captured.append(
            (path, campaign_anchor_at)
        ),
    )
    job_id = f"pj-{'b' * 32}"

    response = calendar_client.post(
        f"/bridge/publish/calendar/carousel/episode-alpha/{job_id}/schedule",
        data={
            "operation": "set",
            "campaign_anchor_local": "2026-09-03T09:00",
            "expected_anchor_token": "carousel-anchor-v1:none",
            "month": "2026-09",
            "return_episode": "episode-alpha",
        },
    )

    assert response.status_code == 303
    assert captured == [
        (
            package_root / "publish_jobs" / f"{job_id}.json",
            datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
        )
    ]
    assert response.headers["location"].endswith("month=2026-09&episode=episode-alpha")


@pytest.mark.parametrize(
    ("path", "data", "expected_status"),
    [
        (
            "/bridge/publish/calendar/release/episode-alpha/S01/schedule",
            {
                "operation": "set",
                "campaign_anchor_local": "not-a-date",
                "expected_anchor_token": "release-anchor-v1:test",
                "month": "2026-09",
            },
            400,
        ),
        (
            "/bridge/publish/calendar/release/episode-alpha/S01/schedule",
            {
                "operation": "clear",
                "expected_anchor_token": "release-anchor-v1:test",
                "month": "2026-9",
            },
            400,
        ),
        (
            "/bridge/publish/calendar/release/episode-alpha/bad:cut/schedule",
            {
                "operation": "clear",
                "expected_anchor_token": "release-anchor-v1:test",
                "month": "2026-09",
            },
            400,
        ),
    ],
)
def test_schedule_routes_fail_closed(
    calendar_client: TestClient, path: str, data: dict[str, str], expected_status: int
) -> None:
    assert calendar_client.post(path, data=data).status_code == expected_status


def test_schedule_route_requires_authentication_before_mutation(monkeypatch) -> None:
    import thousand_sunny.routers.publish_calendar as calendar_router

    called = False

    def mutate(*_args) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(calendar_router, "check_auth", lambda _cookie: False)
    monkeypatch.setattr(calendar_router, "set_release_campaign_anchor", mutate)
    app = FastAPI()
    app.include_router(calendar_router.page_router)

    response = TestClient(app).post(
        "/bridge/publish/calendar/release/episode-alpha/S01/schedule",
        data={
            "operation": "clear",
            "expected_anchor_token": "release-anchor-v1:test",
            "month": "2026-09",
        },
    )

    assert response.status_code == 401
    assert called is False


def test_illegal_release_transition_is_conflict(calendar_client: TestClient, monkeypatch) -> None:
    import thousand_sunny.routers.publish_calendar as calendar_router

    def reject(*_args, **_kwargs) -> None:
        raise ValueError("Campaign Anchor is locked")

    monkeypatch.setattr(calendar_router, "set_release_campaign_anchor", reject)

    response = calendar_client.post(
        "/bridge/publish/calendar/release/episode-alpha/S01/schedule",
        data={
            "operation": "clear",
            "expected_anchor_token": "release-anchor-v1:test",
            "month": "2026-09",
        },
    )

    assert response.status_code == 409


def test_release_open_page_stale_anchor_cannot_overwrite_newer_schedule(
    calendar_client: TestClient, monkeypatch
) -> None:
    import thousand_sunny.routers.publish_calendar as calendar_router

    open_page_token = "release-anchor-v1:test"
    response = calendar_client.get("/bridge/publish/calendar?month=2026-09&episode=all")
    assert f'name="expected_anchor_token" value="{open_page_token}"' in response.text
    first_writer_anchor = datetime(2026, 9, 5, 1, tzinfo=UTC)
    current = {"token": "release-anchor-v1:newer", "anchor": first_writer_anchor}

    def compare_and_set(
        _episode: str,
        _cut_id: str,
        anchor: datetime | None,
        *,
        expected_anchor_token: str,
    ) -> None:
        if expected_anchor_token != current["token"]:
            raise ValueError("stale Campaign Anchor; reload before scheduling")
        current.update(token="release-anchor-v1:written", anchor=anchor)

    monkeypatch.setattr(calendar_router, "set_release_campaign_anchor", compare_and_set)

    stale_response = calendar_client.post(
        "/bridge/publish/calendar/release/episode-alpha/S01/schedule",
        data={
            "operation": "set",
            "campaign_anchor_local": "2026-09-06T09:00",
            "expected_anchor_token": open_page_token,
            "month": "2026-09",
            "return_episode": "all",
        },
    )

    assert stale_response.status_code == 409
    assert current == {"token": "release-anchor-v1:newer", "anchor": first_writer_anchor}


def test_carousel_open_page_stale_anchor_cannot_overwrite_newer_schedule(
    calendar_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    import thousand_sunny.routers.publish_calendar as calendar_router

    open_page_token = "carousel-anchor-v1:none"
    queued_carousel = replace(
        _projection().items[1],
        targets=(PlatformTargetView("instagram", "Instagram Carousel", "pending"),),
        phase="ready_to_schedule",
        schedule_editable=True,
        schedule_disabled_reason=None,
    )
    monkeypatch.setattr(
        calendar_router,
        "build_publish_calendar",
        lambda _root: CalendarProjection(items=(queued_carousel,), diagnostics=()),
    )
    response = calendar_client.get("/bridge/publish/calendar?month=2026-09&episode=all")
    assert f'name="expected_anchor_token" value="{open_page_token}"' in response.text
    package_root = tmp_path / "episode-alpha" / "ig-carousel"
    package_root.mkdir(parents=True)
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(tmp_path))
    first_writer_anchor = datetime(2026, 9, 5, 1, tzinfo=UTC)
    current = {
        "token": "carousel-anchor-v1:utc:2026-09-05T01:00:00+00:00",
        "anchor": first_writer_anchor,
    }

    def compare_and_set(
        _path: Path,
        *,
        campaign_anchor_at: datetime | None,
        expected_anchor_token: str,
    ) -> None:
        if expected_anchor_token != current["token"]:
            raise calendar_router.PublishJobTransitionError(
                "stale Campaign Anchor; reload before scheduling"
            )
        current.update(token="carousel-anchor-v1:written", anchor=campaign_anchor_at)

    monkeypatch.setattr(calendar_router, "set_publish_job_campaign_anchor", compare_and_set)
    job_id = f"pj-{'a' * 32}"

    stale_response = calendar_client.post(
        f"/bridge/publish/calendar/carousel/episode-alpha/{job_id}/schedule",
        data={
            "operation": "set",
            "campaign_anchor_local": "2026-09-06T09:00",
            "expected_anchor_token": open_page_token,
            "month": "2026-09",
            "return_episode": "all",
        },
    )

    assert stale_response.status_code == 409
    assert current == {
        "token": "carousel-anchor-v1:utc:2026-09-05T01:00:00+00:00",
        "anchor": first_writer_anchor,
    }
