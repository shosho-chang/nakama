"""品牌 badge：結構性覆蓋層，不是 Director 提案。

## 為什麼不走語意管線

手冊（`.claude/skills/longform-cut/SKILL.md` 第 197 行）把 badge 的落點寫死了：

> 左下角 logo，**只出現開場（收在名牌進場前，如 7.4s）+ 每個轉場卡結束後 ~8s**。
> `kind:"badge"` + slug `brand-badge-7s`/`brand-badge-8s`/`brand-badge-10s`：
> **定長 fade 預合成**（180px、alpha fade in/out 0.5s）……鋪 track 5

沒有一個欄位需要創意判斷——落點完全由滿版轉場卡與來賓名牌推導得出，素材是跨集
byte 相同的品牌資產。所以它不該進 Director／DP／visual_review 那三關：讓 worker
去「提案」一個規則已經算得出來的東西，只會多一個它可以做錯的地方（而且 badge 進
語意流還會佔掉字卡密度配額、要 visual_review 逐張看一個永遠一樣的 logo）。

legacy 產線本來就是這樣分的——`agents/brook/script_video/highlight_broll.py:29`：

    STRUCTURAL_BROLL_KINDS = {"camera-correction", "guest-namecard", "badge"}

ADR-066 遷移時把這一類整個弄丟了：`badge` 在整個 finished_cut_production 套件裡
出現零次，ADR-066 決策文件裡也零次。修修 2026-09-09：「每次 full transition 完
之後，緊接著的品牌 logo 的動畫也都不見了。」本模組把它補回來。

## 為什麼是 slug 不是 content-addressed ref

badge 是**定長預合成**：fade in／out 烘在檔案裡，位置也烘在檔案裡。窗口比素材短的
時候不能硬剪——剪掉的是淡出。所以這裡的規則是「挑塞得下的最長那支，塞不下就不放」，
而不是「拉長縮短」。素材本身是每集 `assets/broll/<slug>.mov` 的品牌資產，由
materialization 解析與驗檔；核心只負責算落點。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

__all__ = [
    "BRAND_BADGE_SLUG_SECONDS",
    "BRAND_BADGE_TRACK_INDEX",
    "BrandBadgeOverlay",
    "derive_brand_badge_overlays",
]

#: badge 鋪在 v5——v1 主鏡＋字幕、v2 stock、v3 Hero、v4 名牌＋轉場卡。
BRAND_BADGE_TRACK_INDEX = 5

#: 定長預合成的可選長度（秒）。這三支是既有的品牌資產，跨集 byte 相同。
BRAND_BADGE_SLUG_SECONDS: tuple[tuple[str, float], ...] = (
    ("brand-badge-10s", 10.0),
    ("brand-badge-8s", 8.0),
    ("brand-badge-7s", 7.4),
)

#: 開場 badge 的窗口上限；實際還要收在名牌進場前。
OPENING_WINDOW_SEC = 7.4
#: 每張滿版轉場卡結束後的窗口上限。
AFTER_TRANSITION_WINDOW_SEC = 8.0


class _PlacedComponent(Protocol):
    """只用得到這四個欄位。

    刻意不 import `_records.ProjectedComponent`——`_records` 要 import 本模組來鑄造
    overlay，反向 import 會變成循環。
    """

    @property
    def component_id(self) -> str: ...

    @property
    def lane(self) -> str: ...

    @property
    def t0(self) -> float: ...

    @property
    def t1(self) -> float: ...


@dataclass(frozen=True, slots=True)
class BrandBadgeOverlay:
    """一段品牌 badge。落點由核心推導，素材由 materialization 依 slug 解析。"""

    overlay_id: str
    slug: str
    track_index: int
    t0: float
    t1: float
    origin: str

    def __post_init__(self) -> None:
        if not self.overlay_id.strip() or not self.slug.strip() or not self.origin.strip():
            raise ValueError("brand badge overlay fields are required")
        if self.track_index < 1:
            raise ValueError("brand badge overlay track index is invalid")
        if not (0.0 <= self.t0 < self.t1):
            raise ValueError("brand badge overlay timing is invalid")


def _slug_for_window(window_sec: float) -> tuple[str, float] | None:
    """挑塞得進這個窗口的最長 badge；都塞不下就回 None。

    定長素材不能裁——裁掉的是淡出動畫。寧可這一段不放 badge。
    """
    for slug, seconds in BRAND_BADGE_SLUG_SECONDS:
        if seconds <= window_sec + 1e-6:
            return slug, seconds
    return None


def derive_brand_badge_overlays(
    *,
    components: tuple[_PlacedComponent, ...],
    duration_sec: float,
    format: Literal["long", "short"],
) -> tuple[BrandBadgeOverlay, ...]:
    """依手冊規則算出這一支片的 badge 落點。

    短片線（ADR-067）有自己的品牌處理，不套這裡的規則。
    """
    if format != "long" or duration_sec <= 0:
        return ()

    transitions = sorted(
        (component for component in components if component.lane == "fullscreen_transition"),
        key=lambda component: component.t0,
    )
    namecards = tuple(
        (component.t0, component.t1)
        for component in components
        if component.lane == "identity_card"
    )

    overlays: list[BrandBadgeOverlay] = []

    # 開場：從 0 開始，收在名牌進場前（名牌也在左下角，同框會擠）。
    opening_limit = min([OPENING_WINDOW_SEC, duration_sec, *(t0 for t0, _ in namecards)])
    chosen = _slug_for_window(opening_limit)
    if chosen is not None:
        slug, seconds = chosen
        overlays.append(
            BrandBadgeOverlay(
                overlay_id="badge:opening",
                slug=slug,
                track_index=BRAND_BADGE_TRACK_INDEX,
                t0=0.0,
                t1=seconds,
                origin="opening",
            )
        )

    # 每張滿版轉場卡結束後一段。窗口收在「下一張轉場卡進場前」與片尾之間，
    # 免得 badge 壓在下一個章節的滿版卡上。
    for index, transition in enumerate(transitions):
        start = transition.t1
        limit = min(
            start + AFTER_TRANSITION_WINDOW_SEC,
            duration_sec,
            *(
                [transitions[index + 1].t0]
                if index + 1 < len(transitions)
                else []
            ),
            # 名牌也在左下角，同框就是擠——手冊：「開場 badge 窗必須在名牌進場前收掉」。
            # 這條對每一段 badge 都成立，不是只有開場那一段：2026-09-09 第一版只擋了
            # 開場，結果 0:52 轉場卡之後那段 badge 正好壓在 0:55 進場的名牌上。
            *(
                t0
                for t0, _t1 in namecards
                if start <= t0 < start + AFTER_TRANSITION_WINDOW_SEC
            ),
        )
        # badge 起點若落在名牌播放中，整段跳過——往後挪就不是「轉場卡之後」了。
        if any(t0 <= start < t1 for t0, t1 in namecards):
            continue
        chosen = _slug_for_window(limit - start)
        if chosen is None:
            continue
        slug, seconds = chosen
        overlays.append(
            BrandBadgeOverlay(
                overlay_id=f"badge:after:{transition.component_id}",
                slug=slug,
                track_index=BRAND_BADGE_TRACK_INDEX,
                t0=start,
                t1=start + seconds,
                origin=f"after_transition:{transition.component_id}",
            )
        )

    return tuple(sorted(overlays, key=lambda overlay: overlay.t0))
