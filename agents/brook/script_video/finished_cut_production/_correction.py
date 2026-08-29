"""Pre-release inspection and exact-event correction contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from ._policy import PolicyDiagnostic
from ._records import (
    AcceptedStage,
    ComponentLane,
    EventRecord,
    RequestScope,
    StageName,
    StageRequest,
    Status,
)

BuildState = Literal["not_started", "pending", "ready", "failed"]


@dataclass(frozen=True, slots=True)
class RunEventInspection:
    """Public-safe projection of one current semantic event."""

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
    placement_cue_ids: tuple[str, ...] | None
    placement_t0: float | None
    placement_t1: float | None
    placement_section_id: str | None


@dataclass(frozen=True, slots=True)
class RunStageInspection:
    """One current AcceptedStage without its worker packet or inspection media."""

    acceptance_id: str
    stage: StageName
    attempt: int
    scope: RequestScope
    event_id: str | None
    parent_acceptance_id: str | None
    events: tuple[RunEventInspection, ...]


@dataclass(frozen=True, slots=True)
class RunPolicyDiagnostic:
    """Typed, path-free policy evidence exposed to an operator."""

    code: str
    message: str
    component_ids: tuple[str, ...] = ()
    section_ids: tuple[str, ...] = ()
    asset_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunInspection:
    """Immutable public checkpoint for one exact production command."""

    run_id: str
    command_id: str
    episode_id: str
    cut_id: str
    format: Literal["long", "short"]
    status: Status
    outstanding_stage: StageName | None
    outstanding_scope: RequestScope | None
    outstanding_event_id: str | None
    current_stages: tuple[RunStageInspection, ...]
    superseded_acceptance_ids: tuple[str, ...]
    build_state: BuildState
    policy_diagnostics: tuple[RunPolicyDiagnostic, ...] = ()


class _PreReleaseCorrectionError(ValueError):
    """A requested correction does not name exact current run authority."""


@dataclass(frozen=True, slots=True)
class _PreReleaseCorrection:
    """Private same-run cascade state persisted until affected stages are replaced."""

    event_id: str
    feedback: str
    remaining_base_acceptance_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _CorrectionSelection:
    """Core-derived current-chain split; callers never construct this record."""

    base: AcceptedStage
    current_prefix: tuple[AcceptedStage, ...]
    correction: _PreReleaseCorrection


def _select_correction(
    current_stages: tuple[AcceptedStage, ...],
    *,
    stage: StageName,
    event_id: str,
    feedback: str,
) -> _CorrectionSelection:
    normalized_feedback = feedback.strip()
    if not normalized_feedback:
        raise _PreReleaseCorrectionError("pre-release correction feedback is required")
    matching = tuple(
        (index, accepted)
        for index, accepted in enumerate(current_stages)
        if accepted.stage == stage
    )
    if len(matching) != 1:
        raise _PreReleaseCorrectionError("correction stage is not exact current authority")
    index, base = matching[0]
    if event_id not in {event.event_id for event in base.events}:
        raise _PreReleaseCorrectionError("correction event is not in the current stage")
    expected_order = ("director", "dp", "visual_review")
    observed_order = tuple(accepted.stage for accepted in current_stages)
    if observed_order != expected_order[: len(observed_order)]:
        raise _PreReleaseCorrectionError("current acceptance chain is invalid")
    return _CorrectionSelection(
        base=base,
        current_prefix=current_stages[:index],
        correction=_PreReleaseCorrection(
            event_id=event_id,
            feedback=normalized_feedback,
            remaining_base_acceptance_ids=tuple(
                accepted.acceptance_id for accepted in current_stages[index + 1 :]
            ),
        ),
    )


def _merge_exact_event(
    base: tuple[EventRecord, ...],
    replacement: tuple[EventRecord, ...],
    *,
    event_id: str,
) -> tuple[EventRecord, ...]:
    if len(replacement) != 1 or replacement[0].event_id != event_id:
        raise _PreReleaseCorrectionError("event retry must return exactly the requested event")
    if event_id not in {event.event_id for event in base}:
        raise _PreReleaseCorrectionError("event retry base no longer contains the requested event")
    return tuple(replacement[0] if event.event_id == event_id else event for event in base)


def _project_run_inspection(
    *,
    run_id: str,
    command_id: str,
    episode_id: str,
    cut_id: str,
    format: Literal["long", "short"],
    status: Status,
    outstanding_request: StageRequest | None,
    current_stages: tuple[AcceptedStage, ...],
    stage_history: tuple[AcceptedStage, ...],
    build_state: BuildState,
    policy_diagnostics: tuple[PolicyDiagnostic, ...],
) -> RunInspection:
    current_ids = {stage.acceptance_id for stage in current_stages}
    return RunInspection(
        run_id=run_id,
        command_id=command_id,
        episode_id=episode_id,
        cut_id=cut_id,
        format=format,
        status=status,
        outstanding_stage=(outstanding_request.stage if outstanding_request is not None else None),
        outstanding_scope=(outstanding_request.scope if outstanding_request is not None else None),
        outstanding_event_id=(
            outstanding_request.event_id if outstanding_request is not None else None
        ),
        current_stages=tuple(_project_stage(stage) for stage in current_stages),
        superseded_acceptance_ids=tuple(
            stage.acceptance_id for stage in stage_history if stage.acceptance_id not in current_ids
        ),
        build_state=build_state,
        policy_diagnostics=tuple(
            RunPolicyDiagnostic(
                code=diagnostic.code,
                message=diagnostic.message,
                component_ids=diagnostic.component_ids,
                section_ids=diagnostic.section_ids,
                asset_refs=diagnostic.asset_refs,
            )
            for diagnostic in policy_diagnostics
        ),
    )


def _project_stage(stage: AcceptedStage) -> RunStageInspection:
    final_refs = {
        component.event_id: component.final_asset_ref
        for component in getattr(stage, "built_components", ())
    }
    return RunStageInspection(
        acceptance_id=stage.acceptance_id,
        stage=stage.stage,
        attempt=stage.attempt,
        scope=stage.scope,
        event_id=stage.event_id,
        parent_acceptance_id=stage.parent_acceptance_id,
        events=tuple(
            _project_event(
                replace(event, asset_ref=final_refs.get(event.event_id, event.asset_ref))
            )
            for event in stage.events
        ),
    )


def _project_event(event: EventRecord) -> RunEventInspection:
    placement = event.visual_placement
    return RunEventInspection(
        event_id=event.event_id,
        master_cue_ids=event.master_cue_ids,
        text=event.text,
        text_hash=event.text_hash,
        t0=event.t0,
        t1=event.t1,
        section_id=event.section_id,
        intent=event.intent,
        display=event.display,
        semantic_kind=event.semantic_kind,
        implementation_kind=event.implementation_kind,
        lane=event.lane,
        asset_ref=event.asset_ref,
        visual_status=event.visual_status,
        intentional_aroll=event.intentional_aroll,
        placement_cue_ids=(placement.placement_cue_ids if placement is not None else None),
        placement_t0=(placement.t0 if placement is not None else None),
        placement_t1=(placement.t1 if placement is not None else None),
        placement_section_id=(placement.section_id if placement is not None else None),
    )
