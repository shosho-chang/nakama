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
        return _mint_visual_placement(
            placement_cue_ids=placement.master_cue_ids,
            t0=placement.t0,
            t1=placement.t1,
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
