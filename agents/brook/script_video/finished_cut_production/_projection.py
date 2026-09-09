"""Canonical active visual vocabulary for Finished Cut Production.

Historical Release receipts may contain retired values, but they are deliberately
absent from this module so no current worker, acceptance, plan, or materializer can
discover or mint them through the production vocabulary.
"""

from __future__ import annotations

from typing import Literal

ComponentLane = Literal[
    "b_roll",
    "identity_card",
    "hero_title",
    "fullscreen_transition",
    # 已退役（見下方 _WORKER_PROJECTION_COMBINATIONS）。留在型別裡只為了讓既有
    # Release receipt 讀得回來，不代表現役詞彙。
    "visual_effect",
]

_WORKER_PROJECTION_COMBINATIONS = (
    ("chapter", "fullscreen_transition", "fullscreen_transition"),
    ("hero_title", "hero_title", "hero_title"),
    ("b_roll", "stock_video", "b_roll"),
    ("b_roll", "photo", "b_roll"),
    ("b_roll", "non_editorial_clip", "b_roll"),
    ("b_roll", "person_inset", "b_roll"),
    ("identity_card", "identity_card", "identity_card"),
    # ⛔ visual_effect 於 2026-09-08 退役，比照 supporting_title。
    #
    # 它是 ADR-066 的第一個 commit（2a5edf12，2026-08-29）憑空造出來的第六個語意
    # 類別，用來頂替同日退役的 supporting_title。頻道的創意手冊
    # （.claude/skills/longform-cut/SKILL.md）列的長片視覺語彙只有六種：滿版轉場卡、
    # 品牌 badge、來賓名牌、論文第一頁卡、Hero 大字卡、Stock Video——沒有這一項，
    # 整個 .claude/skills/ 裡 grep 不到一次。
    #
    # 沒有設計就沒有規格：它在 _PLACEMENT_DURATION_CEILINGS_SEC 裡連條目都沒有，
    # 所以一張卡可以掛 9.77 秒；渲染成 44px 無底字卡，比 hero_title 更小、在另一條
    # 軌上，看起來像跑掉的字幕。Director 的 intent 寫「適合圖像化呈現而非直接實拍」，
    # 實作卻只給一行字——名字承諾一個視覺，交出來的是字幕。
    #
    # 這幾拍該用 B-roll 或 Hero 大字卡。歷史 Release 仍讀得回來（見上方 docstring）。
)
_ACTIVE_PROJECTION_COMBINATIONS = frozenset(
    (*_WORKER_PROJECTION_COMBINATIONS, ("b_roll", "camera_correction", "b_roll"))
)
_ACTIVE_SEMANTIC_KINDS = frozenset(
    {row[0] for row in _ACTIVE_PROJECTION_COMBINATIONS} | {"intentional_aroll"}
)
_ACTIVE_COMPONENT_LANES = (
    "b_roll",
    "identity_card",
    "hero_title",
    "fullscreen_transition",
)


#: 每個渲染實作目前的版位版本。**這是唯一的真相來源。**
#:
#: 2026-09-09：版本號本來散在兩個地方——`_engine._LAYOUT_VERSIONS` 決定指令的
#: geometry，`_long_visual_renderer._RECIPES` 決定渲染器接受什麼，而渲染器對不上就
#: 直接丟 `long visual geometry does not match its canonical layout`。9-08 把 hero
#: bump 到 v2 時只改了其中一邊的下游，測試 fixture 又各自寫死 `hero_title:v1`，於是
#: 27 個測試一起紅。版本住在契約層，兩邊都 import，就不會再有這種漂移。
LAYOUT_VERSIONS: dict[str, str] = {
    "fullscreen_transition": "v4",
    # hero_title v2：2026-09-08 從 44px 無底字卡改回定版 punch_card_wide tier1
    #   ＋ paper 配方（紙卡、96px、只在標點斷行）。
    "hero_title": "v2",
    # identity_card v2：2026-09-08 從 ADR-066 自創的 identity_plaque 36px 置中藥丸，
    #   改回定版 chapter_label_wide align:left + style:paper（左下紙卡＋手繪橘豎筆觸
    #   ＋姓名 50px／頭銜 29px）。
    "identity_card": "v2",
    # 已退役，僅為歷史 Release receipt 保留。
    "visual_effect": "v1",
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
            semantic_kind == "intentional_aroll"
            and implementation_kind == "intentional_aroll"
            and lane is None
        )
    return lane is not None and _is_active_projection(
        semantic_kind,
        implementation_kind,
        lane,
    )
