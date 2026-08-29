from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._assets import (
    AssetKind,
    AssetRecord,
    InMemoryAssetResolver,
    ResolvedAsset,
    WorkerCatalogItem,
)
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
    _mint_visual_placement,
)
from agents.brook.script_video.finished_cut_production._derived_assets import (
    BuiltComponentAsset,
)
from agents.brook.script_video.finished_cut_production._engine import _ALLOWED_PROJECTION
from agents.brook.script_video.finished_cut_production._policy import (
    LONG_MAX_HERO_TITLES,
    LONG_MAX_NONSTRUCTURAL_VISUAL_GAP_SEC,
    LONG_MAX_TITLE_LIKE_PER_MINUTE,
    LONG_MIN_DISTINCT_STOCK_VIDEO_EVENTS,
    LONG_MIN_DURATION_SEC,
    LONG_TITLE_CLUSTER_MAX_CARDS,
    LONG_TITLE_CLUSTER_WINDOW_SEC,
    SHORT_MAX_DURATION_SEC,
    SHORT_MAX_TITLE_LIKE_CARDS,
)
from agents.brook.script_video.finished_cut_production._records import EventRecord, StageRequest
from agents.brook.script_video.finished_cut_production._worker_packet import (
    InspectionPreview,
    MediaPreviewProcessResult,
    ProductionWorkerPacketMaterializer,
    StoredAssetPreviewer,
    SubprocessMediaPreviewProcessRunner,
    WorkerPacketError,
    WorkerPacketLimits,
    WorkerPacketScope,
    worker_packet_document,
)


class _SuccessfulMediaPreviewProcess:
    def __init__(self, content: bytes = b"\x89PNG\r\n\x1a\ncurrent ProRes frame") -> None:
        self._content = content
        self.invocations: list[tuple[tuple[str, ...], Path, float]] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_sec: float,
    ) -> MediaPreviewProcessResult:
        self.invocations.append((argv, cwd, timeout_sec))
        (cwd / argv[-1]).write_bytes(self._content)
        return MediaPreviewProcessResult(returncode=0)


class _NoOutputMediaPreviewProcess:
    def __init__(self, *, returncode: int = 0) -> None:
        self._returncode = returncode
        self.invocations = 0

    def run(self, *args, **kwargs) -> MediaPreviewProcessResult:
        self.invocations += 1
        return MediaPreviewProcessResult(returncode=self._returncode)


class _UnusedResolver:
    def __getattr__(self, name: str):
        raise AssertionError(f"Director must not resolve an asset through {name}")


class _UnusedPreviewer:
    def preview(self, *args, **kwargs):
        raise AssertionError("Director must not materialize inspection media")


class _Previewer:
    def __init__(self, preview: InspectionPreview) -> None:
        self._preview = preview
        self.references: list[str] = []

    def preview(self, resolution) -> InspectionPreview:
        self.references.append(resolution.record.reference)
        return self._preview


class _SemanticLaunderingResolver:
    def resolve_worker_asset(self, reference: str) -> ResolvedAsset:
        return ResolvedAsset(
            AssetRecord(
                digest="b" * 64,
                extension=".png",
                kind=AssetKind.TITLE_RENDER,
                recipe_identity="old-title-recipe",
            )
        )


class _MismatchedActiveResolver:
    def resolve_active_asset(self, reference: str) -> ResolvedAsset:
        return ResolvedAsset(
            AssetRecord(
                digest="d" * 64,
                extension=".png",
                kind=AssetKind.TITLE_RENDER,
                recipe_identity="recipe-other-cut",
            )
        )


def _director_request() -> StageRequest:
    return StageRequest(
        run_id="run-current",
        request_id="request-current-director",
        command_id="approved-cut:current",
        episode_id="episode-current",
        cut_id="value-L02",
        format="long",
        stage="director",
        attempt=1,
        scope="full_stage",
        event_id=None,
        parent_acceptance_id=None,
        feedback="請保留完整論點。",
        editorial_context=EditorialCutContext(
            episode_id="episode-current",
            cut_id="value-L02",
            format="long",
            editorial_master_id="master-current",
            tight_cut_id="tight-current",
            duration_sec=540.0,
            source_ranges=(CutSourceRange(10.0, 550.0),),
            cues=(CueAnchor("cue-001", "真正重要的第一句", 0.0, 2.0, "section-01"),),
            sections=(CanonicalSection("section-01", "開場論點", 0.0),),
            editorial_feedback=("Hero Title 不要太密。", "Stock 必須是橫式。"),
        ),
    )


def _materializer() -> ProductionWorkerPacketMaterializer:
    return ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope(
            run_id="run-current",
            episode_id="episode-current",
            cut_id="value-L02",
            format="long",
        ),
        asset_resolver=_UnusedResolver(),
        previewer=_UnusedPreviewer(),
    )


def _dp_request() -> StageRequest:
    reference = "asset-sha256:" + "b" * 64
    return StageRequest(
        run_id="run-current",
        request_id="request-current-dp",
        command_id="approved-cut:current",
        episode_id="episode-current",
        cut_id="value-L02",
        format="long",
        stage="dp",
        attempt=1,
        scope="full_stage",
        event_id=None,
        parent_acceptance_id="acceptance-current-director",
        events=(
            EventRecord(
                event_id="event-001",
                master_cue_ids=("cue-001",),
                text_hash="a" * 64,
                intent="呈現忙碌照顧者的壓力",
                text="真正重要的第一句",
                t0=0.0,
                t1=2.0,
                section_id="section-01",
                display="忙碌照顧者",
                semantic_kind="b_roll",
            ),
        ),
        worker_asset_refs=(reference,),
        worker_catalog_items=(
            WorkerCatalogItem(
                reference=reference,
                kind=AssetKind.STOCK,
                visual_summary="焦頭爛額的照顧者同時處理工作與家庭責任",
                width=1920,
                height=1080,
                duration_sec=12.5,
            ),
        ),
    )


def _neutral_resolver() -> InMemoryAssetResolver:
    return InMemoryAssetResolver(
        (
            AssetRecord(
                digest="b" * 64,
                extension=".mp4",
                kind=AssetKind.STOCK,
                visual_summary="焦頭爛額的照顧者同時處理工作與家庭責任",
                width=1920,
                height=1080,
                duration_sec=12.5,
            ),
        )
    )


def _visual_request() -> StageRequest:
    reference = "asset-sha256:" + "c" * 64
    return StageRequest(
        run_id="run-current",
        request_id="request-current-visual",
        command_id="approved-cut:current",
        episode_id="episode-current",
        cut_id="value-L02",
        format="long",
        stage="visual_review",
        attempt=1,
        scope="full_stage",
        event_id=None,
        parent_acceptance_id="acceptance-current-dp",
        events=(
            EventRecord(
                event_id="event-hero",
                master_cue_ids=("cue-001",),
                text_hash="a" * 64,
                intent="強調核心觀點",
                asset_ref=reference,
                text="真正重要的第一句",
                t0=0.0,
                t1=2.0,
                section_id="section-01",
                display="真正重要的是選擇權",
                semantic_kind="hero_title",
                implementation_kind="title_overlay",
                lane="hero_title",
                visual_placement=_mint_visual_placement(
                    placement_cue_ids=("cue-001",),
                    t0=0.25,
                    t1=1.75,
                    section_id="section-01",
                ),
            ),
        ),
        built_components=(
            BuiltComponentAsset(
                component_id="component:event-hero",
                event_id="event-hero",
                source_asset_ref=None,
                final_asset_ref=reference,
                inspection_ref=reference,
                recipe_identity="recipe-current-hero",
            ),
        ),
    )


def _visual_resolver() -> InMemoryAssetResolver:
    return InMemoryAssetResolver(
        (
            AssetRecord(
                digest="c" * 64,
                extension=".png",
                kind=AssetKind.TITLE_RENDER,
                recipe_identity="recipe-current-hero",
            ),
        )
    )


def test_director_packet_uses_only_the_core_current_request() -> None:
    packet = _materializer().materialize(_director_request())

    assert packet.stage == "director"
    assert packet.payload == {}
    assert packet.media == ()


def test_director_packet_exposes_durable_editorial_feedback_only_in_context() -> None:
    request = _director_request()
    document = worker_packet_document(request, _materializer().materialize(request))

    request_payload = document["request"]
    assert request_payload["editorial_context"]["editorial_feedback"] == [
        "Hero Title 不要太密。",
        "Stock 必須是橫式。",
    ]
    assert request_payload["feedback"] == "請保留完整論點。"


def test_director_materializer_requires_context_for_the_current_cut() -> None:
    request = replace(_director_request(), editorial_context=None)

    with pytest.raises(WorkerPacketError, match="current editorial context"):
        _materializer().materialize(request)


def test_long_worker_brief_exposes_exact_core_policy_without_repo_skills() -> None:
    packet = _materializer().materialize(_director_request())

    assert packet.format_policy["policy_id"] == "long_v2"
    assert packet.format_policy["stage"] == "director"
    constraints = packet.format_policy["constraints"]
    assert constraints == {
        "duration_min_sec": LONG_MIN_DURATION_SEC,
        "canonical_chapter_transition_one_to_one": True,
        "hero_title_max_count": LONG_MAX_HERO_TITLES,
        "title_like_max_per_minute": LONG_MAX_TITLE_LIKE_PER_MINUTE,
        "title_cluster_window_sec": LONG_TITLE_CLUSTER_WINDOW_SEC,
        "title_cluster_max_cards": LONG_TITLE_CLUSTER_MAX_CARDS,
        "meaningful_visual_gap_max_sec": LONG_MAX_NONSTRUCTURAL_VISUAL_GAP_SEC,
        "stock_min_distinct_asset_backed_events": LONG_MIN_DISTINCT_STOCK_VIDEO_EVENTS,
        "stock_native_landscape": True,
        "dp_catalog_references_only": True,
        "person_inset_fullscreen": False,
        "single_paper_family": True,
        "orange_allowed": False,
        "ink_allowed": False,
    }
    combinations = {
        (row["semantic_kind"], row["implementation_kind"], row["lane"])
        for row in packet.format_policy["projection_combinations"]
    }
    assert combinations == _ALLOWED_PROJECTION | {("intentional_aroll", "intentional_aroll", None)}


def test_worker_policy_never_advertises_retired_supporting_titles() -> None:
    packet = _materializer().materialize(_director_request())

    assert "supporting_title" not in json.dumps(packet.format_policy, sort_keys=True)


def test_long_worker_brief_prevents_fragmented_or_semantically_duplicated_card_copy() -> None:
    packet = _materializer().materialize(_director_request())

    assert packet.format_policy["editorial_brief"] == {
        "title_copy": {
            "self_contained_claim": True,
            "explicit_subject": True,
            "dangling_slash_allowed": False,
            "orphan_line_allowed": False,
            "ambiguous_fragment_allowed": False,
        },
        "hero_title": {
            "standalone_claim_only": True,
            "list_steps_or_directions_allowed": False,
        },
        "card_sequence": {
            "semantic_duplicate_or_rephrased_cluster_allowed": False,
        },
        "fullscreen_transition": {"canonical_chapter_only": True},
    }


def test_short_worker_brief_does_not_apply_long_policy_constraints() -> None:
    request = _director_request()
    context = request.editorial_context
    assert context is not None
    request = replace(
        request,
        format="short",
        editorial_context=replace(context, format="short", duration_sec=45.0),
    )
    packet = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "short"),
        asset_resolver=_UnusedResolver(),
        previewer=_UnusedPreviewer(),
    ).materialize(request)

    assert packet.format_policy["policy_id"] == "short_v1"
    assert packet.format_policy["constraints"] == {
        "duration_max_sec": SHORT_MAX_DURATION_SEC,
        "title_like_max_cards": SHORT_MAX_TITLE_LIKE_CARDS,
    }
    assert "editorial_brief" not in packet.format_policy
    assert "hero_title_max_count" not in packet.format_policy["constraints"]


@pytest.mark.parametrize(
    ("field", "stale_value"),
    [
        ("run_id", "run-stale"),
        ("episode_id", "episode-stale"),
        ("cut_id", "value-L99"),
        ("format", "short"),
    ],
)
def test_packet_rejects_a_request_outside_its_current_scope(
    field: str,
    stale_value: str,
) -> None:
    request = replace(_director_request(), **{field: stale_value})

    with pytest.raises(WorkerPacketError, match="current worker scope"):
        _materializer().materialize(request)


def test_dp_packet_contains_only_the_exact_current_neutral_catalog() -> None:
    packet = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_neutral_resolver(),
        previewer=_UnusedPreviewer(),
    ).materialize(_dp_request())

    assert packet.stage == "dp"
    assert packet.payload == {
        "catalog": [
            {
                "reference": "asset-sha256:" + "b" * 64,
                "kind": "stock",
                "visual_summary": "焦頭爛額的照顧者同時處理工作與家庭責任",
                "width": 1920,
                "height": 1080,
                "duration_sec": 12.5,
            }
        ]
    }
    assert packet.media == ()
    assert packet.format_policy["stage_instruction"] == (
        "implement_current_events_using_only_catalog_references"
    )


def test_dp_packet_cannot_launder_a_semantic_render_as_neutral_media() -> None:
    materializer = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_SemanticLaunderingResolver(),
        previewer=_UnusedPreviewer(),
    )

    with pytest.raises(WorkerPacketError, match="differs from the Active Asset Store"):
        materializer.materialize(_dp_request())


def test_non_director_packet_cannot_receive_editorial_feedback_context() -> None:
    request = replace(_dp_request(), editorial_context=_director_request().editorial_context)
    materializer = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_neutral_resolver(),
        previewer=_UnusedPreviewer(),
    )

    with pytest.raises(WorkerPacketError, match="only to the Director"):
        materializer.materialize(request)


def test_visual_packet_cannot_receive_editorial_feedback_context() -> None:
    request = replace(_visual_request(), editorial_context=_director_request().editorial_context)
    materializer = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_visual_resolver(),
        previewer=_UnusedPreviewer(),
    )

    with pytest.raises(WorkerPacketError, match="only to the Director"):
        materializer.materialize(request)


def test_visual_packet_contains_only_exact_built_bindings_and_inspection_bytes() -> None:
    previewer = _Previewer(
        InspectionPreview(
            mime_type="image/png",
            inspection_kind="preview_frame",
            bytes=b"\x89PNG\r\n\x1a\ncurrent rendered hero",
        )
    )
    packet = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_visual_resolver(),
        previewer=previewer,
    ).materialize(_visual_request())

    reference = "asset-sha256:" + "c" * 64
    assert packet.stage == "visual_review"
    assert packet.payload == {
        "built_components": [
            {
                "component_id": "component:event-hero",
                "event_id": "event-hero",
                "final_asset_ref": reference,
                "inspection_ref": reference,
            }
        ]
    }
    assert len(packet.media) == 1
    assert packet.media[0].inspection_ref == reference
    assert packet.media[0].for_asset_ref == reference
    assert packet.media[0].logical_name == "component-0001.png"
    assert packet.media[0].bytes == b"\x89PNG\r\n\x1a\ncurrent rendered hero"
    assert previewer.references == [reference]
    assert packet.format_policy["stage_instruction"] == (
        "judge_each_final_rendered_component_from_inspection_bytes"
    )
    document_text = json.dumps(worker_packet_document(_visual_request(), packet))
    assert "recipe_identity" not in document_text
    assert "source_asset_ref" not in document_text
    assert "visual-pipeline" not in document_text


def test_visual_packet_rejects_malformed_inspection_preview() -> None:
    materializer = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_visual_resolver(),
        previewer=_Previewer(
            InspectionPreview(
                mime_type="image/png",
                inspection_kind="preview_frame",
                bytes=b"not a png",
            )
        ),
    )

    with pytest.raises(WorkerPacketError, match="malformed inspection preview"):
        materializer.materialize(_visual_request())


def test_visual_packet_falls_back_to_the_exact_final_ref_for_inspection() -> None:
    request = _visual_request()
    current = request.built_components[0]
    request = replace(request, built_components=(replace(current, inspection_ref=None),))
    previewer = _Previewer(
        InspectionPreview(
            mime_type="image/png",
            inspection_kind="preview_frame",
            bytes=b"\x89PNG\r\n\x1a\nfallback preview",
        )
    )

    packet = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_visual_resolver(),
        previewer=previewer,
    ).materialize(request)

    final_ref = current.final_asset_ref
    assert packet.payload["built_components"][0]["inspection_ref"] == final_ref
    assert packet.media[0].inspection_ref == final_ref
    assert packet.media[0].for_asset_ref == final_ref


def test_visual_packet_stops_when_current_active_media_is_missing() -> None:
    materializer = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=InMemoryAssetResolver(()),
        previewer=_UnusedPreviewer(),
    )

    with pytest.raises(WorkerPacketError, match="Visual asset is unavailable"):
        materializer.materialize(_visual_request())


def test_visual_packet_rejects_cross_cut_asset_resolution() -> None:
    materializer = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_MismatchedActiveResolver(),
        previewer=_UnusedPreviewer(),
    )

    with pytest.raises(WorkerPacketError, match="differs from the current build"):
        materializer.materialize(_visual_request())


def test_visual_packet_rejects_oversized_inspection_bytes() -> None:
    materializer = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_visual_resolver(),
        previewer=_Previewer(
            InspectionPreview(
                mime_type="image/png",
                inspection_kind="preview_frame",
                bytes=b"\x89PNG\r\n\x1a\n" + b"x" * 32,
            )
        ),
        limits=WorkerPacketLimits(max_media_item_bytes=16),
    )

    with pytest.raises(WorkerPacketError, match="size limit"):
        materializer.materialize(_visual_request())


def test_visual_packet_rejects_oversized_total_inspection_bytes() -> None:
    materializer = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_visual_resolver(),
        previewer=_Previewer(
            InspectionPreview(
                mime_type="image/png",
                inspection_kind="preview_frame",
                bytes=b"\x89PNG\r\n\x1a\ncurrent rendered hero",
            )
        ),
        limits=WorkerPacketLimits(max_total_media_bytes=8),
    )

    with pytest.raises(WorkerPacketError, match="total media size limit"):
        materializer.materialize(_visual_request())


def test_stored_asset_previewer_returns_bytes_without_exposing_its_path(tmp_path: Path) -> None:
    content = b"\x89PNG\r\n\x1a\ncurrent rendered hero"
    path = tmp_path / "current.png"
    path.write_bytes(content)
    resolution = ResolvedAsset(
        record=AssetRecord(
            digest="c" * 64,
            extension=".png",
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe-current-hero",
        ),
        path=path,
    )

    preview = StoredAssetPreviewer(max_bytes=1024).preview(resolution)

    assert preview == InspectionPreview("image/png", "preview_frame", content)
    assert not hasattr(preview, "path")


def test_prores_final_is_inspected_through_a_bounded_png_derivative(tmp_path: Path) -> None:
    reference = "asset-sha256:" + "c" * 64
    source = tmp_path / "current-prores-4444.mov"
    source.write_bytes(b"large-prores-source" * 128)
    resolution = ResolvedAsset(
        record=AssetRecord(
            digest="c" * 64,
            extension=".mov",
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe-current-hero",
        ),
        path=source,
    )

    class _Resolver:
        def resolve_active_asset(self, requested: str) -> ResolvedAsset:
            assert requested == reference
            return resolution

    process = _SuccessfulMediaPreviewProcess()
    previewer = StoredAssetPreviewer(max_bytes=64, process_runner=process)

    packet = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_Resolver(),
        previewer=previewer,
        limits=WorkerPacketLimits(max_media_item_bytes=64),
    ).materialize(_visual_request())

    assert packet.payload["built_components"] == [
        {
            "component_id": "component:event-hero",
            "event_id": "event-hero",
            "final_asset_ref": reference,
            "inspection_ref": reference,
        }
    ]
    assert packet.media[0].mime_type == "image/png"
    assert packet.media[0].inspection_kind == "preview_frame"
    assert packet.media[0].for_asset_ref == reference
    assert packet.media[0].inspection_ref == reference
    assert len(process.invocations) == 1
    argv, workspace, timeout_sec = process.invocations[0]
    assert argv[0] == "ffmpeg"
    assert argv[argv.index("-i") + 1] == str(source)
    assert argv[-1] == "preview.png"
    assert timeout_sec > 0
    assert not workspace.exists()
    assert "visual-pipeline" not in json.dumps(worker_packet_document(_visual_request(), packet))


def test_video_previewer_does_not_dispatch_when_the_current_object_is_missing(
    tmp_path: Path,
) -> None:
    process = _NoOutputMediaPreviewProcess()
    resolution = ResolvedAsset(
        record=AssetRecord(
            digest="c" * 64,
            extension=".mov",
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe-current-hero",
        ),
        path=tmp_path / "missing.mov",
    )

    with pytest.raises(WorkerPacketError, match="missing"):
        StoredAssetPreviewer(process_runner=process).preview(resolution)

    assert process.invocations == 0


def test_video_previewer_fails_closed_when_the_process_produces_no_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "current.mov"
    source.write_bytes(b"opaque current media")
    process = _NoOutputMediaPreviewProcess()
    resolution = ResolvedAsset(
        record=AssetRecord(
            digest="c" * 64,
            extension=".mov",
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe-current-hero",
        ),
        path=source,
    )

    with pytest.raises(WorkerPacketError, match="missing"):
        StoredAssetPreviewer(process_runner=process).preview(resolution)


def test_video_previewer_checks_output_size_before_reading_bytes(tmp_path: Path) -> None:
    source = tmp_path / "current.mov"
    source.write_bytes(b"opaque current media")
    process = _SuccessfulMediaPreviewProcess(b"\x89PNG\r\n\x1a\n" + b"oversized" * 16)
    resolution = ResolvedAsset(
        record=AssetRecord(
            digest="c" * 64,
            extension=".mov",
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe-current-hero",
        ),
        path=source,
    )

    with pytest.raises(WorkerPacketError, match="preview size limit"):
        StoredAssetPreviewer(max_bytes=64, process_runner=process).preview(resolution)


def test_media_preview_subprocess_is_invoked_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, object] = {}

    def _run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return MediaPreviewProcessResult(returncode=0)

    monkeypatch.setattr(
        "agents.brook.script_video.finished_cut_production._worker_packet.subprocess.run",
        _run,
    )

    result = SubprocessMediaPreviewProcessRunner().run(
        ("ffmpeg", "-nostdin", "-i", "active-store.mov", "preview.png"),
        cwd=tmp_path,
        timeout_sec=7.5,
    )

    assert result == MediaPreviewProcessResult(returncode=0)
    assert observed["shell"] is False
    assert observed["cwd"] == tmp_path
    assert observed["timeout"] == 7.5
    assert observed["stdout"] is subprocess.DEVNULL
    assert observed["stderr"] is subprocess.DEVNULL
    assert "capture_output" not in observed


def test_same_current_request_materializes_one_deterministic_packet() -> None:
    materializer = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_neutral_resolver(),
        previewer=_UnusedPreviewer(),
    )
    request = _dp_request()

    first = materializer.materialize(request)
    second = materializer.materialize(request)

    assert first == second
    assert worker_packet_document(request, first) == worker_packet_document(request, second)


def test_director_packet_rejects_legacy_path_text_before_it_can_escape() -> None:
    request = replace(
        _director_request(),
        feedback="G:/episode/highlights/visual-pipeline/revisions/r001",
    )

    with pytest.raises(WorkerPacketError, match="legacy or absolute path"):
        _materializer().materialize(request)


def test_director_packet_rejects_oversized_current_context() -> None:
    materializer = ProductionWorkerPacketMaterializer(
        scope=WorkerPacketScope("run-current", "episode-current", "value-L02", "long"),
        asset_resolver=_UnusedResolver(),
        previewer=_UnusedPreviewer(),
        limits=WorkerPacketLimits(max_packet_json_bytes=64),
    )

    with pytest.raises(WorkerPacketError, match="JSON size limit"):
        materializer.materialize(_director_request())
