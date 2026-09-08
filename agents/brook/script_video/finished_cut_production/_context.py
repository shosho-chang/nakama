"""Authoritative Editorial Cut Context for fresh Finished Cut work."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

_VISUAL_PLACEMENT_AUTHORITY = object()
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


@dataclass(frozen=True, slots=True, init=False)
class VisualPlacement:
    """Core-minted temporal range where a DP-selected visual is actually shown."""

    placement_cue_ids: tuple[str, ...]
    t0: float
    t1: float
    section_id: str | None

    def __init__(self, *, _authority: object | None = None, **values: object) -> None:
        if _authority is not _VISUAL_PLACEMENT_AUTHORITY:
            raise TypeError("VisualPlacement can be minted only from Editorial Cut Context")
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, values[name])


def _mint_visual_placement(
    *,
    placement_cue_ids: tuple[str, ...],
    t0: float,
    t1: float,
    section_id: str | None,
) -> VisualPlacement:
    """Single module-private constructor for derived or reloaded placement authority."""

    if (
        not placement_cue_ids
        or len(placement_cue_ids) != len(set(placement_cue_ids))
        or not math.isfinite(t0)
        or not math.isfinite(t1)
        or t0 < 0
        or t0 >= t1
    ):
        raise ValueError("Visual Placement fields are invalid")
    return VisualPlacement(
        _authority=_VISUAL_PLACEMENT_AUTHORITY,
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
