"""Production DerivedAssetBuilder Adapter for current long-format recipes."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ._active_store import ActiveAssetPublication, ActiveAssetStore, ActiveAssetStoreError
from ._assets import WorkerSelectionCatalog
from ._derived_assets import (
    BuiltComponentAsset,
    DerivedAssetBuildRequest,
    DerivedAssetBuildResult,
    DerivedAssetInstruction,
    _placement_duration_is_within_ceiling,
)
from ._hyperframes_renderer import (
    FfprobeGeneratedMediaProbe,
    HyperFramesBrowserRenderer,
    PinnedHyperFramesRuntime,
    RenderProcessRunner,
)
from ._long_visual_renderer import (
    LongVisualRenderer,
    LongVisualRenderError,
    LongVisualRenderRequest,
    RenderedLongVisual,
)
from ._projection import (
    ASSET_KIND_BY_IMPLEMENTATION,
    MEDIA_SUFFIX_BY_IMPLEMENTATION,
    NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS,
)

#: 名單本體在 `_projection.VOCABULARY`。
_NEUTRAL_PASSTHROUGH = NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS
_BROWSER_ROLES = {
    "fullscreen_transition": "chapter",
    "hero_title": "hero_title",
    "identity_card": "identity_card",
    "visual_effect": "visual_effect",
}
STOCK_PLACEMENT_DURATION_TOLERANCE_SEC = 1.0 / 30.0


@dataclass(frozen=True, slots=True)
class LongVisualMediaAdapters:
    """Private composition result for the Resolve-compatible render Adapter."""

    title_renderer: LongVisualRenderer


def build_long_visual_media_adapters(
    *,
    workspace_root: str | Path,
    render_output_root: str | Path,
    runtime: PinnedHyperFramesRuntime,
    runner: RenderProcessRunner,
) -> LongVisualMediaAdapters:
    """Wire generated Long media to one process seam and one strict probe."""

    probe = FfprobeGeneratedMediaProbe(runner=runner)
    return LongVisualMediaAdapters(
        title_renderer=LongVisualRenderer(
            browser=HyperFramesBrowserRenderer(
                workspace_root=workspace_root,
                output_root=render_output_root,
                runtime=runtime,
                runner=runner,
                probe=probe,
            )
        )
    )


class LongDerivedAssetBuilder:
    """Resolve or render every current instruction behind one managed Interface."""

    def __init__(
        self,
        *,
        store: ActiveAssetStore,
        title_renderer: LongVisualRenderer,
    ) -> None:
        self._store = store
        self._title_renderer = title_renderer

    def build(self, request: DerivedAssetBuildRequest) -> DerivedAssetBuildResult:
        if request.format != "long" or request.episode_id != self._store.episode_id:
            return self._failed(request, "build_identity_mismatch")
        preflight_error = self._placement_preflight_error(request)
        if preflight_error is not None:
            return self._failed(request, preflight_error)
        assets: list[BuiltComponentAsset] = []
        for instruction in request.instructions:
            if instruction.implementation_kind in _NEUTRAL_PASSTHROUGH:
                built = self._passthrough(request, instruction)
            elif instruction.implementation_kind in _BROWSER_ROLES:
                built = self._render_browser_visual(instruction)
            else:
                return self._failed(request, "unsupported_long_visual")
            if built is None:
                return self._failed(request, "derived_asset_mismatch")
            assets.append(built)
        return DerivedAssetBuildResult(
            build_request_id=request.build_request_id,
            dp_acceptance_id=request.dp_acceptance_id,
            status="ready",
            assets=tuple(assets),
        )

    def _placement_preflight_error(self, request: DerivedAssetBuildRequest) -> str | None:
        if any(
            not _placement_duration_is_within_ceiling(instruction)
            for instruction in request.instructions
        ):
            return "visual_placement_duration_exceeded"
        request_catalog = WorkerSelectionCatalog(request.worker_catalog_items)
        current_catalog = self._store.worker_selection_catalog()
        for instruction in request.instructions:
            if instruction.implementation_kind != "stock_video":
                continue
            reference = instruction.source_asset_ref
            if reference is None:
                return "derived_asset_mismatch"
            try:
                request_item = request_catalog.item(reference)
                current_item = current_catalog.item(reference)
            except ValueError:
                return "derived_asset_mismatch"
            if request_item != current_item or current_item.duration_sec is None:
                return "derived_asset_mismatch"
            # 「長片 Stock 必須是原生橫式」。ADR-069 階段 2 把這條規則的三份實作
            # （`_policy` 的對照表、`_resolve_fusion` 交易中間、選片那一刻）收成
            # 一份，留下的是選片端這一份——但它原本藏在 `_passthrough` 的一串
            # `return None` 裡，跟「沒有 source ref」「目錄漂掉」「資產類別不符」
            # 一起被呼叫端翻成同一個 `derived_asset_mismatch`，DP 拿到的回饋因此
            # 一個字都沒提到方向。階段 5 自己的主張是「一個 reason code 回答一類
            # 問題」，所以規則收成一份之後，名字也要留住。
            #
            # 放在這一關而不是 `_passthrough`：這裡已經拿著兩份目錄、已經在逐支
            # 掃 stock，多一個判斷不用多讀一次索引。
            if current_item.width is None or current_item.height is None:
                return "stock_video_dimensions_unknown"
            if current_item.width <= current_item.height:
                return "stock_video_not_native_landscape"
            if (
                instruction.show_sec
                > current_item.duration_sec + STOCK_PLACEMENT_DURATION_TOLERANCE_SEC
            ):
                return "stock_placement_exceeds_source_duration"
        return None

    def _passthrough(
        self,
        request: DerivedAssetBuildRequest,
        instruction: DerivedAssetInstruction,
    ) -> BuiltComponentAsset | None:
        reference = instruction.source_asset_ref
        if reference is None or instruction.recipe_identity is not None:
            return None
        try:
            request_item = WorkerSelectionCatalog(request.worker_catalog_items).item(reference)
            current_item = self._store.worker_selection_catalog().item(reference)
            resolution = self._store.resolve_worker_asset(reference)
        except (ActiveAssetStoreError, ValueError):
            return None
        if request_item != current_item:
            return None
        expected_kind = ASSET_KIND_BY_IMPLEMENTATION[instruction.implementation_kind]
        if resolution.record.kind is not expected_kind:
            return None
        return BuiltComponentAsset(
            component_id=instruction.component_id,
            event_id=instruction.event_id,
            source_asset_ref=reference,
            final_asset_ref=reference,
            inspection_ref=reference,
            recipe_identity=None,
        )

    # 無頭瀏覽器的第一次啟動會偶發失敗（空 stderr、非零 exit）。渲染器本身沒有
    # 重試，而 build() 是一支卡失敗就整批中止，所以整輪製作會因為一次冷啟動失敗
    # 而作廢——2026-09-08 實測連續 11 次 advance 全部倒在同一張卡的第一次渲染，
    # 但緊接著手動呼叫同一個 render 就成功。舊路線 run_short_broll._render_card
    # 早就有冷卻重試，ADR-066 這條漏掉了。
    _RENDER_ATTEMPTS = 3
    _RENDER_COOLDOWN_SEC = 5.0

    def _render_with_retry(
        self,
        instruction: DerivedAssetInstruction,
        recipe_identity: str,
    ) -> RenderedLongVisual:
        request = LongVisualRenderRequest(
            recipe_identity=recipe_identity,
            event_id=instruction.event_id,
            role=_BROWSER_ROLES[instruction.implementation_kind],  # type: ignore[arg-type]
            display=instruction.display,
            duration_sec=instruction.show_sec,
            target_width=instruction.geometry.target_width,
            target_height=instruction.geometry.target_height,
            layout_identity=instruction.geometry.layout_identity,
        )
        for attempt in range(1, self._RENDER_ATTEMPTS + 1):
            try:
                return self._title_renderer.render(request)
            except LongVisualRenderError:
                if attempt == self._RENDER_ATTEMPTS:
                    raise
                time.sleep(self._RENDER_COOLDOWN_SEC)
        raise AssertionError("unreachable render retry exit")

    def _render_browser_visual(
        self,
        instruction: DerivedAssetInstruction,
    ) -> BuiltComponentAsset | None:
        recipe_identity = instruction.recipe_identity
        if recipe_identity is None or instruction.source_asset_ref is not None:
            return None
        expected_kind = ASSET_KIND_BY_IMPLEMENTATION[instruction.implementation_kind]
        expected_suffix = MEDIA_SUFFIX_BY_IMPLEMENTATION[instruction.implementation_kind]
        try:
            resolution = self._store.find_exact_recipe(recipe_identity)
            if resolution is None:
                rendered = self._render_with_retry(instruction, recipe_identity)
                resolution = self._store.publish(
                    ActiveAssetPublication(
                        source_path=rendered.media.path,
                        kind=expected_kind,
                        recipe_identity=recipe_identity,
                    )
                )
        except (ActiveAssetStoreError, LongVisualRenderError):
            return None
        if (
            resolution.record.kind is not expected_kind
            # `recipe_identity` 只要求「這份媒體是某個配方算出來的」，不要求就是這一個：
            # 兩個配方算出同樣的 bytes 時，store 會回既有那筆（見 `ActiveAssetStore.publish`）。
            # 要求逐字相等會把那條合法路徑打成 `derived_asset_mismatch`。
            or resolution.record.recipe_identity is None
            or resolution.path is None
            or resolution.path.suffix.lower() != expected_suffix
        ):
            return None
        return BuiltComponentAsset(
            component_id=instruction.component_id,
            event_id=instruction.event_id,
            source_asset_ref=None,
            final_asset_ref=resolution.record.reference,
            inspection_ref=resolution.record.reference,
            recipe_identity=recipe_identity,
        )

    @staticmethod
    def _failed(
        request: DerivedAssetBuildRequest,
        error_code: str,
    ) -> DerivedAssetBuildResult:
        return DerivedAssetBuildResult(
            build_request_id=request.build_request_id,
            dp_acceptance_id=request.dp_acceptance_id,
            status="failed",
            error_code=error_code,
        )
