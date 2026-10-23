"""租屋物件追蹤（修修 2026-10-23：錄音室年底租約到期，找新辦公室）。

每小時掃 591 商用、信義房屋、好房網三站，條件讀 ``config/rental-watch.yaml``；
有新物件或降價就 Slack DM 修修。地下室物件獨立一區排最前面（隔絕馬路噪音、
同預算坪數較大，修修特別要看）。

三站的資料來源都是 2026-10-23 實際打開網站確認的：

- **591**：``bff-business.591.com.tw/v2/web/business/list`` 回 JSON，不需登入。
  樓層是數字，地下室 = 負數（B1 = ``-1``）。類型：6 辦公、12 住辦、5 店面。
- **信義**：列表頁 server-side render，資料直接在 HTML（``div.search_result_item``）。
  第 1 頁 ``index.html``、第 N 頁 ``N.html``。伺服器偶爾回 503 → 有重試。
- **好房網**：``/ashx/search/search.ashx`` POST，``RequestPackage`` 表單欄位內的
  JSON 每個值都 base64 + URL-encode（且不能再被表單編碼一次），回應同樣 base64，
  物件是 HTML 片段。一次最多帶 3 個行政區、``DataUnit=1`` 地區條件才生效。

樂屋網有商用類別，但全站在 Cloudflare 機器人驗證後面（VPS 與家用 IP 的程式請求
都 403），不繞過驗證，所以不追。

狀態存在 runtime data dir 下獨立的 ``rental_watch.db``（不動 state.db schema）。
第一次執行只盤點、不逐筆推播：送一則起始摘要（各站筆數＋全部地下室物件）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import ssl
import time
import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

import httpx
import yaml
from bs4 import BeautifulSoup

from shared.log import get_logger

if TYPE_CHECKING:
    from agents.franky.jobs.rental_assess import Assessment

logger = get_logger("nakama.franky.rental_watch")

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CONFIG_PATH = _ROOT / "config" / "rental-watch.yaml"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# 一個來源連續失敗幾次才 DM 警告（單次失敗多半是對方伺服器抖動，例如信義的 503）。
SOURCE_FAILURE_ALERT_THRESHOLD = 3

# 單則 Slack 訊息最多列幾筆；超過的只報數量，避免一次洗版。
MAX_ITEMS_PER_SECTION = 20

# ---------------------------------------------------------------------------
# 各站行政區代碼（2026-10-23 從各站頁面實際讀到的對照，不是推測）
# ---------------------------------------------------------------------------

# 591：列表頁自己的行政區連結 list?region=1&section=<n>&kind=6（「<區>辦公出租」）。
_591_SECTION_IDS = {
    "中正區": 1,
    "大同區": 2,
    "中山區": 3,
    "松山區": 4,
    "大安區": 5,
    "萬華區": 6,
    "信義區": 7,
    "士林區": 8,
    "北投區": 9,
    "內湖區": 10,
    "南港區": 11,
    "文山區": 12,
}
# 591 篩選介面寫「最多可以選擇 5 個」行政區 → 超過就分批查。
_591_MAX_SECTIONS = 5

# Bridge 條件面板的選項順序（台北 12 區；三站的代碼對照都齊）。
TAIPEI_DISTRICTS = tuple(_591_SECTION_IDS)

# 信義：列表頁的行政區連結 /rent/list/Taipei-city/<zip>-zip/。
_SINYI_ZIPS = {
    "中正區": "100",
    "大同區": "103",
    "中山區": "104",
    "松山區": "105",
    "大安區": "106",
    "萬華區": "108",
    "信義區": "110",
    "士林區": "111",
    "北投區": "112",
    "內湖區": "114",
    "南港區": "115",
    "文山區": "116",
}

# 好房網：AngularJS scope.AreaDetail 的 台北市（cityCode 0000）areaList sid。
_HOUSEFUN_CITY_ID = "0000"
_HOUSEFUN_AREA_IDS = {
    "中正區": "1",
    "大同區": "2",
    "中山區": "3",
    "松山區": "4",
    "大安區": "5",
    "萬華區": "6",
    "信義區": "7",
    "士林區": "8",
    "北投區": "9",
    "內湖區": "10",
    "南港區": "11",
    "文山區": "12",
}

_591_KINDS = {6: "辦公", 12: "住辦", 5: "店面"}
_SINYI_USES = {"office-use": "辦公", "store-use": "店面"}
_HOUSEFUN_PURPOSES = {"6": "辦公/住辦", "5": "店面"}
_STORE_KINDS = {"店面"}

_DISTRICT_RE = re.compile(r"(?:台北市|臺北市)?\s*([一-鿿]{1,3}區)")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Criteria:
    districts: tuple[str, ...]
    price_min: int
    price_max: int
    area_min: float
    area_max: float
    store_basement_only: bool = True
    exclude_rooftop: bool = True
    exclude_title_keywords: tuple[str, ...] = ()
    exclude_title_patterns: tuple[re.Pattern[str], ...] = ()
    mrt_max_m: int = 500
    exclude_basement: bool = False
    bonus_districts: tuple[str, ...] = ()
    # 條件內容的指紋（忽略 yaml 註解）；條件一改，下一輪改成重新盤點，不把新範圍
    # 進來的物件當「新物件」一次推幾十筆。
    config_hash: str = ""

    @property
    def wants_stores(self) -> bool:
        """店面類只留地下室、地下室又全剔除 → 店面類一筆都不會留，乾脆不抓。"""
        return not (self.store_basement_only and self.exclude_basement)


@dataclass(frozen=True)
class Listing:
    source: str  # "591" | "sinyi" | "housefun"
    listing_id: str
    title: str
    url: str
    district: str
    price: int
    area: Optional[float]  # 坪；抓不到時 None
    floor: str  # 顯示用，例如 "B1/7F"
    is_basement: bool
    is_rooftop: bool
    kind: str  # 辦公 / 住辦 / 店面 / 辦公/住辦
    mrt_station: str = ""  # 最近捷運站（591 才有）
    mrt_distance: Optional[int] = None  # 公尺；None = 沒資料
    registration: str = ""  # 591：「可登記」/ 詳細頁登記欄原文 / 「未標示」；其他站空字串
    views: Optional[int] = None  # 591「昨日瀏覽」人數

    @property
    def key(self) -> str:
        return f"{self.source}:{self.listing_id}"


@dataclass
class PriceDrop:
    listing: Listing
    old_price: int


@dataclass
class RentalWatchResult:
    status: str  # ok | notified | baseline | error
    seen: int
    new: int
    price_drops: int
    basement_new: int
    source_counts: dict[str, int] = field(default_factory=dict)
    source_errors: dict[str, str] = field(default_factory=dict)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "seen": self.seen,
            "new": self.new,
            "price_drops": self.price_drops,
            "basement_new": self.basement_new,
            "source_counts": self.source_counts,
            "source_errors": self.source_errors,
        }


def load_criteria(path: Path = DEFAULT_CONFIG_PATH, *, db_path: Optional[Path] = None) -> Criteria:
    """設定檔是預設值；``db_path`` 給了就再疊上修修在 Bridge 面板存的條件。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if db_path is not None:
        raw.update(get_criteria_override(db_path) or {})
    districts = tuple(raw.get("districts") or ())
    if not districts:
        raise ValueError(f"{path}: districts 不可為空")
    return Criteria(
        districts=districts,
        price_min=int(raw["price"]["min"]),
        price_max=int(raw["price"]["max"]),
        area_min=float(raw["area"]["min"]),
        area_max=float(raw["area"]["max"]),
        store_basement_only=bool(raw.get("store_basement_only", True)),
        exclude_rooftop=bool(raw.get("exclude_rooftop", True)),
        exclude_title_keywords=tuple(raw.get("exclude_title_keywords") or ()),
        exclude_title_patterns=tuple(
            re.compile(p) for p in (raw.get("exclude_title_patterns") or ())
        ),
        mrt_max_m=int(raw.get("mrt_max_m", 500)),
        exclude_basement=bool(raw.get("exclude_basement", False)),
        bonus_districts=tuple(raw.get("bonus_districts") or ()),
        config_hash=hashlib.sha1(
            json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:12],
    )


# ---------------------------------------------------------------------------
# Bridge 條件面板（存 rental_watch.db settings 表，蓋過設定檔的同名欄位）
# ---------------------------------------------------------------------------

_OVERRIDE_KEY = "criteria_override"


def get_criteria_override(db_path: Path) -> Optional[dict[str, Any]]:
    if not db_path.exists():
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (_OVERRIDE_KEY,)).fetchone()
    finally:
        conn.close()
    return json.loads(row["value"]) if row else None


def save_criteria_override(db_path: Path, override: Optional[dict[str, Any]]) -> None:
    """存面板條件；``None`` = 清掉、回到設定檔預設。下一輪掃描就套用（指紋變了 → 重新盤點）。"""
    conn = _connect(db_path)
    try:
        with conn:
            if override is None:
                conn.execute("DELETE FROM settings WHERE key = ?", (_OVERRIDE_KEY,))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (_OVERRIDE_KEY, json.dumps(override, ensure_ascii=False, sort_keys=True)),
                )
    finally:
        conn.close()


def _num(value: Any, label: str, errors: list[str], *, as_int: bool = True) -> Optional[float]:
    text = str(value or "").replace(",", "").strip()
    try:
        return int(text) if as_int else float(text)
    except ValueError:
        errors.append(f"{label}要填數字")
        return None


def parse_criteria_form(form: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Bridge 表單 → (override, 錯誤訊息)。``form`` 的多選欄位是 list。"""
    errors: list[str] = []
    picked = set(form.get("districts") or [])
    bonus = set(form.get("bonus_districts") or [])
    districts = [d for d in TAIPEI_DISTRICTS if d in picked]
    if not districts:
        errors.append("至少要選一個行政區")
    if picked - set(TAIPEI_DISTRICTS) or bonus - set(TAIPEI_DISTRICTS):
        errors.append("行政區只能選台北市 12 區")
    if bonus - picked:
        errors.append("加分區要在搜尋的行政區裡")

    price_min = _num(form.get("price_min"), "月租下限", errors)
    price_max = _num(form.get("price_max"), "月租上限", errors)
    area_min = _num(form.get("area_min"), "坪數下限", errors, as_int=False)
    area_max = _num(form.get("area_max"), "坪數上限", errors, as_int=False)
    mrt = _num(form.get("mrt_max_m"), "捷運距離", errors)
    if price_min is not None and price_max is not None:
        if not (1000 <= price_min < price_max <= 500000):
            errors.append("月租要介於 1,000–500,000，而且下限小於上限")
    if area_min is not None and area_max is not None:
        if not (1 <= area_min < area_max <= 1000):
            errors.append("坪數要介於 1–1,000，而且下限小於上限")
    if mrt is not None and not (100 <= mrt <= 3000):
        errors.append("捷運距離要介於 100–3,000 公尺")

    override = {
        "districts": districts,
        "bonus_districts": [d for d in TAIPEI_DISTRICTS if d in bonus],
        "price": {"min": price_min, "max": price_max},
        "area": {"min": area_min, "max": area_max},
        "mrt_max_m": mrt,
        "exclude_basement": bool(form.get("exclude_basement")),
    }
    return override, errors


def _lookup_codes(districts: Iterable[str], table: dict[str, Any], site: str) -> list[Any]:
    missing = [d for d in districts if d not in table]
    if missing:
        raise ValueError(
            f"{site} 沒有這些行政區的代碼對照：{missing}（請補 rental_watch.py 對照表）"
        )
    return [table[d] for d in districts]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _ssl_context() -> ssl.SSLContext:
    """照常驗證憑證鏈，只關掉 X.509 strict。

    591 與好房網的憑證缺 Subject Key Identifier，Python 3.13+ 預設的
    VERIFY_X509_STRICT 會直接拒絕（VPS 的 3.12 沒這個預設，桌機 3.14 有）。
    """
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    return ctx


class Http:
    """httpx 薄包裝：固定 UA、5xx / 連線錯誤重試、請求間隔（對網站客氣一點）。"""

    def __init__(
        self,
        *,
        client: Optional[httpx.Client] = None,
        sleep: Callable[[float], None] = time.sleep,
        pause_s: float = 1.0,
        retries: int = 3,
    ) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": _UA, "Accept-Language": "zh-TW,zh;q=0.9"},
            timeout=20.0,
            verify=_ssl_context(),
            follow_redirects=True,
        )
        self._sleep = sleep
        self._pause_s = pause_s
        self._retries = retries

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, self._retries + 1):
            self._sleep(self._pause_s)
            try:
                resp = self._client.request(method, url, **kwargs)
                if resp.status_code < 500:
                    resp.raise_for_status()
                    return resp
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
            except httpx.TransportError as exc:
                last_exc = exc
            self._sleep(2.0 * attempt)
        assert last_exc is not None
        raise last_exc

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _to_int(text: Any) -> Optional[int]:
    digits = re.sub(r"[^\d]", "", str(text or ""))
    return int(digits) if digits else None


def _to_float(text: Any) -> Optional[float]:
    m = re.search(r"\d+(?:\.\d+)?", str(text or ""))
    return float(m.group()) if m else None


def _district_from(text: str) -> str:
    m = _DISTRICT_RE.search(text or "")
    return m.group(1) if m else ""


def _floor_is_basement(floor: str) -> bool:
    """「B1/7F」「-1~1 / 7」「地下1樓」都算地下室。看的是所在樓層（斜線前那段）。"""
    head = (floor or "").split("/")[0].strip()
    return head.upper().startswith("B") or head.startswith("-") or "地下" in head


def _floor_is_rooftop(floor: str) -> bool:
    return "頂樓加蓋" in (floor or "") or "頂加" in (floor or "")


# ---------------------------------------------------------------------------
# 591
# ---------------------------------------------------------------------------

_591_API = "https://bff-business.591.com.tw/v2/web/business/list"
_591_PAGE_SIZE = 30


def parse_591_item(item: dict[str, Any]) -> Listing:
    floor_num = item.get("floor")
    floor_str = str(item.get("floorStr") or "")
    is_basement = (isinstance(floor_num, int) and floor_num < 0) or _floor_is_basement(floor_str)
    title = str(item.get("address_img_title") or item.get("title") or "").strip()
    surrounding = item.get("surrounding") or {}
    station, distance = "", None
    if surrounding.get("type") == "subway_station":
        station = str(surrounding.get("desc") or "").removeprefix("距").strip()
        distance = _to_int(surrounding.get("distance"))
    tags = {str(t.get("name")) for t in (item.get("rentTag") or item.get("tag") or [])}
    views = item.get("browsenum") if item.get("browsenum_name") == "昨日瀏覽" else None
    return Listing(
        source="591",
        listing_id=str(item["id"]),
        title=title,
        url=f"https://business.591.com.tw/rent/{item['id']}",
        district=str(item.get("section_name") or item.get("sectionname") or ""),
        price=_to_int(item.get("price")) or 0,
        area=_to_float(item.get("area")),
        floor=floor_str,
        is_basement=is_basement,
        is_rooftop=_floor_is_rooftop(floor_str),
        kind=str(item.get("kind_name") or _591_KINDS.get(item.get("kind"), "")),
        mrt_station=station,
        mrt_distance=distance,
        registration="可登記" if "可登記" in tags else _591_REG_UNMARKED,
        views=int(views) if isinstance(views, (int, float)) else None,
    )


_591_REG_UNMARKED = "未標示"
_591_DETAIL = "https://business.591.com.tw/rent/{id}"
# 每輪最多補抓幾筆詳細頁（第一輪會有幾十筆；之後只有新物件）。
_591_DETAIL_BUDGET = 40


def parse_591_registration(html: str) -> str:
    """591 詳細頁「登記」欄（``span.businessRegistration``）的文字，例如「暫不確定」。"""
    el = BeautifulSoup(html, "html.parser").select_one(".businessRegistration")
    return el.get_text(strip=True) if el else ""


def fetch_591_registration(http: Http, listing_id: str) -> str:
    resp = http.request("GET", _591_DETAIL.format(id=listing_id))
    return parse_591_registration(resp.text) or _591_REG_UNMARKED


def fetch_591(http: Http, criteria: Criteria) -> list[Listing]:
    codes = [str(s) for s in _lookup_codes(criteria.districts, _591_SECTION_IDS, "591")]
    out: list[Listing] = []
    for kind, kind_name in _591_KINDS.items():
        if kind_name in _STORE_KINDS and not criteria.wants_stores:
            continue
        for chunk in _chunks(codes, _591_MAX_SECTIONS):
            out.extend(_fetch_591_kind(http, criteria, kind, ",".join(chunk)))
    return out


def _fetch_591_kind(http: Http, criteria: Criteria, kind: int, sections: str) -> list[Listing]:
    out: list[Listing] = []
    first_row = 0
    while True:
        # 591 的價格參數格式是 "15000$_35000$"，$ 不能被 percent-encode
        # （httpx params 會編成 %24），所以自己組 query string。
        qs = (
            f"searchtype=1&type=1&kind={kind}&sort=money_asc&order=money&orderType=asc"
            f"&firstRow={first_row}&regionid=1&sectionid={sections}"
            f"&multiPrice={criteria.price_min}$_{criteria.price_max}$"
            f"&multiArea={int(criteria.area_min)}_{int(criteria.area_max)}"
        )
        resp = http.request(
            "GET", f"{_591_API}?{qs}", headers={"Referer": "https://business.591.com.tw/"}
        )
        payload = resp.json()
        if int(payload.get("status", 0)) != 1:
            raise RuntimeError(f"591 kind={kind} status={payload.get('status')}")
        items = (payload.get("data") or {}).get("data") or []
        out.extend(parse_591_item(i) for i in items)
        total = int(payload.get("records") or 0)
        first_row += _591_PAGE_SIZE
        if not items or first_row >= total:
            return out


# ---------------------------------------------------------------------------
# 信義房屋
# ---------------------------------------------------------------------------

_SINYI_BASE = "https://www.sinyi.com.tw/rent/list/Taipei-city"
_SINYI_MAX_PAGES = 10


def sinyi_list_url(criteria: Criteria, use: str, page: int = 1) -> str:
    zips = "-".join(_lookup_codes(criteria.districts, _SINYI_ZIPS, "信義"))
    leaf = "index.html" if page == 1 else f"{page}.html"
    return (
        f"{_SINYI_BASE}/{zips}-zip/{use}/{criteria.price_min}-{criteria.price_max}-price/"
        f"{int(criteria.area_min)}-{int(criteria.area_max)}-area/{leaf}"
    )


def parse_sinyi_page(html: str, kind: str) -> tuple[int, list[Listing]]:
    """回傳 (總筆數, 本頁物件)。"""
    soup = BeautifulSoup(html, "html.parser")
    count_el = soup.select_one("#search_result_count .num")
    total = _to_int(count_el.get_text()) if count_el else 0
    listings: list[Listing] = []
    for item in soup.select("div.search_result_item"):
        hid = (item.get("id") or "").removeprefix("search_result_")
        if not hid:
            continue
        title_el = item.select_one(".item_title")
        nums = [s.get_text(strip=True) for s in item.select(".detail_line2 span.num")]
        area = floor = None
        for span in item.select(".detail_line2 > span"):
            text = span.get_text(" ", strip=True)
            if text.endswith("坪") and area is None:
                area = _to_float(text)
            elif text.endswith("樓") and floor is None:
                floor = text.removesuffix("樓").strip()
        addr_el = item.select_one(".num-text")
        address = addr_el.get_text(strip=True) if addr_el else ""
        price_el = item.select_one(".price_new .num")
        floor_str = floor or ""
        listings.append(
            Listing(
                source="sinyi",
                listing_id=hid,
                title=title_el.get_text(strip=True) if title_el else (nums[0] if nums else hid),
                url=f"https://www.sinyi.com.tw/rent/houseno/{hid}",
                district=_district_from(address),
                price=_to_int(price_el.get_text()) if price_el else 0,
                area=area,
                floor=floor_str,
                is_basement=_floor_is_basement(floor_str),
                is_rooftop=_floor_is_rooftop(floor_str),
                kind=kind,
            )
        )
    return total or 0, listings


def fetch_sinyi(http: Http, criteria: Criteria) -> list[Listing]:
    out: list[Listing] = []
    for use, kind in _SINYI_USES.items():
        if kind in _STORE_KINDS and not criteria.wants_stores:
            continue
        seen: set[str] = set()
        for page in range(1, _SINYI_MAX_PAGES + 1):
            resp = http.request("GET", sinyi_list_url(criteria, use, page))
            total, items = parse_sinyi_page(resp.text, kind)
            fresh = [i for i in items if i.listing_id not in seen]
            seen.update(i.listing_id for i in fresh)
            out.extend(fresh)
            if not fresh or len(seen) >= total:
                break
    return out


# ---------------------------------------------------------------------------
# 好房網
# ---------------------------------------------------------------------------

_HOUSEFUN_SEARCH = "https://rent.housefun.com.tw/ashx/search/search.ashx"
_HOUSEFUN_MAX_PAGES = 15
# objSearch() 的欄位（rent.housefun.com.tw/bundles/searchjs），伺服器要求全部帶上。
_HOUSEFUN_FIELDS = (
    "DataUnit DataType DataTab OrderBy OrderType CityId CityId2 CityId3 CityName CityName2 "
    "CityName3 AreaId AreaId2 AreaId3 AreaName AreaName2 AreaName3 PurposeID PriceRental "
    "PriceL PriceH MRTLine MRTLine2 MRTLine3 MRTLineName MRTLineName2 MRTLineName3 MRTStation "
    "MRTStation2 MRTStation3 MRTStationName MRTStationName2 MRTStationName3 SchoolType "
    "SchoolTypeName SchoolId SchoolName BuildingID BuildingName KeyWord KWkind KWID KWCounty "
    "KWDistrict NotRequiredOtherFeeID NotRequiredEquipmentID NotRequiredBaseMent Room "
    "LevelGroundID CaseTypeID AgentPositionID CaseFromFloor CaseToFloor BuildYear BuildYearL "
    "BuildYearH chkOTLimSex OTLimSex OTParkingSpace OTLimWithLandlord OTLimPet TGType "
    "EquipmentID PMPage BrowseMode CenterLat CenterLng SID Distance SearchList MemberID "
    "MainShopID AgentName LandlordNo"
).split()


def _hf_encode(value: str) -> str:
    return urllib.parse.quote(base64.b64encode(value.encode("utf-8")).decode("ascii"), safe="")


def _hf_decode(value: Any) -> Any:
    if isinstance(value, str) and value:
        return base64.b64decode(value).decode("utf-8")
    return value


def housefun_request_body(fields: dict[str, str]) -> bytes:
    data = {
        name: (_hf_encode(fields[name]) if fields.get(name) else "") for name in _HOUSEFUN_FIELDS
    }
    package = json.dumps({"Method": _hf_encode("INQUIRE"), "Data": data}, separators=(",", ":"))
    # 值已經 percent-encode 過；整包不能再被表單編碼（否則 %3D 變 %253D，伺服器回
    # 「不是有效的 Base-64 字串」）。
    return ("RequestPackage=" + package).encode("utf-8")


def housefun_decode_response(payload: dict[str, Any]) -> dict[str, Any]:
    if _hf_decode(payload.get("Status")) != "1":
        raise RuntimeError(f"好房網回應失敗：{_hf_decode(payload.get('StatusMessage'))}")
    data = payload.get("Data") or {}
    return {k: _hf_decode(v) for k, v in data.items()} if isinstance(data, dict) else {}


def parse_housefun_html(html: str, kind: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[Listing] = []
    for art in soup.select("article.DataList"):
        link = art.select_one("h3.title a[href]")
        if not link:
            continue
        m = re.search(r"/rent/house/(\d+)", link["href"])
        if not m:
            continue
        rid = m.group(1)
        addr_el = art.select_one("address.addr")
        address = addr_el.get_text(strip=True) if addr_el else ""
        pattern_el = art.select_one(".pattern")
        floor = (pattern_el.get_text(strip=True) if pattern_el else "").removeprefix("樓層：")
        floor = re.sub(r"\s*/\s*", "/", floor)
        price = area = None
        for li in art.select("li.InfoList"):
            label = li.select_one(".title")
            value = li.select_one(".infos")
            if not (label and value):
                continue
            if "租金" in label.get_text():
                price = _to_int(value.get_text())
            elif "坪數" in label.get_text():
                area = _to_float(value.get_text())
        out.append(
            Listing(
                source="housefun",
                listing_id=rid,
                title=(link.get("title") or link.get_text()).strip(),
                url=f"https://rent.housefun.com.tw/rent/house/{rid}/",
                district=_district_from(address),
                price=price or 0,
                area=area,
                floor=floor,
                is_basement=_floor_is_basement(floor),
                is_rooftop=_floor_is_rooftop(floor),
                kind=kind,
            )
        )
    return out


def _chunks(seq: list[str], n: int) -> list[list[str]]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]


def fetch_housefun(http: Http, criteria: Criteria) -> list[Listing]:
    districts = list(criteria.districts)
    _lookup_codes(districts, _HOUSEFUN_AREA_IDS, "好房網")
    out: list[Listing] = []
    for purpose, kind in _HOUSEFUN_PURPOSES.items():
        if kind in _STORE_KINDS and not criteria.wants_stores:
            continue
        for group in _chunks(districts, 3):  # 好房網一次最多 3 個行政區
            fields = {
                "DataUnit": "1",  # 1 = 依地區搜尋；不帶的話地區條件被忽略、回全台
                "PurposeID": purpose,
                "PriceL": str(criteria.price_min),
                "PriceH": str(criteria.price_max),
            }
            for idx, district in enumerate(group):
                suffix = "" if idx == 0 else str(idx + 1)
                fields[f"CityId{suffix}"] = _HOUSEFUN_CITY_ID
                fields[f"CityName{suffix}"] = "台北市"
                fields[f"AreaId{suffix}"] = _HOUSEFUN_AREA_IDS[district]
                fields[f"AreaName{suffix}"] = district
            for page in range(1, _HOUSEFUN_MAX_PAGES + 1):
                fields["PMPage"] = str(page)
                resp = http.request(
                    "POST",
                    _HOUSEFUN_SEARCH,
                    content=housefun_request_body(fields),
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "Referer": "https://rent.housefun.com.tw/",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                data = housefun_decode_response(resp.json())
                out.extend(parse_housefun_html(data.get("SearchContent") or "", kind))
                current, _, last = str(data.get("PageCount") or "1/1").partition("/")
                if _to_int(current) is None or (_to_int(current) or 0) >= (_to_int(last) or 0):
                    break
    return out


DEFAULT_FETCHERS: dict[str, Callable[[Http, Criteria], list[Listing]]] = {
    "591": fetch_591,
    "sinyi": fetch_sinyi,
    "housefun": fetch_housefun,
}

SOURCE_LABELS = {"591": "591", "sinyi": "信義", "housefun": "好房網"}


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def passes(listing: Listing, criteria: Criteria) -> bool:
    """三站共用的最終把關（站方的篩選不一定齊：好房網沒有坪數條件、信義混類型）。"""
    if listing.district not in criteria.districts:
        return False
    if not (criteria.price_min <= listing.price <= criteria.price_max):
        return False
    if listing.area is not None and not (criteria.area_min <= listing.area <= criteria.area_max):
        return False
    if criteria.exclude_rooftop and listing.is_rooftop:
        return False
    if criteria.exclude_basement and listing.is_basement:
        return False
    if criteria.store_basement_only and listing.kind in _STORE_KINDS and not listing.is_basement:
        return False
    if any(kw in listing.title for kw in criteria.exclude_title_keywords):
        return False
    if any(p.search(listing.title) for p in criteria.exclude_title_patterns):
        return False
    return True


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    district TEXT NOT NULL,
    price INTEGER NOT NULL,
    prev_price INTEGER,
    area REAL,
    floor TEXT NOT NULL,
    is_basement INTEGER NOT NULL,
    kind TEXT NOT NULL,
    mrt_station TEXT NOT NULL DEFAULT '',
    mrt_distance INTEGER,
    registration TEXT NOT NULL DEFAULT '',
    registration_checked INTEGER NOT NULL DEFAULT 0,
    views INTEGER,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    alerted INTEGER NOT NULL DEFAULT 0,
    last_success_at TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    key TEXT PRIMARY KEY,
    verdict TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

VERDICTS = {"interested": "有興趣", "pass": "不考慮"}

BRIDGE_URL = "https://nakama.shosho.tw/bridge/rental"


def default_db_path() -> Path:
    """runtime data dir 下的 ``rental_watch.db``；``RENTAL_WATCH_DB`` 可覆寫（dev server 用）。"""
    override = os.environ.get("RENTAL_WATCH_DB", "").strip()
    if override:
        return Path(override)
    from shared.config import get_runtime_data_dir

    return get_runtime_data_dir() / "rental_watch.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _row_to_listing(r: sqlite3.Row) -> Listing:
    return Listing(
        source=r["source"],
        listing_id=r["listing_id"],
        title=r["title"],
        url=r["url"],
        district=r["district"],
        price=r["price"],
        area=r["area"],
        floor=r["floor"],
        is_basement=bool(r["is_basement"]),
        is_rooftop=False,  # 頂加在寫入前就被 passes() 濾掉了
        kind=r["kind"],
        mrt_station=r["mrt_station"],
        mrt_distance=r["mrt_distance"],
        registration=r["registration"],
        views=r["views"],
    )


# ---------------------------------------------------------------------------
# Bridge 讀寫（/bridge/rental）
# ---------------------------------------------------------------------------


@dataclass
class SourceStatus:
    source: str
    label: str
    ok: bool
    consecutive_failures: int
    last_error: Optional[str]
    last_success_at: Optional[str]


@dataclass
class BoardItem:
    listing: Listing
    assessment: "Assessment"
    first_seen: str
    is_new: bool  # 起始盤點之後才出現、且 48 小時內
    prev_price: Optional[int]  # 降價前的價格
    verdict: Optional[str]  # interested | pass | None
    reason: str


@dataclass
class Board:
    items: list[BoardItem]
    sources: list[SourceStatus]
    last_scan_at: Optional[str]
    baseline_at: Optional[str]


def load_board(
    db_path: Path, criteria: Criteria, *, now: Optional[datetime] = None
) -> Optional[Board]:
    """Bridge 頁面用：目前在架的物件＋評估＋修修的標記。DB 不存在回 None。

    「在架」= 該物件最後一次被看到的時間 ≥ 該站最後一次成功掃描的時間；
    之後沒再出現的（已出租或下架）就不顯示。
    """
    from agents.franky.jobs.rental_assess import assess_all

    if not db_path.exists():
        return None
    now = now or datetime.now(timezone.utc)
    conn = _connect(db_path)
    try:
        health = {r["source"]: r for r in conn.execute("SELECT * FROM source_health")}
        rows = conn.execute("SELECT * FROM listings").fetchall()
        fb = {r["key"]: r for r in conn.execute("SELECT * FROM feedback")}
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()

    active, listings = [], []
    for r in rows:
        h = health.get(r["source"])
        last_ok = h["last_success_at"] if h else None
        if last_ok and r["last_seen"] < last_ok:
            continue
        it = _row_to_listing(r)
        # 面板剛收窄條件時，DB 裡還有舊條件抓的物件 → 頁面先照新條件濾掉，不用等下一輪。
        if not passes(it, criteria):
            continue
        active.append(r)
        listings.append(it)
    assessments = assess_all(listings, criteria)
    baseline_at = meta.get("baseline_done")
    items = []
    for r, it in zip(active, listings):
        first_seen = r["first_seen"]
        is_new = bool(
            baseline_at
            and first_seen > baseline_at
            and (now - datetime.fromisoformat(first_seen)).total_seconds() < 48 * 3600
        )
        f = fb.get(it.key)
        items.append(
            BoardItem(
                listing=it,
                assessment=assessments[it.key],
                first_seen=first_seen,
                is_new=is_new,
                prev_price=r["prev_price"],
                verdict=f["verdict"] if f else None,
                reason=f["reason"] if f else "",
            )
        )

    sources = []
    for src in DEFAULT_FETCHERS:
        h = health.get(src)
        sources.append(
            SourceStatus(
                source=src,
                label=SOURCE_LABELS.get(src, src),
                ok=bool(h) and h["consecutive_failures"] == 0,
                consecutive_failures=h["consecutive_failures"] if h else 0,
                last_error=h["last_error"] if h else None,
                last_success_at=h["last_success_at"] if h else None,
            )
        )
    return Board(
        items=items,
        sources=sources,
        last_scan_at=meta.get("last_scan_at"),
        baseline_at=baseline_at,
    )


def set_feedback(
    db_path: Path,
    key: str,
    verdict: Optional[str],
    reason: str = "",
    *,
    now: Optional[datetime] = None,
) -> None:
    """修修在 Bridge 標「有興趣 / 不考慮」（verdict=None 清掉標記）。"""
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}")
    now_iso = (now or datetime.now(timezone.utc)).isoformat()
    conn = _connect(db_path)
    try:
        with conn:
            if verdict is None:
                conn.execute("DELETE FROM feedback WHERE key = ?", (key,))
            else:
                conn.execute(
                    "INSERT INTO feedback (key, verdict, reason, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET verdict = excluded.verdict, "
                    "reason = excluded.reason, updated_at = excluded.updated_at",
                    (key, verdict, reason.strip()[:500], now_iso),
                )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Slack formatting
# ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "｜")


def listing_facts(listing: Listing) -> list[str]:
    """一行事實：區・坪・樓層・捷運・登記・來源。Slack 與 Bridge 共用。"""
    parts = [listing.district]
    if listing.area:
        parts.append(f"{listing.area:g}坪")
    if listing.floor:
        parts.append(listing.floor)
    if listing.mrt_distance is not None:
        parts.append(f"{listing.mrt_station or '捷運'} {listing.mrt_distance}m")
    if listing.registration:
        parts.append(listing.registration)
    parts.append(f"{SOURCE_LABELS.get(listing.source, listing.source)} {listing.kind}")
    return parts


def format_listing(
    listing: Listing,
    assessment: Optional["Assessment"] = None,
    *,
    old_price: Optional[int] = None,
) -> str:
    if old_price is not None:
        price = f"~{old_price:,}~ → *{listing.price:,}元*"
    else:
        price = f"{listing.price:,}元"
    facts = listing_facts(listing)
    parts = [*facts[:-1], price]
    if listing.area:
        parts.append(f"每坪 {round(listing.price / listing.area):,}")
    parts.append(facts[-1])
    line = f"• <{listing.url}|{_esc(listing.title or listing.listing_id)}> — " + "｜".join(parts)
    if assessment is not None:
        notes = [c.text for c in assessment.chips if c.tone in ("good", "warn")][:3]
        if notes:
            line += "\n    ↳ " + "・".join(notes)
    return line


def _section(header: str, lines: list[str]) -> list[str]:
    if not lines:
        return []
    shown = lines[:MAX_ITEMS_PER_SECTION]
    extra = len(lines) - len(shown)
    return [header, *shown, *([f"  …另外 {extra} 筆"] if extra > 0 else []), ""]


def format_update_message(
    new: list[Listing],
    drops: list[PriceDrop],
    assessments: dict[str, "Assessment"],
) -> Optional[str]:
    """新物件與降價 → 一則 DM。確定不符合必要條件的不推；全都不符合就回 None。"""
    from agents.franky.jobs.rental_assess import TIER_CHECK, TIER_MATCH, TIER_OUT, sort_key

    def keep(it: Listing) -> bool:
        return assessments[it.key].tier != TIER_OUT

    new = sorted((it for it in new if keep(it)), key=lambda it: sort_key(it, assessments[it.key]))
    drops = [d for d in drops if keep(d.listing)]
    if not new and not drops:
        return None

    urgent = [it for it in new if assessments[it.key].urgent]
    match = [
        it
        for it in new
        if assessments[it.key].tier == TIER_MATCH and not assessments[it.key].urgent
    ]
    check = [it for it in new if assessments[it.key].tier == TIER_CHECK]
    head = f"🏠 租屋追蹤：{len(new)} 筆新物件" + (f"、{len(drops)} 筆降價" if drops else "")
    if urgent:
        head += f"（{len(urgent)} 筆建議儘快聯繫）"
    lines = [head, ""]
    lines += _section(
        "🔥 *建議儘快聯繫*", [format_listing(it, assessments[it.key]) for it in urgent]
    )
    lines += _section(
        "✅ *符合條件*（捷運近、可登記）",
        [format_listing(it, assessments[it.key]) for it in match],
    )
    lines += _section(
        "📉 *降價*",
        [
            format_listing(d.listing, assessments[d.listing.key], old_price=d.old_price)
            for d in drops
        ],
    )
    lines += _section(
        "❔ *待確認*（捷運距離或可否登記沒有資料）",
        [format_listing(it, assessments[it.key]) for it in check],
    )
    lines.append(f"完整清單、標記有興趣／不考慮：<{BRIDGE_URL}|Bridge 租屋追蹤>")
    return "\n".join(lines).rstrip()


def format_baseline_message(
    listings: list[Listing],
    assessments: dict[str, "Assessment"],
    source_counts: dict[str, int],
    criteria: Criteria,
    *,
    updated: bool = False,
) -> str:
    from agents.franky.jobs.rental_assess import TIER_CHECK, TIER_MATCH, TIER_OUT, sort_key

    counts = "、".join(f"{SOURCE_LABELS.get(s, s)} {n}" for s, n in source_counts.items())
    tier_counts = {
        t: sum(1 for it in listings if assessments[it.key].tier == t)
        for t in (TIER_MATCH, TIER_CHECK, TIER_OUT)
    }
    urgent = sorted(
        (it for it in listings if assessments[it.key].urgent),
        key=lambda it: sort_key(it, assessments[it.key]),
    )
    lines = [
        (
            "🏠 租屋追蹤條件已更新，重新盤點：之後照常每小時通知新物件與降價。"
            if updated
            else "🏠 租屋追蹤開始了：之後每小時掃一次，有新物件或降價就通知你。"
        ),
        f"條件：{'、'.join(criteria.districts)}｜{criteria.price_min:,}–{criteria.price_max:,} 元｜"
        f"{criteria.area_min:g}–{criteria.area_max:g} 坪｜"
        f"捷運 {criteria.mrt_max_m}m 內｜可營業登記"
        + ("｜不含地下室" if criteria.exclude_basement else "")
        + (f"｜加分：{'、'.join(criteria.bonus_districts)}" if criteria.bonus_districts else ""),
        f"目前在架 {len(listings)} 筆（{counts}）：符合 {tier_counts[TIER_MATCH]}・"
        f"待確認 {tier_counts[TIER_CHECK]}・不符合 {tier_counts[TIER_OUT]}",
        "",
    ]
    lines += _section(
        f"🔥 *建議儘快聯繫（{len(urgent)} 筆）*",
        [format_listing(it, assessments[it.key]) for it in urgent],
    )
    lines.append(f"全部清單（排序、理由、標記有興趣／不考慮）：<{BRIDGE_URL}|Bridge 租屋追蹤>")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _post(slack_bot: Any, text: str, context: str) -> None:
    try:
        slack_bot.post_plain(text, context=context)
    except Exception as exc:  # noqa: BLE001 — a Slack hiccup mustn't fail the cron
        logger.warning("rental_watch Slack post failed (%s): %s", context, exc)


def _update_source_health(
    conn: sqlite3.Connection,
    source: str,
    error: Optional[str],
    slack_bot: Any,
    now_iso: str,
) -> None:
    row = conn.execute(
        "SELECT consecutive_failures, alerted FROM source_health WHERE source = ?", (source,)
    ).fetchone()
    failures = row["consecutive_failures"] if row else 0
    alerted = bool(row["alerted"]) if row else False
    label = SOURCE_LABELS.get(source, source)
    if error is None:
        if alerted and slack_bot is not None:
            _post(slack_bot, f"✅ 租屋追蹤：{label} 恢復正常了。", "rental_watch_source")
        failures, alerted = 0, False
    else:
        failures += 1
        if failures >= SOURCE_FAILURE_ALERT_THRESHOLD and not alerted and slack_bot is not None:
            _post(
                slack_bot,
                f"⚠️ 租屋追蹤：{label} 已連續 {failures} 次抓取失敗，這段時間它的新物件不會通知。"
                f"\n最後的錯誤：{error[:300]}",
                "rental_watch_source",
            )
            alerted = True
    conn.execute(
        "INSERT INTO source_health (source, consecutive_failures, last_error, alerted, "
        "last_success_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(source) DO UPDATE SET "
        "consecutive_failures = excluded.consecutive_failures, "
        "last_error = excluded.last_error, alerted = excluded.alerted, "
        "last_success_at = COALESCE(excluded.last_success_at, source_health.last_success_at)",
        (source, failures, error, int(alerted), None if error else now_iso),
    )


def _enrich_registration(
    http: Http,
    listings: list[Listing],
    existing: dict[str, sqlite3.Row],
    criteria: Criteria,
) -> tuple[list[Listing], set[str]]:
    """591 沒標「可登記」的，補抓詳細頁的「登記」欄（每筆只抓一次，結果存 DB）。

    回傳 (補好的 listings, 這輪新查過的 key)。離捷運太遠的不查（反正不符合）。
    """
    from agents.franky.jobs.rental_assess import mrt_status

    budget = _591_DETAIL_BUDGET
    checked: set[str] = set()
    out = []
    for it in listings:
        if it.source == "591" and it.registration == _591_REG_UNMARKED:
            row = existing.get(it.key)
            if row is not None and row["registration_checked"]:
                it = replace(it, registration=row["registration"])
            elif budget > 0 and mrt_status(it, criteria.mrt_max_m) is not False:
                budget -= 1
                try:
                    it = replace(it, registration=fetch_591_registration(http, it.listing_id))
                    checked.add(it.key)
                except Exception as exc:  # noqa: BLE001 — 下一輪再試
                    logger.warning("rental_watch 591 detail %s failed: %s", it.listing_id, exc)
        out.append(it)
    return out, checked


def run_once(
    *,
    dry_run: bool = False,
    slack_bot: Optional[Any] = None,
    db_path: Optional[Path] = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    fetchers: Optional[dict[str, Callable[[Http, Criteria], list[Listing]]]] = None,
    http: Optional[Http] = None,
    now: Optional[datetime] = None,
) -> RentalWatchResult:
    """掃一輪。``dry_run`` 只抓、不寫 DB、不發 Slack（也不補抓詳細頁）。"""
    from agents.franky.jobs.rental_assess import assess_all, sort_key

    db_path = db_path or default_db_path()
    criteria = load_criteria(config_path, db_path=db_path)
    fetchers = fetchers if fetchers is not None else DEFAULT_FETCHERS
    now_iso = (now or datetime.now(timezone.utc)).isoformat()
    own_http = http is None
    http = http or Http()

    found: dict[str, Listing] = {}
    source_counts: dict[str, int] = {}
    source_errors: dict[str, str] = {}
    try:
        for source, fetch in fetchers.items():
            try:
                kept = [it for it in fetch(http, criteria) if passes(it, criteria)]
            except Exception as exc:  # noqa: BLE001 — one site breaking mustn't stop the others
                source_errors[source] = f"{type(exc).__name__}: {exc}"
                logger.warning("rental_watch %s fetch failed: %s", source, exc)
                continue
            for it in kept:
                found.setdefault(it.key, it)  # 同一站的跨類型重複（591 辦公/住辦）只留一筆
            source_counts[source] = len({it.key for it in kept})

        listings = list(found.values())
        if dry_run:
            assessments = assess_all(listings, criteria)
            for it in sorted(listings, key=lambda it: sort_key(it, assessments[it.key])):
                a = assessments[it.key]
                logger.info("dry-run [%s] %s", a.tier, format_listing(it, a))
            basements = sum(1 for it in listings if it.is_basement)
            return RentalWatchResult(
                "ok", len(listings), 0, 0, basements, source_counts, source_errors
            )

        # 網路請求（詳細頁）不放在寫入 transaction 裡，免得卡住 Bridge 的標記寫入。
        conn = _connect(db_path)
        try:
            existing = {r["key"]: r for r in conn.execute("SELECT * FROM listings").fetchall()}
        finally:
            conn.close()
        listings, newly_checked = _enrich_registration(http, listings, existing, criteria)
    finally:
        if own_http:
            http.close()

    conn = _connect(db_path)
    try:
        with conn:
            for source in fetchers:
                _update_source_health(conn, source, source_errors.get(source), slack_bot, now_iso)
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_scan_at', ?)", (now_iso,)
            )
            meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
            criteria_changed = (
                "baseline_done" in meta and meta.get("criteria_hash") != criteria.config_hash
            )
            is_baseline = "baseline_done" not in meta or criteria_changed
            new = [it for it in listings if it.key not in existing]
            drops = [
                PriceDrop(it, existing[it.key]["price"])
                for it in listings
                if it.key in existing and it.price < existing[it.key]["price"]
            ]
            dropped = {d.listing.key: d.old_price for d in drops}

            for it in listings:
                old = existing.get(it.key)
                prev_price = dropped.get(it.key, old["prev_price"] if old else None)
                checked = int(it.key in newly_checked or bool(old and old["registration_checked"]))
                conn.execute(
                    "INSERT INTO listings (key, source, listing_id, title, url, district, price, "
                    "prev_price, area, floor, is_basement, kind, mrt_station, mrt_distance, "
                    "registration, registration_checked, views, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET title = excluded.title, url = excluded.url, "
                    "price = excluded.price, prev_price = excluded.prev_price, "
                    "area = excluded.area, floor = excluded.floor, "
                    "is_basement = excluded.is_basement, kind = excluded.kind, "
                    "mrt_station = excluded.mrt_station, mrt_distance = excluded.mrt_distance, "
                    "registration = excluded.registration, "
                    "registration_checked = excluded.registration_checked, "
                    "views = excluded.views, last_seen = excluded.last_seen",
                    (
                        it.key,
                        it.source,
                        it.listing_id,
                        it.title,
                        it.url,
                        it.district,
                        it.price,
                        prev_price,
                        it.area,
                        it.floor,
                        int(it.is_basement),
                        it.kind,
                        it.mrt_station,
                        it.mrt_distance,
                        it.registration,
                        checked,
                        it.views,
                        now_iso,
                        now_iso,
                    ),
                )

            # 第一輪只要有任何一站成功就算完成盤點；全掛的話下一輪再盤點，
            # 免得之後把整批既有物件當成「新物件」洗版。
            # 條件改了也一樣：重新盤點、更新 criteria_hash。
            if is_baseline and source_counts:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('baseline_done', ?)",
                    (now_iso,),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('criteria_hash', ?)",
                    (criteria.config_hash,),
                )
    finally:
        conn.close()

    assessments = assess_all(listings, criteria)
    basement_new = sum(1 for it in new if it.is_basement)
    if is_baseline:
        if source_counts and slack_bot is not None:
            _post(
                slack_bot,
                format_baseline_message(
                    listings, assessments, source_counts, criteria, updated=criteria_changed
                ),
                "rental_watch_baseline",
            )
        status = "baseline" if source_counts else "error"
        return RentalWatchResult(status, len(listings), 0, 0, 0, source_counts, source_errors)

    message = format_update_message(new, drops, assessments)
    if message and slack_bot is not None:
        _post(slack_bot, message, "rental_watch_update")
    status = "notified" if message else ("error" if not source_counts else "ok")
    logger.info(
        "rental_watch %s seen=%d new=%d drops=%d errors=%s",
        status,
        len(listings),
        len(new),
        len(drops),
        list(source_errors),
    )
    return RentalWatchResult(
        status, len(listings), len(new), len(drops), basement_new, source_counts, source_errors
    )
