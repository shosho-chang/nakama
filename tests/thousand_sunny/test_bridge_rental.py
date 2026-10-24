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
                floor="3F/12F",
            ),
            _listing(listing_id="3", title="太遠的物件", mrt_distance=900, floor="7F/12F"),
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
        data={
            "key": "591:1",
            "verdict": "interested",
            "reason": "地點好",
            "return_to": "sec-match",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # 回到補位的地方（這裡是原本的區塊），不是被移到「有興趣」的那一筆本身
    assert resp.headers["location"] == "/bridge/rental?saved=1&k=591%3A1#sec-match"
    html = c.get(resp.headers["location"]).text
    assert 'id="sec-interested"' in html
    assert "地點好" in html
    assert "已把「想看的物件」標為有興趣" in html

    resp = c.post(
        "/bridge/rental/feedback",
        data={"key": "591:1", "verdict": "clear", "return_to": "sec-interested"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/bridge/rental?saved=0&k=591%3A1#sec-interested"
    html = c.get(resp.headers["location"]).text
    assert 'id="sec-interested"' not in html
    assert "已取消「想看的物件」的標記" in html


def test_each_form_returns_to_the_row_that_takes_its_place(client, db_path):
    c, _ = client
    _seed(
        db_path,
        [
            _listing(listing_id="1", title="第一筆", price=21000),
            _listing(listing_id="2", title="第二筆", price=22000, floor="8F/12F"),
        ],
    )
    html = c.get("/bridge/rental").text
    first = html[html.index('id="r-591-1"') : html.index('id="r-591-2"')]
    second = html[html.index('id="r-591-2"') :]
    assert '<input type="hidden" name="return_to" value="r-591-2">' in first
    # 區塊最後一筆 → 回到區塊本身
    assert '<input type="hidden" name="return_to" value="sec-' in second
    assert "/static/shosho/bridge-rental.js" in html


def test_feedback_with_js_skips_anchor(client, db_path):
    c, _ = client
    _seed(db_path, [_listing()])
    resp = c.post(
        "/bridge/rental/feedback",
        data={"key": "591:1", "verdict": "pass", "return_to": "r-591-2", "js": "1"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/bridge/rental?saved=1&k=591%3A1"


def test_feedback_rejects_odd_return_to(client, db_path):
    c, _ = client
    _seed(db_path, [_listing()])
    resp = c.post(
        "/bridge/rental/feedback",
        data={
            "key": "591:1",
            "verdict": "pass",
            "return_to": "x" + chr(13) + chr(10) + "Set-Cookie: a=b",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/bridge/rental?saved=1&k=591%3A1"


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


def _seed_same_unit(db_path: Path) -> None:
    """591 兩家仲介＋信義各刊一次同一間（坪數四捨五入、報價略不同），外加另一間。"""
    rw.run_once(
        db_path=db_path,
        fetchers={
            "591": lambda h, c: [
                _listing(listing_id="1", title="仲介甲刊的", area=32.0, price=30000),
                _listing(
                    listing_id="2", title="仲介乙刊的", area=32.0, price=31000, mrt_distance=450
                ),
                _listing(
                    listing_id="9", title="樓上另一間", area=32.0, price=30000, floor="9F/12F"
                ),
            ],
            "sinyi": lambda h, c: [
                _listing(
                    source="sinyi",
                    listing_id="C1",
                    title="信義刊的",
                    area=31.62,
                    price=30000,
                    floor="5/12",
                    url="https://www.sinyi.com.tw/rent/houseno/C1",
                )
            ],
        },
        http=object(),
        now=NOW,
    )


def test_same_unit_listings_render_as_one_row(client, db_path):
    c, _ = client
    _seed_same_unit(db_path)
    html = c.get("/bridge/rental").text
    assert html.count('class="rt-row ') == 2  # 同一間 + 樓上另一間
    assert "同一間還有 2 筆刊登" in html
    start = html.index('<details class="rt-dups">')
    dups = html[start : html.index("</details>", start)]
    assert "仲介乙刊的" in dups and "信義刊的" in dups and "樓上另一間" not in dups
    assert "31,000元" in dups and "大安 450m" in dups
    assert 'href="https://www.sinyi.com.tw/rent/houseno/C1"' in dups


def test_pass_on_one_listing_marks_the_whole_unit(client, db_path):
    c, _ = client
    _seed_same_unit(db_path)
    resp = c.post(
        "/bridge/rental/feedback",
        data={"key": "591:1", "verdict": "pass", "reason": "格局不好", "js": "1"},
        follow_redirects=False,
    )
    html = c.get(resp.headers["location"]).text
    assert "已把「仲介甲刊的」與同一間另 2 筆刊登標為不考慮" in html
    board = rw.load_board(db_path, rw.load_criteria(), now=NOW)
    verdicts = {i.listing.key: i.verdict for i in board.items}
    assert verdicts == {"591:1": "pass", "591:2": "pass", "sinyi:C1": "pass", "591:9": None}
    passed = html[html.index('id="sec-pass"') :]
    assert "仲介甲刊的" in passed and "格局不好" in passed

    resp = c.post(
        "/bridge/rental/feedback",
        data={"key": "591:1", "verdict": "clear", "js": "1"},
        follow_redirects=False,
    )
    board = rw.load_board(db_path, rw.load_criteria(), now=NOW)
    assert all(i.verdict is None for i in board.items)


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


_PANEL_FORM = {
    "districts": ["大安區", "文山區"],
    "bonus_districts": ["文山區"],
    "price_min": "20000",
    "price_max": "40000",
    "area_min": "10",
    "area_max": "50",
    "mrt_max_m": "600",
    "exclude_basement": "1",
    "action": "save",
}


def test_criteria_panel_renders_current_values(client):
    c, _ = client
    html = c.get("/bridge/rental").text
    assert 'id="criteria"' in html
    assert "目前：設定檔預設" in html
    assert 'name="districts" value="大安區" checked' in html
    assert 'name="districts" value="內湖區">' in html  # 沒勾
    assert "恢復設定檔預設" not in html


def test_criteria_save_then_reset(client, db_path):
    c, _ = client
    resp = c.post("/bridge/rental/criteria", data=_PANEL_FORM, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/bridge/rental?saved=criteria"
    assert rw.get_criteria_override(db_path)["districts"] == ["大安區", "文山區"]

    html = c.get("/bridge/rental?saved=criteria").text
    assert "目前：面板設定" in html
    assert "條件已儲存" in html
    assert 'name="districts" value="中正區">' in html  # 存檔後中正區不勾
    assert "恢復設定檔預設" in html

    resp = c.post("/bridge/rental/criteria", data={"action": "reset"}, follow_redirects=False)
    assert resp.headers["location"] == "/bridge/rental?saved=criteria-reset"
    assert rw.get_criteria_override(db_path) is None


def test_criteria_invalid_keeps_input_and_shows_errors(client, db_path):
    c, _ = client
    bad = {**_PANEL_FORM, "price_min": "50000", "bonus_districts": ["內湖區"]}
    resp = c.post("/bridge/rental/criteria", data=bad, follow_redirects=False)
    assert resp.status_code == 422
    assert "沒有存檔，請修正" in resp.text
    assert "加分區要在搜尋的行政區裡" in resp.text
    assert 'value="50000"' in resp.text  # 保留剛填的值
    assert '<details class="rt-edit" id="criteria" open>' in resp.text
    assert rw.get_criteria_override(db_path) is None


def test_criteria_requires_auth(client, monkeypatch):
    c, br = client
    monkeypatch.setattr(br, "check_auth", lambda cookie: False)
    resp = c.post("/bridge/rental/criteria", data=_PANEL_FORM, follow_redirects=False)
    assert resp.status_code == 302


def test_row_shows_thumbnail_and_gallery_from_best_posting(client, db_path):
    c, _ = client
    big = "https://img2.591.com.tw/house/1.jpg"
    rw.run_once(
        db_path=db_path,
        fetchers={
            "591": lambda h, c: [
                # 代表那筆（排序最前）沒照片，同一間另一家仲介有兩張 → 用那兩張
                _listing(listing_id="1", title="沒照片的", price=24000),
                _listing(
                    listing_id="2",
                    title="有照片的",
                    price=25000,
                    photos=(big, "https://img2.591.com.tw/house/2.jpg"),
                ),
                _listing(listing_id="3", title="另一間", floor="9F/12F"),
            ]
        },
        http=object(),
        now=NOW,
    )
    html = c.get("/bridge/rental").text
    row = html[html.index('id="r-591-1"') :]
    row = row[: row.index("</li>")]
    assert f'src="{big}!400x300.jpg"' in row
    assert 'referrerpolicy="no-referrer"' in row and 'loading="lazy"' in row
    assert f"{big}!1000x750.jpg" in row  # 燈箱大圖在 data-gallery
    assert "2 張" in row
    other = html[html.index('id="r-591-3"') :]
    assert "沒有照片" in other[: other.index("</li>")]
    assert 'id="rt-gallery"' in html
