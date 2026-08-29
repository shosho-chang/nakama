from __future__ import annotations

import hashlib

import pytest

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
    CommandRejectedError,
)
from agents.brook.script_video.finished_cut_production._engine import (
    InMemoryProductionSystem,
    advance,
    inspect_current,
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


def test_authoritative_approved_cut_starts_current_director_request() -> None:
    system = InMemoryProductionSystem(approved_cuts=(_approved_cut(),))

    view = advance(_approved_cut().command_id, system=system)

    assert view.status == "pending"
    assert view.outstanding_request is not None
    assert view.outstanding_request.stage == "director"
    assert view.outstanding_request.scope == "full_stage"
    assert view.outstanding_request.parent_acceptance_id is None
    assert view.materialization_plan is None


def test_system_semantic_adapter_receives_only_the_current_typed_request() -> None:
    adapter = _RecordingSemanticAdapter()
    system = InMemoryProductionSystem(
        approved_cuts=(_approved_cut(),),
        semantic_adapter=adapter,
    )
    waiting = advance(_approved_cut().command_id, system=system)

    unchanged = advance(_approved_cut().command_id, system=system)

    assert adapter.requests == [waiting.outstanding_request]
    assert unchanged == waiting


def test_unknown_origin_command_is_rejected_before_run_creation() -> None:
    system = InMemoryProductionSystem(approved_cuts=(_approved_cut(),))

    with pytest.raises(CommandRejectedError, match="authoritative command"):
        advance("legacy:episode-1:cut-1", system=system)


def test_old_static_response_cannot_authorize_current_run() -> None:
    system = InMemoryProductionSystem(approved_cuts=(_approved_cut(),))
    waiting = advance(_approved_cut().command_id, system=system)
    assert waiting.outstanding_request is not None
    system.respond(
        waiting.outstanding_request,
        events=(EventRecord("old", ("cue-old",), "old-hash", "old intent"),),
        request_id="responses/director/all.json",
    )

    rejected = advance(_approved_cut().command_id, system=system)

    assert rejected.status == "needs_review"
    assert rejected.accepted_stages == ()
    assert rejected.materialization_plan is None


def test_current_director_proposal_is_accepted_at_most_once() -> None:
    system = InMemoryProductionSystem(approved_cuts=(_approved_cut(),))
    waiting = advance(_approved_cut().command_id, system=system)
    assert waiting.outstanding_request is not None
    system.respond(
        waiting.outstanding_request,
        events=(
            EventRecord(
                event_id="hero-1",
                master_cue_ids=("cue-10",),
                text_hash="text-10",
                intent="Explain the first claim",
            ),
        ),
    )

    accepted = advance(_approved_cut().command_id, system=system)
    repeated = advance(_approved_cut().command_id, system=system)

    assert len(accepted.accepted_stages) == 1
    assert accepted.accepted_stages[0].stage == "director"
    assert repeated.accepted_stages == accepted.accepted_stages
    assert repeated.outstanding_request is not None
    assert repeated.outstanding_request.stage == "dp"
    assert (
        repeated.outstanding_request.parent_acceptance_id
        == accepted.accepted_stages[0].acceptance_id
    )


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
        "CurrentReleaseReader",
        "CutSourceRange",
        "CutView",
        "EventView",
        "FinishedCutInspection",
        "FinishedCutProduction",
        "FinishedCutProductionApplication",
        "ProductionCutoverConfiguration",
        "ProductionPaths",
        "ProductionResolveConfiguration",
        "build_resolve_configuration",
        "ProductionStatusView",
        "ResolveCutBinding",
        "ResolveDatabaseIdentity",
        "ResolveProjectBinding",
        "ResolveProjectLocator",
        "RunEventInspection",
        "RunInspection",
        "RunPolicyDiagnostic",
        "RunStageInspection",
        "RunView",
        "StageName",
        "Status",
        "TimelineIdentity",
        "build_current_release_reader",
        "build_production_application",
    }
    assert not hasattr(finished_cut_production, "AcceptedStage")
    assert not hasattr(finished_cut_production, "StagedReleaseCandidate")
    assert not hasattr(finished_cut_production, "TargetedRevisionCommand")
    assert not hasattr(finished_cut_production, "MaterializationPlan")
    assert not hasattr(finished_cut_production, "FinishedCutRelease")
    assert not hasattr(finished_cut_production, "FinishedCutView")
    assert not hasattr(finished_cut_production, "ReleaseArtifact")
    assert not hasattr(finished_cut_production, "StageRequest")
    assert not hasattr(finished_cut_production, "StageProposal")
    assert not hasattr(finished_cut_production, "InMemoryProductionSystem")
    assert not hasattr(finished_cut_production, "advance")
    assert not hasattr(finished_cut_production, "request_revision")
    assert not hasattr(finished_cut_production, "inspect_current")
    assert not hasattr(finished_cut_production, "AcceptedStageStore")
    assert not hasattr(finished_cut_production, "FinishedCutReleaseLifecycle")
    assert not hasattr(finished_cut_production, "ResolveTransactionManager")
    assert not hasattr(finished_cut_production, "_cut_view")
    assert hasattr(finished_cut_production, "FinishedCutProduction")
    assert finished_cut_production.FinishedCutProduction is FinishedCutProduction


def test_forged_state_view_cannot_authorize_a_materialization_plan() -> None:
    system = InMemoryProductionSystem(approved_cuts=(_approved_cut(),))
    waiting = advance(_approved_cut().command_id, system=system)
    system.replace_state_view(
        waiting.run_id,
        {
            "status": "review_ready",
            "stages": {
                "director": {"status": "approved"},
                "dp": {"status": "approved"},
                "visual_review": {"status": "approved"},
            },
            "materialization_plan": {"events": [{"event_id": "forged"}]},
        },
    )

    observed = advance(_approved_cut().command_id, system=system)

    assert observed.status == "pending"
    assert observed.outstanding_request == waiting.outstanding_request
    assert observed.materialization_plan is None


def test_wrong_parent_dp_proposal_is_rejected_without_full_stage_rerun() -> None:
    system = InMemoryProductionSystem(approved_cuts=(_approved_cut(),))
    director_wait = advance(_approved_cut().command_id, system=system)
    assert director_wait.outstanding_request is not None
    event = EventRecord(
        event_id="stock-1",
        master_cue_ids=("cue-20",),
        text_hash="text-20",
        intent="Show a concrete workplace example",
    )
    system.respond(director_wait.outstanding_request, events=(event,))
    dp_wait = advance(_approved_cut().command_id, system=system)
    assert dp_wait.outstanding_request is not None
    system.respond(
        dp_wait.outstanding_request,
        events=(
            EventRecord(
                event_id=event.event_id,
                master_cue_ids=event.master_cue_ids,
                text_hash=event.text_hash,
                intent=event.intent,
                asset_ref="asset-sha256:neutral-1",
            ),
        ),
        parent_acceptance_id="acceptance-from-an-old-run",
    )

    rejected = advance(_approved_cut().command_id, system=system)
    unchanged = advance(_approved_cut().command_id, system=system)

    assert rejected.status == "needs_review"
    assert len(rejected.accepted_stages) == 1
    assert rejected.outstanding_request == dp_wait.outstanding_request
    assert unchanged.outstanding_request == rejected.outstanding_request


def test_dp_rejects_well_formed_asset_reference_outside_current_catalog() -> None:
    allowed = _asset("allowed")
    forged = _asset("not-in-this-catalog")
    system = InMemoryProductionSystem(
        approved_cuts=(_approved_cut(),),
        asset_resolver=InMemoryAssetResolver((allowed,)),
    )
    director_wait = advance(_approved_cut().command_id, system=system)
    assert director_wait.outstanding_request is not None
    event = EventRecord("stock-1", ("cue-25",), "text-25", "Use one neutral stock clip")
    system.respond(director_wait.outstanding_request, events=(event,))
    dp_wait = advance(_approved_cut().command_id, system=system)
    assert dp_wait.outstanding_request is not None
    system.respond(
        dp_wait.outstanding_request,
        events=(
            EventRecord(
                event.event_id,
                event.master_cue_ids,
                event.text_hash,
                event.intent,
                asset_ref=forged.reference,
            ),
        ),
    )

    rejected = advance(_approved_cut().command_id, system=system)

    assert forged.reference.startswith("asset-sha256:")
    assert rejected.status == "needs_review"
    assert len(rejected.accepted_stages) == 1


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
    release = system.install_current_release(ready, release_id="release-1")

    revision_id = request_revision(
        release.release_id,
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


def test_targeted_retry_changes_only_one_event_in_the_typed_plan() -> None:
    system = InMemoryProductionSystem(
        approved_cuts=(_approved_cut(),),
        asset_resolver=InMemoryAssetResolver(
            (_asset("hero-1"), _asset("hero-2"), _asset("hero-2-revised"))
        ),
    )
    director_events = (
        EventRecord("hero-1", ("cue-50",), "text-50", "Keep this intent"),
        EventRecord("hero-2", ("cue-51",), "text-51", "Old dense title"),
    )
    ready, original_events = _advance_ready(system, director_events)
    release = system.install_current_release(ready, release_id="release-2")
    revision_id = request_revision(
        release.release_id,
        "hero-2",
        "Use a concise contextual title",
        system=system,
    )
    director_wait = advance(revision_id, system=system)
    assert director_wait.outstanding_request is not None
    revised_director = EventRecord(
        "hero-2",
        ("cue-51",),
        "text-51",
        "A concise title with its subject",
    )
    system.respond(director_wait.outstanding_request, events=(revised_director,))
    dp_wait = advance(revision_id, system=system)
    assert dp_wait.outstanding_request is not None
    assert tuple(event.event_id for event in dp_wait.outstanding_request.events) == ("hero-2",)
    revised_dp = EventRecord(
        revised_director.event_id,
        revised_director.master_cue_ids,
        revised_director.text_hash,
        revised_director.intent,
        asset_ref=_asset("hero-2-revised").reference,
    )
    system.respond(dp_wait.outstanding_request, events=(revised_dp,))
    visual_wait = advance(revision_id, system=system)
    assert visual_wait.outstanding_request is not None
    assert tuple(event.event_id for event in visual_wait.outstanding_request.events) == ("hero-2",)
    revised_visual = EventRecord(
        revised_dp.event_id,
        revised_dp.master_cue_ids,
        revised_dp.text_hash,
        revised_dp.intent,
        asset_ref=revised_dp.asset_ref,
        visual_status="approved",
    )
    system.respond(visual_wait.outstanding_request, events=(revised_visual,))

    checkpoint = advance(revision_id, system=system)
    revised = advance(revision_id, system=system)

    assert checkpoint.status == "pending"
    assert revised.status == "review_ready"
    assert revised.materialization_plan is not None
    by_id = {event.event_id: event for event in revised.materialization_plan.events}
    original_by_id = {event.event_id: event for event in original_events}
    assert set(by_id) == {"hero-1", "hero-2"}
    assert by_id["hero-1"] == original_by_id["hero-1"]
    assert by_id["hero-2"] == revised_visual


def test_inspect_and_revision_use_only_exact_current_release() -> None:
    system = InMemoryProductionSystem(
        approved_cuts=(_approved_cut(),),
        asset_resolver=InMemoryAssetResolver((_asset("hero-1"),)),
    )
    ready, _ = _advance_ready(
        system,
        (EventRecord("hero-1", ("cue-60",), "text-60", "Current event"),),
    )
    historical = system.install_current_release(ready, release_id="release-historical")
    current = system.install_current_release(ready, release_id="release-current")

    assert inspect_current("episode-1", system=system) == (current,)
    assert inspect_current(historical.release_id, system=system) == ()
    with pytest.raises(CommandRejectedError, match="not exact current"):
        request_revision(
            historical.release_id,
            "hero-1",
            "This stale release must not authorize a revision",
            system=system,
        )
