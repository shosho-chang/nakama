"""Authoritative Editorial Cut Context for fresh Finished Cut work."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

#: 收尾那一句的 t1 允許超出 duration 的浮點誤差上限。
#:
#: `duration_sec` 是 `sum(t1 - t0 for source_ranges)`——一串二進位浮點加起來，尾巴
#: 一定帶誤差（punch-L03 六段相加得 490.3029999999999）。cue 是從同一個 tight cut
#: 切出來的，最後一句本來就結束在片尾，寫下來是 490.303。兩個數學上相等的值浮點上
#: 差 6e-14，於是「cue 越界」為真。這條規則以前在登錄與物化各有一份實作，容忍度一旦
#: 走鐘就會「登錄過得去、物化卻擋下來」；現在只剩 `_approved_cut._validate_cues`
#: 這一份——只有登錄那一刻才保證 `duration_sec` 就是來源範圍的總和。
#:
#: 1e-6 只吸收表示誤差：真正越界的 cue（毫秒級以上）照樣擋下。
CUE_END_EPSILON_SEC = 1e-6
_CHAPTER_PLACEMENT_DURATION_SEC = 3.0


@dataclass(frozen=True, slots=True)
class CutSourceRange:
    t0: float
    t1: float


@dataclass(frozen=True, slots=True)
class CueAnchor:
    cue_id: str
    text: str
    t0: float
    t1: float
    section_id: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalSection:
    section_id: str
    chapter_title: str
    t0: float
    transition_before: bool = False
    transition_title: str | None = None
    # 上游 miner 每一節都會寫「這一段完成的論點」，但 ADR-066 之前一路被丟在註冊
    # 門口。轉場卡的驗收標準是「只看卡就知道這節在講什麼」——沒有這個欄位，就沒有
    # 東西可以拿來對照卡片。見 `agents/brook/script_video/transition_cold_read.py`。
    summary: str = ""


@dataclass(frozen=True, slots=True)
class DerivedEventAnchor:
    master_cue_ids: tuple[str, ...]
    text: str
    text_hash: str
    t0: float
    t1: float
    section_id: str | None


@dataclass(frozen=True, slots=True)
class VisualPlacement:
    """Temporal range where a DP-selected visual is actually shown."""

    placement_cue_ids: tuple[str, ...]
    t0: float
    t1: float
    section_id: str | None

    def __post_init__(self) -> None:
        # 這條檢查以前只在 `_mint_visual_placement` 裡跑，於是「誰造的」跟「造得對
        # 不對」綁在一起：任何直接建構的路徑（含 store 讀回）都繞得過去。搬進
        # `__post_init__` 之後，不管是推導出來的還是從磁碟讀回來的，同一條規則都
        # 會跑；ADR-069 要留的就是這種便宜的結構檢查。
        if (
            not self.placement_cue_ids
            or len(self.placement_cue_ids) != len(set(self.placement_cue_ids))
            or not math.isfinite(self.t0)
            or not math.isfinite(self.t1)
            or self.t0 < 0
            or self.t0 >= self.t1
        ):
            raise ValueError("Visual Placement fields are invalid")


def _mint_visual_placement(
    *,
    placement_cue_ids: tuple[str, ...],
    t0: float,
    t1: float,
    section_id: str | None,
) -> VisualPlacement:
    """Keyword-only spelling used by the derivation sites."""

    return VisualPlacement(
        placement_cue_ids=placement_cue_ids,
        t0=t0,
        t1=t1,
        section_id=section_id,
    )


@dataclass(frozen=True, slots=True)
class EditorialCutContext:
    episode_id: str
    cut_id: str
    format: Literal["long", "short"]
    editorial_master_id: str
    tight_cut_id: str
    duration_sec: float
    source_ranges: tuple[CutSourceRange, ...]
    cues: tuple[CueAnchor, ...]
    sections: tuple[CanonicalSection, ...] = ()
    editorial_feedback: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse to exist unless the ranges and cues are individually well-formed.

        這裡放的是**只有 context 自己知道**的結構規則：每一段來源範圍、每一句 cue
        本身站不站得住。以前它們住在 `_materialization._validate_context_contract`，
        於是只有物化那條路驗得到——store 讀回來的、worker packet 拿去的，都繞過去了。

        刻意**不**放兩件事，因為已經有別人在管，搬過來只會變成第三份實作：

        * 「來源範圍加總等於 duration」是 `_policy` 的 `source_range_sum_mismatch`
          ——它要把這件事當成**診斷**報給修修看，不是讓物件造不出來。
        * 「cue 不可為空」是 `_approved_cut` 登錄那關的事。引擎的 in-memory 假
          authority（`_engine._in_memory_editorial_context`）本來就沒有 cue。
        """

        if not math.isfinite(self.duration_sec) or self.duration_sec <= 0:
            raise ValueError("Editorial Cut Context duration is invalid")
        if not self.source_ranges:
            raise ValueError("Editorial Cut Context has no source ranges")
        previous_source_end = -1.0
        for source in self.source_ranges:
            if (
                not math.isfinite(source.t0)
                or not math.isfinite(source.t1)
                or source.t0 < 0
                or source.t0 >= source.t1
                or source.t0 < previous_source_end
            ):
                raise ValueError("Editorial Cut Context source ranges are invalid")
            previous_source_end = source.t1
        cue_ids: set[str] = set()
        previous_cue_end = -1.0
        for cue in self.cues:
            if (
                not cue.cue_id
                or cue.cue_id in cue_ids
                or not cue.text
                or not math.isfinite(cue.t0)
                or not math.isfinite(cue.t1)
                or cue.t0 < previous_cue_end
                or cue.t0 < 0
                or cue.t0 >= cue.t1
            ):
                raise ValueError("Editorial Cut Context cue contract is invalid")
            cue_ids.add(cue.cue_id)
            previous_cue_end = cue.t1

    def derive_anchor(self, cue_ids: tuple[str, ...]) -> DerivedEventAnchor:
        """Derive event authority from current tight cues, never worker timing."""

        if not cue_ids:
            raise ValueError("event anchor requires current cue IDs")
        positions = {cue.cue_id: index for index, cue in enumerate(self.cues)}
        try:
            indices = tuple(positions[cue_id] for cue_id in cue_ids)
        except KeyError as error:
            raise ValueError("event anchor contains a cue outside current tight context") from error
        if len(set(cue_ids)) != len(cue_ids) or indices != tuple(
            range(indices[0], indices[0] + len(indices))
        ):
            raise ValueError("event anchor cue IDs must be unique, ordered, and contiguous")
        selected = tuple(self.cues[index] for index in indices)
        section_ids = {cue.section_id for cue in selected}
        if len(section_ids) != 1:
            raise ValueError("event anchor cannot cross canonical sections")
        text = "\n".join(cue.text for cue in selected)
        return DerivedEventAnchor(
            master_cue_ids=cue_ids,
            text=text,
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            t0=selected[0].t0,
            t1=selected[-1].t1,
            section_id=selected[0].section_id,
        )

    def derive_visual_placement(
        self,
        *,
        semantic_cue_ids: tuple[str, ...],
        placement_cue_ids: tuple[str, ...],
        semantic_kind: str,
        min_show_sec: float | None = None,
    ) -> VisualPlacement:
        """Mint DP temporal authority from exact current cue and section facts."""

        semantic = self.derive_anchor(semantic_cue_ids)
        if semantic_kind == "chapter":
            if placement_cue_ids != semantic_cue_ids:
                raise ValueError("chapter placement cue IDs must echo its semantic proof")
            sections = tuple(
                section
                for section in self.sections
                if section.section_id == semantic.section_id and section.transition_before
            )
            if len(sections) != 1:
                raise ValueError("chapter placement requires one canonical transition section")
            section_cues = tuple(cue for cue in self.cues if cue.section_id == semantic.section_id)
            if not section_cues or semantic_cue_ids != (section_cues[0].cue_id,):
                raise ValueError(
                    "chapter semantic proof must be the first current cue of its "
                    "canonical transition section"
                )
            t0 = sections[0].t0
            t1 = min(t0 + _CHAPTER_PLACEMENT_DURATION_SEC, self.duration_sec)
            if not math.isfinite(t0) or not math.isfinite(t1) or t0 < 0 or t0 >= t1:
                raise ValueError("canonical chapter placement is outside the current cut")
            return _mint_visual_placement(
                placement_cue_ids=placement_cue_ids,
                t0=t0,
                t1=t1,
                section_id=semantic.section_id,
            )

        if semantic_kind == "hero_title" and placement_cue_ids != semantic_cue_ids:
            # Hero 的「說什麼」是 Director 決定、「什麼時候說」原本是 DP 決定，而 DP 只
            # 被要求落在 Director 證據的**子集**內。Director 的證據跨度可以橫跨一分多鐘，
            # DP 挑最前面那幾句就合法——於是卡片可以在講者說出那個主張之前就先講完。
            #
            # 2026-09-09 蘇予昕 punch-L04：Hero「原來這一切的源頭是我爸」落在 3:50.29
            # （「他就會突然幫我連結到／喔我爸就是這樣」），但講者說出「因此他看到原來
            # 源頭」是 5:13.96——早了 84 秒把結論講完。修修 review 時直接刪掉。
            #
            # 章節卡本來就是這樣鎖的（見上面 chapter 分支）：落點必須逐字回應它的語意
            # 證據。Hero 比照辦理——主張與落點是同一個事實，錯了只會錯在一個地方。
            raise ValueError("hero placement cue IDs must echo its semantic proof")

        placement = self.derive_anchor(placement_cue_ids)
        if not set(placement.master_cue_ids).issubset(semantic.master_cue_ids):
            raise ValueError("visual placement must be a subset of Director semantic evidence")
        if placement.section_id != semantic.section_id:
            raise ValueError("visual placement must remain in the Director canonical section")
        t1 = placement.t1
        if min_show_sec is not None and t1 - placement.t0 < min_show_sec:
            # 字卡要停留到讀得完。cue 證據完全不動——延長的只是卡片在畫面上多待
            # 一會兒，跨過下一句的開頭，這在剪輯上是正常的。
            #
            # 2026-09-08 蘇予昕 punch-L04：「花了快一百萬」六個字只給 1.07 秒，含
            # 進退場動畫根本讀不完。秒數從來不是設計出來的，是 DP 挑的
            # placement_cue_ids 決定的，而整條 pipeline 只有上限沒有下限。
            #
            # 這裡選擇「自動延長」而不是「擋下來重來」：DP 未必有更多 cue 可挑，
            # 擋下來會製造無解狀態——跟素材庫不夠時逼 DP 重試是同一種錯。
            t1 = min(placement.t0 + min_show_sec, self.duration_sec)
        return _mint_visual_placement(
            placement_cue_ids=placement.master_cue_ids,
            t0=placement.t0,
            t1=t1,
            section_id=placement.section_id,
        )


class EditorialCutContextResolver(Protocol):
    """Resolve only the exact approved Master/tight-cut context."""

    def resolve(
        self,
        *,
        episode_id: str,
        cut_id: str,
        editorial_master_id: str,
        tight_cut_id: str,
    ) -> EditorialCutContext | None: ...


class InMemoryEditorialCutContextResolver:
    """Deterministic fixture adapter for the Editorial Cut Context seam."""

    def __init__(self, contexts: Iterable[EditorialCutContext]) -> None:
        self._contexts = {
            (
                context.episode_id,
                context.cut_id,
                context.editorial_master_id,
                context.tight_cut_id,
            ): context
            for context in contexts
        }

    def resolve(
        self,
        *,
        episode_id: str,
        cut_id: str,
        editorial_master_id: str,
        tight_cut_id: str,
    ) -> EditorialCutContext | None:
        return self._contexts.get((episode_id, cut_id, editorial_master_id, tight_cut_id))
