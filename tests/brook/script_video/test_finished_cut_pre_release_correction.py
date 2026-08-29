from __future__ import annotations

import hashlib
from dataclasses import asdict, replace

import pytest

from agents.brook.script_video.finished_cut_production import (
    CommandRejectedError,
    FinishedCutProduction,
)
from agents.brook.script_video.finished_cut_production._assets import (
    AssetKind,
    AssetRecord,
    InMemoryAssetResolver,
)
from agents.brook.script_video.finished_cut_production._commands import ApprovedCutCommand
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
    InMemoryEditorialCutContextResolver,
)
from agents.brook.script_video.finished_cut_production._derived_assets import (
    DerivedAssetBuildResult,
)
from agents.brook.script_video.finished_cut_production._policy import PolicyDecision
from agents.brook.script_video.finished_cut_production._records import (
    DirectorEventProposal,
    DPEventProposal,
    VisualEventProposal,
    _mint_accepted_stage,
)
from agents.brook.script_video.finished_cut_production._semantic import (
    InMemorySemanticAdapter,
    SemanticDispatchOutcome,
)
from agents.brook.script_video.finished_cut_production._store import (
    InMemoryApprovedCutStore,
    _StoredRun,
)

COMMAND_ID = "approved-cut:fedcba9876543210fedcba9876543210"


def _approved_cut() -> ApprovedCutCommand:
    return ApprovedCutCommand(
        command_id=COMMAND_ID,
        episode_id="episode-correction",
        cut_id="long-3",
        format="long",
        editorial_master_id="master-current",
        winner_id="winner-current",
        tight_cut_id="tight-current",
    )


def _editorial_context() -> EditorialCutContext:
    return EditorialCutContext(
        episode_id="episode-correction",
        cut_id="long-3",
        format="long",
        editorial_master_id="master-current",
        tight_cut_id="tight-current",
        duration_sec=540.0,
        source_ranges=(CutSourceRange(0.0, 540.0),),
        cues=(
            CueAnchor(
                "cue-purpose",
                "工作除了 Purpose，也需要清楚知道自己的 Calling。",
                12.0,
                16.0,
                "section-purpose",
            ),
            CueAnchor(
                "cue-golden-age",
                "下一個黃金年代，會屬於能善用 AI 的工作者。",
                26.0,
                31.0,
                "section-purpose",
            ),
        ),
        sections=(CanonicalSection("section-purpose", "工作的下一步", 0.0),),
    )


def _asset(name: str) -> AssetRecord:
    return AssetRecord(
        digest=hashlib.sha256(name.encode()).hexdigest(),
        extension=".mp4",
        kind=AssetKind.STOCK,
        visual_summary=f"Neutral landscape footage: {name}",
        width=1920,
        height=1080,
        duration_sec=12.0,
    )


def _production(
    tmp_path,
    *,
    assets: tuple[AssetRecord, ...] = (),
    long_policy=None,
    context: EditorialCutContext | None = None,
):
    semantic = InMemorySemanticAdapter()
    production = FinishedCutProduction(
        store_root=tmp_path / "runtime",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver(assets),
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver(
            (context if context is not None else _editorial_context(),)
        ),
        long_policy=long_policy,
    )
    return production, semantic


class _CountingPendingSemanticAdapter:
    def __init__(self) -> None:
        self.dispatches = 0

    def dispatch(self, request) -> SemanticDispatchOutcome:
        self.dispatches += 1
        return SemanticDispatchOutcome(request.request_id, "pending")

    def outcome_for(self, _request_id: str) -> None:
        return None

    def dispatch_state(self, _request_id: str) -> str:
        return "unclaimed"


class _AcceptingPolicy:
    def validate(self, _candidate) -> PolicyDecision:
        return PolicyDecision("accepted")


class _CountingFailedBuilder:
    def __init__(self) -> None:
        self.calls = 0

    def build(self, request) -> DerivedAssetBuildResult:
        self.calls += 1
        return DerivedAssetBuildResult(
            request.build_request_id,
            request.dp_acceptance_id,
            "failed",
            error_code="should_not_dispatch",
        )


def test_director_checkpoint_is_publicly_inspectable_before_dp_dispatch(tmp_path) -> None:
    production, semantic = _production(tmp_path)
    production.advance(COMMAND_ID)
    request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        request,
        events=(
            DirectorEventProposal(
                event_id="event-purpose",
                master_cue_ids=("cue-purpose",),
                intent="State the complete relationship between purpose and calling",
                display="Purpose / Calling",
                semantic_kind="hero_title",
            ),
            DirectorEventProposal(
                event_id="event-golden-age",
                master_cue_ids=("cue-golden-age",),
                intent="Name who the next golden age belongs to",
                display="AI 工作力的下一個黃金年代",
                semantic_kind="b_roll",
            ),
        ),
    )

    production.advance(COMMAND_ID)
    observed = production.inspect_run(COMMAND_ID)

    assert observed.command_id == COMMAND_ID
    assert observed.status == "pending"
    assert observed.outstanding_stage == "dp"
    assert len(observed.current_stages) == 1
    director = observed.current_stages[0]
    assert director.stage == "director"
    assert director.scope == "full_stage"
    assert tuple(event.event_id for event in director.events) == (
        "event-purpose",
        "event-golden-age",
    )
    purpose = director.events[0]
    assert purpose.master_cue_ids == ("cue-purpose",)
    assert purpose.text == "工作除了 Purpose，也需要清楚知道自己的 Calling。"
    assert purpose.display == "Purpose / Calling"
    assert purpose.semantic_kind == "hero_title"
    assert purpose.implementation_kind == ""
    assert purpose.lane is None
    assert purpose.asset_ref is None
    assert purpose.visual_status is None
    assert observed.superseded_acceptance_ids == ()
    assert observed.build_state == "not_started"
    assert observed.policy_diagnostics == ()
    assert semantic.current_request(COMMAND_ID).stage == "director"
    serialized = asdict(observed)
    assert "path" not in repr(serialized).lower()
    assert "inspection" not in repr(serialized).lower()
    assert "visual-pipeline" not in repr(serialized).lower()


def test_director_correction_replaces_only_one_event_then_allows_first_dp(tmp_path) -> None:
    production, semantic = _production(tmp_path)
    production.advance(COMMAND_ID)
    request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        request,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "State the complete relationship between purpose and calling",
                "Purpose / Calling",
                "hero_title",
            ),
            DirectorEventProposal(
                "event-golden-age",
                ("cue-golden-age",),
                "Name who the next golden age belongs to",
                "AI 工作力的下一個黃金年代",
                "b_roll",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    before = production.inspect_run(COMMAND_ID)
    base = before.current_stages[0]
    unaffected_before = base.events[1]

    request_id = production.request_correction(
        COMMAND_ID,
        "director",
        "event-purpose",
        "必須寫成有主詞、可獨立理解的完整命題，不可只寫斜線並列。",
    )
    waiting = production.inspect_run(COMMAND_ID)

    assert waiting.current_stages == ()
    assert waiting.superseded_acceptance_ids == (base.acceptance_id,)
    assert waiting.outstanding_stage == "director"
    assert waiting.outstanding_scope == "event_retry"
    assert waiting.outstanding_event_id == "event-purpose"
    production.advance(COMMAND_ID)
    retry = semantic.current_request(COMMAND_ID)
    assert retry.request_id == request_id
    assert retry.stage == "director"
    assert retry.scope == "event_retry"
    assert retry.event_id == "event-purpose"
    assert retry.base_acceptance_id == base.acceptance_id
    assert retry.parent_acceptance_id is None
    assert tuple(event.event_id for event in retry.events) == ("event-purpose",)
    semantic.respond(
        retry,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "State a self-contained relationship between purpose and calling",
                "工作的意義來自 Purpose，也要落在 Calling",
                "hero_title",
            ),
        ),
    )

    production.advance(COMMAND_ID)
    corrected = production.inspect_run(COMMAND_ID)

    assert corrected.outstanding_stage == "dp"
    assert corrected.outstanding_scope == "full_stage"
    assert corrected.current_stages[0].scope == "event_retry"
    assert corrected.current_stages[0].events[0].display == (
        "工作的意義來自 Purpose，也要落在 Calling"
    )
    assert corrected.current_stages[0].events[1] == unaffected_before
    assert corrected.superseded_acceptance_ids == (base.acceptance_id,)
    assert semantic.current_request(COMMAND_ID).stage == "director"


def test_dp_correction_keeps_other_selection_and_first_build_visual_are_full(tmp_path) -> None:
    first = _asset("purpose-first")
    replacement = _asset("purpose-replacement")
    unaffected = _asset("golden-age")
    production, semantic = _production(
        tmp_path,
        assets=(first, replacement, unaffected),
    )
    production.advance(COMMAND_ID)
    director_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "Show work with a concrete sense of purpose",
                "工作的意義與召喚",
                "b_roll",
            ),
            DirectorEventProposal(
                "event-golden-age",
                ("cue-golden-age",),
                "Show workers using AI together",
                "AI 工作力的下一個黃金年代",
                "b_roll",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    dp_request = semantic.current_request(COMMAND_ID)
    assert dp_request.stage == "dp"
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "event-purpose", "stock_video", "b_roll", first.reference, ("cue-purpose",)
            ),
            DPEventProposal(
                "event-golden-age",
                "stock_video",
                "b_roll",
                unaffected.reference,
                ("cue-golden-age",),
            ),
        ),
    )
    production.advance(COMMAND_ID)
    before = production.inspect_run(COMMAND_ID)
    base_dp = before.current_stages[1]
    unaffected_before = base_dp.events[1]
    assert before.build_state == "pending"

    production.request_correction(
        COMMAND_ID,
        "dp",
        "event-purpose",
        "原素材情緒太開心，改成忙碌但有方向感的橫式工作畫面。",
    )
    production.advance(COMMAND_ID)
    retry = semantic.current_request(COMMAND_ID)
    assert retry.stage == "dp"
    assert retry.scope == "event_retry"
    assert retry.base_acceptance_id == base_dp.acceptance_id
    assert tuple(event.event_id for event in retry.events) == ("event-purpose",)
    retry_checkpoint = production.inspect_run(COMMAND_ID)
    semantic.respond(
        retry,
        events=(
            DPEventProposal(
                "event-purpose",
                "stock_video",
                "b_roll",
                replacement.reference,
                ("cue-purpose",),
            ),
            DPEventProposal(
                "event-golden-age",
                "stock_video",
                "b_roll",
                unaffected.reference,
                ("cue-golden-age",),
            ),
        ),
    )
    rejected_extra = production.advance(COMMAND_ID)
    assert rejected_extra.status == "needs_review"
    assert production.inspect_run(COMMAND_ID).current_stages == retry_checkpoint.current_stages
    semantic.respond(
        retry,
        events=(
            DPEventProposal(
                "event-purpose",
                "stock_video",
                "b_roll",
                replacement.reference,
                ("cue-purpose",),
            ),
        ),
    )
    production.advance(COMMAND_ID)
    corrected_dp = production.inspect_run(COMMAND_ID)

    assert corrected_dp.current_stages[1].events[0].asset_ref == replacement.reference
    assert corrected_dp.current_stages[1].events[1] == unaffected_before
    assert corrected_dp.build_state == "pending"
    production.advance(COMMAND_ID)
    built = production.inspect_run(COMMAND_ID)
    assert built.outstanding_stage == "visual_review"
    assert built.build_state == "ready"
    production.advance(COMMAND_ID)
    visual_request = semantic.current_request(COMMAND_ID)
    assert visual_request.stage == "visual_review"
    assert visual_request.scope == "full_stage"
    assert visual_request.event_id is None
    assert tuple(event.event_id for event in visual_request.events) == (
        "event-purpose",
        "event-golden-age",
    )
    assert tuple(component.final_asset_ref for component in visual_request.built_components) == (
        replacement.reference,
        unaffected.reference,
    )


def test_dp_correction_changes_only_target_visual_placement_authority(tmp_path) -> None:
    context = EditorialCutContext(
        episode_id="episode-correction",
        cut_id="long-3",
        format="long",
        editorial_master_id="master-current",
        tight_cut_id="tight-current",
        duration_sec=540.0,
        source_ranges=(CutSourceRange(0.0, 540.0),),
        cues=(
            CueAnchor(
                "cue-purpose-1",
                "工作需要清楚的 Purpose。",
                12.0,
                14.0,
                "section-purpose",
            ),
            CueAnchor(
                "cue-purpose-2",
                "也要知道自己的 Calling。",
                14.0,
                16.0,
                "section-purpose",
            ),
            CueAnchor(
                "cue-golden-age",
                "下一個黃金年代，會屬於能善用 AI 的工作者。",
                26.0,
                31.0,
                "section-purpose",
            ),
        ),
        sections=(CanonicalSection("section-purpose", "工作的下一步", 0.0),),
    )
    first = _asset("purpose-placement-first")
    replacement = _asset("purpose-placement-replacement")
    unaffected = _asset("golden-placement-unaffected")
    production, semantic = _production(
        tmp_path,
        assets=(first, replacement, unaffected),
        context=context,
    )
    production.advance(COMMAND_ID)
    director = semantic.current_request(COMMAND_ID)
    semantic.respond(
        director,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose-1", "cue-purpose-2"),
                "State the complete relationship between purpose and calling",
                "工作的意義與召喚",
                "b_roll",
            ),
            DirectorEventProposal(
                "event-golden-age",
                ("cue-golden-age",),
                "Show workers using AI together",
                "AI 工作力的下一個黃金年代",
                "b_roll",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    dp = semantic.current_request(COMMAND_ID)
    semantic.respond(
        dp,
        events=(
            DPEventProposal(
                "event-purpose",
                "stock_video",
                "b_roll",
                first.reference,
                ("cue-purpose-1",),
            ),
            DPEventProposal(
                "event-golden-age",
                "stock_video",
                "b_roll",
                unaffected.reference,
                ("cue-golden-age",),
            ),
        ),
    )
    production.advance(COMMAND_ID)
    before = production.inspect_run(COMMAND_ID)
    base_dp = before.current_stages[1]
    target_before = base_dp.events[0]
    unaffected_before = base_dp.events[1]

    production.request_correction(
        COMMAND_ID,
        "dp",
        "event-purpose",
        "保留語意證據，但實際上畫改到 Calling 這一句。",
    )
    production.advance(COMMAND_ID)
    retry = semantic.current_request(COMMAND_ID)
    assert tuple(cue.cue_id for cue in retry.placement_candidates[0].cues) == (
        "cue-purpose-1",
        "cue-purpose-2",
    )
    semantic.respond(
        retry,
        events=(
            DPEventProposal(
                "event-purpose",
                "stock_video",
                "b_roll",
                replacement.reference,
                ("cue-purpose-2",),
            ),
        ),
    )

    production.advance(COMMAND_ID)
    after = production.inspect_run(COMMAND_ID).current_stages[1]
    target_after = after.events[0]

    assert (target_before.t0, target_before.t1) == (12.0, 16.0)
    assert (target_after.t0, target_after.t1) == (12.0, 16.0)
    assert (target_before.placement_t0, target_before.placement_t1) == (12.0, 14.0)
    assert (target_after.placement_t0, target_after.placement_t1) == (14.0, 16.0)
    assert after.events[1] == unaffected_before


def test_failed_visual_checkpoint_can_retry_one_event_without_materializing(tmp_path) -> None:
    purpose = _asset("purpose-visual")
    golden = _asset("golden-visual")
    production, semantic = _production(tmp_path, assets=(purpose, golden))
    production.advance(COMMAND_ID)
    director_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "Show a concrete purposeful workplace",
                "工作的意義與召喚",
                "b_roll",
            ),
            DirectorEventProposal(
                "event-golden-age",
                ("cue-golden-age",),
                "Show workers using AI together",
                "AI 工作力的下一個黃金年代",
                "b_roll",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    dp_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "event-purpose", "stock_video", "b_roll", purpose.reference, ("cue-purpose",)
            ),
            DPEventProposal(
                "event-golden-age",
                "stock_video",
                "b_roll",
                golden.reference,
                ("cue-golden-age",),
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    visual_request = semantic.current_request(COMMAND_ID)
    assert visual_request.stage == "visual_review"
    semantic.respond(
        visual_request,
        events=(
            VisualEventProposal("event-purpose", "failed"),
            VisualEventProposal("event-golden-age", "approved"),
        ),
    )

    failed_view = production.advance(COMMAND_ID)
    checkpoint = production.inspect_run(COMMAND_ID)

    assert failed_view.status == "needs_review"
    assert failed_view.current_stage is None
    assert len(checkpoint.current_stages) == 3
    base_visual = checkpoint.current_stages[2]
    assert tuple(event.visual_status for event in base_visual.events) == (
        "failed",
        "approved",
    )
    assert tuple(event.asset_ref for event in base_visual.events) == (
        purpose.reference,
        golden.reference,
    )
    unaffected_before = base_visual.events[1]

    production.request_correction(
        COMMAND_ID,
        "visual_review",
        "event-purpose",
        "這個畫面語意不符；只重看這個 final component。",
    )
    production.advance(COMMAND_ID)
    retry = semantic.current_request(COMMAND_ID)
    assert retry.stage == "visual_review"
    assert retry.scope == "event_retry"
    assert retry.base_acceptance_id == base_visual.acceptance_id
    assert tuple(event.event_id for event in retry.events) == ("event-purpose",)
    assert tuple(component.event_id for component in retry.built_components) == ("event-purpose",)
    semantic.respond(
        retry,
        events=(VisualEventProposal("event-purpose", "approved"),),
    )

    corrected_view = production.advance(COMMAND_ID)
    corrected = production.inspect_run(COMMAND_ID)

    assert corrected_view.status == "pending"
    assert corrected_view.current_stage is None
    assert corrected.current_stages[2].events[0].visual_status == "approved"
    assert corrected.current_stages[2].events[1] == unaffected_before
    assert base_visual.acceptance_id in corrected.superseded_acceptance_ids
    assert corrected.policy_diagnostics == ()


def test_visual_checkpoint_can_return_one_event_to_dp_and_cascade_only_that_event(
    tmp_path,
) -> None:
    first = _asset("purpose-before-visual-correction")
    replacement = _asset("purpose-after-visual-correction")
    unaffected = _asset("golden-unchanged")
    production, semantic = _production(
        tmp_path,
        assets=(first, replacement, unaffected),
    )
    production.advance(COMMAND_ID)
    director_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "Show a purposeful workplace",
                "工作的意義與召喚",
                "b_roll",
            ),
            DirectorEventProposal(
                "event-golden-age",
                ("cue-golden-age",),
                "Show workers using AI together",
                "AI 工作力的下一個黃金年代",
                "b_roll",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    dp_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "event-purpose", "stock_video", "b_roll", first.reference, ("cue-purpose",)
            ),
            DPEventProposal(
                "event-golden-age",
                "stock_video",
                "b_roll",
                unaffected.reference,
                ("cue-golden-age",),
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    visual_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        visual_request,
        events=(
            VisualEventProposal("event-purpose", "failed"),
            VisualEventProposal("event-golden-age", "approved"),
        ),
    )
    production.advance(COMMAND_ID)
    before = production.inspect_run(COMMAND_ID)
    base_dp = before.current_stages[1]
    base_visual = before.current_stages[2]
    unaffected_dp = base_dp.events[1]
    unaffected_visual = base_visual.events[1]

    production.request_correction(
        COMMAND_ID,
        "dp",
        "event-purpose",
        "final 畫面語意不符，回到 DP 只替這個事件重選素材。",
    )
    production.advance(COMMAND_ID)
    retry_dp = semantic.current_request(COMMAND_ID)
    assert retry_dp.stage == "dp"
    assert retry_dp.scope == "event_retry"
    assert retry_dp.base_acceptance_id == base_dp.acceptance_id
    semantic.respond(
        retry_dp,
        events=(
            DPEventProposal(
                "event-purpose",
                "stock_video",
                "b_roll",
                replacement.reference,
                ("cue-purpose",),
            ),
        ),
    )
    production.advance(COMMAND_ID)
    after_dp = production.inspect_run(COMMAND_ID)
    assert after_dp.current_stages[1].events[1] == unaffected_dp
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    retry_visual = semantic.current_request(COMMAND_ID)

    assert retry_visual.stage == "visual_review"
    assert retry_visual.scope == "event_retry"
    assert retry_visual.event_id == "event-purpose"
    assert retry_visual.base_acceptance_id == base_visual.acceptance_id
    assert tuple(event.event_id for event in retry_visual.events) == ("event-purpose",)
    assert tuple(component.final_asset_ref for component in retry_visual.built_components) == (
        replacement.reference,
    )
    semantic.respond(
        retry_visual,
        events=(VisualEventProposal("event-purpose", "approved"),),
    )
    production.advance(COMMAND_ID)
    corrected = production.inspect_run(COMMAND_ID)

    assert corrected.status == "pending"
    assert corrected.current_stages[2].events[0].asset_ref == replacement.reference
    assert corrected.current_stages[2].events[1] == unaffected_visual
    assert base_dp.acceptance_id in corrected.superseded_acceptance_ids
    assert base_visual.acceptance_id in corrected.superseded_acceptance_ids


def test_correction_rejects_a_downstream_request_that_was_already_dispatched(tmp_path) -> None:
    production, semantic = _production(tmp_path)
    production.advance(COMMAND_ID)
    director_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "State the complete relationship",
                "工作的意義來自 Purpose，也要落在 Calling",
                "hero_title",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    assert semantic.current_request(COMMAND_ID).stage == "dp"
    before = production.inspect_run(COMMAND_ID)

    with pytest.raises(CommandRejectedError, match="already claimed"):
        production.request_correction(
            COMMAND_ID,
            "director",
            "event-purpose",
            "修正文案。",
        )

    assert production.inspect_run(COMMAND_ID) == before


def test_stale_correction_base_fails_before_worker_dispatch_after_restart(tmp_path) -> None:
    production, semantic = _production(tmp_path)
    production.advance(COMMAND_ID)
    director_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "State a complete claim",
                "工作的意義來自 Purpose，也要落在 Calling",
                "hero_title",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.request_correction(
        COMMAND_ID,
        "director",
        "event-purpose",
        "修正文案。",
    )
    stored = production._store.load_run(COMMAND_ID)
    assert stored is not None
    assert stored.view.outstanding_request is not None
    production._store.save_run(
        _StoredRun(
            command=stored.command,
            view=replace(
                stored.view,
                outstanding_request=replace(
                    stored.view.outstanding_request,
                    base_acceptance_id="acceptance-stale",
                ),
            ),
            worker_catalog=stored.worker_catalog,
            base_release_id=stored.base_release_id,
        )
    )
    counter = _CountingPendingSemanticAdapter()
    restarted = FinishedCutProduction(
        store_root=tmp_path / "runtime",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=counter,
        context_resolver=InMemoryEditorialCutContextResolver(()),
    )

    stopped = restarted.advance(COMMAND_ID)

    assert stopped.status == "needs_review"
    assert stopped.current_stage == "director"
    assert counter.dispatches == 0


def test_superseded_same_stage_base_fails_before_worker_dispatch(tmp_path) -> None:
    production, semantic = _production(tmp_path)
    production.advance(COMMAND_ID)
    director_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "State a complete claim",
                "工作的意義來自 Purpose，也要落在 Calling",
                "hero_title",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    first_base = production.inspect_run(COMMAND_ID).current_stages[0].acceptance_id
    production.request_correction(COMMAND_ID, "director", "event-purpose", "第一次修正。")
    production.advance(COMMAND_ID)
    retry = semantic.current_request(COMMAND_ID)
    semantic.respond(
        retry,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "State a complete corrected claim",
                "Purpose 與 Calling 共同定義工作的意義",
                "hero_title",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.request_correction(COMMAND_ID, "director", "event-purpose", "第二次修正。")
    stored = production._store.load_run(COMMAND_ID)
    assert stored is not None
    assert stored.view.outstanding_request is not None
    production._store.save_run(
        _StoredRun(
            command=stored.command,
            view=replace(
                stored.view,
                outstanding_request=replace(
                    stored.view.outstanding_request,
                    base_acceptance_id=first_base,
                ),
            ),
            worker_catalog=stored.worker_catalog,
            base_release_id=stored.base_release_id,
        )
    )
    counter = _CountingPendingSemanticAdapter()
    restarted = FinishedCutProduction(
        store_root=tmp_path / "runtime",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=counter,
        context_resolver=InMemoryEditorialCutContextResolver(()),
    )

    stopped = restarted.advance(COMMAND_ID)

    assert stopped.status == "needs_review"
    assert counter.dispatches == 0


def test_director_change_to_aroll_keeps_visual_retry_and_drops_obsolete_build(
    tmp_path,
) -> None:
    stock = _asset("purpose-before-aroll")
    production, semantic = _production(tmp_path, assets=(stock,))
    production.advance(COMMAND_ID)
    director_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "Show a purposeful workplace",
                "工作的意義與召喚",
                "b_roll",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    dp_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "event-purpose", "stock_video", "b_roll", stock.reference, ("cue-purpose",)
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    visual_request = semantic.current_request(COMMAND_ID)
    semantic.respond(
        visual_request,
        events=(VisualEventProposal("event-purpose", "approved"),),
    )
    production.advance(COMMAND_ID)
    before = production.inspect_run(COMMAND_ID)
    old_visual = before.current_stages[2]

    production.request_correction(
        COMMAND_ID,
        "director",
        "event-purpose",
        "這裡保留講者原畫面，不要 B-roll。",
    )
    production.advance(COMMAND_ID)
    director_retry = semantic.current_request(COMMAND_ID)
    semantic.respond(
        director_retry,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "Keep the speaker's complete argument",
                "保留講者完整論述",
                "intentional_aroll",
                intentional_aroll=True,
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    dp_retry = semantic.current_request(COMMAND_ID)
    semantic.respond(
        dp_retry,
        events=(DPEventProposal("event-purpose", "intentional_aroll", None),),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    visual_retry = semantic.current_request(COMMAND_ID)

    assert visual_retry.stage == "visual_review"
    assert visual_retry.scope == "event_retry"
    assert visual_retry.base_acceptance_id == old_visual.acceptance_id
    assert visual_retry.built_components == ()
    semantic.respond(
        visual_retry,
        events=(VisualEventProposal("event-purpose", "approved"),),
    )
    production.advance(COMMAND_ID)
    corrected = production.inspect_run(COMMAND_ID)
    assert corrected.current_stages[2].events[0].intentional_aroll is True
    assert corrected.current_stages[2].events[0].asset_ref is None


def test_tampered_visual_lineage_cannot_mint_materialization_plan(tmp_path) -> None:
    stock = _asset("lineage-stock")
    production, semantic = _production(
        tmp_path,
        assets=(stock,),
        long_policy=_AcceptingPolicy(),
    )
    production.advance(COMMAND_ID)
    director = semantic.current_request(COMMAND_ID)
    semantic.respond(
        director,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "Show a purposeful workplace",
                "工作的意義與召喚",
                "b_roll",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    dp = semantic.current_request(COMMAND_ID)
    semantic.respond(
        dp,
        events=(
            DPEventProposal(
                "event-purpose", "stock_video", "b_roll", stock.reference, ("cue-purpose",)
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    visual = semantic.current_request(COMMAND_ID)
    semantic.respond(visual, events=(VisualEventProposal("event-purpose", "approved"),))
    production.advance(COMMAND_ID)
    stored = production._store.load_run(COMMAND_ID)
    assert stored is not None
    accepted = stored.view.accepted_stages
    forged_visual = _mint_accepted_stage(
        acceptance_id=accepted[2].acceptance_id,
        run_id=accepted[2].run_id,
        request_id=accepted[2].request_id,
        stage=accepted[2].stage,
        attempt=accepted[2].attempt,
        scope=accepted[2].scope,
        event_id=accepted[2].event_id,
        parent_acceptance_id="acceptance-superseded",
        events=accepted[2].events,
        components=accepted[2].components,
        built_components=accepted[2].built_components,
    )
    production._store.save_run(
        _StoredRun(
            command=stored.command,
            view=replace(
                stored.view,
                accepted_stages=(
                    accepted[0],
                    accepted[1],
                    forged_visual,
                ),
            ),
            worker_catalog=stored.worker_catalog,
            base_release_id=stored.base_release_id,
        )
    )

    stopped = production.advance(COMMAND_ID)
    authority = production._store.load_run(COMMAND_ID)

    assert stopped.status == "needs_review"
    assert authority is not None
    assert authority.view.materialization_plan is None


def test_tampered_derived_request_fails_before_builder_dispatch(tmp_path) -> None:
    stock = _asset("derived-authority-stock")
    production, semantic = _production(tmp_path, assets=(stock,))
    production.advance(COMMAND_ID)
    director = semantic.current_request(COMMAND_ID)
    semantic.respond(
        director,
        events=(
            DirectorEventProposal(
                "event-purpose",
                ("cue-purpose",),
                "Show a purposeful workplace",
                "工作的意義與召喚",
                "b_roll",
            ),
        ),
    )
    production.advance(COMMAND_ID)
    production.advance(COMMAND_ID)
    dp = semantic.current_request(COMMAND_ID)
    semantic.respond(
        dp,
        events=(
            DPEventProposal(
                "event-purpose", "stock_video", "b_roll", stock.reference, ("cue-purpose",)
            ),
        ),
    )
    production.advance(COMMAND_ID)
    stored = production._store.load_run(COMMAND_ID)
    assert stored is not None
    build_request = stored.view.derived_asset_request
    assert build_request is not None
    tampered_instruction = replace(
        build_request.instructions[0],
        geometry=replace(build_request.instructions[0].geometry, target_width=640),
    )
    production._store.save_run(
        _StoredRun(
            command=stored.command,
            view=replace(
                stored.view,
                derived_asset_request=replace(
                    build_request,
                    instructions=(tampered_instruction,),
                ),
            ),
            worker_catalog=stored.worker_catalog,
            base_release_id=stored.base_release_id,
        )
    )
    builder = _CountingFailedBuilder()
    restarted = FinishedCutProduction(
        store_root=tmp_path / "runtime",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver((stock,)),
        semantic_adapter=InMemorySemanticAdapter(),
        derived_asset_builder=builder,
        context_resolver=InMemoryEditorialCutContextResolver((_editorial_context(),)),
    )

    stopped = restarted.advance(COMMAND_ID)

    assert stopped.status == "needs_review"
    assert builder.calls == 0

    after_geometry = restarted._store.load_run(COMMAND_ID)
    assert after_geometry is not None
    restarted._store.save_run(
        _StoredRun(
            command=after_geometry.command,
            view=replace(
                after_geometry.view,
                status="pending",
                derived_asset_request=replace(build_request, scope="forged"),
            ),
            worker_catalog=after_geometry.worker_catalog,
            base_release_id=after_geometry.base_release_id,
        )
    )
    forged_scope_builder = _CountingFailedBuilder()
    forged_scope_process = FinishedCutProduction(
        store_root=tmp_path / "runtime",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver((stock,)),
        semantic_adapter=InMemorySemanticAdapter(),
        derived_asset_builder=forged_scope_builder,
        context_resolver=InMemoryEditorialCutContextResolver((_editorial_context(),)),
    )

    forged_scope = forged_scope_process.advance(COMMAND_ID)

    assert forged_scope.status == "needs_review"
    assert forged_scope_builder.calls == 0
