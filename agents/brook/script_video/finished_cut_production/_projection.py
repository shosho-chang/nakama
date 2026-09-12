"""視覺詞彙的**唯一**宣告（ADR-069 階段 1）。

## 為什麼是一份

同一套詞彙本來在 9 個地方各自宣告一次：這裡、`_release.allowed_projection`、
`_resolve_fusion._LANE_TRACKS`、`_derived_assets` 的 generated／passthrough 兩個
frozenset、`_visual_assets` 的兩張 `expected_kind` 對照、`_worker_packet`、`_policy`、
`_long_visual_renderer._RECIPES` 的 key、`_persistence._component_from_payload` 的
lane 集合。任何一份漏改就是一批測試紅，而且錯誤訊息指向的是「渲染器對不上版位」
這種離根因很遠的地方。

2026-09-09 版位版本就是這樣裂成兩個真相來源，害 27 個測試一起紅（6d464d19 把
`LAYOUT_VERSIONS` 收進來，是這條路的第一步）。2026-09-12 一個三行規則能講清楚的
brand badge 撞上四道各自封閉的契約，兩個回合、零行可用程式碼——那四道門並不是
四個不同的檢查，是同一張表的四份副本。

所以：**`VOCABULARY` 是唯一宣告，其餘全部從它推導。** 新增一種視覺元素只改這個檔。

## 退役詞彙

`retired=True` 的條目**不可被 worker 提案、不進現役投影集合**，只為了讓既有的
Release receipt 與已完結的 run JSON 讀得回來。ADR-069 階段 2 會在歸檔那些 run
之後把它們整批刪掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from ._assets import AssetKind

#: 型別層的 lane 白名單。**這是型別不是資料**，所以無法從 `VOCABULARY` 推導；
#: `test_finished_cut_layout_versions.py` 鎖住它與 `VOCABULARY` 一致。
ComponentLane = Literal[
    "b_roll",
    "identity_card",
    "hero_title",
    "fullscreen_transition",
    # 已退役，僅為既有 receipt／run 保留（見模組 docstring）。
    "visual_effect",
]


@dataclass(frozen=True, slots=True)
class ImplementationSpec:
    """一種視覺實作的全部屬性。每個欄位都是某個下游本來自己宣告一份的東西。"""

    #: Director 提案時用的語意類別。
    semantic_kind: str
    #: 投影到哪一條 lane（`_records.ProjectedComponent.lane`）。
    lane: str
    #: Resolve 上的 video track。V1 是 owner 的剪輯，機器只用 2–7。
    track_index: int
    #: True = 由 core 依配方算出來的（字卡、轉場卡）；False = 直接用取得的素材。
    generated: bool
    #: 成品資產發佈進 Active Store 時的類別；`camera_correction` 不產資產所以是 None。
    asset_kind: AssetKind | None
    #: worker 挑進來的**來源**素材類別。跟 `asset_kind` 常常一樣，但 person_inset
    #: 是挑一張 PHOTO、算出一支 COMPOSITE，兩者不同。不吃來源素材的是 None。
    source_asset_kind: AssetKind | None
    #: 版位版本。只有 `generated` 的才有；renderer 與指令兩邊都讀這一份。
    layout_version: str | None
    #: 產出的檔案後綴。滿版轉場卡是不透明 mp4，其餘字卡要 alpha 所以是 mov。
    media_suffix: str | None
    #: worker（Director／DP）可以提案嗎？`camera_correction` 只有 core 能鑄。
    worker_selectable: bool
    #: 已退役：不進現役集合，只為讀得回既有 receipt。
    retired: bool = False

    @property
    def projection(self) -> tuple[str, str, str]:
        """`(semantic_kind, implementation_kind, lane)` 三元組裡的頭尾。"""
        return (self.semantic_kind, "", self.lane)


VOCABULARY: dict[str, ImplementationSpec] = {
    "fullscreen_transition": ImplementationSpec(
        semantic_kind="chapter",
        lane="fullscreen_transition",
        track_index=6,
        generated=True,
        asset_kind=AssetKind.CHAPTER_RENDER,
        source_asset_kind=None,
        layout_version="v4",
        media_suffix=".mp4",
        worker_selectable=True,
    ),
    # hero_title v2：2026-09-08 從 44px 無底字卡改回定版 punch_card_wide tier1
    #   ＋ paper 配方（紙卡、96px、只在標點斷行）。
    "hero_title": ImplementationSpec(
        semantic_kind="hero_title",
        lane="hero_title",
        track_index=3,
        generated=True,
        asset_kind=AssetKind.TITLE_RENDER,
        source_asset_kind=None,
        layout_version="v2",
        media_suffix=".mov",
        worker_selectable=True,
    ),
    # identity_card v2：2026-09-08 從 ADR-066 自創的 identity_plaque 36px 置中藥丸，
    #   改回定版 chapter_label_wide align:left + style:paper（左下紙卡＋手繪橘豎筆觸
    #   ＋姓名 50px／頭銜 29px）。
    "identity_card": ImplementationSpec(
        semantic_kind="identity_card",
        lane="identity_card",
        track_index=4,
        generated=True,
        asset_kind=AssetKind.CONCEPT_RENDER,
        source_asset_kind=None,
        layout_version="v2",
        media_suffix=".mov",
        worker_selectable=True,
    ),
    "stock_video": ImplementationSpec(
        semantic_kind="b_roll",
        lane="b_roll",
        track_index=2,
        generated=False,
        asset_kind=AssetKind.STOCK,
        source_asset_kind=AssetKind.STOCK,
        layout_version=None,
        media_suffix=None,
        worker_selectable=True,
    ),
    "photo": ImplementationSpec(
        semantic_kind="b_roll",
        lane="b_roll",
        track_index=2,
        generated=False,
        asset_kind=AssetKind.PHOTO,
        source_asset_kind=AssetKind.PHOTO,
        layout_version=None,
        media_suffix=None,
        worker_selectable=True,
    ),
    "non_editorial_clip": ImplementationSpec(
        semantic_kind="b_roll",
        lane="b_roll",
        track_index=2,
        generated=False,
        asset_kind=AssetKind.NON_EDITORIAL_CLIP,
        source_asset_kind=AssetKind.NON_EDITORIAL_CLIP,
        layout_version=None,
        media_suffix=None,
        worker_selectable=True,
    ),
    "person_inset": ImplementationSpec(
        semantic_kind="b_roll",
        lane="b_roll",
        track_index=2,
        generated=True,
        asset_kind=AssetKind.COMPOSITE,
        source_asset_kind=AssetKind.PHOTO,
        layout_version="v1",
        media_suffix=".mov",
        worker_selectable=True,
    ),
    # core-only：worker 提案不出來，由 core 鑄。
    "camera_correction": ImplementationSpec(
        semantic_kind="b_roll",
        lane="b_roll",
        track_index=2,
        generated=False,
        asset_kind=None,
        source_asset_kind=None,
        layout_version=None,
        media_suffix=None,
        worker_selectable=False,
    ),
    # ⛔ 2026-09-08 退役，比照 supporting_title。它是 ADR-066 第一個 commit
    # （2a5edf12）憑空造出來的第六個語意類別，用來頂替同日退役的 supporting_title。
    # 頻道的創意手冊列的長片視覺語彙裡沒有這一項，整個 `.claude/skills/` grep 不到
    # 一次。沒有設計就沒有規格：它在 `_PLACEMENT_DURATION_CEILINGS_SEC` 裡連條目都
    # 沒有，所以一張卡可以掛 9.77 秒，渲染成 44px 無底字卡看起來像跑掉的字幕。
    "visual_effect": ImplementationSpec(
        semantic_kind="visual_effect",
        lane="visual_effect",
        track_index=7,
        generated=True,
        asset_kind=AssetKind.CONCEPT_RENDER,
        source_asset_kind=None,
        layout_version="v1",
        media_suffix=".mov",
        worker_selectable=False,
        retired=True,
    ),
}

#: 只存在於既有 Release receipt 裡的投影三元組（沒有 lane track、不在 `VOCABULARY`）。
#: reader 要寬鬆、writer 要嚴格——這是刻意的不對稱。
RETIRED_RELEASE_PROJECTIONS: frozenset[tuple[str, str, str]] = frozenset(
    {("supporting_title", "supporting_title", "supporting_title")}
)

#: 不投影成 component 的語意類別（Director 刻意不配畫面）。
_INTENTIONAL_AROLL = "intentional_aroll"


def _spec_items(*, retired: bool | None = None) -> tuple[tuple[str, ImplementationSpec], ...]:
    return tuple(
        (kind, spec)
        for kind, spec in VOCABULARY.items()
        if retired is None or spec.retired is retired
    )


# --------------------------------------------------------------------------
# 以下全部從 VOCABULARY 推導。**不要在任何下游另外宣告一份。**
# --------------------------------------------------------------------------

#: worker（Director／DP）可以提案的投影三元組。
_WORKER_PROJECTION_COMBINATIONS: tuple[tuple[str, str, str], ...] = tuple(
    (spec.semantic_kind, kind, spec.lane)
    for kind, spec in _spec_items(retired=False)
    if spec.worker_selectable
)

#: 現役的全部投影三元組（worker 可提案的 ＋ core-only）。
_ACTIVE_PROJECTION_COMBINATIONS: frozenset[tuple[str, str, str]] = frozenset(
    (spec.semantic_kind, kind, spec.lane) for kind, spec in _spec_items(retired=False)
)

#: Release reader 接受的投影三元組（現役 ＋ 退役，含只在 receipt 裡的）。
RELEASE_PROJECTIONS: frozenset[tuple[str, str, str]] = (
    frozenset((spec.semantic_kind, kind, spec.lane) for kind, spec in VOCABULARY.items())
    | RETIRED_RELEASE_PROJECTIONS
)

_ACTIVE_SEMANTIC_KINDS: frozenset[str] = frozenset(
    {spec.semantic_kind for _kind, spec in _spec_items(retired=False)} | {_INTENTIONAL_AROLL}
)

#: 順序取自 `ComponentLane` 的宣告順序，不是 `VOCABULARY` 的——它會被寫進 worker
#: 的 response schema enum（`_codex_semantic.py:789`），順序變了 schema bytes 就變了。
_ACTIVE_LANES = frozenset(spec.lane for _kind, spec in _spec_items(retired=False))
_ACTIVE_COMPONENT_LANES: tuple[str, ...] = tuple(
    lane for lane in get_args(ComponentLane) if lane in _ACTIVE_LANES
)

#: 持久化 reader 接受的 lane（含退役——既有 run JSON 還帶著）。
PERSISTED_COMPONENT_LANES: frozenset[str] = frozenset(
    spec.lane for _kind, spec in VOCABULARY.items()
)

#: lane → Resolve video track。V1 是 owner 的剪輯，機器只寫 2–7。
LANE_TRACKS: dict[str, int] = {spec.lane: spec.track_index for _kind, spec in VOCABULARY.items()}

#: 由 core 依配方算出來的實作（字卡、轉場卡、person inset composite）。
GENERATED_IMPLEMENTATIONS: frozenset[str] = frozenset(
    kind for kind, spec in VOCABULARY.items() if spec.generated
)

#: 直接使用取得素材、不經渲染的實作。
NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS: frozenset[str] = frozenset(
    kind for kind, spec in VOCABULARY.items() if not spec.generated and spec.asset_kind is not None
)

#: 實作 → Active Store 資產類別。`camera_correction` 不產資產，不在表裡。
ASSET_KIND_BY_IMPLEMENTATION: dict[str, AssetKind] = {
    kind: spec.asset_kind for kind, spec in VOCABULARY.items() if spec.asset_kind is not None
}

#: 實作 → worker 挑進來的來源素材類別。`person_inset` 挑 PHOTO、產 COMPOSITE，
#: 所以這張表跟 `ASSET_KIND_BY_IMPLEMENTATION` 不是同一份。
SOURCE_ASSET_KIND_BY_IMPLEMENTATION: dict[str, AssetKind] = {
    kind: spec.source_asset_kind
    for kind, spec in VOCABULARY.items()
    if spec.source_asset_kind is not None
}

#: 吃取得素材的實作（＝有來源素材類別的）。policy 的視覺覆蓋率算這幾種。
ASSET_BACKED_IMPLEMENTATIONS: frozenset[str] = frozenset(SOURCE_ASSET_KIND_BY_IMPLEMENTATION)

#: 實作 → 產出檔案後綴（只有 generated 的才有）。
MEDIA_SUFFIX_BY_IMPLEMENTATION: dict[str, str] = {
    kind: spec.media_suffix
    for kind, spec in VOCABULARY.items()
    if spec.generated and spec.media_suffix is not None
}

#: 每個渲染實作目前的版位版本。指令的 geometry 與渲染器接受的配方都讀這一份。
LAYOUT_VERSIONS: dict[str, str] = {
    kind: spec.layout_version
    for kind, spec in VOCABULARY.items()
    if spec.layout_version is not None
}


def layout_identity(implementation_kind: str) -> str:
    """回傳這個實作的 canonical layout identity（例如 `hero_title:v2`）。"""
    return f"{implementation_kind}:{LAYOUT_VERSIONS.get(implementation_kind, 'v1')}"


def _is_active_semantic_kind(value: str) -> bool:
    return value in _ACTIVE_SEMANTIC_KINDS


def _is_active_projection(
    semantic_kind: str,
    implementation_kind: str,
    lane: str,
) -> bool:
    return (semantic_kind, implementation_kind, lane) in _ACTIVE_PROJECTION_COMBINATIONS


def _event_has_active_projection(
    *,
    semantic_kind: str,
    implementation_kind: str,
    lane: str | None,
    intentional_aroll: bool,
) -> bool:
    if intentional_aroll:
        return (
            semantic_kind == _INTENTIONAL_AROLL
            and implementation_kind == _INTENTIONAL_AROLL
            and lane is None
        )
    return lane is not None and _is_active_projection(
        semantic_kind,
        implementation_kind,
        lane,
    )
