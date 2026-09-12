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

#: 落點要差到這個程度才算「移動過」。frame 級的量化誤差（1/30 秒 ≈ 0.033）不算
#: 改動——把它算進去的話，每一輪都會整份標成 moved，diff 就沒有訊號了。
EVENT_SHIFT_EPSILON_SEC = 0.05

EventChange = Literal["added", "removed", "moved", "retitled", "recast"]


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
class RunEventDiff:
    """一個 event 在這一輪與上一輪之間的差異。

    一個 event 可以同時被搬過又被改寫，所以 `changes` 是集合而不是單一分類——
    強迫二選一只會讓其中一半的事實消失。
    """

    event_id: str
    changes: tuple[EventChange, ...]
    #: 第幾秒（`removed` 時是上一輪的落點——那是它最後出現的地方）。
    t0: float
    #: 哪種卡。
    implementation_kind: str
    #: 原文 → 新文。`added` 沒有原文，`removed` 沒有新文。
    display: str | None
    previous_display: str | None = None
    previous_t0: float | None = None
    #: `moved` 時的位移。整份都是同一個常數 → 那不是剪輯判斷，是機器整份平移。
    shift_sec: float | None = None


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
    #: 這一輪 vs 上一輪，按落點排序。第一輪全部是 `added`。
    event_diff: tuple[RunEventDiff, ...] = ()
    #: 拿來比的上一輪是哪一次驗收。第一輪為 None。
    event_diff_previous_acceptance_id: str | None = None
    #: 每一個移動過的 event 位移都相同時的那個常數，否則 None。
    #:
    #: 2026-09-09 punch-L04：worker 回了一份把 34 個 event 整份平移同一個常數的
    #: 複製品。逐條看是 34 個「可能合理」的判斷；看到這一格有值，就知道它是機器
    #: 產物而不是剪輯判斷。
    uniform_shift_sec: float | None = None


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
    this_round = _latest_round(current_stages)
    last_round = (
        None
        if this_round is None
        else _latest_round(
            tuple(
                stage
                for stage in stage_history
                if stage.acceptance_id not in current_ids
                and stage.stage == this_round.stage
            )
        )
    )
    diff = _event_diff(
        () if this_round is None else this_round.events,
        () if last_round is None else last_round.events,
    )
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
        event_diff=diff,
        event_diff_previous_acceptance_id=(
            None if last_round is None else last_round.acceptance_id
        ),
        uniform_shift_sec=_uniform_shift(diff),
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


#: 語意流程的先後。`_latest_round` 用它判斷「走得最遠的那一關」——那一關的 event
#: 集合才是修修現在在 timeline 上看到的東西。
_STAGE_PROGRESS: tuple[StageName, ...] = ("director", "dp", "visual_review")


def _latest_round(stages: tuple[AcceptedStage, ...]) -> AcceptedStage | None:
    """走得最遠、而且在那一關裡最後被驗收的那一次。

    同一關可能被重試過好幾次（event retry），所以先取最遠的那一關，再在那一關裡
    取 tuple 順序上最後的一次——`accepted_stage_history` 是 append 上去的，順序就是
    時間順序。
    """

    if not stages:
        return None
    furthest = max(stages, key=lambda stage: _STAGE_PROGRESS.index(stage.stage)).stage
    return tuple(stage for stage in stages if stage.stage == furthest)[-1]


def _event_diff(
    current: tuple[EventRecord, ...],
    previous: tuple[EventRecord, ...],
) -> tuple[RunEventDiff, ...]:
    """這一輪 vs 上一輪，按落點排序。上一輪不存在時全部標 `added`。"""

    before = {event.event_id: event for event in previous}
    rows: list[RunEventDiff] = []
    for event in current:
        prior = before.pop(event.event_id, None)
        if prior is None:
            rows.append(
                RunEventDiff(
                    event_id=event.event_id,
                    changes=("added",),
                    t0=event.t0,
                    implementation_kind=event.implementation_kind,
                    display=event.display,
                )
            )
            continue
        changes: list[EventChange] = []
        shift = event.t0 - prior.t0
        if abs(shift) > EVENT_SHIFT_EPSILON_SEC:
            changes.append("moved")
        if event.display != prior.display:
            changes.append("retitled")
        if event.implementation_kind != prior.implementation_kind:
            changes.append("recast")
        if not changes:
            continue
        rows.append(
            RunEventDiff(
                event_id=event.event_id,
                changes=tuple(changes),
                t0=event.t0,
                implementation_kind=event.implementation_kind,
                display=event.display,
                previous_display=prior.display,
                previous_t0=prior.t0,
                shift_sec=shift if "moved" in changes else None,
            )
        )
    for event in before.values():
        # 上一輪有、這一輪沒有。落點記的是它最後出現的地方——那才是修修記得的位置。
        rows.append(
            RunEventDiff(
                event_id=event.event_id,
                changes=("removed",),
                t0=event.t0,
                implementation_kind=event.implementation_kind,
                display=None,
                previous_display=event.display,
                previous_t0=event.t0,
            )
        )
    return tuple(sorted(rows, key=lambda row: (row.t0, row.event_id)))


def _uniform_shift(diff: tuple[RunEventDiff, ...]) -> float | None:
    """整份都被平移同一個常數時，回那個常數。

    一個 event 被搬 3 秒是剪輯判斷；34 個 event 全被搬同樣的 3 秒不是——那是機器
    整份複製過來再平移（2026-09-09 punch-L04）。逐條看的時候每一條都「可能合理」，
    所以這件事要由機器講出來。
    """

    shifts = tuple(row.shift_sec for row in diff if row.shift_sec is not None)
    if len(shifts) < 2 or len(diff) != len(shifts):
        return None
    first = shifts[0]
    if any(abs(shift - first) > EVENT_SHIFT_EPSILON_SEC for shift in shifts):
        return None
    return first
