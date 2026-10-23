"""租屋物件評估：分層（符合／待確認／不符合）＋排序分數＋理由標籤。

Slack 推播（``rental_watch``）與 Bridge 頁面（``/bridge/rental``）共用這一份，
兩邊看到的判斷一定一致。全部是看得到原因的規則，不是黑箱分數：每一個加減分
都會變成一個標籤顯示給修修。

修修 2026-10-23 的兩個必要條件：

- **捷運要近**：距最近捷運站 ≤ ``Criteria.mrt_max_m``。只有 591 有距離資料。
- **要能營業登記**：591 列表的「可登記」標籤，或詳細頁「登記」欄。

資料不足不等於不符合——信義、好房網沒有這兩項資料，放「待確認」讓修修問；
只有**確定**不符合（距離超過門檻、標示不可登記）才分到「不符合」。

排序用的訊號都是代理指標，標籤上直接寫數字，讓修修自己判斷：

- 每坪租金 vs 同區中位數（目前這批在架物件算的，不是市場行情）
- 坪數、地下室、離捷運多近
- 591「昨日瀏覽」人數（熱度）
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:
    from agents.franky.jobs.rental_watch import Criteria, Listing

TIER_MATCH = "match"
TIER_CHECK = "check"
TIER_OUT = "out"

# 熱度門檻：591 昨日瀏覽至少這麼多人，且落在這批的前 10%，才標「搶手」。
HOT_MIN_VIEWS = 20
HOT_PERCENTILE = 0.9

# 「建議儘快聯繫」：符合條件，且（搶手 或 加分 ≥ 這個數）。
URGENT_MIN_SCORE = 3


@dataclass(frozen=True)
class Chip:
    text: str
    tone: str  # good | warn | bad | info


@dataclass(frozen=True)
class Assessment:
    tier: str  # match | check | out
    score: int
    chips: tuple[Chip, ...]
    hot: bool
    urgent: bool
    price_per_ping: Optional[int]
    vs_median_pct: Optional[int]  # 每坪租金比同區中位數 +/-%（負數 = 比較便宜）


def registration_status(text: str) -> Optional[bool]:
    """591 的登記欄位 → True 可登記 / False 不可 / None 不確定或沒資料。"""
    text = (text or "").strip()
    if not text:
        return None
    if "不可" in text:
        return False
    if text in ("可登記", "可"):
        return True
    return None  # 「暫不確定」「未標示」等


def mrt_status(listing: "Listing", mrt_max_m: int) -> Optional[bool]:
    if listing.mrt_distance is None:
        return None
    return listing.mrt_distance <= mrt_max_m


def price_per_ping(listing: "Listing") -> Optional[float]:
    if not listing.area or listing.area <= 0 or listing.price <= 0:
        return None
    return listing.price / listing.area


def district_medians(listings: Iterable["Listing"]) -> dict[str, float]:
    by_district: dict[str, list[float]] = {}
    for it in listings:
        ppp = price_per_ping(it)
        if ppp is not None:
            by_district.setdefault(it.district, []).append(ppp)
    # 樣本太少的區不算中位數（2 筆的「中位數」沒有參考價值）
    return {d: statistics.median(v) for d, v in by_district.items() if len(v) >= 3}


def hot_threshold(listings: Iterable["Listing"]) -> int:
    views = sorted(it.views for it in listings if it.views is not None)
    if not views:
        return HOT_MIN_VIEWS
    p90 = views[min(len(views) - 1, int(len(views) * HOT_PERCENTILE))]
    return max(HOT_MIN_VIEWS, p90)


def assess(
    listing: "Listing",
    *,
    medians: dict[str, float],
    hot_min_views: int,
    mrt_max_m: int,
) -> Assessment:
    chips: list[Chip] = []
    score = 0

    # ── 必要條件 → 分層 ───────────────────────────────────────────────
    mrt_ok = mrt_status(listing, mrt_max_m)
    reg_ok = registration_status(listing.registration)
    if mrt_ok is False:
        chips.append(Chip(f"離捷運 {listing.mrt_distance}m，超過 {mrt_max_m}m", "bad"))
    elif mrt_ok is None:
        chips.append(Chip("捷運距離未知", "warn"))
    if reg_ok is False:
        chips.append(Chip(f"登記：{listing.registration}", "bad"))
    elif reg_ok is None:
        label = listing.registration if listing.registration else "沒有資料"
        chips.append(Chip(f"可否登記：{label}，要問", "warn"))

    if mrt_ok is False or reg_ok is False:
        tier = TIER_OUT
    elif mrt_ok and reg_ok:
        tier = TIER_MATCH
    else:
        tier = TIER_CHECK

    # ── 加減分（都附數字，讓修修自己判斷） ────────────────────────────
    ppp = price_per_ping(listing)
    vs_pct: Optional[int] = None
    median = medians.get(listing.district)
    if ppp is not None and median:
        vs_pct = round((ppp / median - 1) * 100)
        if vs_pct <= -20:
            score += 2
            chips.append(Chip(f"每坪比同區便宜 {-vs_pct}%", "good"))
        elif vs_pct <= -10:
            score += 1
            chips.append(Chip(f"每坪比同區便宜 {-vs_pct}%", "good"))
        elif vs_pct >= 20:
            score -= 1
            chips.append(Chip(f"每坪比同區貴 {vs_pct}%", "warn"))

    if listing.area and listing.area >= 30:
        score += 1
        chips.append(Chip(f"空間大 {listing.area:g} 坪", "good"))
    if listing.is_basement:
        score += 1
        chips.append(Chip("地下室", "info"))
    if mrt_ok and listing.mrt_distance is not None and listing.mrt_distance <= 200:
        score += 1
        chips.append(Chip(f"離{listing.mrt_station or '捷運'}只有 {listing.mrt_distance}m", "good"))

    hot = listing.views is not None and listing.views >= hot_min_views
    if hot:
        chips.append(Chip(f"搶手：昨天 {listing.views} 人看過", "good"))

    urgent = tier == TIER_MATCH and (hot or score >= URGENT_MIN_SCORE)
    return Assessment(
        tier=tier,
        score=score,
        chips=tuple(chips),
        hot=hot,
        urgent=urgent,
        price_per_ping=round(ppp) if ppp is not None else None,
        vs_median_pct=vs_pct,
    )


def assess_all(listings: list["Listing"], criteria: "Criteria") -> dict[str, Assessment]:
    """以這一批在架物件當比較基準，評估每一筆。回傳 key → Assessment。"""
    medians = district_medians(listings)
    hot_min = hot_threshold(listings)
    return {
        it.key: assess(it, medians=medians, hot_min_views=hot_min, mrt_max_m=criteria.mrt_max_m)
        for it in listings
    }


def sort_key(listing: "Listing", a: Assessment) -> tuple:
    """同一層內：建議儘快聯繫 → 分數高 → 每坪便宜。"""
    return (not a.urgent, -a.score, a.price_per_ping if a.price_per_ping is not None else 10**9)
