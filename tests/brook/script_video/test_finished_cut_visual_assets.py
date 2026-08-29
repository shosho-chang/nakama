from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._active_store import (
    ActiveAssetPublication,
    ActiveAssetStore,
)
from agents.brook.script_video.finished_cut_production._assets import (
    AssetKind,
    CompactAssetReceipt,
    WorkerCatalogItem,
    WorkerSelectionCatalog,
)
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
)
from agents.brook.script_video.finished_cut_production._derived_assets import (
    DerivedAssetBuildRequest,
    DerivedAssetGeometry,
    DerivedAssetInstruction,
)
from agents.brook.script_video.finished_cut_production._hyperframes_renderer import (
    GeneratedMediaProbe,
)
from agents.brook.script_video.finished_cut_production._long_visual_renderer import (
    LongVisualRenderer,
)
from agents.brook.script_video.finished_cut_production._visual_assets import (
    FaceSafePlacement,
    FfmpegPersonInsetCompositor,
    FfmpegProcessResult,
    LongDerivedAssetBuilder,
    PersonInsetCompositeRequest,
)


class _NeverBrowser:
    def render(self, recipe):
        raise AssertionError("neutral Stock must not call the browser renderer")


class _CatalogOnlyStore:
    episode_id = "episode-001"

    def __init__(self, item: WorkerCatalogItem) -> None:
        self._catalog = WorkerSelectionCatalog((item,))
        self.resolve_calls = 0

    def worker_selection_catalog(self) -> WorkerSelectionCatalog:
        return self._catalog

    def resolve_worker_asset(self, _reference: str):
        self.resolve_calls += 1
        raise AssertionError("oversized placement must fail before Active Store resolution")


def _neutral_receipt(content: bytes, *, source_class: str) -> CompactAssetReceipt:
    if source_class == "licensed_stock":
        provider = "pexels"
        provider_item_id = "7106572"
        source_url = "https://www.pexels.com/video/7106572/"
        license_name = "Pexels license: https://www.pexels.com/license/"
    else:
        provider = "nakama-self-archive"
        provider_item_id = "creator-provided-portrait"
        source_url = "https://nakama.example/archive/creator-provided-portrait"
        license_name = "Creator-provided archive"
    return CompactAssetReceipt(
        origin="neutral_acquisition",
        media_sha256=hashlib.sha256(content).hexdigest(),
        media_bytes=len(content),
        source_class=source_class,
        provider=provider,
        provider_item_id=provider_item_id,
        source_url=source_url,
        license=license_name,
        acquired_at="2026-08-26T01:00:00Z",
        forensic_receipt_ref=f"forensic-sha256:{'f' * 64}",
    )


class _Browser:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = 0
        self.recipes = []

    def render(self, recipe):
        self.calls += 1
        self.recipes.append(recipe)
        path = self.root / f"title-{self.calls}{recipe.extension}"
        path.write_bytes(recipe.recipe_identity.encode("utf-8"))
        from agents.brook.script_video.finished_cut_production._long_visual_renderer import (
            BrowserRenderResult,
        )

        return BrowserRenderResult(
            path=path,
            width=recipe.canvas_width,
            height=recipe.canvas_height,
            duration_sec=recipe.duration_sec,
            has_alpha=recipe.has_alpha,
            codec_name=recipe.codec_name,
            pixel_format=recipe.pixel_format,
        )


class _NeverFfmpegRunner:
    def run(self, arguments, *, cwd, timeout_sec):
        raise AssertionError("this build must not call ffmpeg")


class _NeverFacePlacement:
    def place(self, request):
        raise AssertionError("neutral Stock must not call facial placement")


class _FacePlacement:
    def __init__(self) -> None:
        self.requests = []
        self.result = FaceSafePlacement(
            x_ratio=0.75,
            y_ratio=0.24,
            width_ratio=0.20,
            height_ratio=0.42,
            avoids_faces=True,
        )

    def place(self, request):
        self.requests.append(request)
        return self.result


class _FfmpegRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, arguments, *, cwd, timeout_sec):
        self.calls.append((arguments, cwd, timeout_sec))
        Path(arguments[-1]).write_bytes(b"person inset alpha animation")
        return FfmpegProcessResult(returncode=0, stdout="", stderr="")


class _NeverMediaProbe:
    def inspect(self, path: Path):
        raise AssertionError("this build must not probe generated media")


class _PersonInsetProbe:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def inspect(self, path: Path) -> GeneratedMediaProbe:
        self.paths.append(path)
        return GeneratedMediaProbe(
            codec_name="prores",
            pixel_format="yuva444p12le",
            width=1920,
            height=1080,
            duration_sec=4.0,
            has_alpha=True,
        )


class _MismatchedPersonInsetProbe(_PersonInsetProbe):
    def inspect(self, path: Path) -> GeneratedMediaProbe:
        result = super().inspect(path)
        return replace(result, pixel_format="yuv420p", has_alpha=False)


def test_sixty_second_semantic_evidence_renders_only_four_second_hero_placement(
    tmp_path: Path,
) -> None:
    context = EditorialCutContext(
        episode_id="episode-001",
        cut_id="value-L03",
        format="long",
        editorial_master_id="master-current",
        tight_cut_id="tight-current",
        duration_sec=60.0,
        source_ranges=(CutSourceRange(100.0, 160.0),),
        cues=(
            CueAnchor("cue-semantic", "完整語意證據", 0.0, 56.0, "section-01"),
            CueAnchor("cue-placement", "四秒視覺位置", 56.0, 60.0, "section-01"),
        ),
        sections=(CanonicalSection("section-01", "完整論點", 0.0),),
    )
    semantic_cue_ids = ("cue-semantic", "cue-placement")
    semantic_anchor = context.derive_anchor(semantic_cue_ids)
    placement = context.derive_visual_placement(
        semantic_cue_ids=semantic_cue_ids,
        placement_cue_ids=("cue-placement",),
        semantic_kind="hero_title",
    )
    instruction = DerivedAssetInstruction(
        component_id="component-hero-placement",
        event_id="event-hero-placement",
        semantic_kind="hero_title",
        implementation_kind="hero_title",
        lane="hero_title",
        display="完整命題",
        t0=placement.t0,
        t1=placement.t1,
        source_asset_ref=None,
        geometry=DerivedAssetGeometry(1920, 1080, "hero_title:v1"),
        recipe_identity="recipe:hero:placement-current",
    )
    request = DerivedAssetBuildRequest(
        build_request_id="build-hero-placement-current",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L03",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=(instruction,),
        worker_catalog_items=(),
    )
    store = ActiveAssetStore.open(tmp_path / "assets-v2", episode_id="episode-001")
    browser = _Browser(tmp_path)
    builder = LongDerivedAssetBuilder(
        store=store,
        title_renderer=LongVisualRenderer(browser=browser),
        compositor=FfmpegPersonInsetCompositor(
            output_root=tmp_path / "composites",
            runner=_NeverFfmpegRunner(),
            probe=_NeverMediaProbe(),
        ),
        face_placement=_NeverFacePlacement(),
    )

    result = builder.build(request)

    assert semantic_anchor.t1 - semantic_anchor.t0 == 60.0
    assert instruction.show_sec == 4.0
    assert result.status == "ready"
    assert browser.recipes[0].duration_sec == 4.0


def test_native_horizontal_stock_within_one_frame_duration_tolerance_passes_through(
    tmp_path: Path,
) -> None:
    source = tmp_path / "stock.mp4"
    source.write_bytes(b"native horizontal stock")
    store = ActiveAssetStore.open(tmp_path / "assets-v2", episode_id="episode-001")
    stock = store.publish(
        ActiveAssetPublication(
            source_path=source,
            kind=AssetKind.STOCK,
            visual_summary="忙碌地同時處理工作與家庭責任",
            width=1920,
            height=1080,
            duration_sec=5.98,
            compact_receipt=_neutral_receipt(source.read_bytes(), source_class="licensed_stock"),
        )
    )
    request = DerivedAssetBuildRequest(
        build_request_id="build-stock-current",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=(
            DerivedAssetInstruction(
                component_id="component-stock",
                event_id="event-stock",
                semantic_kind="b_roll",
                implementation_kind="stock_video",
                lane="b_roll",
                display="焦頭爛額的工作狀態",
                t0=12.0,
                t1=18.0,
                source_asset_ref=stock.record.reference,
                geometry=DerivedAssetGeometry(
                    target_width=1920,
                    target_height=1080,
                    layout_identity="long.stock.native-horizontal.v1",
                ),
                recipe_identity=None,
            ),
        ),
        worker_catalog_items=store.worker_selection_catalog().items(),
    )
    builder = LongDerivedAssetBuilder(
        store=store,
        title_renderer=LongVisualRenderer(browser=_NeverBrowser()),
        compositor=FfmpegPersonInsetCompositor(
            output_root=tmp_path / "composites",
            runner=_NeverFfmpegRunner(),
            probe=_NeverMediaProbe(),
        ),
        face_placement=_NeverFacePlacement(),
    )

    result = builder.build(request)

    assert result.status == "ready"
    assert len(result.assets) == 1
    assert result.assets[0].source_asset_ref == stock.record.reference
    assert result.assets[0].final_asset_ref == stock.record.reference
    assert result.assets[0].inspection_ref == stock.record.reference
    assert result.assets[0].recipe_identity is None


def test_vertical_stock_is_rejected_instead_of_being_reframed(tmp_path: Path) -> None:
    source = tmp_path / "vertical-stock.mp4"
    source.write_bytes(b"vertical stock")
    store = ActiveAssetStore.open(tmp_path / "assets-v2", episode_id="episode-001")
    stock = store.publish(
        ActiveAssetPublication(
            source_path=source,
            kind=AssetKind.STOCK,
            visual_summary="直式素材",
            width=1080,
            height=1920,
            duration_sec=6.0,
            compact_receipt=_neutral_receipt(source.read_bytes(), source_class="licensed_stock"),
        )
    )
    request = DerivedAssetBuildRequest(
        build_request_id="build-vertical-stock",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=(
            DerivedAssetInstruction(
                component_id="component-stock",
                event_id="event-stock",
                semantic_kind="b_roll",
                implementation_kind="stock_video",
                lane="b_roll",
                display="直式素材",
                t0=12.0,
                t1=18.0,
                source_asset_ref=stock.record.reference,
                geometry=DerivedAssetGeometry(1920, 1080, "long.stock.native-horizontal.v1"),
                recipe_identity=None,
            ),
        ),
        worker_catalog_items=store.worker_selection_catalog().items(),
    )
    builder = LongDerivedAssetBuilder(
        store=store,
        title_renderer=LongVisualRenderer(browser=_NeverBrowser()),
        compositor=FfmpegPersonInsetCompositor(
            output_root=tmp_path / "composites",
            runner=_NeverFfmpegRunner(),
            probe=_NeverMediaProbe(),
        ),
        face_placement=_NeverFacePlacement(),
    )

    result = builder.build(request)

    assert result.status == "failed"
    assert result.error_code == "derived_asset_mismatch"


def test_exact_current_hero_recipe_reuses_active_asset_without_rendering_again(
    tmp_path: Path,
) -> None:
    store = ActiveAssetStore.open(tmp_path / "assets-v2", episode_id="episode-001")
    browser = _Browser(tmp_path)
    builder = LongDerivedAssetBuilder(
        store=store,
        title_renderer=LongVisualRenderer(browser=browser),
        compositor=FfmpegPersonInsetCompositor(
            output_root=tmp_path / "composites",
            runner=_NeverFfmpegRunner(),
            probe=_NeverMediaProbe(),
        ),
        face_placement=_NeverFacePlacement(),
    )
    instruction = DerivedAssetInstruction(
        component_id="component-hero",
        event_id="event-hero",
        semantic_kind="hero_title",
        implementation_kind="hero_title",
        lane="hero_title",
        display="真正的選擇不是二選一",
        t0=22.0,
        t1=25.0,
        source_asset_ref=None,
        geometry=DerivedAssetGeometry(
            target_width=1920,
            target_height=1080,
            layout_identity="hero_title:v1",
        ),
        recipe_identity="recipe:hero:current",
    )
    request = DerivedAssetBuildRequest(
        build_request_id="build-hero-current",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=(instruction,),
        worker_catalog_items=(),
    )

    first = builder.build(request)
    second = builder.build(request)
    next_request = replace(
        request,
        build_request_id="build-hero-next",
        instructions=(replace(instruction, recipe_identity="recipe:hero:next"),),
    )
    next_result = builder.build(next_request)

    assert first.status == "ready"
    assert second == first
    assert next_result.status == "ready"
    assert browser.calls == 2
    assert next_result.assets[0].final_asset_ref != first.assets[0].final_asset_ref
    assert first.assets[0].recipe_identity == "recipe:hero:current"
    assert first.assets[0].final_asset_ref == first.assets[0].inspection_ref
    assert store.resolve_exact_recipe("recipe:hero:current").record.kind is AssetKind.TITLE_RENDER


def test_oversized_chapter_placement_fails_before_browser_render(tmp_path: Path) -> None:
    store = ActiveAssetStore.open(tmp_path / "assets-v2", episode_id="episode-001")
    browser = _Browser(tmp_path)
    builder = LongDerivedAssetBuilder(
        store=store,
        title_renderer=LongVisualRenderer(browser=browser),
        compositor=FfmpegPersonInsetCompositor(
            output_root=tmp_path / "composites",
            runner=_NeverFfmpegRunner(),
            probe=_NeverMediaProbe(),
        ),
        face_placement=_NeverFacePlacement(),
    )
    request = DerivedAssetBuildRequest(
        build_request_id="build-chapter-oversized",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=(
            DerivedAssetInstruction(
                component_id="component-chapter",
                event_id="event-chapter",
                semantic_kind="chapter",
                implementation_kind="fullscreen_transition",
                lane="fullscreen_transition",
                display="下一章",
                t0=100.0,
                t1=104.001,
                source_asset_ref=None,
                geometry=DerivedAssetGeometry(1920, 1080, "fullscreen_transition:v1"),
                recipe_identity="recipe:chapter:oversized",
            ),
        ),
        worker_catalog_items=(),
    )

    result = builder.build(request)

    assert result.status == "failed"
    assert result.error_code == "visual_placement_duration_exceeded"
    assert browser.calls == 0


@pytest.mark.parametrize(
    ("semantic_kind", "implementation_kind", "lane"),
    [
        ("hero_title", "hero_title", "hero_title"),
        ("identity_card", "identity_card", "identity_card"),
    ],
)
def test_oversized_title_or_identity_placement_fails_before_browser_render(
    tmp_path: Path,
    semantic_kind: str,
    implementation_kind: str,
    lane: str,
) -> None:
    store = ActiveAssetStore.open(tmp_path / "assets-v2", episode_id="episode-001")
    browser = _Browser(tmp_path)
    builder = LongDerivedAssetBuilder(
        store=store,
        title_renderer=LongVisualRenderer(browser=browser),
        compositor=FfmpegPersonInsetCompositor(
            output_root=tmp_path / "composites",
            runner=_NeverFfmpegRunner(),
            probe=_NeverMediaProbe(),
        ),
        face_placement=_NeverFacePlacement(),
    )
    request = DerivedAssetBuildRequest(
        build_request_id=f"build-{implementation_kind}-oversized",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=(
            DerivedAssetInstruction(
                component_id=f"component-{implementation_kind}",
                event_id=f"event-{implementation_kind}",
                semantic_kind=semantic_kind,
                implementation_kind=implementation_kind,
                lane=lane,  # type: ignore[arg-type]
                display="完整命題",
                t0=100.0,
                t1=108.001,
                source_asset_ref=None,
                geometry=DerivedAssetGeometry(1920, 1080, f"{implementation_kind}:v1"),
                recipe_identity=f"recipe:{implementation_kind}:oversized",
            ),
        ),
        worker_catalog_items=(),
    )

    result = builder.build(request)

    assert result.status == "failed"
    assert result.error_code == "visual_placement_duration_exceeded"
    assert browser.calls == 0


@pytest.mark.parametrize(
    ("implementation_kind", "asset_kind", "duration_sec", "recipe_identity"),
    [
        ("stock_video", AssetKind.STOCK, 30.0, None),
        ("photo", AssetKind.PHOTO, None, None),
        ("non_editorial_clip", AssetKind.NON_EDITORIAL_CLIP, 30.0, None),
        ("person_inset", AssetKind.PHOTO, None, "recipe:person-inset:oversized"),
    ],
)
def test_oversized_asset_backed_broll_fails_before_resolution_or_render(
    tmp_path: Path,
    implementation_kind: str,
    asset_kind: AssetKind,
    duration_sec: float | None,
    recipe_identity: str | None,
) -> None:
    reference = "asset-sha256:" + "a" * 64
    catalog_item = WorkerCatalogItem(
        reference=reference,
        kind=asset_kind,
        visual_summary="Current neutral visual",
        width=1920,
        height=1080,
        duration_sec=duration_sec,
    )
    builder = LongDerivedAssetBuilder(
        store=_CatalogOnlyStore(catalog_item),  # type: ignore[arg-type]
        title_renderer=LongVisualRenderer(browser=_NeverBrowser()),
        compositor=FfmpegPersonInsetCompositor(
            output_root=tmp_path / "composites",
            runner=_NeverFfmpegRunner(),
            probe=_NeverMediaProbe(),
        ),
        face_placement=_NeverFacePlacement(),
    )
    request = DerivedAssetBuildRequest(
        build_request_id=f"build-{implementation_kind}-oversized",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=(
            DerivedAssetInstruction(
                component_id=f"component-{implementation_kind}",
                event_id=f"event-{implementation_kind}",
                semantic_kind="b_roll",
                implementation_kind=implementation_kind,
                lane="b_roll",
                display="Current neutral visual",
                t0=100.0,
                t1=112.001,
                source_asset_ref=reference,
                geometry=DerivedAssetGeometry(1920, 1080, f"{implementation_kind}:v1"),
                recipe_identity=recipe_identity,
            ),
        ),
        worker_catalog_items=(catalog_item,),
    )

    result = builder.build(request)

    assert result.status == "failed"
    assert result.error_code == "visual_placement_duration_exceeded"


def test_stock_placement_longer_than_source_by_more_than_one_frame_fails_before_resolution(
    tmp_path: Path,
) -> None:
    reference = "asset-sha256:" + "a" * 64
    catalog_item = WorkerCatalogItem(
        reference=reference,
        kind=AssetKind.STOCK,
        visual_summary="Current landscape stock",
        width=1920,
        height=1080,
        duration_sec=9.95,
    )
    store = _CatalogOnlyStore(catalog_item)
    builder = LongDerivedAssetBuilder(
        store=store,  # type: ignore[arg-type]
        title_renderer=LongVisualRenderer(browser=_NeverBrowser()),
        compositor=FfmpegPersonInsetCompositor(
            output_root=tmp_path / "composites",
            runner=_NeverFfmpegRunner(),
            probe=_NeverMediaProbe(),
        ),
        face_placement=_NeverFacePlacement(),
    )
    request = DerivedAssetBuildRequest(
        build_request_id="build-stock-too-short",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=(
            DerivedAssetInstruction(
                component_id="component-stock",
                event_id="event-stock",
                semantic_kind="b_roll",
                implementation_kind="stock_video",
                lane="b_roll",
                display="Current landscape stock",
                t0=100.0,
                t1=110.0,
                source_asset_ref=reference,
                geometry=DerivedAssetGeometry(1920, 1080, "stock_video:v1"),
                recipe_identity=None,
            ),
        ),
        worker_catalog_items=(catalog_item,),
    )

    result = builder.build(request)

    assert result.status == "failed"
    assert result.error_code == "stock_placement_exceeds_source_duration"
    assert store.resolve_calls == 0


def test_legacy_webm_title_cannot_be_reused_as_current_resolve_media(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy-hero.webm"
    legacy_path.write_bytes(b"vp9 alpha from retired renderer")
    store = ActiveAssetStore.open(tmp_path / "assets-v2", episode_id="episode-001")
    store.publish(
        ActiveAssetPublication(
            source_path=legacy_path,
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe:hero:current",
        )
    )
    builder = LongDerivedAssetBuilder(
        store=store,
        title_renderer=LongVisualRenderer(browser=_NeverBrowser()),
        compositor=FfmpegPersonInsetCompositor(
            output_root=tmp_path / "composites",
            runner=_NeverFfmpegRunner(),
            probe=_NeverMediaProbe(),
        ),
        face_placement=_NeverFacePlacement(),
    )
    request = DerivedAssetBuildRequest(
        build_request_id="build-hero-current",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=(
            DerivedAssetInstruction(
                component_id="component-hero",
                event_id="event-hero",
                semantic_kind="hero_title",
                implementation_kind="hero_title",
                lane="hero_title",
                display="真正的選擇",
                t0=22.0,
                t1=25.0,
                source_asset_ref=None,
                geometry=DerivedAssetGeometry(1920, 1080, "hero_title:v1"),
                recipe_identity="recipe:hero:current",
            ),
        ),
        worker_catalog_items=(),
    )

    result = builder.build(request)

    assert result.status == "failed"
    assert result.error_code == "derived_asset_mismatch"


def test_all_current_generated_browser_components_publish_final_assets(tmp_path: Path) -> None:
    store = ActiveAssetStore.open(tmp_path / "assets-v2", episode_id="episode-001")
    browser = _Browser(tmp_path)
    builder = LongDerivedAssetBuilder(
        store=store,
        title_renderer=LongVisualRenderer(browser=browser),
        compositor=FfmpegPersonInsetCompositor(
            output_root=tmp_path / "composites",
            runner=_NeverFfmpegRunner(),
            probe=_NeverMediaProbe(),
        ),
        face_placement=_NeverFacePlacement(),
    )
    # Each role pins its own canonical layout identity; the renderer rejects a
    # request that does not carry the exact one for that role.
    roles = (
        ("chapter", "chapter", "fullscreen_transition", "第一章", "fullscreen_transition:v4"),
        ("hero", "hero_title", "hero_title", "真正的選擇", "hero_title:v1"),
        ("identity", "identity_card", "identity_card", "簡立峰博士", "identity_card:v1"),
        ("effect", "visual_effect", "visual_effect", "焦點強調", "visual_effect:v1"),
    )
    instructions = tuple(
        DerivedAssetInstruction(
            component_id=f"component-{name}",
            event_id=f"event-{name}",
            semantic_kind=semantic_kind,
            implementation_kind=implementation_kind,
            lane=implementation_kind,
            display=display,
            t0=10.0 + index * 5,
            t1=13.0 + index * 5,
            source_asset_ref=None,
            geometry=DerivedAssetGeometry(1920, 1080, layout_identity),
            recipe_identity=f"recipe:{implementation_kind}:current",
        )
        for index, (
            name,
            semantic_kind,
            implementation_kind,
            display,
            layout_identity,
        ) in enumerate(roles)
    )
    request = DerivedAssetBuildRequest(
        build_request_id="build-browser-components-current",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=instructions,
        worker_catalog_items=(),
    )

    result = builder.build(request)

    assert result.status == "ready"
    assert browser.calls == len(instructions)
    expected_kinds = (
        AssetKind.CHAPTER_RENDER,
        AssetKind.TITLE_RENDER,
        AssetKind.CONCEPT_RENDER,
        AssetKind.CONCEPT_RENDER,
    )
    assert (
        tuple(
            store.resolve_active_asset(asset.final_asset_ref).record.kind for asset in result.assets
        )
        == expected_kinds
    )


def test_person_inset_is_alpha_animated_small_and_face_safe(tmp_path: Path) -> None:
    portrait_path = tmp_path / "doctor.png"
    portrait_path.write_bytes(b"portrait with alpha")
    store = ActiveAssetStore.open(tmp_path / "assets-v2", episode_id="episode-001")
    portrait = store.publish(
        ActiveAssetPublication(
            source_path=portrait_path,
            kind=AssetKind.PHOTO,
            visual_summary="簡立峰博士的中性大頭照",
            width=800,
            height=1000,
            compact_receipt=_neutral_receipt(
                portrait_path.read_bytes(), source_class="provided_self_archive"
            ),
        )
    )
    face_placement = _FacePlacement()
    ffmpeg = _FfmpegRunner()
    probe = _PersonInsetProbe()
    compositor = FfmpegPersonInsetCompositor(
        output_root=tmp_path / "composites",
        runner=ffmpeg,
        probe=probe,
    )
    builder = LongDerivedAssetBuilder(
        store=store,
        title_renderer=LongVisualRenderer(browser=_NeverBrowser()),
        compositor=compositor,
        face_placement=face_placement,
    )
    request = DerivedAssetBuildRequest(
        build_request_id="build-person-current",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=(
            DerivedAssetInstruction(
                component_id="component-person",
                event_id="event-person",
                semantic_kind="b_roll",
                implementation_kind="person_inset",
                lane="b_roll",
                display="簡立峰博士",
                t0=30.0,
                t1=34.0,
                source_asset_ref=portrait.record.reference,
                geometry=DerivedAssetGeometry(
                    target_width=1920,
                    target_height=1080,
                    layout_identity="person_inset:v1",
                ),
                recipe_identity="recipe:person-inset:current",
            ),
        ),
        worker_catalog_items=store.worker_selection_catalog().items(),
    )

    result = builder.build(request)

    assert result.status == "ready"
    assert len(face_placement.requests) == 1
    assert len(ffmpeg.calls) == 1
    arguments, cwd, timeout_sec = ffmpeg.calls[0]
    command = " ".join(arguments)
    assert "prores_ks" in command
    assert "-profile:v 4" in command
    assert "yuva444p12le" in command
    assert "alpha=1" in command
    assert "overlay=" in command
    assert cwd is None
    assert arguments[arguments.index("-an") :] == (
        "-an",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4",
        "-pix_fmt",
        "yuva444p12le",
        "-movflags",
        "+faststart",
        arguments[-1],
    )
    assert Path(arguments[-1]).suffix == ".mov"
    assert timeout_sec > 0
    assert face_placement.result.avoids_faces is True
    assert face_placement.result.width_ratio <= 0.24
    assert len(probe.paths) == 1
    assert result.assets[0].source_asset_ref == portrait.record.reference
    assert result.assets[0].final_asset_ref != portrait.record.reference
    assert result.assets[0].inspection_ref == result.assets[0].final_asset_ref
    assert store.resolve_active_asset(result.assets[0].final_asset_ref).path.suffix == ".mov"
    assert (
        store.resolve_exact_recipe("recipe:person-inset:current").record.kind is AssetKind.COMPOSITE
    )


def test_person_inset_probe_mismatch_leaves_no_unverified_output(tmp_path: Path) -> None:
    source = tmp_path / "portrait.png"
    source.write_bytes(b"portrait")
    output_root = tmp_path / "composites"
    compositor = FfmpegPersonInsetCompositor(
        output_root=output_root,
        runner=_FfmpegRunner(),
        probe=_MismatchedPersonInsetProbe(),
    )

    with pytest.raises(ValueError, match="probe"):
        compositor.composite(
            PersonInsetCompositeRequest(
                render_identity="recipe:person-inset:mismatch",
                source_path=source,
                target_width=1920,
                target_height=1080,
                duration_sec=4.0,
                placement=FaceSafePlacement(
                    x_ratio=0.75,
                    y_ratio=0.24,
                    width_ratio=0.20,
                    height_ratio=0.42,
                    avoids_faces=True,
                ),
            )
        )

    assert list(output_root.iterdir()) == []


def test_legacy_webm_person_inset_cannot_be_reused_as_resolve_composite(
    tmp_path: Path,
) -> None:
    portrait_path = tmp_path / "portrait.png"
    portrait_path.write_bytes(b"portrait")
    legacy_path = tmp_path / "person-inset.webm"
    legacy_path.write_bytes(b"legacy vp9 composite")
    store = ActiveAssetStore.open(tmp_path / "assets-v2", episode_id="episode-001")
    portrait = store.publish(
        ActiveAssetPublication(
            source_path=portrait_path,
            kind=AssetKind.PHOTO,
            visual_summary="中性人物照片",
            width=800,
            height=1000,
            compact_receipt=_neutral_receipt(
                portrait_path.read_bytes(), source_class="provided_self_archive"
            ),
        )
    )
    store.publish(
        ActiveAssetPublication(
            source_path=legacy_path,
            kind=AssetKind.COMPOSITE,
            recipe_identity="recipe:person-inset:current",
        )
    )
    builder = LongDerivedAssetBuilder(
        store=store,
        title_renderer=LongVisualRenderer(browser=_NeverBrowser()),
        compositor=FfmpegPersonInsetCompositor(
            output_root=tmp_path / "composites",
            runner=_NeverFfmpegRunner(),
            probe=_NeverMediaProbe(),
        ),
        face_placement=_NeverFacePlacement(),
    )
    request = DerivedAssetBuildRequest(
        build_request_id="build-person-current",
        run_id="run-current",
        command_id="command-current",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        dp_acceptance_id="acceptance-dp-current",
        scope="full_stage",
        event_id=None,
        instructions=(
            DerivedAssetInstruction(
                component_id="component-person",
                event_id="event-person",
                semantic_kind="b_roll",
                implementation_kind="person_inset",
                lane="b_roll",
                display="簡立峰博士",
                t0=30.0,
                t1=34.0,
                source_asset_ref=portrait.record.reference,
                geometry=DerivedAssetGeometry(1920, 1080, "person_inset:v1"),
                recipe_identity="recipe:person-inset:current",
            ),
        ),
        worker_catalog_items=store.worker_selection_catalog().items(),
    )

    result = builder.build(request)

    assert result.status == "failed"
    assert result.error_code == "derived_asset_mismatch"
