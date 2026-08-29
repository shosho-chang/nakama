"""Format-neutral projection of typed plans into pre-rendered Timeline placements."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ._projection import _ACTIVE_PROJECTION_COMBINATIONS
from ._records import ComponentLane, MaterializationPlan

_ALLOWED_PROJECTIONS = _ACTIVE_PROJECTION_COMBINATIONS


class TimelineApplyError(ValueError):
    """A typed plan cannot be projected into exact derived Timeline lanes."""


@dataclass(frozen=True, slots=True)
class PreRenderedAsset:
    reference: str
    path: Path

    def __post_init__(self) -> None:
        if not self.reference.strip():
            raise TimelineApplyError("pre-rendered asset reference is empty")
        object.__setattr__(self, "path", Path(self.path))


class PreRenderedAssetCatalog:
    """Immutable exact-reference catalog used only for mechanical application."""

    def __init__(self, assets: Iterable[PreRenderedAsset]) -> None:
        items = tuple(assets)
        if len({asset.reference for asset in items}) != len(items):
            raise TimelineApplyError("pre-rendered asset reference is ambiguous")
        self._by_reference = {asset.reference: asset for asset in items}

    def resolve(self, reference: str) -> Path:
        try:
            asset = self._by_reference[reference]
        except KeyError as exc:
            raise TimelineApplyError("typed component asset is not in the exact catalog") from exc
        try:
            path = asset.path.resolve(strict=True)
        except OSError as exc:
            raise TimelineApplyError("pre-rendered asset is missing") from exc
        if not path.is_file():
            raise TimelineApplyError("pre-rendered asset is not a regular file")
        return path


@dataclass(frozen=True, slots=True)
class TimelinePlacement:
    component_id: str
    event_id: str
    semantic_kind: str
    implementation_kind: str
    lane: ComponentLane
    display: str
    t0: float
    t1: float
    source_path: Path


@dataclass(frozen=True, slots=True)
class TimelineApplication:
    plan_id: str
    episode_id: str
    cut_id: str
    placements: tuple[TimelinePlacement, ...]


def project_timeline_application(
    plan: MaterializationPlan,
    assets: PreRenderedAssetCatalog,
) -> TimelineApplication:
    """Resolve every typed component before exposing any Timeline mutation input."""

    event_ids = {event.event_id for event in plan.events}
    component_ids = [component.component_id for component in plan.components]
    if len(component_ids) != len(set(component_ids)):
        raise TimelineApplyError("typed plan contains duplicate component identities")
    placements: list[TimelinePlacement] = []
    for component in plan.components:
        projection = (
            component.semantic_kind,
            component.implementation_kind,
            component.lane,
        )
        if projection not in _ALLOWED_PROJECTIONS:
            raise TimelineApplyError("typed component classification is not mechanically valid")
        if component.event_id not in event_ids:
            raise TimelineApplyError("typed component event is not in the materialization plan")
        if not component.asset_ref:
            raise TimelineApplyError("typed component has no final materialized asset")
        if (
            not math.isfinite(component.t0)
            or not math.isfinite(component.t1)
            or component.t0 < 0
            or component.t0 >= component.t1
        ):
            raise TimelineApplyError("typed component time range is invalid")
        placements.append(
            TimelinePlacement(
                component_id=component.component_id,
                event_id=component.event_id,
                semantic_kind=component.semantic_kind,
                implementation_kind=component.implementation_kind,
                lane=component.lane,
                display=component.display,
                t0=component.t0,
                t1=component.t1,
                source_path=assets.resolve(component.asset_ref),
            )
        )
    return TimelineApplication(
        plan_id=plan.plan_id,
        episode_id=plan.episode_id,
        cut_id=plan.cut_id,
        placements=tuple(placements),
    )
