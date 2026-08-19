from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.publish_calendar import CalendarDiagnostic, CalendarItem, CalendarProjection

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
                platform="youtube",
                platform_label="Podcast YouTube",
                status="approved",
                calendar_at=datetime(2026, 9, 1, 9, 0, tzinfo=TAIPEI),
                date_basis="scheduled",
                detail_url="/bridge/publish/episode-alpha/S01",
            ),
            CalendarItem(
                item_id="carousel:episode-beta:x:instagram",
                episode="episode-beta",
                content_id="r026",
                title="Carousel 已發布但日期未知",
                content_type="carousel",
                platform="instagram",
                platform_label="Instagram Carousel",
                status="published",
                calendar_at=None,
                date_basis=None,
                detail_url="/bridge/ig-cards/episode-beta/publish",
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
    assert "published · 日期未定" in response.text
    assert "待排程" not in response.text


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
