"""Immutable public views emitted by Finished Cut Production."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ._assets import WorkerCatalogItem
from ._context import CueAnchor, EditorialCutContext, VisualPlacement
from ._derived_assets import BuiltComponentAsset, DerivedAssetBuildRequest
from ._policy import PolicyDiagnostic
from ._projection import ComponentLane, _event_has_active_projection, _is_active_projection

if TYPE_CHECKING:
    from ._correction import _PreReleaseCorrection

Status = Literal["pending", "needs_review", "review_ready", "failed"]
StageName = Literal["director", "dp", "visual_review"]
RequestScope = Literal["full_stage", "event_retry"]
InspectionState = Literal["ready", "missing", "invalid"]
InspectionErrorCode = Literal["current_release_missing", "current_release_invalid"]
STAGE_RESPONSE_SCHEMA = "nakama.finished-cut-stage-response.v1"
_STAGE_AUTHORITY = object()
_PLAN_AUTHORITY = object()
_RELEASE_AUTHORITY = object()
_PROJECTED_COMPONENT_AUTHORITY = object()
ProbeValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class ComponentProposal:
    component_id: str
    event_id: str
    semantic_kind: str
    implementation_kind: str
    lane: ComponentLane
    display: str
    t0: float
    t1: float


@dataclass(frozen=True, slots=True)
class DirectorEventProposal:
    """Worker-owned semantic intent; authoritative anchors are derived by core."""

    event_id: str
    master_cue_ids: tuple[str, ...]
    intent: str
    display: str
    semantic_kind: str
    intentional_aroll: bool = False


@dataclass(frozen=True, slots=True)
class DPEventProposal:
    """DP-owned implementation choice bound to one Director event."""

    event_id: str
    implementation_kind: str
    lane: ComponentLane | None
    asset_ref: str | None = None
    placement_cue_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VisualEventProposal:
    """Visual reviewer decision; it cannot restate DP implementation fields."""

    event_id: str
    status: Literal["approved", "failed"]


@dataclass(frozen=True, slots=True)
class EventPlacementCandidates:
    """Exact current cue rows a DP may select within one Director event."""

    event_id: str
    cues: tuple[CueAnchor, ...]


@dataclass(frozen=True, slots=True, init=False)
class ProjectedComponent:
    component_id: str
    event_id: str
    semantic_kind: str
    implementation_kind: str
    lane: ComponentLane
    display: str
    t0: float
    t1: float
    asset_ref: str | None

    def __init__(self, *, _authority: object | None = None, **values: object) -> None:
        if _authority is not _PROJECTED_COMPONENT_AUTHORITY:
            raise TypeError("ProjectedComponent can be minted only by Finished Cut Production")
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, values[name])


def _mint_projected_component(
    *,
    component_id: str,
    event_id: str,
    semantic_kind: str,
    implementation_kind: str,
    lane: ComponentLane,
    display: str,
    t0: float,
    t1: float,
    asset_ref: str | None,
) -> ProjectedComponent:
    if not _is_active_projection(semantic_kind, implementation_kind, lane):
        raise ValueError("retired or unsupported component projection")
    return ProjectedComponent(
        _authority=_PROJECTED_COMPONENT_AUTHORITY,
        component_id=component_id,
        event_id=event_id,
        semantic_kind=semantic_kind,
        implementation_kind=implementation_kind,
        lane=lane,
        display=display,
        t0=t0,
        t1=t1,
        asset_ref=asset_ref,
    )


def _rehydrate_release_projected_component(
    *,
    component_id: str,
    event_id: str,
    semantic_kind: str,
    implementation_kind: str,
    lane: ComponentLane,
    display: str,
    t0: float,
    t1: float,
    asset_ref: str | None,
) -> ProjectedComponent:
    """Read an already-sealed receipt without granting current production authority."""

    return ProjectedComponent(
        _authority=_PROJECTED_COMPONENT_AUTHORITY,
        component_id=component_id,
        event_id=event_id,
        semantic_kind=semantic_kind,
        implementation_kind=implementation_kind,
        lane=lane,
        display=display,
        t0=t0,
        t1=t1,
        asset_ref=asset_ref,
    )


@dataclass(frozen=True, slots=True)
class StageRequest:
    run_id: str
    request_id: str
    command_id: str
    episode_id: str
    cut_id: str
    format: Literal["long", "short"]
    stage: StageName
    attempt: int
    scope: RequestScope
    event_id: str | None
    parent_acceptance_id: str | None
    base_acceptance_id: str | None = None
    events: tuple[EventRecord, ...] = ()
    placement_candidates: tuple[EventPlacementCandidates, ...] = ()
    feedback: str | None = None
    worker_asset_refs: tuple[str, ...] = ()
    worker_catalog_items: tuple[WorkerCatalogItem, ...] = ()
    components: tuple[ComponentProposal, ...] = ()
    built_components: tuple[BuiltComponentAsset, ...] = ()
    editorial_context: EditorialCutContext | None = None
    schema: str = STAGE_RESPONSE_SCHEMA


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: str
    master_cue_ids: tuple[str, ...]
    text_hash: str
    intent: str
    asset_ref: str | None = None
    visual_status: str | None = None
    text: str = ""
    t0: float = 0.0
    t1: float = 0.0
    section_id: str | None = None
    display: str = ""
    semantic_kind: str = ""
    intentional_aroll: bool = False
    implementation_kind: str = ""
    lane: ComponentLane | None = None
    visual_placement: VisualPlacement | None = None


@dataclass(frozen=True, slots=True)
class StageProposal:
    run_id: str
    request_id: str
    episode_id: str
    cut_id: str
    format: Literal["long", "short"]
    stage: StageName
    attempt: int
    scope: RequestScope
    event_id: str | None
    parent_acceptance_id: str | None
    events: tuple[
        DirectorEventProposal | DPEventProposal | VisualEventProposal | EventRecord,
        ...,
    ]
    components: tuple[ComponentProposal, ...] = ()
    schema: str = STAGE_RESPONSE_SCHEMA


@dataclass(frozen=True, slots=True, init=False)
class AcceptedStage:
    acceptance_id: str
    run_id: str
    request_id: str
    stage: StageName
    attempt: int
    scope: RequestScope
    event_id: str | None
    parent_acceptance_id: str | None
    events: tuple[EventRecord, ...]
    components: tuple[ComponentProposal, ...]
    built_components: tuple[BuiltComponentAsset, ...]

    def __init__(self, *, _authority: object | None = None, **values: object) -> None:
        if _authority is not _STAGE_AUTHORITY:
            raise TypeError("AcceptedStage can be minted only by the aggregate")
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, values[name])


def _mint_accepted_stage(
    *,
    acceptance_id: str,
    run_id: str,
    request_id: str,
    stage: StageName,
    attempt: int,
    scope: RequestScope,
    event_id: str | None,
    parent_acceptance_id: str | None,
    events: tuple[EventRecord, ...],
    components: tuple[ComponentProposal, ...] = (),
    built_components: tuple[BuiltComponentAsset, ...] = (),
) -> AcceptedStage:
    return AcceptedStage(
        _authority=_STAGE_AUTHORITY,
        acceptance_id=acceptance_id,
        run_id=run_id,
        request_id=request_id,
        stage=stage,
        attempt=attempt,
        scope=scope,
        event_id=event_id,
        parent_acceptance_id=parent_acceptance_id,
        events=events,
        components=components,
        built_components=built_components,
    )


@dataclass(frozen=True, slots=True, init=False)
class MaterializationPlan:
    plan_id: str
    run_id: str
    command_id: str
    episode_id: str
    cut_id: str
    format: Literal["long", "short"]
    director_acceptance_id: str
    dp_acceptance_id: str
    visual_acceptance_id: str
    events: tuple[EventRecord, ...]
    components: tuple[ProjectedComponent, ...]

    def __init__(
        self,
        *,
        _authority: object,
        plan_id: str,
        run_id: str,
        command_id: str,
        episode_id: str,
        cut_id: str,
        format: Literal["long", "short"],
        director_acceptance_id: str,
        dp_acceptance_id: str,
        visual_acceptance_id: str,
        events: tuple[EventRecord, ...],
        components: tuple[ProjectedComponent, ...] = (),
    ) -> None:
        if _authority is not _PLAN_AUTHORITY:
            raise TypeError("MaterializationPlan can be minted only by Finished Cut Production")
        for name, value in (
            ("plan_id", plan_id),
            ("run_id", run_id),
            ("command_id", command_id),
            ("episode_id", episode_id),
            ("cut_id", cut_id),
            ("format", format),
            ("director_acceptance_id", director_acceptance_id),
            ("dp_acceptance_id", dp_acceptance_id),
            ("visual_acceptance_id", visual_acceptance_id),
            ("events", events),
            ("components", components),
        ):
            object.__setattr__(self, name, value)


def _mint_materialization_plan(
    *,
    plan_id: str,
    run_id: str,
    command_id: str,
    episode_id: str,
    cut_id: str,
    format: Literal["long", "short"],
    director_acceptance_id: str,
    dp_acceptance_id: str,
    visual_acceptance_id: str,
    events: tuple[EventRecord, ...],
    components: tuple[ProjectedComponent, ...] = (),
) -> MaterializationPlan:
    if any(
        event.semantic_kind
        and not _event_has_active_projection(
            semantic_kind=event.semantic_kind,
            implementation_kind=event.implementation_kind,
            lane=event.lane,
            intentional_aroll=event.intentional_aroll,
        )
        for event in events
    ) or any(
        not _is_active_projection(
            component.semantic_kind,
            component.implementation_kind,
            component.lane,
        )
        for component in components
    ):
        raise ValueError("MaterializationPlan contains a retired or unsupported projection")
    return MaterializationPlan(
        _authority=_PLAN_AUTHORITY,
        plan_id=plan_id,
        run_id=run_id,
        command_id=command_id,
        episode_id=episode_id,
        cut_id=cut_id,
        format=format,
        director_acceptance_id=director_acceptance_id,
        dp_acceptance_id=dp_acceptance_id,
        visual_acceptance_id=visual_acceptance_id,
        events=events,
        components=components,
    )


@dataclass(frozen=True, slots=True)
class ReleaseArtifact:
    path: str
    bytes: int
    sha256: str
    duration_sec: float | None = None
    probe: tuple[tuple[str, ProbeValue], ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactView:
    reference: str
    bytes: int
    sha256: str
    duration_sec: float | None = None
    probe: tuple[tuple[str, ProbeValue], ...] = ()


@dataclass(frozen=True, slots=True)
class EventView:
    event_id: str
    master_cue_ids: tuple[str, ...]
    text: str
    text_hash: str
    t0: float
    t1: float
    section_id: str | None
    intent: str
    display: str
    semantic_kind: str
    implementation_kind: str
    lane: ComponentLane | None
    asset_ref: str | None
    visual_status: str | None
    intentional_aroll: bool


@dataclass(frozen=True, slots=True)
class ComponentView:
    component_id: str
    event_id: str
    semantic_kind: str
    implementation_kind: str
    lane: ComponentLane
    display: str
    t0: float
    t1: float
    asset_ref: str | None


@dataclass(frozen=True, slots=True)
class CutView:
    release_id: str
    cut_id: str
    format: Literal["long", "short"]
    preview: ArtifactView
    subtitle: ArtifactView
    events: tuple[EventView, ...]
    components: tuple[ComponentView, ...]


@dataclass(frozen=True, slots=True)
class FinishedCutInspection:
    """Stable public result for exact-current inspection."""

    episode_id: str
    state: InspectionState
    cuts: tuple[CutView, ...] = ()
    error_code: InspectionErrorCode | None = None


@dataclass(frozen=True, slots=True, init=False)
class StagedReleaseCandidate:
    candidate_id: str
    episode_id: str
    cut_id: str
    format: Literal["long", "short"]
    command_id: str
    run_id: str
    editorial_master_id: str
    winner_id: str
    tight_cut_id: str
    director_acceptance_id: str
    dp_acceptance_id: str
    visual_acceptance_id: str
    materialization_plan: MaterializationPlan
    preview: ReleaseArtifact
    subtitle: ReleaseArtifact
    preview_ready_transaction_id: str
    components: tuple[ProjectedComponent, ...]

    def __init__(self, *, _authority: object, **values: object) -> None:
        if _authority is not _RELEASE_AUTHORITY:
            raise TypeError("StagedReleaseCandidate can be minted only inside the module")
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, values[name])


def _mint_staged_release_candidate(
    *,
    candidate_id: str,
    episode_id: str,
    cut_id: str,
    format: Literal["long", "short"],
    command_id: str,
    run_id: str,
    editorial_master_id: str,
    winner_id: str,
    tight_cut_id: str,
    director_acceptance_id: str,
    dp_acceptance_id: str,
    visual_acceptance_id: str,
    materialization_plan: MaterializationPlan,
    preview: ReleaseArtifact,
    subtitle: ReleaseArtifact,
    preview_ready_transaction_id: str,
    components: tuple[ProjectedComponent, ...] | None = None,
) -> StagedReleaseCandidate:
    return StagedReleaseCandidate(
        _authority=_RELEASE_AUTHORITY,
        candidate_id=candidate_id,
        episode_id=episode_id,
        cut_id=cut_id,
        format=format,
        command_id=command_id,
        run_id=run_id,
        editorial_master_id=editorial_master_id,
        winner_id=winner_id,
        tight_cut_id=tight_cut_id,
        director_acceptance_id=director_acceptance_id,
        dp_acceptance_id=dp_acceptance_id,
        visual_acceptance_id=visual_acceptance_id,
        materialization_plan=materialization_plan,
        preview=preview,
        subtitle=subtitle,
        preview_ready_transaction_id=preview_ready_transaction_id,
        components=materialization_plan.components if components is None else components,
    )


@dataclass(frozen=True, slots=True, init=False)
class FinishedCutRelease:
    release_id: str
    episode_id: str
    cut_id: str
    format: Literal["long", "short"]
    command_id: str
    run_id: str
    editorial_master_id: str
    winner_id: str
    tight_cut_id: str
    director_acceptance_id: str
    dp_acceptance_id: str
    visual_acceptance_id: str
    materialization_plan_id: str
    events: tuple[EventRecord, ...]
    preview: ReleaseArtifact
    subtitle: ReleaseArtifact
    transaction_receipt_id: str
    rollback_ref: str
    components: tuple[ProjectedComponent, ...]

    def __init__(self, *, _authority: object, **values: object) -> None:
        if _authority is not _RELEASE_AUTHORITY:
            raise TypeError("FinishedCutRelease can be sealed only inside the module")
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, values[name])


def _seal_finished_cut_release(
    *,
    release_id: str,
    episode_id: str,
    cut_id: str,
    format: Literal["long", "short"],
    command_id: str,
    run_id: str,
    editorial_master_id: str,
    winner_id: str,
    tight_cut_id: str,
    director_acceptance_id: str,
    dp_acceptance_id: str,
    visual_acceptance_id: str,
    materialization_plan_id: str,
    events: tuple[EventRecord, ...],
    preview: ReleaseArtifact,
    subtitle: ReleaseArtifact,
    transaction_receipt_id: str,
    rollback_ref: str,
    components: tuple[ProjectedComponent, ...] = (),
) -> FinishedCutRelease:
    if any(
        event.semantic_kind
        and not _event_has_active_projection(
            semantic_kind=event.semantic_kind,
            implementation_kind=event.implementation_kind,
            lane=event.lane,
            intentional_aroll=event.intentional_aroll,
        )
        for event in events
    ) or any(
        not _is_active_projection(
            component.semantic_kind,
            component.implementation_kind,
            component.lane,
        )
        for component in components
    ):
        raise ValueError("FinishedCutRelease contains a retired or unsupported projection")
    return FinishedCutRelease(
        _authority=_RELEASE_AUTHORITY,
        release_id=release_id,
        episode_id=episode_id,
        cut_id=cut_id,
        format=format,
        command_id=command_id,
        run_id=run_id,
        editorial_master_id=editorial_master_id,
        winner_id=winner_id,
        tight_cut_id=tight_cut_id,
        director_acceptance_id=director_acceptance_id,
        dp_acceptance_id=dp_acceptance_id,
        visual_acceptance_id=visual_acceptance_id,
        materialization_plan_id=materialization_plan_id,
        events=events,
        preview=preview,
        subtitle=subtitle,
        transaction_receipt_id=transaction_receipt_id,
        rollback_ref=rollback_ref,
        components=components,
    )


def _rehydrate_finished_cut_release(**values: object) -> FinishedCutRelease:
    """Read an immutable historical receipt without exposing a production mint seam."""

    return FinishedCutRelease(_authority=_RELEASE_AUTHORITY, **values)


@dataclass(frozen=True, slots=True)
class _ProductionRun:
    run_id: str
    command_id: str
    editorial_context: EditorialCutContext
    status: Status
    outstanding_request: StageRequest | None
    accepted_stages: tuple[AcceptedStage, ...] = ()
    accepted_stage_history: tuple[AcceptedStage, ...] = ()
    derived_asset_request: DerivedAssetBuildRequest | None = None
    materialization_plan: MaterializationPlan | None = None
    correction: _PreReleaseCorrection | None = None
    policy_diagnostics: tuple[PolicyDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class RunView:
    """Read-only progress projection; private stage authority is intentionally absent."""

    run_id: str
    command_id: str
    status: Status
    current_stage: StageName | None
    scope: RequestScope | None
    event_id: str | None
