"""Tests for agents/franky/jobs/rental_watch.py — 租屋物件追蹤 cron。

Fixtures 是照 2026-10-23 三站實際回應的結構手寫的（資料內容虛構，不含真實
房東姓名電話）。
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.franky.jobs import rental_assess as rw_assess
from agents.franky.jobs import rental_watch as rw

NOW = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)

CONFIG_YAML = """
districts: [中正區, 中山區, 大安區, 信義區, 文山區]
price: {min: 15000, max: 35000}
area: {min: 10, max: 50}
store_basement_only: true
exclude_rooftop: true
exclude_title_keywords: [免費會議室, 商務中心]
exclude_title_patterns: ['\\d+\\s*[~～\\-－至]\\s*\\d+\\s*人']
"""


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    p = tmp_path / "rental-watch.yaml"
    p.write_text(CONFIG_YAML, encoding="utf-8")
    return p


@pytest.fixture
def criteria(config_path: Path) -> rw.Criteria:
    return rw.load_criteria(config_path)


def _listing(**overrides: Any) -> rw.Listing:
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
    )
    base.update(overrides)
    return rw.Listing(**base)


# ---------------------------------------------------------------------------
# Fake HTTP
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, *, text: str = "", payload: Any = None) -> None:
        self.text = text
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeHttp:
    """依 URL 回應的假 Http；``routes`` 是 (predicate, handler) 清單。"""

    def __init__(self, routes: list) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _Resp:
        self.calls.append((method, url, kwargs))
        for match, handler in self.routes:
            if match(method, url, kwargs):
                return handler(method, url, kwargs)
        raise AssertionError(f"unexpected request {method} {url}")

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 591
# ---------------------------------------------------------------------------


def _591_item(i: int, **overrides: Any) -> dict[str, Any]:
    item = {
        "id": 22000000 + i,
        "kind": 6,
        "kind_name": "辦公",
        "address_img_title": f"測試辦公室{i}",
        "price": "25,000",
        "area": 20.5,
        "floor": 5,
        "floorStr": "5F/12F",
        "section_name": "大安區",
    }
    item.update(overrides)
    return item


def test_parse_591_item_basement_from_negative_floor():
    it = rw.parse_591_item(_591_item(1, floor=-1, floorStr="B1/7F", price="24,000"))
    assert it.is_basement is True
    assert it.price == 24000
    assert it.floor == "B1/7F"
    assert it.url == "https://business.591.com.tw/rent/22000001"
    assert it.key == "591:22000001"


def test_parse_591_item_rooftop():
    it = rw.parse_591_item(_591_item(2, floor=100, floorStr="頂樓加蓋/7F"))
    assert it.is_rooftop is True
    assert it.is_basement is False


def test_fetch_591_paginates_each_kind_and_keeps_dollar_signs(criteria):
    def handler(method, url, kwargs):
        kind = int(re.search(r"kind=(\d+)", url).group(1))
        first = int(re.search(r"firstRow=(\d+)", url).group(1))
        total = 35 if kind == 6 else 0
        n = max(0, min(30, total - first))
        items = [_591_item(first + i) for i in range(n)]
        return _Resp(payload={"status": 1, "records": str(total), "data": {"data": items}})

    http = FakeHttp([(lambda m, u, k: "bff-business.591" in u, handler)])
    out = rw.fetch_591(http, criteria)

    assert len(out) == 35
    urls = [u for _, u, _ in http.calls]
    # kind 6 兩頁 + kind 12 / 5 各一頁
    assert len(urls) == 4
    assert "multiPrice=15000$_35000$" in urls[0]
    assert "sectionid=1,3,5,7,12" in urls[0]
    assert "multiArea=10_50" in urls[0]


def test_fetch_591_bad_status_raises(criteria):
    http = FakeHttp([(lambda m, u, k: True, lambda *a: _Resp(payload={"status": 0}))])
    with pytest.raises(RuntimeError):
        rw.fetch_591(http, criteria)


# ---------------------------------------------------------------------------
# 信義
# ---------------------------------------------------------------------------


def _sinyi_item(hid: str, title: str, area: str, floor: str, address: str, price: str) -> str:
    return f"""
<div id="search_result_{hid}" class="search_result_item">
  <a href="houseno/{hid}" target="_blank">
    <div class="item_box item_box_right">
      <div class="item_titlebox"><span class="item_title">{title}</span></div>
      <div class="item_detailbox"><div class="detail_left left">
        <div class="detail_line1"><span><!-- 台北市士林區天母北路 --></span></div>
        <div class="detail_line2">
          <span><span class="num num-1">成屋</span></span>
          <span><span class="num">{area}</span>坪</span>
          <span><span class="num">{floor}</span>樓</span>
          <span><span class='num'>1廳1衛</span></span>
        </div>
        <div class="detail_line2"><span class="num-ss">
          <span class="num num-text">{address}</span>
        </span></div>
      </div>
      <div class="detail_right right"><div class="detail_price">
        <div class="price_new"><span class="num">{price}</span>元/月</div>
      </div></div></div>
    </div>
  </a>
</div>"""


def _sinyi_page(total: int, items: list[str]) -> str:
    return f"""<html><body>
<div id="search_result_count" class="left">共有 <div class="num">{total}</div>間房屋</div>
<div id="search_result_list" class="ddhouse">{"".join(items)}</div></body></html>"""


def test_sinyi_list_url(criteria):
    assert rw.sinyi_list_url(criteria, "office-use") == (
        "https://www.sinyi.com.tw/rent/list/Taipei-city/100-104-106-110-116-zip/office-use/"
        "15000-35000-price/10-50-area/index.html"
    )
    assert rw.sinyi_list_url(criteria, "store-use", 2).endswith("/10-50-area/2.html")


def test_parse_sinyi_page():
    html = _sinyi_page(
        2,
        [
            _sinyi_item(
                "C1", "和平東路邊間辦公", "31.62", "5/6", "台北市大安區和平東路三段", "30,000"
            ),
            _sinyi_item("C2", "地下室大空間", "40.00", "B1/7", "台北市中山區長春路", "28,000"),
        ],
    )
    total, items = rw.parse_sinyi_page(html, "辦公")
    assert total == 2
    a, b = items
    assert (a.listing_id, a.title, a.area, a.floor, a.district, a.price) == (
        "C1",
        "和平東路邊間辦公",
        31.62,
        "5/6",
        "大安區",
        30000,
    )
    assert a.url == "https://www.sinyi.com.tw/rent/houseno/C1"
    assert a.is_basement is False
    assert b.is_basement is True


def test_fetch_sinyi_follows_pages_until_total(criteria):
    page1 = _sinyi_page(
        3, [_sinyi_item(f"C{i}", f"物件{i}", "20", "3/5", "台北市中正區", "20,000") for i in (1, 2)]
    )
    page2 = _sinyi_page(3, [_sinyi_item("C3", "物件3", "20", "3/5", "台北市中正區", "20,000")])
    empty = _sinyi_page(0, [])

    def handler(method, url, kwargs):
        if "office-use" in url:
            return _Resp(text=page1 if url.endswith("index.html") else page2)
        return _Resp(text=empty)

    http = FakeHttp([(lambda m, u, k: "sinyi.com.tw" in u, handler)])
    out = rw.fetch_sinyi(http, criteria)
    assert [it.listing_id for it in out] == ["C1", "C2", "C3"]
    assert all(it.kind == "辦公" for it in out)


# ---------------------------------------------------------------------------
# 好房網
# ---------------------------------------------------------------------------


def _hf_b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def _hf_article(rid: str, title: str, addr: str, floor: str, price: str, area: str) -> str:
    return f"""<article class="DataList both"><div class="Data">
  <h3 class="title"><a href="/rent/house/{rid}/" title="{title}">{title}</a></h3>
  <address class="addr">{addr}</address>
  <span class="sectionList">
    <span class="level">0房(室)1廳1衛</span><span class="pattern">樓層：{floor}</span>
  </span>
</div><div class="info"><ul>
  <li class="InfoList">
    <span class="title">租金：</span><span class="infos num">{price} 元/月</span>
  </li>
  <li class="InfoList"><span class="title">坪數：</span><span class="infos">{area} 坪</span></li>
  <li class="InfoList num"><span class="title">更新：</span><span class="infos">1小時前</span></li>
</ul></div></article>"""


def test_housefun_request_body_is_not_double_encoded():
    body = rw.housefun_request_body({"PriceL": "15000", "PurposeID": "6"}).decode("utf-8")
    assert body.startswith("RequestPackage=")
    pkg = json.loads(body.removeprefix("RequestPackage="))
    # "15000" → base64 "MTUwMDA=" → percent-encode "MTUwMDA%3D"（不是 %253D）
    assert pkg["Data"]["PriceL"] == "MTUwMDA%3D"
    assert pkg["Data"]["PurposeID"] == "Ng%3D%3D"
    assert pkg["Data"]["CityId"] == ""
    assert set(pkg["Data"]) == set(rw._HOUSEFUN_FIELDS)
    assert base64.b64decode(pkg["Method"].replace("%3D", "=")).decode() == "INQUIRE"


def test_housefun_decode_response_error():
    with pytest.raises(RuntimeError, match="無效"):
        rw.housefun_decode_response({"Status": _hf_b64("-1"), "StatusMessage": _hf_b64("無效")})


def test_parse_housefun_html_basement_floor_range():
    html = _hf_article(
        "1968859", "獨立出入口可辦可倉", "台北市中山區農安街", "-1~1 / 7", "22,000", "24.11"
    )
    (it,) = rw.parse_housefun_html(html, "店面")
    assert it.listing_id == "1968859"
    assert it.district == "中山區"
    assert it.floor == "-1~1/7"
    assert it.is_basement is True
    assert (it.price, it.area) == (22000, 24.11)
    assert it.url == "https://rent.housefun.com.tw/rent/house/1968859/"


def test_fetch_housefun_groups_districts_and_paginates(criteria):
    seen_packages: list[dict] = []

    def handler(method, url, kwargs):
        pkg = json.loads(kwargs["content"].decode().removeprefix("RequestPackage="))
        seen_packages.append(pkg["Data"])
        page = base64.b64decode(urllib_unquote(pkg["Data"]["PMPage"])).decode()
        purpose = base64.b64decode(urllib_unquote(pkg["Data"]["PurposeID"])).decode()
        html = _hf_article(f"{purpose}{page}", "辦公", "台北市大安區", "3 / 5", "20,000", "20")
        return _Resp(
            payload={
                "Status": _hf_b64("1"),
                "Data": {"SearchContent": _hf_b64(html), "PageCount": _hf_b64(f"{page}/2")},
            }
        )

    http = FakeHttp([(lambda m, u, k: "search.ashx" in u, handler)])
    out = rw.fetch_housefun(http, criteria)

    # 2 用途 × 2 地區群組（3 + 2 區）× 2 頁
    assert len(seen_packages) == 8
    first = seen_packages[0]
    assert base64.b64decode(urllib_unquote(first["DataUnit"])).decode() == "1"
    assert first["AreaId3"]  # 第一組帶滿 3 區
    assert not seen_packages[2]["AreaId3"]  # 第二組只有信義、文山
    assert len(out) == 8


def urllib_unquote(s: str) -> str:
    from urllib.parse import unquote

    return unquote(s)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, True),
        ({"district": "內湖區"}, False),
        ({"price": 36000}, False),
        ({"price": 15000}, True),
        ({"area": 4.0}, False),
        ({"area": None}, True),
        ({"is_rooftop": True}, False),
        ({"kind": "店面"}, False),
        ({"kind": "店面", "is_basement": True}, True),
        ({"title": "台北車站5~7人辦公室"}, False),
        ({"title": "全新裝潢4-6人獨立辦公"}, False),
        ({"title": "含免費會議室"}, False),
    ],
)
def test_passes(criteria, overrides, expected):
    assert rw.passes(_listing(**overrides), criteria) is expected


def test_load_criteria_rejects_empty_districts(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "districts: []\nprice: {min: 1, max: 2}\narea: {min: 1, max: 2}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        rw.load_criteria(p)


def test_unknown_district_raises(criteria):
    bad = rw.Criteria(**{**criteria.__dict__, "districts": ("火星區",)})
    with pytest.raises(ValueError, match="火星區"):
        rw.sinyi_list_url(bad, "office-use")


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def test_format_listing_escapes_link_text():
    it = _listing(title="A&B <大空間> | 近捷運")
    line = rw.format_listing(it)
    assert "<https://business.591.com.tw/rent/1|A&amp;B &lt;大空間&gt; ｜ 近捷運>" in line
    assert "每坪 1,250" in line


def test_update_message_tiers_and_skips_out(criteria):
    urgent = _listing(
        listing_id="u",
        title="捷運旁便宜大空間",
        area=35.0,
        price=20000,
        mrt_station="大安",
        mrt_distance=150,
        registration="可登記",
    )
    match = _listing(listing_id="m", mrt_distance=400, registration="可登記")
    check = _listing(listing_id="c", source="sinyi", title="信義物件")
    out = _listing(listing_id="o", title="太遠的", mrt_distance=900, registration="可登記")
    peers = [_listing(listing_id=f"p{i}", price=30000) for i in range(3)]  # 讓大安區有中位數
    drop = _listing(listing_id="d", price=22000, mrt_distance=300, registration="可登記")
    a = rw_assess.assess_all([urgent, match, check, out, drop, *peers], criteria)

    msg = rw.format_update_message([urgent, match, check, out], [rw.PriceDrop(drop, 25000)], a)
    assert msg.index("🔥") < msg.index("✅") < msg.index("📉") < msg.index("❔")
    assert "太遠的" not in msg
    assert "~25,000~ → *22,000元*" in msg
    assert msg.startswith("🏠 租屋追蹤：3 筆新物件、1 筆降價（1 筆建議儘快聯繫）")
    assert rw.BRIDGE_URL in msg


def test_update_message_none_when_everything_is_out(criteria):
    out = _listing(mrt_distance=900, registration="可登記")
    a = rw_assess.assess_all([out], criteria)
    assert rw.format_update_message([out], [], a) is None


def test_update_message_caps_sections(criteria):
    new = [_listing(listing_id=str(i)) for i in range(rw.MAX_ITEMS_PER_SECTION + 3)]
    a = rw_assess.assess_all(new, criteria)
    assert "另外 3 筆" in rw.format_update_message(new, [], a)


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


def _run(config_path, db_path, fetchers, slack_bot):
    return rw.run_once(
        slack_bot=slack_bot,
        db_path=db_path,
        config_path=config_path,
        fetchers=fetchers,
        http=FakeHttp([]),
        now=NOW,
    )


def test_run_once_baseline_then_new_and_price_drop(config_path, tmp_path):
    db = tmp_path / "rw.db"
    bot = MagicMock()
    current = [
        _listing(listing_id="1"),
        _listing(listing_id="2", is_basement=True, floor="B1/5F", title="地下室A"),
        _listing(listing_id="3", district="內湖區"),  # 被條件濾掉
    ]
    fetchers = {"591": lambda http, c: list(current)}

    r1 = _run(config_path, db, fetchers, bot)
    assert r1.status == "baseline"
    assert r1.seen == 2
    (call,) = bot.post_plain.call_args_list
    assert call.kwargs["context"] == "rental_watch_baseline"
    assert "目前在架 2 筆" in call.args[0]
    assert rw.BRIDGE_URL in call.args[0]

    bot.reset_mock()
    r2 = _run(config_path, db, fetchers, bot)
    assert (r2.status, r2.new, r2.price_drops) == ("ok", 0, 0)
    bot.post_plain.assert_not_called()

    current[0] = _listing(listing_id="1", price=22000)
    current.append(_listing(listing_id="4", is_basement=True, floor="B1/4F", title="地下室B"))
    r3 = _run(config_path, db, fetchers, bot)
    assert (r3.status, r3.new, r3.price_drops, r3.basement_new) == ("notified", 1, 1, 1)
    (call,) = bot.post_plain.call_args_list
    assert call.kwargs["context"] == "rental_watch_update"
    assert "地下室B" in call.args[0]
    assert "~25,000~ → *22,000元*" in call.args[0]

    # 價格再降一次才會再通知；持平不通知
    bot.reset_mock()
    _run(config_path, db, fetchers, bot)
    bot.post_plain.assert_not_called()


def test_run_once_baseline_waits_until_a_source_succeeds(config_path, tmp_path):
    db = tmp_path / "rw.db"
    bot = MagicMock()

    def broken(http, c):
        raise RuntimeError("503")

    r1 = _run(config_path, db, {"591": broken}, bot)
    assert r1.status == "error"
    assert r1.source_errors == {"591": "RuntimeError: 503"}
    bot.post_plain.assert_not_called()

    r2 = _run(config_path, db, {"591": lambda http, c: [_listing()]}, bot)
    assert r2.status == "baseline"  # 不會把既有物件當成新物件洗版


def test_source_failure_alert_after_threshold_then_recovery(config_path, tmp_path):
    db = tmp_path / "rw.db"
    bot = MagicMock()
    ok = lambda http, c: [_listing()]  # noqa: E731

    def broken(http, c):
        raise RuntimeError("upstream connect error")

    _run(config_path, db, {"591": ok, "sinyi": ok}, bot)  # baseline
    bot.reset_mock()

    for _ in range(rw.SOURCE_FAILURE_ALERT_THRESHOLD - 1):
        _run(config_path, db, {"591": ok, "sinyi": broken}, bot)
    bot.post_plain.assert_not_called()

    _run(config_path, db, {"591": ok, "sinyi": broken}, bot)
    (call,) = bot.post_plain.call_args_list
    assert "信義 已連續 3 次抓取失敗" in call.args[0]

    bot.reset_mock()
    _run(config_path, db, {"591": ok, "sinyi": broken}, bot)  # 已警告過，不重複
    bot.post_plain.assert_not_called()

    _run(config_path, db, {"591": ok, "sinyi": ok}, bot)
    (call,) = bot.post_plain.call_args_list
    assert "信義 恢復正常" in call.args[0]


def test_dry_run_writes_nothing(config_path, tmp_path):
    db = tmp_path / "rw.db"
    bot = MagicMock()
    r = rw.run_once(
        dry_run=True,
        slack_bot=bot,
        db_path=db,
        config_path=config_path,
        fetchers={"591": lambda http, c: [_listing(is_basement=True)]},
        http=FakeHttp([]),
    )
    assert (r.status, r.seen, r.basement_new) == ("ok", 1, 1)
    assert not db.exists()
    bot.post_plain.assert_not_called()


def test_default_config_file_loads():
    c = rw.load_criteria()
    assert c.districts == ("中正區", "中山區", "大安區", "信義區", "文山區")
    assert (c.price_min, c.price_max, c.area_min, c.area_max) == (15000, 35000, 10, 50)


# ---------------------------------------------------------------------------
# 捷運 / 登記 / 熱度（2026-10-23 修修：捷運要近、要能營業登記）
# ---------------------------------------------------------------------------


def test_parse_591_item_mrt_registration_views():
    it = rw.parse_591_item(
        _591_item(
            7,
            surrounding={"type": "subway_station", "desc": "距古亭", "distance": "189公尺"},
            rentTag=[{"id": 2, "name": "近捷運"}, {"id": 3, "name": "可登記"}],
            browsenum=31,
            browsenum_name="昨日瀏覽",
        )
    )
    assert (it.mrt_station, it.mrt_distance) == ("古亭", 189)
    assert it.registration == "可登記"
    assert it.views == 31


def test_parse_591_item_bus_stop_is_not_mrt_and_untagged_registration():
    it = rw.parse_591_item(
        _591_item(8, surrounding={"type": "bus_station", "desc": "距公車站", "distance": "50公尺"})
    )
    assert it.mrt_distance is None
    assert it.registration == "未標示"
    assert it.views is None


def test_parse_591_registration_from_detail_html():
    html = (
        '<div class="label-item"><span class="label-name right">登記</span>'
        '<span class="businessRegistration label-value right">'
        '<span class="length-limit">不可工商登記</span></span></div>'
    )
    assert rw.parse_591_registration(html) == "不可工商登記"
    assert rw.parse_591_registration("<html></html>") == ""


def test_registration_detail_fetched_once_and_cached(config_path, tmp_path):
    db = tmp_path / "rw.db"
    detail_html = '<span class="businessRegistration"><span>暫不確定</span></span>'
    item = _listing(listing_id="9", registration="未標示", mrt_distance=200)
    far = _listing(listing_id="10", registration="未標示", mrt_distance=900)
    http = FakeHttp([(lambda m, u, k: "/rent/9" in u, lambda *a: _Resp(text=detail_html))])
    fetchers = {"591": lambda h, c: [item, far]}

    for _ in range(2):
        rw.run_once(db_path=db, config_path=config_path, fetchers=fetchers, http=http, now=NOW)

    assert [u for _, u, _ in http.calls] == ["https://business.591.com.tw/rent/9"]  # 遠的不查
    board = rw.load_board(db, rw.load_criteria(config_path), now=NOW)
    regs = {i.listing.listing_id: i.listing.registration for i in board.items}
    assert regs == {"9": "暫不確定", "10": "未標示"}


def test_registration_detail_failure_retries_next_run(config_path, tmp_path):
    db = tmp_path / "rw.db"
    item = _listing(listing_id="9", registration="未標示", mrt_distance=200)
    calls = {"n": 0}

    def detail(*a):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("503")
        return _Resp(text='<span class="businessRegistration">可登記</span>')

    http = FakeHttp([(lambda m, u, k: "/rent/9" in u, detail)])
    fetchers = {"591": lambda h, c: [item]}
    rw.run_once(db_path=db, config_path=config_path, fetchers=fetchers, http=http, now=NOW)
    rw.run_once(db_path=db, config_path=config_path, fetchers=fetchers, http=http, now=NOW)
    board = rw.load_board(db, rw.load_criteria(config_path), now=NOW)
    assert board.items[0].listing.registration == "可登記"


@pytest.mark.parametrize(
    "text,expected",
    [("可登記", True), ("不可工商登記", False), ("暫不確定", None), ("未標示", None), ("", None)],
)
def test_registration_status(text, expected):
    assert rw_assess.registration_status(text) is expected


@pytest.mark.parametrize(
    "overrides,tier",
    [
        ({"mrt_distance": 300, "registration": "可登記"}, rw_assess.TIER_MATCH),
        ({"mrt_distance": 500, "registration": "可登記"}, rw_assess.TIER_MATCH),
        ({"mrt_distance": 501, "registration": "可登記"}, rw_assess.TIER_OUT),
        ({"mrt_distance": 300, "registration": "不可工商登記"}, rw_assess.TIER_OUT),
        ({"mrt_distance": 300, "registration": "暫不確定"}, rw_assess.TIER_CHECK),
        ({"mrt_distance": None, "registration": "可登記"}, rw_assess.TIER_CHECK),
        ({"source": "sinyi"}, rw_assess.TIER_CHECK),
    ],
)
def test_assess_tiers(criteria, overrides, tier):
    a = rw_assess.assess(
        _listing(**overrides), medians={}, hot_min_views=20, mrt_max_m=criteria.mrt_max_m
    )
    assert a.tier == tier


def test_assess_scoring_chips_and_urgent(criteria):
    it = _listing(
        area=40.0,
        price=20000,  # 500/坪
        is_basement=True,
        mrt_station="古亭",
        mrt_distance=150,
        registration="可登記",
    )
    a = rw_assess.assess(it, medians={"大安區": 1000.0}, hot_min_views=20, mrt_max_m=500)
    texts = [c.text for c in a.chips]
    assert "每坪比同區便宜 50%" in texts
    assert "空間大 40 坪" in texts
    assert "地下室" in texts
    assert "離古亭只有 150m" in texts
    assert a.score == 5
    assert a.urgent is True
    assert (a.price_per_ping, a.vs_median_pct) == (500, -50)


def test_assess_hot_makes_match_urgent_but_not_check(criteria):
    hot_match = _listing(mrt_distance=300, registration="可登記", views=40)
    hot_check = _listing(mrt_distance=300, registration="暫不確定", views=40)
    kw = dict(medians={}, hot_min_views=20, mrt_max_m=500)
    assert rw_assess.assess(hot_match, **kw).urgent is True
    assert rw_assess.assess(hot_check, **kw).urgent is False
    assert any(c.text == "搶手：昨天 40 人看過" for c in rw_assess.assess(hot_check, **kw).chips)


def test_district_median_needs_three_samples():
    two = [_listing(listing_id=str(i)) for i in range(2)]
    assert rw_assess.district_medians(two) == {}
    three = [_listing(listing_id=str(i), price=p) for i, p in enumerate((20000, 25000, 30000))]
    assert rw_assess.district_medians(three) == {"大安區": 1250.0}


def test_hot_threshold_uses_p90_with_floor():
    low = [_listing(listing_id=str(i), views=v) for i, v in enumerate(range(10))]
    assert rw_assess.hot_threshold(low) == rw_assess.HOT_MIN_VIEWS
    high = [_listing(listing_id=str(i), views=v) for i, v in enumerate(range(0, 100, 10))]
    assert rw_assess.hot_threshold(high) == 90


# ---------------------------------------------------------------------------
# Bridge board / feedback
# ---------------------------------------------------------------------------


def test_load_board_missing_db_returns_none(tmp_path, criteria):
    assert rw.load_board(tmp_path / "nope.db", criteria) is None


def test_load_board_hides_delisted_and_flags_new(config_path, tmp_path):
    db = tmp_path / "rw.db"
    current = [_listing(listing_id="1"), _listing(listing_id="2")]
    fetchers = {"591": lambda h, c: list(current)}
    t0 = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 23, 2, 0, tzinfo=timezone.utc)
    rw.run_once(db_path=db, config_path=config_path, fetchers=fetchers, http=FakeHttp([]), now=t0)

    current[:] = [_listing(listing_id="1"), _listing(listing_id="3")]  # 2 下架、3 新上架
    rw.run_once(db_path=db, config_path=config_path, fetchers=fetchers, http=FakeHttp([]), now=t1)

    board = rw.load_board(db, rw.load_criteria(config_path), now=t1)
    by_id = {i.listing.listing_id: i for i in board.items}
    assert set(by_id) == {"1", "3"}
    assert by_id["3"].is_new is True
    assert by_id["1"].is_new is False
    assert board.last_scan_at == t1.isoformat()
    status = {src.source: src for src in board.sources}
    assert status["591"].ok and status["591"].last_success_at == t1.isoformat()
    assert status["sinyi"].ok is False  # 這個測試沒跑信義 → 頁面顯示「未掃描」


def test_load_board_keeps_listings_of_failed_source(config_path, tmp_path):
    db = tmp_path / "rw.db"
    ok = {"591": lambda h, c: [_listing(listing_id="1")]}

    def broken(h, c):
        raise RuntimeError("503")

    t0 = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 23, 2, 0, tzinfo=timezone.utc)
    rw.run_once(db_path=db, config_path=config_path, fetchers=ok, http=FakeHttp([]), now=t0)
    rw.run_once(
        db_path=db, config_path=config_path, fetchers={"591": broken}, http=FakeHttp([]), now=t1
    )
    board = rw.load_board(db, rw.load_criteria(config_path), now=t1)
    assert [i.listing.listing_id for i in board.items] == ["1"]  # 抓不到 ≠ 下架
    assert {src.source: src for src in board.sources}["591"].ok is False


def test_set_feedback_roundtrip(config_path, tmp_path):
    db = tmp_path / "rw.db"
    fetchers = {"591": lambda h, c: [_listing(listing_id="1")]}
    rw.run_once(db_path=db, config_path=config_path, fetchers=fetchers, http=FakeHttp([]), now=NOW)

    rw.set_feedback(db, "591:1", "interested", "  地點好  ")
    item = rw.load_board(db, rw.load_criteria(config_path), now=NOW).items[0]
    assert (item.verdict, item.reason) == ("interested", "地點好")

    rw.set_feedback(db, "591:1", None)
    item = rw.load_board(db, rw.load_criteria(config_path), now=NOW).items[0]
    assert item.verdict is None

    with pytest.raises(ValueError):
        rw.set_feedback(db, "591:1", "maybe")


def test_default_db_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("RENTAL_WATCH_DB", str(tmp_path / "x.db"))
    assert rw.default_db_path() == tmp_path / "x.db"
