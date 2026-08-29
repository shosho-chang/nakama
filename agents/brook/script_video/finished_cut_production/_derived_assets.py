"""Private port for core-authorized component asset construction.

The semantic workers choose intent and neutral source media.  Finished Cut
Production owns the exact recipe and asks an injected builder to either resolve
an unchanged acquisition asset or publish a generated component into the Active
Asset Store.  The builder never mints semantic authority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol

from ._assets import WorkerCatalogItem
from ._projection import ComponentLane, _is_active_projection

BuildStatus = Literal["ready", "pending", "failed"]
BuildScope = Literal["full_stage", "event_retry"]
CutFormat = Literal["long", "short"]
_GENERATED_IMPLEMENTATIONS = frozenset(
    {
        "fullscreen_transition",
        "hero_title",
        "person_inset",
        "identity_card",
        "visual_effect",
    }
)
_NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS = frozenset({"stock_video", "photo", "non_editorial_clip"})
MAX_FULLSCREEN_TRANSITION_SHOW_SEC = 4.0
MAX_TITLE_OR_IDENTITY_SHOW_SEC = 8.0
MAX_ASSET_BACKED_BROLL_SHOW_SEC = 12.0
_PLACEMENT_DURATION_CEILINGS_SEC = {
    "fullscreen_transition": MAX_FULLSCREEN_TRANSITION_SHOW_SEC,
    "hero_title": MAX_TITLE_OR_IDENTITY_SHOW_SEC,
    "identity_card": MAX_TITLE_OR_IDENTITY_SHOW_SEC,
    "stock_video": MAX_ASSET_BACKED_BROLL_SHOW_SEC,
    "photo": MAX_ASSET_BACKED_BROLL_SHOW_SEC,
    "non_editorial_clip": MAX_ASSET_BACKED_BROLL_SHOW_SEC,
    "person_inset": MAX_ASSET_BACKED_BROLL_SHOW_SEC,
}


class DerivedAssetContractError(ValueError):
    """A build instruction/result crossed the private asset authority seam."""


@dataclass(frozen=True, slots=True)
class DerivedAssetGeometry:
    """Core-owned output geometry included in an exact recipe identity."""

    target_width: int
    target_height: int
    layout_identity: str

    def __post_init__(self) -> None:
        if self.target_width <= 0 or self.target_height <= 0:
            raise DerivedAssetContractError("derived asset geometry must be positive")
        if not self.layout_identity.strip():
            raise DerivedAssetContractError("derived asset layout identity is required")


@dataclass(frozen=True, slots=True)
class DerivedAssetInstruction:
    """One exact component recipe minted by the production aggregate."""

    component_id: str
    event_id: str
    semantic_kind: str
    implementation_kind: str
    lane: ComponentLane
    display: str
    t0: float
    t1: float
    source_asset_ref: str | None
    geometry: DerivedAssetGeometry
    recipe_identity: str | None

    @property
    def show_sec(self) -> float:
        """Duration of the core-minted visual placement, never its semantic evidence."""

        return self.t1 - self.t0

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.component_id,
                self.event_id,
                self.semantic_kind,
                self.implementation_kind,
                self.display,
            )
        ):
            raise DerivedAssetContractError("derived asset instruction fields are required")
        if not _is_active_projection(
            self.semantic_kind,
            self.implementation_kind,
            self.lane,
        ):
            raise DerivedAssetContractError("retired or unsupported derived asset projection")
        if (
            not math.isfinite(self.t0)
            or not math.isfinite(self.t1)
            or self.t0 < 0
            or self.t0 >= self.t1
        ):
            raise DerivedAssetContractError("derived asset instruction timing is invalid")
        if self.source_asset_ref is not None and not self.source_asset_ref.startswith(
            "asset-sha256:"
        ):
            raise DerivedAssetContractError("derived source must be an opaque asset reference")
        if self.implementation_kind in _GENERATED_IMPLEMENTATIONS:
            if not self.recipe_identity:
                raise DerivedAssetContractError(
                    "generated component requires a core recipe identity"
                )
        elif self.implementation_kind in _NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS:
            if self.source_asset_ref is None:
                raise DerivedAssetContractError(
                    "neutral pass-through requires a source asset reference"
                )
            if self.recipe_identity is not None:
                raise DerivedAssetContractError(
                    "neutral pass-through must not invent a semantic recipe"
                )
        else:
            raise DerivedAssetContractError("unsupported derived asset implementation")
        if self.implementation_kind == "person_inset" and self.source_asset_ref is None:
            raise DerivedAssetContractError("person inset requires a neutral photo source")


def _placement_duration_is_within_ceiling(instruction: DerivedAssetInstruction) -> bool:
    ceiling = _PLACEMENT_DURATION_CEILINGS_SEC.get(instruction.implementation_kind)
    return ceiling is None or instruction.show_sec <= ceiling


@dataclass(frozen=True, slots=True)
class DerivedAssetBuildRequest:
    """Immutable current-run packet sent to a managed DerivedAssetBuilder."""

    build_request_id: str
    run_id: str
    command_id: str
    episode_id: str
    cut_id: str
    format: CutFormat
    dp_acceptance_id: str
    scope: BuildScope
    event_id: str | None
    instructions: tuple[DerivedAssetInstruction, ...]
    worker_catalog_items: tuple[WorkerCatalogItem, ...]

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.build_request_id,
                self.run_id,
                self.command_id,
                self.episode_id,
                self.cut_id,
                self.dp_acceptance_id,
            )
        ):
            raise DerivedAssetContractError("derived build request identities are required")
        event_ids = [instruction.event_id for instruction in self.instructions]
        component_ids = [instruction.component_id for instruction in self.instructions]
        if len(event_ids) != len(set(event_ids)) or len(component_ids) != len(set(component_ids)):
            raise DerivedAssetContractError("derived build instructions must be unique")
        if self.scope == "event_retry" and (self.event_id is None or event_ids != [self.event_id]):
            raise DerivedAssetContractError("event retry build must contain its exact event")
        if self.scope == "full_stage" and self.event_id is not None:
            raise DerivedAssetContractError("full-stage build cannot carry an event identity")


@dataclass(frozen=True, slots=True)
class BuiltComponentAsset:
    """Builder output resolved to exact final and inspection packet references."""

    component_id: str
    event_id: str
    source_asset_ref: str | None
    final_asset_ref: str
    inspection_ref: str | None
    recipe_identity: str | None

    def __post_init__(self) -> None:
        if not self.component_id.strip() or not self.event_id.strip():
            raise DerivedAssetContractError("built component identities are required")
        for reference in (self.source_asset_ref, self.final_asset_ref, self.inspection_ref):
            if reference is not None and not reference.startswith("asset-sha256:"):
                raise DerivedAssetContractError("built asset must use an opaque asset reference")


@dataclass(frozen=True, slots=True)
class DerivedAssetBuildResult:
    """One idempotent response to an exact build request."""

    build_request_id: str
    dp_acceptance_id: str
    status: BuildStatus
    assets: tuple[BuiltComponentAsset, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not self.build_request_id.strip() or not self.dp_acceptance_id.strip():
            raise DerivedAssetContractError("derived build result identities are required")
        if self.status == "ready" and self.error_code is not None:
            raise DerivedAssetContractError("ready build cannot carry an error code")
        if self.status != "ready" and self.assets:
            raise DerivedAssetContractError("incomplete build cannot publish component assets")
        if self.status == "failed" and not (self.error_code and self.error_code.strip()):
            raise DerivedAssetContractError("failed build requires an error code")


class DerivedAssetBuilder(Protocol):
    """Managed Adapter seam; implementations resolve bytes only via Active Store."""

    def build(self, request: DerivedAssetBuildRequest) -> DerivedAssetBuildResult: ...
