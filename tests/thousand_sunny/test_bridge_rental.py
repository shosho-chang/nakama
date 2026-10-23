"""/bridge/rental 租屋追蹤頁 router 測試。

Minimal app 掛 bridge_rental.page_router + dev auth bypass + ``RENTAL_WATCH_DB`` 指到 tmp，
驗 GET 空狀態／分層渲染、POST 標記寫入與 303、未登入導回 login。
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.franky.jobs import rental_watch as rw

NOW = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)


def _listing(**overrides) -> rw.Listing:
    base = dict(
        source="591",
        listing_id="1",
        title="大安捷運旁辦公室",
        url="https://business.591.com.tw/rent/1",
        district="大安區",
        price=25000,
        area=20.0,
        floor="5F/12F",
        is_basement=False,
        is_rooftop=False,
        kind="辦公",
        mrt_station="大安",
        mrt_distance=300,
        registration="可登記",
    )
    base.update(overrides)
    return rw.Listing(**base)


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "rental.db"
    monkeypatch.setenv("RENTAL_WATCH_DB", str(p))
    return p


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setenv("NAKAMA_DEV_AUTH_BYPASS", "1")
    import thousand_sunny.auth as auth

    importlib.reload(auth)
    import thousand_sunny.routers.bridge_rental as br

    importlib.reload(br)
    app = FastAPI()
    app.include_router(br.page_router)
    return TestClient(app), br


def _seed(db_path: Path, listings: list[rw.Listing]) -> None:
    rw.run_once(
        db_path=db_path,
        fetchers={"591": lambda h, c: list(listings)},
        http=object(),  # 這些 listing 都標了可登記，不會打詳細頁
        now=NOW,
    )


def test_empty_state_without_db(client):
    c, _ = client
    resp = c.get("/bridge/rental")
    assert resp.status_code == 200
    assert "還沒有掃描資料" in resp.text
    assert "租屋追蹤" in resp.text


def test_renders_tiers_reasons_and_folds(client, db_path):
    c, _ = client
    _seed(
        db_path,
        [
            _listing(listing_id="1", title="符合的辦公室"),
            _listing(
                listing_id="2",
                title="待確認的辦公室",
                registration="暫不確定",
            ),
            _listing(listing_id="3", title="太遠的物件", mrt_distance=900),
        ],
    )
    resp = c.get("/bridge/rental")
    html = resp.text
    assert resp.status_code == 200
    assert html.index("符合的辦公室") < html.index("待確認的辦公室") < html.index("太遠的物件")
    assert "可否登記：暫不確定，要問" in html
    assert "離捷運 900m，超過 600m" in html
    assert '<details class="rt-sec rt-sec--fold" id="sec-out">' in html
    assert 'href="https://business.591.com.tw/rent/1"' in html


def test_feedback_post_marks_and_redirects(client, db_path):
    c, br = client
    _seed(db_path, [_listing(listing_id="1", title="想看的物件")])
    resp = c.post(
        "/bridge/rental/feedback",
        data={"key": "591:1", "verdict": "interested", "reason": "地點好"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/bridge/rental?saved=1#r-591-1"
    html = c.get("/bridge/rental?saved=1").text
    assert 'id="sec-interested"' in html
    assert "地點好" in html
    assert "已標記" in html

    resp = c.post(
        "/bridge/rental/feedback",
        data={"key": "591:1", "verdict": "clear"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/bridge/rental?saved=0#r-591-1"
    assert 'id="sec-interested"' not in c.get("/bridge/rental").text


def test_feedback_unknown_verdict_is_ignored(client, db_path):
    c, _ = client
    _seed(db_path, [_listing()])
    resp = c.post(
        "/bridge/rental/feedback",
        data={"key": "591:1", "verdict": "maybe"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/bridge/rental"
    board = rw.load_board(db_path, rw.load_criteria(), now=NOW)
    assert board.items[0].verdict is None


def test_same_unit_hint_across_sites(client, db_path):
    c, _ = client
    rw.run_once(
        db_path=db_path,
        fetchers={
            "591": lambda h, c: [_listing(listing_id="1", area=32.0, price=30000)],
            "sinyi": lambda h, c: [
                _listing(
                    source="sinyi",
                    listing_id="C1",
                    area=31.62,
                    price=30000,
                    url="https://www.sinyi.com.tw/rent/houseno/C1",
                )
            ],
        },
        http=object(),
        now=NOW,
    )
    assert "可能是同一間：信義 也有刊" in c.get("/bridge/rental").text


def test_requires_auth(client, monkeypatch):
    c, br = client
    monkeypatch.setattr(br, "check_auth", lambda cookie: False)
    resp = c.get("/bridge/rental", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?next=/bridge/rental"
    resp = c.post(
        "/bridge/rental/feedback",
        data={"key": "591:1", "verdict": "interested"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
