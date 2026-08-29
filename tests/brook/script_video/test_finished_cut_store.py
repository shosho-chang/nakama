from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from agents.brook.script_video.finished_cut_production import (
    CommandRejectedError,
    FinishedCutInspection,
    FinishedCutProduction,
    RunView,
)
from agents.brook.script_video.finished_cut_production import _store as store_module
from agents.brook.script_video.finished_cut_production._assets import (
    AssetKind,
    AssetRecord,
    InMemoryAssetResolver,
)
from agents.brook.script_video.finished_cut_production._commands import (
    ApprovedCutCommand,
)
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
    InMemoryEditorialCutContextResolver,
)
from agents.brook.script_video.finished_cut_production._derived_assets import (
    BuiltComponentAsset,
    DerivedAssetBuildRequest,
    DerivedAssetBuildResult,
)
from agents.brook.script_video.finished_cut_production._policy import (
    StockVideoMetadata,
)
from agents.brook.script_video.finished_cut_production._records import (
    ComponentProposal,
    DirectorEventProposal,
    DPEventProposal,
    EventRecord,
    ProjectedComponent,
    ReleaseArtifact,
    VisualEventProposal,
    _mint_projected_component,
    _rehydrate_finished_cut_release,
    _seal_finished_cut_release,
)
from agents.brook.script_video.finished_cut_production._release import (
    FinishedCutReleaseLifecycle,
)
from agents.brook.script_video.finished_cut_production._semantic import (
    InMemorySemanticAdapter,
)
from agents.brook.script_video.finished_cut_production._store import (
    InMemoryApprovedCutStore,
    InMemoryCurrentReleaseIndex,
)


def _approved_cut(*, format: str = "long") -> ApprovedCutCommand:
    return ApprovedCutCommand(
        command_id="approved-cut:0123456789abcdef0123456789abcdef",
        episode_id="episode-1",
        cut_id="cut-1",
        format=format,
        editorial_master_id="master-1",
        winner_id="winner-1",
        tight_cut_id="tight-1",
    )


def _asset(
    name: str,
    *,
    kind: AssetKind = AssetKind.STOCK,
    extension: str = ".mp4",
) -> AssetRecord:
    return AssetRecord(
        digest=hashlib.sha256(name.encode()).hexdigest(),
        extension=extension,
        kind=kind,
        visual_summary=f"Neutral acquisition media for {name}",
        width=1920,
        height=1080,
        duration_sec=None if kind is AssetKind.PHOTO else 12.0,
    )


def _editorial_context(*, format: str = "long", duration_sec: float = 540.0) -> EditorialCutContext:
    return EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format=format,
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=duration_sec,
        source_ranges=(CutSourceRange(0.0, duration_sec),),
        cues=(
            CueAnchor("cue-context-1", "第一句正確逐字稿", 12.0, 14.5, "section-1"),
            CueAnchor("cue-context-2", "第二句正確逐字稿", 14.5, 17.0, "section-1"),
        ),
        sections=(CanonicalSection("section-1", "第一章", 0.0),),
    )


def _current_request(
    production: FinishedCutProduction,
    semantic: InMemorySemanticAdapter,
    command_id: str,
):
    observed = None
    for _ in range(3):
        view = production.advance(command_id)
        try:
            observed = semantic.current_request(command_id)
        except KeyError:
            continue
        if view.current_stage == observed.stage:
            return observed
    assert observed is not None
    return observed


def _run_authority(production: FinishedCutProduction, command_id: str):
    stored = production._store.load_run(command_id)
    assert stored is not None
    return stored.view


class _PendingDerivedAssetBuilder:
    def __init__(self) -> None:
        self.requests: list[DerivedAssetBuildRequest] = []

    def build(self, request: DerivedAssetBuildRequest) -> DerivedAssetBuildResult:
        self.requests.append(request)
        return DerivedAssetBuildResult(
            build_request_id=request.build_request_id,
            dp_acceptance_id=request.dp_acceptance_id,
            status="pending",
        )


class _FailedDerivedAssetBuilder:
    def __init__(self) -> None:
        self.requests: list[DerivedAssetBuildRequest] = []

    def build(self, request: DerivedAssetBuildRequest) -> DerivedAssetBuildResult:
        self.requests.append(request)
        return DerivedAssetBuildResult(
            build_request_id=request.build_request_id,
            dp_acceptance_id=request.dp_acceptance_id,
            status="failed",
            error_code="renderer_failed",
        )


class _ReadyDerivedAssetBuilder:
    def __init__(self, resolver: InMemoryAssetResolver) -> None:
        self.resolver = resolver
        self.requests: list[DerivedAssetBuildRequest] = []

    def build(self, request: DerivedAssetBuildRequest) -> DerivedAssetBuildResult:
        self.requests.append(request)
        assets: list[BuiltComponentAsset] = []
        for instruction in request.instructions:
            if instruction.recipe_identity is None:
                assert instruction.source_asset_ref is not None
                final_ref = instruction.source_asset_ref
            else:
                kind = {
                    "fullscreen_transition": AssetKind.CHAPTER_RENDER,
                    "hero_title": AssetKind.TITLE_RENDER,
                    "person_inset": AssetKind.COMPOSITE,
                    "identity_card": AssetKind.CONCEPT_RENDER,
                    "visual_effect": AssetKind.CONCEPT_RENDER,
                }[instruction.implementation_kind]
                record = AssetRecord(
                    digest=hashlib.sha256(instruction.recipe_identity.encode()).hexdigest(),
                    extension=".mov",
                    kind=kind,
                    recipe_identity=instruction.recipe_identity,
                )
                self.resolver.publish_derived(record)
                final_ref = record.reference
            assets.append(
                BuiltComponentAsset(
                    component_id=instruction.component_id,
                    event_id=instruction.event_id,
                    source_asset_ref=instruction.source_asset_ref,
                    final_asset_ref=final_ref,
                    inspection_ref=final_ref,
                    recipe_identity=instruction.recipe_identity,
                )
            )
        return DerivedAssetBuildResult(
            build_request_id=request.build_request_id,
            dp_acceptance_id=request.dp_acceptance_id,
            status="ready",
            assets=tuple(assets),
        )


class _ForgedDerivedAssetBuilder:
    def build(self, request: DerivedAssetBuildRequest) -> DerivedAssetBuildResult:
        instruction = request.instructions[0]
        forged = "asset-sha256:" + "f" * 64
        return DerivedAssetBuildResult(
            build_request_id=request.build_request_id,
            dp_acceptance_id=request.dp_acceptance_id,
            status="ready",
            assets=(
                BuiltComponentAsset(
                    component_id=instruction.component_id,
                    event_id=instruction.event_id,
                    source_asset_ref=instruction.source_asset_ref,
                    final_asset_ref=forged,
                    inspection_ref=forged,
                    recipe_identity=instruction.recipe_identity,
                ),
            ),
        )


def test_director_anchor_authority_is_derived_only_from_editorial_cut_context(
    tmp_path,
) -> None:
    context = _editorial_context()
    semantic = InMemorySemanticAdapter()
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )
    waiting = production.advance(_approved_cut().command_id)
    director_request = _current_request(production, semantic, waiting.command_id)
    assert director_request.editorial_context == context
    with pytest.raises(TypeError, match="unexpected keyword argument 'text_hash'"):
        DirectorEventProposal(
            event_id="event-context",
            master_cue_ids=("cue-context-1", "cue-context-2"),
            intent="Explain the current claim",
            display="目前論點",
            semantic_kind="identity_card",
            text_hash="worker-old-hash",
            t0=99.0,
            t1=100.0,
            section_id="worker-old-section",
        )
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                event_id="event-context",
                master_cue_ids=("cue-context-1", "cue-context-2"),
                intent="Explain the current claim",
                display="目前論點",
                semantic_kind="identity_card",
            ),
        ),
    )

    dp_wait = production.advance(_approved_cut().command_id)
    dp_request = _current_request(production, semantic, dp_wait.command_id)

    derived = dp_request.events[0]
    anchor = context.derive_anchor(("cue-context-1", "cue-context-2"))
    assert (
        derived.text,
        derived.text_hash,
        derived.t0,
        derived.t1,
        derived.section_id,
    ) == (anchor.text, anchor.text_hash, anchor.t0, anchor.t1, anchor.section_id)


def test_retired_supporting_title_worker_response_fails_closed(tmp_path) -> None:
    command = _approved_cut()
    semantic = InMemorySemanticAdapter()
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-retired-supporting-title",
        approved_cut_store=InMemoryApprovedCutStore((command,)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver((_editorial_context(),)),
    )
    production.advance(command.command_id)
    director_request = _current_request(production, semantic, command.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                event_id="retired-support",
                master_cue_ids=("cue-context-1",),
                intent="Add a secondary title",
                display="已退役的補充字卡",
                semantic_kind="supporting_title",
            ),
        ),
    )

    rejected = production.advance(command.command_id)
    authority = _run_authority(production, command.command_id)

    assert (rejected.status, rejected.current_stage) == ("needs_review", "director")
    assert authority.accepted_stages == ()
    assert authority.outstanding_request == director_request
    assert authority.materialization_plan is None


def test_historical_supporting_title_release_cannot_authorize_a_revision(tmp_path) -> None:
    artifact = ReleaseArtifact(path="historical", bytes=1, sha256="a" * 64)
    historical = _rehydrate_finished_cut_release(
        release_id="release-historical-support",
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        command_id="approved-cut-historical",
        run_id="run-historical",
        editorial_master_id="master-1",
        winner_id="winner-1",
        tight_cut_id="tight-1",
        director_acceptance_id="director-historical",
        dp_acceptance_id="dp-historical",
        visual_acceptance_id="visual-historical",
        materialization_plan_id="plan-historical",
        events=(
            EventRecord(
                event_id="historical-support",
                master_cue_ids=("cue-context-1",),
                text_hash="b" * 64,
                intent="historical only",
                semantic_kind="supporting_title",
                implementation_kind="supporting_title",
                lane="supporting_title",  # type: ignore[arg-type]
            ),
        ),
        preview=artifact,
        subtitle=artifact,
        transaction_receipt_id="receipt-historical",
        rollback_ref="rollback-historical",
        components=(),
    )
    current = InMemoryCurrentReleaseIndex()
    current.publish((historical,))
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-historical-read-only",
        approved_cut_store=InMemoryApprovedCutStore(()),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
        current_release_index=current,
    )

    with pytest.raises(CommandRejectedError, match="historical Release is read-only"):
        production.request_revision(
            historical.release_id,
            "historical-support",
            "Do not reuse the retired card",
        )


def test_dp_visual_placement_is_distinct_from_director_semantic_evidence(tmp_path) -> None:
    command = _approved_cut(format="short")
    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format="short",
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=60.0,
        source_ranges=(CutSourceRange(0.0, 60.0),),
        cues=(
            CueAnchor("cue-long-1", "先交代完整背景", 5.0, 25.0, "section-1"),
            CueAnchor("cue-long-2", "再說明主要論點", 25.0, 46.0, "section-1"),
            CueAnchor("cue-visual", "最後落在一句短結論", 46.0, 50.0, "section-1"),
        ),
        sections=(CanonicalSection("section-1", "完整論點", 0.0),),
    )
    semantic = InMemorySemanticAdapter()
    builder = _PendingDerivedAssetBuilder()
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-placement",
        approved_cut_store=InMemoryApprovedCutStore((command,)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        derived_asset_builder=builder,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )
    production.advance(command.command_id)
    director_request = _current_request(production, semantic, command.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-claim",
                ("cue-long-1", "cue-long-2", "cue-visual"),
                "Preserve the complete claim as semantic evidence",
                "一句短結論",
                "identity_card",
            ),
        ),
    )
    production.advance(command.command_id)
    dp_request = _current_request(production, semantic, command.command_id)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "event-claim",
                "identity_card",
                "identity_card",
                placement_cue_ids=("cue-visual",),
            ),
        ),
    )

    production.advance(command.command_id)
    checkpoint = production.inspect_run(command.command_id)
    production.advance(command.command_id)

    director_event = checkpoint.current_stages[0].events[0]
    dp_event = checkpoint.current_stages[1].events[0]
    assert (director_event.t0, director_event.t1) == (5.0, 50.0)
    assert (dp_event.t0, dp_event.t1) == (5.0, 50.0)
    assert (
        dp_event.placement_cue_ids,
        dp_event.placement_t0,
        dp_event.placement_t1,
    ) == (("cue-visual",), 46.0, 50.0)
    assert len(builder.requests) == 1
    assert (builder.requests[0].instructions[0].t0, builder.requests[0].instructions[0].t1) == (
        46.0,
        50.0,
    )


def test_chapter_visual_placement_uses_canonical_section_window_not_cue_time(tmp_path) -> None:
    command = _approved_cut(format="short")
    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format="short",
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=45.0,
        source_ranges=(CutSourceRange(0.0, 45.0),),
        cues=(CueAnchor("cue-chapter", "下一個章節", 10.0, 12.0, "section-2"),),
        sections=(
            CanonicalSection(
                "section-2",
                "下一個章節",
                10.522,
                transition_before=True,
                transition_title="下一個章節",
            ),
        ),
    )
    semantic = InMemorySemanticAdapter()
    builder = _PendingDerivedAssetBuilder()
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-chapter-placement",
        approved_cut_store=InMemoryApprovedCutStore((command,)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        derived_asset_builder=builder,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )
    production.advance(command.command_id)
    director_request = _current_request(production, semantic, command.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "chapter-2",
                ("cue-chapter",),
                "Open the canonical chapter",
                "下一個章節",
                "chapter",
            ),
        ),
    )
    production.advance(command.command_id)
    dp_request = _current_request(production, semantic, command.command_id)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "chapter-2",
                "fullscreen_transition",
                "fullscreen_transition",
                placement_cue_ids=("cue-chapter",),
            ),
        ),
    )

    production.advance(command.command_id)
    checkpoint = production.inspect_run(command.command_id)
    production.advance(command.command_id)

    event = checkpoint.current_stages[1].events[0]
    assert (event.t0, event.t1) == (10.0, 12.0)
    assert (event.placement_t0, event.placement_t1) == (10.522, 13.522)
    assert (builder.requests[0].instructions[0].t0, builder.requests[0].instructions[0].t1) == (
        10.522,
        13.522,
    )


def test_chapter_visual_placement_requires_a_canonical_transition_section() -> None:
    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=540.0,
        source_ranges=(CutSourceRange(0.0, 540.0),),
        cues=(CueAnchor("cue-opening", "開場", 0.0, 2.0, "section-opening"),),
        sections=(CanonicalSection("section-opening", "開場", 0.0),),
    )

    with pytest.raises(ValueError, match="canonical transition section"):
        context.derive_visual_placement(
            semantic_cue_ids=("cue-opening",),
            placement_cue_ids=("cue-opening",),
            semantic_kind="chapter",
        )


def test_chapter_visual_placement_rejects_semantic_proof_from_mid_section() -> None:
    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=300.0,
        source_ranges=(CutSourceRange(0.0, 300.0),),
        cues=(
            CueAnchor("cue-0100", "章節第一句", 232.0, 234.0, "section-agency"),
            CueAnchor("cue-0108", "章節中段論點", 244.333, 247.0, "section-agency"),
        ),
        sections=(
            CanonicalSection(
                "section-agency",
                "Agency 與自主權",
                232.073,
                transition_before=True,
            ),
        ),
    )

    with pytest.raises(ValueError, match="first current cue"):
        context.derive_visual_placement(
            semantic_cue_ids=("cue-0108",),
            placement_cue_ids=("cue-0108",),
            semantic_kind="chapter",
        )


@pytest.mark.parametrize(
    ("placement_cue_ids", "message"),
    (
        (("cue-outside",), "subset"),
        (("cue-3", "cue-cross-section"), "cross canonical sections"),
        (("cue-1", "cue-3"), "unique, ordered, and contiguous"),
        (("cue-2", "cue-2"), "unique, ordered, and contiguous"),
    ),
)
def test_visual_placement_rejects_invalid_cue_authority(
    placement_cue_ids: tuple[str, ...],
    message: str,
) -> None:
    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=60.0,
        source_ranges=(CutSourceRange(0.0, 60.0),),
        cues=(
            CueAnchor("cue-1", "第一句", 1.0, 3.0, "section-1"),
            CueAnchor("cue-2", "第二句", 3.0, 5.0, "section-1"),
            CueAnchor("cue-3", "第三句", 5.0, 7.0, "section-1"),
            CueAnchor("cue-cross-section", "下一章", 7.0, 9.0, "section-2"),
            CueAnchor("cue-outside", "章外證據", 9.0, 11.0, "section-2"),
        ),
        sections=(
            CanonicalSection("section-1", "第一章", 0.0),
            CanonicalSection("section-2", "第二章", 7.0),
        ),
    )

    with pytest.raises(ValueError, match=message):
        context.derive_visual_placement(
            semantic_cue_ids=("cue-1", "cue-2", "cue-3"),
            placement_cue_ids=placement_cue_ids,
            semantic_kind="b_roll",
        )


def test_production_run_persists_exact_editorial_context_as_run_authority(tmp_path) -> None:
    context = _editorial_context()
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )

    production.advance(_approved_cut().command_id)
    stored = production._store.load_run(_approved_cut().command_id)

    assert stored is not None
    assert stored.view.editorial_context == context


def test_restart_uses_persisted_run_context_without_reresolving_same_cut(tmp_path) -> None:
    context = _editorial_context()
    store_root = tmp_path / "finished-cut-authority"
    first_process = FinishedCutProduction(
        store_root=store_root,
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )
    before_restart = first_process.advance(_approved_cut().command_id)
    restarted = FinishedCutProduction(
        store_root=store_root,
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
        context_resolver=InMemoryEditorialCutContextResolver(()),
    )

    after_restart = restarted.advance(_approved_cut().command_id)

    assert after_restart == before_restart
    stored = restarted._store.load_run(_approved_cut().command_id)
    assert stored is not None
    assert stored.view.editorial_context == context


def test_old_wire_pending_director_run_reopens_without_minting_new_authority(tmp_path) -> None:
    context = _editorial_context()
    store_root = tmp_path / "finished-cut-old-wire"
    approved = InMemoryApprovedCutStore((_approved_cut(),))
    first = FinishedCutProduction(
        store_root=store_root,
        approved_cut_store=approved,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )
    before = first.advance(_approved_cut().command_id)
    authority_path = store_root / "authority.json"
    document = json.loads(authority_path.read_text(encoding="utf-8"))
    persisted = document["runs"][_approved_cut().command_id]["view"]
    del persisted["outstanding_request"]["placement_candidates"]
    authority_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    reopened = FinishedCutProduction(
        store_root=store_root,
        approved_cut_store=approved,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
        context_resolver=InMemoryEditorialCutContextResolver(()),
    )

    observed = reopened.inspect_run(_approved_cut().command_id)
    stored = reopened._store.load_run(_approved_cut().command_id)

    assert (observed.run_id, observed.status, observed.outstanding_stage) == (
        before.run_id,
        "pending",
        "director",
    )
    assert observed.current_stages == ()
    assert stored is not None
    assert stored.view.outstanding_request is not None
    assert stored.view.outstanding_request.placement_candidates == ()


def test_old_wire_failed_run_does_not_block_a_fresh_command_in_same_runtime(tmp_path) -> None:
    old_command = _approved_cut()
    fresh_command = ApprovedCutCommand(
        command_id="approved-cut:fedcba9876543210fedcba9876543210",
        episode_id=old_command.episode_id,
        cut_id=old_command.cut_id,
        format=old_command.format,
        editorial_master_id=old_command.editorial_master_id,
        winner_id=old_command.winner_id,
        tight_cut_id=old_command.tight_cut_id,
    )
    context = _editorial_context()
    store_root = tmp_path / "finished-cut-old-wire-failed"
    semantic = InMemorySemanticAdapter()
    first = FinishedCutProduction(
        store_root=store_root,
        approved_cut_store=InMemoryApprovedCutStore((old_command,)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )
    first.advance(old_command.command_id)
    old_request = _current_request(first, semantic, old_command.command_id)
    semantic.respond(
        old_request,
        events=(
            DirectorEventProposal(
                "event-invalid",
                ("cue-not-current",),
                "Invalid old proposal",
                "舊失敗提案",
                "hero_title",
            ),
        ),
    )
    assert first.advance(old_command.command_id).status == "needs_review"
    authority_path = store_root / "authority.json"
    document = json.loads(authority_path.read_text(encoding="utf-8"))
    old_wire = document["runs"][old_command.command_id]["view"]
    del old_wire["outstanding_request"]["placement_candidates"]
    authority_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    reopened = FinishedCutProduction(
        store_root=store_root,
        approved_cut_store=InMemoryApprovedCutStore((old_command, fresh_command)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )

    old_observed = reopened.inspect_run(old_command.command_id)
    fresh_observed = reopened.advance(fresh_command.command_id)

    assert old_observed.status == "needs_review"
    assert old_observed.current_stages == ()
    assert fresh_observed.status == "pending"
    assert fresh_observed.current_stage == "director"
    assert fresh_observed.run_id != old_observed.run_id


def test_dp_owns_implementation_lane_and_current_catalog_asset(tmp_path) -> None:
    context = _editorial_context()
    semantic = InMemorySemanticAdapter()
    stock = _asset("current-stock")
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver((stock,)),
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )
    director_wait = production.advance(_approved_cut().command_id)
    director_request = _current_request(production, semantic, director_wait.command_id)
    with pytest.raises(TypeError, match="unexpected keyword argument 'implementation_kind'"):
        DirectorEventProposal(
            event_id="event-stock",
            master_cue_ids=("cue-context-1",),
            intent="Show a concrete example",
            display="忙碌的工作現場",
            semantic_kind="b_roll",
            implementation_kind="stock_video",
            lane="b_roll",
            asset_ref=stock.reference,
        )
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                event_id="event-stock",
                master_cue_ids=("cue-context-1",),
                intent="Show a concrete example",
                display="忙碌的工作現場",
                semantic_kind="b_roll",
            ),
        ),
    )
    dp_wait = production.advance(_approved_cut().command_id)
    dp_request = _current_request(production, semantic, dp_wait.command_id)
    director_authority = dp_request.events[0]
    assert director_authority.asset_ref is None
    assert director_authority.implementation_kind == ""
    assert director_authority.lane is None
    assert tuple(item.reference for item in dp_request.worker_catalog_items) == (stock.reference,)
    assert dp_request.worker_catalog_items[0].visual_summary == stock.visual_summary
    assert (
        dp_request.worker_catalog_items[0].width,
        dp_request.worker_catalog_items[0].height,
    ) == (
        1920,
        1080,
    )
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                event_id="event-stock",
                implementation_kind="stock_video",
                lane="b_roll",
                asset_ref=stock.reference,
                placement_cue_ids=("cue-context-1",),
            ),
        ),
    )

    visual_wait = production.advance(_approved_cut().command_id)
    visual_request = _current_request(production, semantic, visual_wait.command_id)

    selected = visual_request.events[0]
    assert (
        selected.implementation_kind,
        selected.lane,
        selected.asset_ref,
    ) == ("stock_video", "b_roll", stock.reference)
    assert (selected.t0, selected.t1) == (12.0, 14.5)


def test_assetless_title_stops_at_derived_build_before_visual_or_plan(tmp_path) -> None:
    command = _approved_cut(format="short")
    context = _editorial_context(format="short", duration_sec=45.0)
    semantic = InMemorySemanticAdapter()
    builder = _PendingDerivedAssetBuilder()
    approved_cuts = InMemoryApprovedCutStore((command,))
    resolver = InMemoryAssetResolver(())
    store_root = tmp_path / "finished-cut-authority"
    production = FinishedCutProduction(
        store_root=store_root,
        approved_cut_store=approved_cuts,
        asset_resolver=resolver,
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
        derived_asset_builder=builder,
    )
    director_wait = production.advance(command.command_id)
    director_request = _current_request(production, semantic, director_wait.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-title",
                ("cue-context-1",),
                "State the contextual claim",
                "下一個黃金年代是什麼？",
                "hero_title",
            ),
        ),
    )
    dp_wait = production.advance(command.command_id)
    dp_request = _current_request(production, semantic, dp_wait.command_id)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "event-title",
                "hero_title",
                "hero_title",
                None,
                ("cue-context-1",),
            ),
        ),
    )

    build_wait = production.advance(command.command_id)
    assert build_wait.status == "pending"
    assert builder.requests == []
    restarted = FinishedCutProduction(
        store_root=store_root,
        approved_cut_store=approved_cuts,
        asset_resolver=resolver,
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
        derived_asset_builder=builder,
    )
    stopped = restarted.advance(command.command_id)
    authority = _run_authority(restarted, command.command_id)

    assert stopped.status == "needs_review"
    assert stopped.current_stage is None
    assert authority.materialization_plan is None
    assert len(authority.accepted_stages) == 2
    assert authority.derived_asset_request == builder.requests[0]
    assert builder.requests[0].instructions[0].recipe_identity is not None
    assert semantic.current_request(command.command_id) == dp_request


def test_failed_build_stays_in_review_without_calling_visual_or_rerunning_dp(tmp_path) -> None:
    command = _approved_cut(format="short")
    context = _editorial_context(format="short", duration_sec=45.0)
    semantic = InMemorySemanticAdapter()
    builder = _FailedDerivedAssetBuilder()
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore((command,)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
        derived_asset_builder=builder,
    )
    production.advance(command.command_id)
    director_request = _current_request(production, semantic, command.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-title",
                ("cue-context-1",),
                "State the contextual claim",
                "Purpose / Calling",
                "hero_title",
            ),
        ),
    )
    production.advance(command.command_id)
    dp_request = _current_request(production, semantic, command.command_id)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "event-title",
                "hero_title",
                "hero_title",
                None,
                ("cue-context-1",),
            ),
        ),
    )
    production.advance(command.command_id)

    stopped = production.advance(command.command_id)
    authority = _run_authority(production, command.command_id)

    assert (stopped.status, stopped.current_stage) == ("needs_review", None)
    assert len(builder.requests) == 1
    assert len(authority.accepted_stages) == 2
    assert authority.materialization_plan is None
    assert semantic.current_request(command.command_id) == dp_request


def test_visual_receives_the_exact_built_final_and_inspection_references(tmp_path) -> None:
    command = _approved_cut(format="short")
    context = _editorial_context(format="short", duration_sec=45.0)
    semantic = InMemorySemanticAdapter()
    resolver = InMemoryAssetResolver(())
    builder = _ReadyDerivedAssetBuilder(resolver)
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore((command,)),
        asset_resolver=resolver,
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
        derived_asset_builder=builder,
    )
    director_wait = production.advance(command.command_id)
    director_request = _current_request(production, semantic, director_wait.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-title",
                ("cue-context-1",),
                "State the contextual claim",
                "K 型發展的兩個面向",
                "hero_title",
            ),
        ),
    )
    dp_wait = production.advance(command.command_id)
    dp_request = _current_request(production, semantic, dp_wait.command_id)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "event-title",
                "hero_title",
                "hero_title",
                None,
                ("cue-context-1",),
            ),
        ),
    )
    build_wait = production.advance(command.command_id)
    assert build_wait.current_stage is None

    visual_wait = production.advance(command.command_id)
    visual_request = _current_request(production, semantic, visual_wait.command_id)

    assert visual_request.stage == "visual_review"
    assert len(builder.requests) == 1
    built = visual_request.built_components[0]
    assert built.final_asset_ref == built.inspection_ref
    assert built.recipe_identity == builder.requests[0].instructions[0].recipe_identity
    assert resolver.resolve_active_asset(built.final_asset_ref).record.recipe_identity == (
        builder.requests[0].instructions[0].recipe_identity
    )
    semantic.respond(
        visual_request,
        events=(VisualEventProposal("event-title", "approved"),),
    )

    checkpoint = production.advance(command.command_id)
    assert checkpoint.status == "pending"
    ready = production.advance(command.command_id)
    plan = _run_authority(production, command.command_id).materialization_plan

    assert ready.status == "review_ready"
    assert plan is not None
    assert tuple(component.asset_ref for component in plan.components) == (built.final_asset_ref,)


def test_well_formed_but_unpublished_final_ref_never_reaches_visual(tmp_path) -> None:
    command = _approved_cut(format="short")
    context = _editorial_context(format="short", duration_sec=45.0)
    semantic = InMemorySemanticAdapter()
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore((command,)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
        derived_asset_builder=_ForgedDerivedAssetBuilder(),
    )
    director_wait = production.advance(command.command_id)
    director_request = _current_request(production, semantic, director_wait.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-title",
                ("cue-context-1",),
                "State the contextual claim",
                "Agency 自主權",
                "identity_card",
            ),
        ),
    )
    dp_wait = production.advance(command.command_id)
    dp_request = _current_request(production, semantic, dp_wait.command_id)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "event-title",
                "identity_card",
                "identity_card",
                None,
                ("cue-context-1",),
            ),
        ),
    )
    production.advance(command.command_id)

    rejected = production.advance(command.command_id)

    assert rejected.status == "needs_review"
    assert rejected.current_stage is None
    assert _run_authority(production, command.command_id).materialization_plan is None
    assert semantic.current_request(command.command_id) == dp_request


def test_visual_only_approves_exact_dp_selection_and_core_projects_it(tmp_path) -> None:
    command = _approved_cut(format="short")
    context = _editorial_context(format="short", duration_sec=45.0)
    semantic = InMemorySemanticAdapter()
    stock = _asset("visual-stock")
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore((command,)),
        asset_resolver=InMemoryAssetResolver((stock,)),
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )
    director_wait = production.advance(command.command_id)
    director_request = _current_request(production, semantic, director_wait.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-stock",
                ("cue-context-1",),
                "Show a concrete example",
                "忙碌的工作現場",
                "b_roll",
            ),
        ),
    )
    dp_wait = production.advance(command.command_id)
    dp_request = _current_request(production, semantic, dp_wait.command_id)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "event-stock",
                "stock_video",
                "b_roll",
                stock.reference,
                ("cue-context-1",),
            ),
        ),
    )
    visual_wait = production.advance(command.command_id)
    visual_request = _current_request(production, semantic, visual_wait.command_id)
    with pytest.raises(TypeError, match="unexpected keyword argument 'asset_ref'"):
        VisualEventProposal(
            event_id="event-stock",
            status="approved",
            asset_ref="worker-overreach",
        )
    semantic.respond(
        visual_request,
        events=(VisualEventProposal("event-stock", "approved"),),
    )

    checkpoint = production.advance(command.command_id)
    assert checkpoint.status == "pending"
    ready = production.advance(command.command_id)
    authority = _run_authority(production, command.command_id)

    assert ready.status == "review_ready"
    assert authority.materialization_plan is not None
    assert tuple(
        (
            component.event_id,
            component.semantic_kind,
            component.implementation_kind,
            component.lane,
            component.display,
            component.t0,
            component.t1,
            component.asset_ref,
        )
        for component in authority.materialization_plan.components
    ) == (
        (
            "event-stock",
            "b_roll",
            "stock_video",
            "b_roll",
            "忙碌的工作現場",
            12.0,
            14.5,
            stock.reference,
        ),
    )


def test_intentional_aroll_needs_no_asset_and_projects_no_component(tmp_path) -> None:
    command = _approved_cut(format="short")
    context = _editorial_context(format="short", duration_sec=45.0)
    semantic = InMemorySemanticAdapter()
    builder = _PendingDerivedAssetBuilder()
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore((command,)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        derived_asset_builder=builder,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )
    director_wait = production.advance(command.command_id)
    director_request = _current_request(production, semantic, director_wait.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-aroll",
                ("cue-context-1",),
                "Keep the speaker on screen for this personal statement",
                "保留講者原畫面",
                "intentional_aroll",
                intentional_aroll=True,
            ),
        ),
    )
    dp_wait = production.advance(command.command_id)
    dp_request = _current_request(production, semantic, dp_wait.command_id)
    semantic.respond(
        dp_request,
        events=(DPEventProposal("event-aroll", "intentional_aroll", None, None),),
    )
    visual_wait = production.advance(command.command_id)
    visual_request = _current_request(production, semantic, visual_wait.command_id)
    semantic.respond(
        visual_request,
        events=(VisualEventProposal("event-aroll", "approved"),),
    )

    checkpoint = production.advance(command.command_id)
    assert checkpoint.status == "pending"
    ready = production.advance(command.command_id)
    authority = _run_authority(production, command.command_id)

    assert ready.status == "review_ready"
    assert authority.materialization_plan is not None
    assert authority.materialization_plan.events[0].asset_ref is None
    assert authority.materialization_plan.components == ()
    assert builder.requests == []


def test_four_minute_nineteen_second_long_stops_before_plan_without_rerun(
    tmp_path,
) -> None:
    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=259.0,
        source_ranges=(CutSourceRange(0.0, 259.0),),
        cues=(CueAnchor("cue-short-long", "完整論點", 0.0, 3.0, "section-1"),),
        sections=(CanonicalSection("section-1", "完整論點", 0.0),),
    )
    semantic = InMemorySemanticAdapter()
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )
    director_wait = production.advance(_approved_cut().command_id)
    director_request = _current_request(production, semantic, director_wait.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "event-aroll",
                ("cue-short-long",),
                "Keep the intentional speaker beat",
                "保留講者",
                "intentional_aroll",
                intentional_aroll=True,
            ),
        ),
    )
    dp_wait = production.advance(_approved_cut().command_id)
    dp_request = _current_request(production, semantic, dp_wait.command_id)
    semantic.respond(
        dp_request,
        events=(DPEventProposal("event-aroll", "intentional_aroll", None),),
    )
    visual_wait = production.advance(_approved_cut().command_id)
    visual_request = _current_request(production, semantic, visual_wait.command_id)
    semantic.respond(
        visual_request,
        events=(VisualEventProposal("event-aroll", "approved"),),
    )

    checkpoint = production.advance(_approved_cut().command_id)
    assert checkpoint.status == "pending"
    rejected = production.advance(_approved_cut().command_id)
    unchanged = production.advance(_approved_cut().command_id)
    authority = _run_authority(production, rejected.command_id)

    assert rejected.status == "needs_review"
    assert authority.materialization_plan is None
    assert authority.outstanding_request is None
    assert len(authority.accepted_stages) == 3
    assert tuple(item.code for item in authority.policy_diagnostics) == (
        "long_duration_below_minimum",
    )
    assert unchanged == rejected


def test_good_long_chain_passes_long_policy_before_plan_mint(tmp_path) -> None:
    rows = (
        ("stock-1", 0.0, "section-1", "b_roll", "Stock 1"),
        ("stock-2", 60.0, "section-1", "b_roll", "Stock 2"),
        ("stock-3", 120.0, "section-1", "b_roll", "Stock 3"),
        ("chapter-2", 180.0, "section-2", "chapter", "第二章"),
        ("clip-180", 180.0, "section-2", "b_roll", "Chapter example"),
        ("clip-240", 240.0, "section-2", "b_roll", "Concrete example"),
        ("photo-300", 300.0, "section-2", "b_roll", "Workplace"),
        ("chapter-3", 360.0, "section-3", "chapter", "第三章"),
        ("clip-360", 360.0, "section-3", "b_roll", "Chapter example"),
        ("person-420", 420.0, "section-3", "b_roll", "Expert"),
        ("clip-480", 480.0, "section-3", "b_roll", "Closing example"),
    )
    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=540.0,
        source_ranges=(CutSourceRange(0.0, 540.0),),
        cues=tuple(
            CueAnchor(f"cue-{event_id}", display, t0, t0 + 10.0, section_id)
            for event_id, t0, section_id, _semantic_kind, display in rows
        ),
        sections=(
            CanonicalSection("section-1", "開場", 0.0),
            CanonicalSection(
                "section-2",
                "第二章",
                180.0,
                transition_before=True,
                transition_title="第二章",
            ),
            CanonicalSection(
                "section-3",
                "第三章",
                360.0,
                transition_before=True,
                transition_title="第三章",
            ),
        ),
    )
    assets = {
        "stock-1": _asset("long-stock-1"),
        "stock-2": _asset("long-stock-2"),
        "stock-3": _asset("long-stock-3"),
        "clip-180": _asset("long-clip-180", kind=AssetKind.NON_EDITORIAL_CLIP),
        "clip-240": _asset("long-clip-240", kind=AssetKind.NON_EDITORIAL_CLIP),
        "photo-300": _asset("long-photo-300", kind=AssetKind.PHOTO, extension=".jpg"),
        "person-420": _asset("long-person-420", kind=AssetKind.PHOTO, extension=".jpg"),
        "clip-360": _asset("long-clip-360", kind=AssetKind.NON_EDITORIAL_CLIP),
        "clip-480": _asset("long-clip-480", kind=AssetKind.NON_EDITORIAL_CLIP),
    }
    semantic = InMemorySemanticAdapter()
    resolver = InMemoryAssetResolver(assets.values())
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=resolver,
        semantic_adapter=semantic,
        derived_asset_builder=_ReadyDerivedAssetBuilder(resolver),
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
        stock_video_metadata=tuple(
            StockVideoMetadata(assets[event_id].reference, 1920, 1080)
            for event_id in ("stock-1", "stock-2", "stock-3")
        ),
    )
    director_wait = production.advance(_approved_cut().command_id)
    director_request = _current_request(production, semantic, director_wait.command_id)
    semantic.respond(
        director_request,
        events=tuple(
            DirectorEventProposal(
                event_id,
                (f"cue-{event_id}",),
                f"Intent for {event_id}",
                display,
                semantic_kind,
            )
            for event_id, _t0, _section_id, semantic_kind, display in rows
        ),
    )
    dp_wait = production.advance(_approved_cut().command_id)
    dp_request = _current_request(production, semantic, dp_wait.command_id)
    implementations = {
        "stock-1": ("stock_video", "b_roll"),
        "stock-2": ("stock_video", "b_roll"),
        "stock-3": ("stock_video", "b_roll"),
        "chapter-2": ("fullscreen_transition", "fullscreen_transition"),
        "clip-180": ("non_editorial_clip", "b_roll"),
        "clip-240": ("non_editorial_clip", "b_roll"),
        "photo-300": ("photo", "b_roll"),
        "chapter-3": ("fullscreen_transition", "fullscreen_transition"),
        "clip-360": ("non_editorial_clip", "b_roll"),
        "person-420": ("person_inset", "b_roll"),
        "clip-480": ("non_editorial_clip", "b_roll"),
    }
    semantic.respond(
        dp_request,
        events=tuple(
            DPEventProposal(
                event_id,
                implementations[event_id][0],
                implementations[event_id][1],
                assets[event_id].reference if event_id in assets else None,
                (f"cue-{event_id}",),
            )
            for event_id, _t0, _section_id, _semantic_kind, _display in rows
        ),
    )
    visual_wait = production.advance(_approved_cut().command_id)
    visual_request = _current_request(production, semantic, visual_wait.command_id)
    semantic.respond(
        visual_request,
        events=tuple(
            VisualEventProposal(event_id, "approved")
            for event_id, _t0, _section_id, _semantic_kind, _display in rows
        ),
    )

    checkpoint = production.advance(_approved_cut().command_id)
    assert checkpoint.status == "pending"
    ready = production.advance(_approved_cut().command_id)
    authority = _run_authority(production, ready.command_id)

    assert ready.status == "review_ready"
    assert authority.materialization_plan is not None
    assert len(authority.materialization_plan.components) == len(rows)


def test_inspect_current_returns_only_public_immutable_finished_cut_view(tmp_path) -> None:
    artifact = ReleaseArtifact(
        path="highlights/preview/cut-1.mp4",
        bytes=7,
        sha256="a" * 64,
        duration_sec=45.0,
        probe=(("codec", "h264"),),
    )
    event = EventRecord(
        "event-1",
        ("cue-context-1",),
        "b" * 64,
        "Show the example",
        text="正確逐字稿",
        t0=12.0,
        t1=14.5,
        section_id="section-1",
        display="忙碌的工作現場",
        semantic_kind="b_roll",
        implementation_kind="stock_video",
        lane="b_roll",
        asset_ref="asset-sha256:" + "c" * 64,
        visual_status="approved",
    )
    component = _mint_projected_component(
        component_id="component:event-1",
        event_id=event.event_id,
        semantic_kind=event.semantic_kind,
        implementation_kind=event.implementation_kind,
        lane="b_roll",
        display=event.display,
        t0=event.t0,
        t1=event.t1,
        asset_ref=event.asset_ref,
    )
    release = _seal_finished_cut_release(
        release_id="release-1",
        episode_id="episode-1",
        cut_id="cut-1",
        format="short",
        command_id=_approved_cut(format="short").command_id,
        run_id="run-1",
        editorial_master_id="master-1",
        winner_id="winner-1",
        tight_cut_id="tight-1",
        director_acceptance_id="director-1",
        dp_acceptance_id="dp-1",
        visual_acceptance_id="visual-1",
        materialization_plan_id="plan-1",
        events=(event,),
        components=(component,),
        preview=artifact,
        subtitle=artifact,
        transaction_receipt_id="receipt-1",
        rollback_ref="rollback-1",
    )
    current = InMemoryCurrentReleaseIndex()
    current.publish((release,))
    production = FinishedCutProduction(
        store_root=tmp_path / "authority",
        approved_cut_store=InMemoryApprovedCutStore(()),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
        current_release_index=current,
    )

    view = production.inspect_current("episode-1")

    assert isinstance(view, FinishedCutInspection)
    assert view.episode_id == "episode-1"
    assert view.state == "ready"
    assert view.error_code is None
    assert len(view.cuts) == 1
    assert view.cuts[0].preview.reference == artifact.path
    assert view.cuts[0].events[0].text == event.text
    assert view.cuts[0].components[0].lane == "b_roll"
    with pytest.raises(FrozenInstanceError):
        view.cuts[0].cut_id = "forged"  # type: ignore[misc]


def test_inspect_current_returns_typed_missing_and_invalid_results(tmp_path) -> None:
    class Transactions:
        def inspect_transaction(self, transaction_id: str) -> dict[str, object]:
            return {}

    def lifecycle(episode_root):
        return FinishedCutReleaseLifecycle(
            episode_root,
            transactions=Transactions(),
            preview_probe=lambda _path: {},
        )

    common = {
        "store_root": tmp_path / "authority",
        "approved_cut_store": InMemoryApprovedCutStore(()),
        "asset_resolver": InMemoryAssetResolver(()),
        "semantic_adapter": InMemorySemanticAdapter(),
    }
    missing = FinishedCutProduction(
        **common,
        current_release_index=lifecycle(tmp_path / "missing-episode"),
    ).inspect_current("episode-1")

    corrupt_root = tmp_path / "corrupt-episode"
    corrupt_manifest = (
        corrupt_root / "highlights" / "review" / "finished_review_manifest_current.json"
    )
    corrupt_manifest.parent.mkdir(parents=True)
    corrupt_manifest.write_text("{", encoding="utf-8")
    invalid = FinishedCutProduction(
        **common,
        current_release_index=lifecycle(corrupt_root),
    ).inspect_current("episode-1")

    assert (missing.state, missing.error_code, missing.cuts) == (
        "missing",
        "current_release_missing",
        (),
    )
    assert (invalid.state, invalid.error_code, invalid.cuts) == (
        "invalid",
        "current_release_invalid",
        (),
    )


def test_advance_returns_only_public_immutable_run_view(tmp_path) -> None:
    context = _editorial_context()
    production = FinishedCutProduction(
        store_root=tmp_path / "authority",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )

    view = production.advance(_approved_cut().command_id)

    assert isinstance(view, RunView)
    assert view.current_stage == "director"
    assert not hasattr(view, "outstanding_request")
    assert not hasattr(view, "accepted_stages")
    assert not hasattr(view, "materialization_plan")
    with pytest.raises(FrozenInstanceError):
        view.status = "review_ready"  # type: ignore[misc]


def test_restart_restores_the_same_outstanding_current_run(tmp_path) -> None:
    approved_cuts = InMemoryApprovedCutStore((_approved_cut(),))
    semantic = InMemorySemanticAdapter()
    contexts = InMemoryEditorialCutContextResolver((_editorial_context(),))
    first_process = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=approved_cuts,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=contexts,
    )

    before_restart = first_process.advance(_approved_cut().command_id)
    second_process = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=approved_cuts,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=contexts,
    )

    after_restart = second_process.advance(_approved_cut().command_id)

    assert after_restart == before_restart
    assert after_restart.current_stage == "director"


def test_current_proposal_is_accepted_once_and_survives_restart(tmp_path) -> None:
    approved_cuts = InMemoryApprovedCutStore((_approved_cut(),))
    semantic = InMemorySemanticAdapter()
    contexts = InMemoryEditorialCutContextResolver((_editorial_context(),))
    first_process = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=approved_cuts,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=contexts,
    )
    waiting = first_process.advance(_approved_cut().command_id)
    director_request = _current_request(first_process, semantic, waiting.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "hero-1",
                ("cue-context-1",),
                "Explain the claim",
                "目前論點",
                "hero_title",
            ),
        ),
    )

    second_process = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=approved_cuts,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=contexts,
    )
    accepted = second_process.advance(_approved_cut().command_id)
    third_process = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=approved_cuts,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=contexts,
    )

    after_second_restart = third_process.advance(_approved_cut().command_id)
    authority = _run_authority(third_process, accepted.command_id)

    assert len(authority.accepted_stages) == 1
    assert accepted.current_stage == "dp"
    assert after_second_restart == accepted


def test_unknown_legacy_and_payload_bearing_ids_are_rejected_before_a_run(tmp_path) -> None:
    legacy_command = ApprovedCutCommand(
        command_id="legacy:episode-1:cut-1",
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        editorial_master_id="master-1",
        winner_id="winner-1",
        tight_cut_id="tight-1",
    )
    payload_command = ApprovedCutCommand(
        command_id="approved-cut:ffffffffffffffffffffffffffffffff",
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        editorial_master_id=r"G:\raw\master.mp4",
        winner_id="winner-1",
        tight_cut_id="tight-1",
    )
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore(
            (legacy_command, payload_command, _approved_cut())
        ),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
        context_resolver=InMemoryEditorialCutContextResolver((_editorial_context(),)),
    )

    for rejected_id in (
        "unknown:0123456789abcdef0123456789abcdef",
        legacy_command.command_id,
        '{"command_id":"approved-cut:0123456789abcdef0123456789abcdef"}',
        payload_command.command_id,
    ):
        with pytest.raises(CommandRejectedError, match="opaque authoritative command ID"):
            production.advance(rejected_id)

    valid = production.advance(_approved_cut().command_id)
    assert valid.status == "pending"


def test_missing_editorial_context_is_rejected_before_run_creation(tmp_path) -> None:
    production = FinishedCutProduction(
        store_root=tmp_path / "finished-cut-authority",
        approved_cut_store=InMemoryApprovedCutStore((_approved_cut(),)),
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
    )

    with pytest.raises(CommandRejectedError, match="exact Editorial Cut Context"):
        production.advance(_approved_cut().command_id)

    assert production._store.load_run(_approved_cut().command_id) is None


def test_complete_current_chain_and_plan_survive_every_process_restart(tmp_path) -> None:
    root = tmp_path / "finished-cut-authority"
    command = _approved_cut(format="short")
    context = _editorial_context(format="short", duration_sec=45.0)
    approved_cuts = InMemoryApprovedCutStore((command,))
    semantic = InMemorySemanticAdapter()

    def reopen() -> FinishedCutProduction:
        return FinishedCutProduction(
            store_root=root,
            approved_cut_store=approved_cuts,
            asset_resolver=InMemoryAssetResolver(()),
            semantic_adapter=semantic,
            context_resolver=InMemoryEditorialCutContextResolver((context,)),
        )

    director_process = reopen()
    director_process.advance(command.command_id)
    director_request = _current_request(director_process, semantic, command.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "speaker-beat",
                ("cue-context-1",),
                "Keep the speaker on screen",
                "保留講者",
                "intentional_aroll",
                intentional_aroll=True,
            ),
        ),
    )

    dp_process = reopen()
    dp_process.advance(command.command_id)
    dp_request = _current_request(dp_process, semantic, command.command_id)
    semantic.respond(
        dp_request,
        events=(DPEventProposal("speaker-beat", "intentional_aroll", None),),
    )

    visual_process = reopen()
    visual_process.advance(command.command_id)
    visual_request = _current_request(visual_process, semantic, command.command_id)
    semantic.respond(
        visual_request,
        events=(VisualEventProposal("speaker-beat", "approved"),),
    )

    checkpoint_process = reopen()
    checkpoint = checkpoint_process.advance(command.command_id)
    assert checkpoint.status == "pending"
    ready_process = reopen()
    ready = ready_process.advance(command.command_id)
    final_process = reopen()
    after_final_restart = final_process.advance(command.command_id)

    assert ready.status == "review_ready"
    assert _run_authority(ready_process, ready.command_id).materialization_plan is not None
    assert after_final_restart == ready


def test_interrupted_atomic_write_cannot_publish_an_accepted_stage(tmp_path, monkeypatch) -> None:
    root = tmp_path / "finished-cut-authority"
    approved_cuts = InMemoryApprovedCutStore((_approved_cut(),))
    semantic = InMemorySemanticAdapter()
    contexts = InMemoryEditorialCutContextResolver((_editorial_context(),))
    production = FinishedCutProduction(
        store_root=root,
        approved_cut_store=approved_cuts,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=contexts,
    )
    waiting = production.advance(_approved_cut().command_id)
    director_request = _current_request(production, semantic, waiting.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "hero-1",
                ("cue-context-1",),
                "Explain the claim",
                "目前論點",
                "hero_title",
            ),
        ),
    )

    real_replace = store_module.os.replace

    def crash_before_commit(source, destination) -> None:
        raise OSError("simulated process crash before atomic replace")

    monkeypatch.setattr(store_module.os, "replace", crash_before_commit)
    with pytest.raises(OSError, match="simulated process crash"):
        production.advance(_approved_cut().command_id)
    monkeypatch.setattr(store_module.os, "replace", real_replace)

    restarted = FinishedCutProduction(
        store_root=root,
        approved_cut_store=approved_cuts,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=InMemorySemanticAdapter(),
        context_resolver=contexts,
    )
    after_restart = restarted.advance(_approved_cut().command_id)
    authority = _run_authority(restarted, after_restart.command_id)

    assert authority.accepted_stages == ()
    assert authority.outstanding_request == director_request


def test_core_projection_keeps_chapter_hero_and_support_distinct_after_restart(
    tmp_path,
) -> None:
    root = tmp_path / "finished-cut-authority"
    command = _approved_cut(format="short")
    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format="short",
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=45.0,
        source_ranges=(CutSourceRange(0.0, 45.0),),
        cues=(
            CueAnchor("cue-chapter", "第一章", 0.0, 2.0, "section-1"),
            CueAnchor("cue-hero", "工作不等於使命", 3.0, 6.0, "section-1"),
            CueAnchor("cue-support", "先保留選擇權", 7.0, 9.0, "section-1"),
            CueAnchor("cue-inset", "簡立峰博士", 10.0, 13.0, "section-1"),
        ),
        sections=(
            CanonicalSection(
                "section-1",
                "第一章",
                0.0,
                transition_before=True,
                transition_title="第一章",
            ),
        ),
    )
    approved_cuts = InMemoryApprovedCutStore((command,))
    semantic = InMemorySemanticAdapter()
    inset = _asset("inset", kind=AssetKind.PHOTO, extension=".jpg")
    resolver = InMemoryAssetResolver((inset,))
    builder = _ReadyDerivedAssetBuilder(resolver)

    class ProjectionPolicy:
        def validate(self, candidate):
            return type("Decision", (), {"status": "accepted"})()

    def reopen() -> FinishedCutProduction:
        return FinishedCutProduction(
            store_root=root,
            approved_cut_store=approved_cuts,
            asset_resolver=resolver,
            semantic_adapter=semantic,
            derived_asset_builder=builder,
            context_resolver=InMemoryEditorialCutContextResolver((context,)),
            short_policy=ProjectionPolicy(),
        )

    director_process = reopen()
    director_process.advance(command.command_id)
    director_request = _current_request(director_process, semantic, command.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal("chapter-event", ("cue-chapter",), "Open", "第一章", "chapter"),
            DirectorEventProposal(
                "hero-event", ("cue-hero",), "State thesis", "工作不等於使命", "hero_title"
            ),
            DirectorEventProposal(
                "support-event",
                ("cue-support",),
                "Support detail",
                "先保留選擇權",
                "identity_card",
            ),
            DirectorEventProposal(
                "inset-event", ("cue-inset",), "Show person", "簡立峰博士", "b_roll"
            ),
        ),
    )
    dp_process = reopen()
    dp_process.advance(command.command_id)
    dp_request = _current_request(dp_process, semantic, command.command_id)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal(
                "chapter-event",
                "fullscreen_transition",
                "fullscreen_transition",
                None,
                ("cue-chapter",),
            ),
            DPEventProposal("hero-event", "hero_title", "hero_title", None, ("cue-hero",)),
            DPEventProposal(
                "support-event",
                "identity_card",
                "identity_card",
                None,
                ("cue-support",),
            ),
            DPEventProposal(
                "inset-event", "person_inset", "b_roll", inset.reference, ("cue-inset",)
            ),
        ),
    )
    visual_process = reopen()
    visual_process.advance(command.command_id)
    visual_request = _current_request(visual_process, semantic, command.command_id)
    semantic.respond(
        visual_request,
        events=tuple(
            VisualEventProposal(event_id, "approved")
            for event_id in ("chapter-event", "hero-event", "support-event", "inset-event")
        ),
    )

    ready_process = reopen()
    ready_process.advance(command.command_id)
    after_restart_process = reopen()
    after_restart_process.advance(command.command_id)
    ready = _run_authority(ready_process, command.command_id)
    after_restart = _run_authority(after_restart_process, command.command_id)

    assert ready.materialization_plan is not None
    assert after_restart.materialization_plan is not None
    assert ready.materialization_plan.components == after_restart.materialization_plan.components
    assert tuple(
        (component.semantic_kind, component.implementation_kind, component.lane)
        for component in ready.materialization_plan.components
    ) == (
        ("chapter", "fullscreen_transition", "fullscreen_transition"),
        ("hero_title", "hero_title", "hero_title"),
        ("identity_card", "identity_card", "identity_card"),
        ("b_roll", "person_inset", "b_roll"),
    )
    assert all(
        component.asset_ref is not None for component in ready.materialization_plan.components
    )
    inset_event = next(
        event for event in ready.materialization_plan.events if event.event_id == "inset-event"
    )
    inset_component = next(
        component
        for component in ready.materialization_plan.components
        if component.event_id == "inset-event"
    )
    assert inset_event.asset_ref == inset.reference
    assert inset_component.asset_ref != inset_event.asset_ref
    assert (
        resolver.resolve_active_asset(inset_component.asset_ref).record.kind is AssetKind.COMPOSITE
    )
    with pytest.raises(TypeError, match="minted only"):
        ProjectedComponent(
            component_id="forged",
            event_id="support-event",
            semantic_kind="identity_card",
            implementation_kind="identity_card",
            lane="hero_title",
            display="forged",
            t0=7.0,
            t1=9.0,
            asset_ref=None,
        )


def test_targeted_revision_survives_restart_and_changes_only_one_event(tmp_path) -> None:
    root = tmp_path / "finished-cut-authority"
    command = _approved_cut(format="short")
    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format="short",
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=45.0,
        source_ranges=(CutSourceRange(0.0, 45.0),),
        cues=(
            CueAnchor("cue-1", "保留這個事件", 1.0, 3.0, "section-1"),
            CueAnchor("cue-2", "修改這個事件", 5.0, 7.0, "section-1"),
        ),
        sections=(CanonicalSection("section-1", "重點", 0.0),),
    )
    approved_cuts = InMemoryApprovedCutStore((command,))
    semantic = InMemorySemanticAdapter()
    current = InMemoryCurrentReleaseIndex()
    resolver = InMemoryAssetResolver(())
    builder = _ReadyDerivedAssetBuilder(resolver)

    def reopen() -> FinishedCutProduction:
        return FinishedCutProduction(
            store_root=root,
            approved_cut_store=approved_cuts,
            asset_resolver=resolver,
            semantic_adapter=semantic,
            derived_asset_builder=builder,
            context_resolver=InMemoryEditorialCutContextResolver((context,)),
            current_release_index=current,
        )

    director_process = reopen()
    director_process.advance(command.command_id)
    director_request = _current_request(director_process, semantic, command.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "hero-1", ("cue-1",), "Keep this event", "保留事件", "hero_title"
            ),
            DirectorEventProposal(
                "hero-2",
                ("cue-2",),
                "Revise this event",
                "待修改事件",
                "identity_card",
            ),
        ),
    )
    dp_process = reopen()
    dp_process.advance(command.command_id)
    dp_request = _current_request(dp_process, semantic, command.command_id)
    semantic.respond(
        dp_request,
        events=(
            DPEventProposal("hero-1", "hero_title", "hero_title", None, ("cue-1",)),
            DPEventProposal("hero-2", "identity_card", "identity_card", None, ("cue-2",)),
        ),
    )
    visual_process = reopen()
    visual_process.advance(command.command_id)
    visual_request = _current_request(visual_process, semantic, command.command_id)
    semantic.respond(
        visual_request,
        events=(
            VisualEventProposal("hero-1", "approved"),
            VisualEventProposal("hero-2", "approved"),
        ),
    )
    checkpoint_process = reopen()
    checkpoint = checkpoint_process.advance(command.command_id)
    assert checkpoint.status == "pending"
    ready_process = reopen()
    ready = ready_process.advance(command.command_id)
    original_plan = _run_authority(ready_process, command.command_id).materialization_plan
    assert ready.status == "review_ready"
    assert original_plan is not None

    artifact = ReleaseArtifact(path="fixture", bytes=1, sha256="a" * 64, duration_sec=45.0)
    release = _seal_finished_cut_release(
        release_id="release-current",
        episode_id=command.episode_id,
        cut_id=command.cut_id,
        format=command.format,
        command_id=command.command_id,
        run_id=original_plan.run_id,
        editorial_master_id="master-1",
        winner_id="winner-1",
        tight_cut_id="tight-1",
        director_acceptance_id=original_plan.director_acceptance_id,
        dp_acceptance_id=original_plan.dp_acceptance_id,
        visual_acceptance_id=original_plan.visual_acceptance_id,
        materialization_plan_id=original_plan.plan_id,
        events=original_plan.events,
        components=original_plan.components,
        preview=artifact,
        subtitle=artifact,
        transaction_receipt_id="receipt-1",
        rollback_ref="rollback-1",
    )
    current.publish((release,))

    revision_id = reopen().request_revision(
        release.release_id,
        "hero-2",
        "Use a concise supporting title",
    )
    retry_process = reopen()
    retry = retry_process.advance(revision_id)
    retry_request = _current_request(retry_process, semantic, revision_id)
    after_restart_process = reopen()
    after_restart = after_restart_process.advance(revision_id)

    assert retry == after_restart
    assert (retry.scope, retry.event_id) == ("event_retry", "hero-2")
    assert retry_request.parent_acceptance_id == release.director_acceptance_id
    assert tuple(event.event_id for event in retry_request.events) == ("hero-2",)

    semantic.respond(
        retry_request,
        events=(
            DirectorEventProposal(
                "hero-2",
                ("cue-2",),
                "Use a concise supporting title",
                "精簡標題",
                "identity_card",
            ),
        ),
    )
    retry_dp_process = reopen()
    retry_dp = retry_dp_process.advance(revision_id)
    retry_dp_request = _current_request(retry_dp_process, semantic, revision_id)
    assert (retry_dp.scope, retry_dp.event_id) == ("event_retry", "hero-2")
    assert tuple(event.event_id for event in retry_dp_request.events) == ("hero-2",)
    semantic.respond(
        retry_dp_request,
        events=(DPEventProposal("hero-2", "identity_card", "identity_card", None, ("cue-2",)),),
    )
    retry_visual_process = reopen()
    retry_visual_process.advance(revision_id)
    retry_visual_request = _current_request(retry_visual_process, semantic, revision_id)
    retry_visual = retry_visual_process.advance(revision_id)
    assert (retry_visual.scope, retry_visual.event_id) == ("event_retry", "hero-2")
    assert tuple(event.event_id for event in retry_visual_request.events) == ("hero-2",)
    semantic.respond(
        retry_visual_request,
        events=(VisualEventProposal("hero-2", "approved"),),
    )

    checkpoint_process = reopen()
    checkpoint = checkpoint_process.advance(revision_id)
    revised_process = reopen()
    revised = revised_process.advance(revision_id)
    revised_after_restart_process = reopen()
    revised_after_restart = revised_after_restart_process.advance(revision_id)
    revised_plan = _run_authority(revised_process, revision_id).materialization_plan

    assert checkpoint.status == "pending"
    assert revised == revised_after_restart
    assert revised_plan is not None
    original_by_id = {event.event_id: event for event in original_plan.events}
    revised_by_id = {event.event_id: event for event in revised_plan.events}
    assert revised_by_id["hero-1"] == original_by_id["hero-1"]
    assert revised_by_id["hero-2"].display == "精簡標題"
    assert revised_by_id["hero-2"].master_cue_ids == original_by_id["hero-2"].master_cue_ids
    assert tuple(instruction.event_id for instruction in builder.requests[-1].instructions) == (
        "hero-2",
    )
    original_components = {component.event_id: component for component in original_plan.components}
    revised_components = {component.event_id: component for component in revised_plan.components}
    assert revised_components["hero-1"] == original_components["hero-1"]
    assert revised_components["hero-2"] != original_components["hero-2"]


def test_persisted_current_catalog_rejects_a_forged_opaque_reference(tmp_path) -> None:
    root = tmp_path / "finished-cut-authority"
    approved_cuts = InMemoryApprovedCutStore((_approved_cut(),))
    semantic = InMemorySemanticAdapter()
    allowed = _asset("allowed")
    forged = _asset("not-in-current-catalog")
    contexts = InMemoryEditorialCutContextResolver((_editorial_context(),))

    def reopen() -> FinishedCutProduction:
        return FinishedCutProduction(
            store_root=root,
            approved_cut_store=approved_cuts,
            asset_resolver=InMemoryAssetResolver((allowed,)),
            semantic_adapter=semantic,
            context_resolver=contexts,
        )

    director_process = reopen()
    director_wait = director_process.advance(_approved_cut().command_id)
    director_request = _current_request(director_process, semantic, director_wait.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "stock-1", ("cue-context-1",), "Show one example", "工作現場", "b_roll"
            ),
        ),
    )
    dp_process = reopen()
    dp_wait = dp_process.advance(_approved_cut().command_id)
    dp_request = _current_request(dp_process, semantic, dp_wait.command_id)
    semantic.respond(
        dp_request,
        events=(DPEventProposal("stock-1", "stock_video", "b_roll", forged.reference),),
    )

    rejected_process = reopen()
    rejected = rejected_process.advance(_approved_cut().command_id)
    after_restart_process = reopen()
    after_restart = after_restart_process.advance(_approved_cut().command_id)
    authority = _run_authority(after_restart_process, after_restart.command_id)

    assert forged.reference.startswith("asset-sha256:")
    assert rejected.status == "needs_review"
    assert len(authority.accepted_stages) == 1
    assert after_restart == rejected
    assert authority.outstanding_request == dp_request


def test_wrong_parent_and_replayed_request_cannot_accept_a_stage(tmp_path) -> None:
    root = tmp_path / "finished-cut-authority"
    approved_cuts = InMemoryApprovedCutStore((_approved_cut(),))
    semantic = InMemorySemanticAdapter()
    asset = _asset("allowed")
    contexts = InMemoryEditorialCutContextResolver((_editorial_context(),))

    def reopen() -> FinishedCutProduction:
        return FinishedCutProduction(
            store_root=root,
            approved_cut_store=approved_cuts,
            asset_resolver=InMemoryAssetResolver((asset,)),
            semantic_adapter=semantic,
            context_resolver=contexts,
        )

    director_process = reopen()
    director_wait = director_process.advance(_approved_cut().command_id)
    director_request = _current_request(director_process, semantic, director_wait.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "stock-1", ("cue-context-1",), "Show one example", "工作現場", "b_roll"
            ),
        ),
    )
    dp_process = reopen()
    dp_wait = dp_process.advance(_approved_cut().command_id)
    dp_request = _current_request(dp_process, semantic, dp_wait.command_id)
    dp_event = DPEventProposal(
        "stock-1", "stock_video", "b_roll", asset.reference, ("cue-context-1",)
    )
    semantic.respond(
        dp_request,
        events=(dp_event,),
        parent_acceptance_id="acceptance-from-an-old-run",
    )

    rejected_process = reopen()
    rejected = rejected_process.advance(_approved_cut().command_id)
    rejected_authority = _run_authority(rejected_process, rejected.command_id)
    assert rejected.status == "needs_review"
    assert rejected_authority.outstanding_request == dp_request
    assert len(rejected_authority.accepted_stages) == 1

    semantic.respond(dp_request, events=(dp_event,))
    visual_process = reopen()
    visual_wait = visual_process.advance(_approved_cut().command_id)
    visual_request = _current_request(visual_process, semantic, visual_wait.command_id)
    visual_wait = visual_process.advance(_approved_cut().command_id)
    visual_authority = _run_authority(visual_process, visual_wait.command_id)
    assert visual_request.stage == "visual_review"
    assert len(visual_authority.accepted_stages) == 2

    semantic.respond(dp_request, events=(dp_event,))
    replay_process = reopen()
    replay_ignored = replay_process.advance(_approved_cut().command_id)

    assert replay_ignored == visual_wait
    assert len(_run_authority(replay_process, replay_ignored.command_id).accepted_stages) == 2


def test_stage_shape_failures_stay_on_exact_request_without_full_rerun(tmp_path) -> None:
    command = _approved_cut(format="short")
    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format="short",
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=45.0,
        source_ranges=(CutSourceRange(0.0, 45.0),),
        cues=(
            CueAnchor("cue-1", "核心主張", 1.0, 3.0, "section-1"),
            CueAnchor("cue-2", "補充說明", 5.0, 7.0, "section-1"),
        ),
        sections=(CanonicalSection("section-1", "重點", 0.0),),
    )
    semantic = InMemorySemanticAdapter()
    resolver = InMemoryAssetResolver(())
    production = FinishedCutProduction(
        store_root=tmp_path / "authority",
        approved_cut_store=InMemoryApprovedCutStore((command,)),
        asset_resolver=resolver,
        semantic_adapter=semantic,
        derived_asset_builder=_ReadyDerivedAssetBuilder(resolver),
        context_resolver=InMemoryEditorialCutContextResolver((context,)),
    )
    production.advance(command.command_id)
    director_request = _current_request(production, semantic, command.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal("hero", ("cue-1",), "State the thesis", "核心主張", "hero_title"),
            DirectorEventProposal(
                "support",
                ("cue-2",),
                "Support the thesis",
                "補充說明",
                "identity_card",
            ),
        ),
    )
    production.advance(command.command_id)
    dp_request = _current_request(production, semantic, command.command_id)

    semantic.respond(
        dp_request,
        events=(DPEventProposal("hero", "hero_title", "hero_title", None, ("cue-1",)),),
    )
    missing = production.advance(command.command_id)
    assert (missing.status, missing.current_stage) == ("needs_review", "dp")
    assert _run_authority(production, command.command_id).outstanding_request == dp_request

    semantic.respond(
        dp_request,
        events=(
            DPEventProposal("hero", "hero_title", "hero_title", None, ("cue-1",)),
            DPEventProposal("support", "identity_card", "identity_card", None, ("cue-2",)),
            DPEventProposal("extra", "hero_title", "hero_title", None, ("cue-1",)),
        ),
    )
    extra = production.advance(command.command_id)
    assert (extra.status, extra.current_stage) == ("needs_review", "dp")

    semantic.respond(
        dp_request,
        events=(
            DPEventProposal("support", "identity_card", "identity_card", None, ("cue-2",)),
            DPEventProposal("hero", "hero_title", "hero_title", None, ("cue-1",)),
        ),
    )
    reordered = production.advance(command.command_id)
    assert (reordered.status, reordered.current_stage) == ("needs_review", "dp")

    semantic.respond(
        dp_request,
        events=(
            DPEventProposal("hero", "hero_title", "hero_title", None, ("cue-1",)),
            DPEventProposal("support", "identity_card", "identity_card", None, ("cue-2",)),
        ),
        components=(
            ComponentProposal(
                "worker-overreach",
                "hero",
                "hero_title",
                "hero_title",
                "hero_title",
                "forged",
                20.0,
                22.0,
            ),
        ),
    )
    overreach = production.advance(command.command_id)
    authority = _run_authority(production, command.command_id)
    assert (overreach.status, overreach.current_stage) == ("needs_review", "dp")
    assert len(authority.accepted_stages) == 1
    assert authority.outstanding_request == dp_request

    semantic.respond(
        dp_request,
        events=(
            DPEventProposal("hero", "hero_title", "hero_title", None, ("cue-1",)),
            DPEventProposal("support", "identity_card", "identity_card", None, ("cue-2",)),
        ),
    )
    production.advance(command.command_id)
    visual_request = _current_request(production, semantic, command.command_id)
    semantic.respond(
        visual_request,
        events=(VisualEventProposal("hero", "approved"),),
    )
    visual_missing = production.advance(command.command_id)
    visual_authority = _run_authority(production, command.command_id)
    assert (visual_missing.status, visual_missing.current_stage) == (
        "needs_review",
        "visual_review",
    )
    assert len(visual_authority.accepted_stages) == 2
    assert visual_authority.outstanding_request == visual_request


def test_forged_state_and_static_response_dropbox_are_never_authority(tmp_path) -> None:
    root = tmp_path / "finished-cut-authority"
    approved_cuts = InMemoryApprovedCutStore((_approved_cut(),))
    semantic = InMemorySemanticAdapter()
    contexts = InMemoryEditorialCutContextResolver((_editorial_context(),))
    production = FinishedCutProduction(
        store_root=root,
        approved_cut_store=approved_cuts,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=contexts,
    )
    waiting = production.advance(_approved_cut().command_id)
    director_request = _current_request(production, semantic, waiting.command_id)

    (root / "state.json").write_text(
        '{"status":"review_ready","accepted_stages":["forged"]}',
        encoding="utf-8",
    )
    static_response = root / "responses" / "director" / "all.json"
    static_response.parent.mkdir(parents=True)
    static_response.write_text(
        '{"request_id":"forged","events":[{"event_id":"old"}]}',
        encoding="utf-8",
    )

    restarted = FinishedCutProduction(
        store_root=root,
        approved_cut_store=approved_cuts,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=contexts,
    )
    after_restart = restarted.advance(_approved_cut().command_id)
    authority = _run_authority(restarted, after_restart.command_id)

    assert after_restart == waiting
    assert authority.accepted_stages == ()
    assert authority.materialization_plan is None
    assert authority.outstanding_request == director_request


def test_cross_format_proposal_cannot_enter_the_current_chain(tmp_path) -> None:
    root = tmp_path / "finished-cut-authority"
    approved_cuts = InMemoryApprovedCutStore((_approved_cut(),))
    semantic = InMemorySemanticAdapter()
    contexts = InMemoryEditorialCutContextResolver((_editorial_context(),))
    production = FinishedCutProduction(
        store_root=root,
        approved_cut_store=approved_cuts,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=contexts,
    )
    waiting = production.advance(_approved_cut().command_id)
    director_request = _current_request(production, semantic, waiting.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "hero-1", ("cue-context-1",), "Explain", "目前論點", "hero_title"
            ),
        ),
        format="short",
    )

    rejected = production.advance(_approved_cut().command_id)
    authority = _run_authority(production, rejected.command_id)

    assert rejected.status == "needs_review"
    assert authority.accepted_stages == ()
    assert authority.outstanding_request == director_request


def test_old_schema_proposal_cannot_enter_the_current_chain(tmp_path) -> None:
    root = tmp_path / "finished-cut-authority"
    approved_cuts = InMemoryApprovedCutStore((_approved_cut(),))
    semantic = InMemorySemanticAdapter()
    contexts = InMemoryEditorialCutContextResolver((_editorial_context(),))
    production = FinishedCutProduction(
        store_root=root,
        approved_cut_store=approved_cuts,
        asset_resolver=InMemoryAssetResolver(()),
        semantic_adapter=semantic,
        context_resolver=contexts,
    )
    waiting = production.advance(_approved_cut().command_id)
    director_request = _current_request(production, semantic, waiting.command_id)
    semantic.respond(
        director_request,
        events=(
            DirectorEventProposal(
                "hero-1", ("cue-context-1",), "Explain", "目前論點", "hero_title"
            ),
        ),
        schema="nakama.legacy-stage-response.v0",
    )

    rejected = production.advance(_approved_cut().command_id)
    authority = _run_authority(production, rejected.command_id)

    assert rejected.status == "needs_review"
    assert authority.accepted_stages == ()
    assert authority.outstanding_request == director_request
