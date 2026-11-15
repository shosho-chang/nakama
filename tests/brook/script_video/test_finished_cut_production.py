from __future__ import annotations

import hashlib

import agents.brook.script_video.finished_cut_production as finished_cut_production
from agents.brook.script_video.finished_cut_production import (
    FinishedCutProduction,
)
from agents.brook.script_video.finished_cut_production._assets import (
    AssetKind,
    AssetRecord,
    InMemoryAssetResolver,
)
from agents.brook.script_video.finished_cut_production._commands import (
    ApprovedCutCommand,
)
from agents.brook.script_video.finished_cut_production._engine import (
    InMemoryProductionSystem,
    advance,
    request_revision,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    MaterializationPlan,
    RunView,
    StageRequest,
)
from agents.brook.script_video.finished_cut_production._semantic import (
    SemanticDispatchOutcome,
)


class _RecordingSemanticAdapter:
    def __init__(self) -> None:
        self.requests: list[StageRequest] = []

    def dispatch(self, request: StageRequest) -> SemanticDispatchOutcome:
        self.requests.append(request)
        return SemanticDispatchOutcome(request.request_id, "pending")

    def outcome_for(self, _request_id: str) -> None:
        return None


def _approved_cut() -> ApprovedCutCommand:
    return ApprovedCutCommand(
        command_id="approved:episode-1:cut-1",
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        editorial_master_id="master-1",
        winner_id="winner-1",
        tight_cut_id="tight-1",
    )


def _asset(event_id: str) -> AssetRecord:
    return AssetRecord(
        digest=hashlib.sha256(event_id.encode()).hexdigest(),
        extension=".mp4",
        kind=AssetKind.STOCK,
        visual_summary=f"Neutral stock footage for {event_id}",
        width=1920,
        height=1080,
        duration_sec=12.0,
    )


def _advance_ready(
    system: InMemoryProductionSystem,
    director_events: tuple[EventRecord, ...],
) -> tuple[RunView, tuple[EventRecord, ...]]:
    director_wait = advance(_approved_cut().command_id, system=system)
    assert director_wait.outstanding_request is not None
    system.respond(director_wait.outstanding_request, events=director_events)
    dp_wait = advance(_approved_cut().command_id, system=system)
    assert dp_wait.outstanding_request is not None
    dp_events = tuple(
        EventRecord(
            event_id=event.event_id,
            master_cue_ids=event.master_cue_ids,
            text_hash=event.text_hash,
            intent=event.intent,
            asset_ref=_asset(event.event_id).reference,
        )
        for event in director_events
    )
    system.respond(dp_wait.outstanding_request, events=dp_events)
    visual_wait = advance(_approved_cut().command_id, system=system)
    assert visual_wait.outstanding_request is not None
    visual_events = tuple(
        EventRecord(
            event_id=event.event_id,
            master_cue_ids=event.master_cue_ids,
            text_hash=event.text_hash,
            intent=event.intent,
            asset_ref=event.asset_ref,
            visual_status="approved",
        )
        for event in dp_events
    )
    system.respond(visual_wait.outstanding_request, events=visual_events)
    checkpoint = advance(_approved_cut().command_id, system=system)
    assert checkpoint.status == "pending"
    assert checkpoint.materialization_plan is None
    return advance(_approved_cut().command_id, system=system), visual_events


def test_public_module_does_not_expose_private_authority_constructors() -> None:
    # Three surfaces are public: the aggregate, the composition root an inbound
    # adapter needs to build it, and read-only exact-current access.  Everything
    # below the "not hasattr" line stays private no matter how this list grows.
    assert set(finished_cut_production.__all__) == {
        "ApprovedCutRegistration",
        "RESOLVE_BINDING_SCHEMA",
        "ArtifactView",
        "CanonicalSection",
        "CommandRejectedError",
        "ComponentView",
        "CueAnchor",
        "PlanRecordReader",
        "CutSourceRange",
        "CutView",
        "EventView",
        "FinishedCutInspection",
        "FinishedCutProduction",
        "FinishedCutProductionApplication",
        "ProductionPaths",
        "ProductionResolveConfiguration",
        "build_resolve_configuration",
        "ProductionStatusView",
        "ResolveCutBinding",
        "ResolveDatabaseIdentity",
        "ResolveProjectBinding",
        "ResolveProjectLocator",
        "RunEventDiff",
        "RunEventInspection",
        "RunInspection",
        "RunPolicyDiagnostic",
        "RunStageInspection",
        "RunView",
        "StageName",
        "Status",
        "TimelineIdentity",
        "build_plan_record_reader",
        "build_production_application",
    }
    assert not hasattr(finished_cut_production, "AcceptedStage")
    assert not hasattr(finished_cut_production, "StagedReleaseCandidate")
    assert not hasattr(finished_cut_production, "TargetedRevisionCommand")
    assert not hasattr(finished_cut_production, "MaterializationPlan")
    assert not hasattr(finished_cut_production, "PlanRecord")
    assert not hasattr(finished_cut_production, "FinishedCutView")
    assert not hasattr(finished_cut_production, "ReleaseArtifact")
    assert not hasattr(finished_cut_production, "StageRequest")
    assert not hasattr(finished_cut_production, "StageProposal")
    assert not hasattr(finished_cut_production, "InMemoryProductionSystem")
    assert not hasattr(finished_cut_production, "advance")
    assert not hasattr(finished_cut_production, "request_revision")
    assert not hasattr(finished_cut_production, "inspect_current")
    assert not hasattr(finished_cut_production, "AcceptedStageStore")
    assert not hasattr(finished_cut_production, "PlanRecordStore")
    assert not hasattr(finished_cut_production, "ResolveTransactionManager")
    assert not hasattr(finished_cut_production, "_cut_view")
    assert hasattr(finished_cut_production, "FinishedCutProduction")
    assert finished_cut_production.FinishedCutProduction is FinishedCutProduction


def test_director_cannot_overreach_into_dp_or_visual_fields() -> None:
    allowed = _asset("allowed-director-overreach")
    system = InMemoryProductionSystem(
        approved_cuts=(_approved_cut(),),
        asset_resolver=InMemoryAssetResolver((allowed,)),
    )
    waiting = advance(_approved_cut().command_id, system=system)
    assert waiting.outstanding_request is not None
    system.respond(
        waiting.outstanding_request,
        events=(
            EventRecord(
                "stock-1",
                ("cue-26",),
                "text-26",
                "Director owns only visual intent",
                asset_ref=allowed.reference,
                visual_status="approved",
            ),
        ),
    )

    rejected = advance(_approved_cut().command_id, system=system)

    assert rejected.status == "needs_review"
    assert rejected.accepted_stages == ()
    assert rejected.outstanding_request == waiting.outstanding_request


def test_current_director_dp_visual_chain_produces_typed_plan() -> None:
    system = InMemoryProductionSystem(
        approved_cuts=(_approved_cut(),),
        asset_resolver=InMemoryAssetResolver((_asset("stock-1"),)),
    )
    director_event = EventRecord(
        event_id="stock-1",
        master_cue_ids=("cue-30", "cue-31"),
        text_hash="text-30-31",
        intent="Show the speaker's concrete example",
    )
    ready, visual_events = _advance_ready(system, (director_event,))

    assert ready.status == "review_ready"
    assert ready.outstanding_request is None
    assert isinstance(ready.materialization_plan, MaterializationPlan)
    assert ready.materialization_plan.events == visual_events
    assert (
        ready.materialization_plan.director_acceptance_id == ready.accepted_stages[0].acceptance_id
    )
    assert ready.materialization_plan.dp_acceptance_id == ready.accepted_stages[1].acceptance_id
    assert ready.materialization_plan.visual_acceptance_id == ready.accepted_stages[2].acceptance_id


def test_request_revision_mints_one_current_event_retry_command() -> None:
    system = InMemoryProductionSystem(
        approved_cuts=(_approved_cut(),),
        asset_resolver=InMemoryAssetResolver((_asset("hero-1"), _asset("hero-2"))),
    )
    events = (
        EventRecord("hero-1", ("cue-40",), "text-40", "Keep this event"),
        EventRecord("hero-2", ("cue-41",), "text-41", "Revise this event"),
    )
    ready, _ = _advance_ready(system, events)
    release = system.install_plan_record(ready)

    revision_id = request_revision(
        release.plan_id,
        "hero-2",
        "Use a clearer, less oppressive Hero title",
        system=system,
    )
    retry = advance(revision_id, system=system)

    assert retry.status == "pending"
    assert retry.outstanding_request is not None
    assert retry.outstanding_request.stage == "director"
    assert retry.outstanding_request.scope == "event_retry"
    assert retry.outstanding_request.event_id == "hero-2"
    assert retry.outstanding_request.feedback == "Use a clearer, less oppressive Hero title"
    assert tuple(event.event_id for event in retry.outstanding_request.events) == ("hero-2",)
