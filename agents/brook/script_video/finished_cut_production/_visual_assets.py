"""Production DerivedAssetBuilder Adapter for current long-format recipes."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ._active_store import ActiveAssetPublication, ActiveAssetStore, ActiveAssetStoreError
from ._assets import AssetKind, WorkerSelectionCatalog
from ._derived_assets import (
    BuiltComponentAsset,
    DerivedAssetBuildRequest,
    DerivedAssetBuildResult,
    DerivedAssetInstruction,
    _placement_duration_is_within_ceiling,
)
from ._hyperframes_renderer import (
    FfprobeGeneratedMediaProbe,
    GeneratedMediaProbePort,
    HyperFramesBrowserRenderer,
    PinnedHyperFramesRuntime,
    RenderProcessResult,
    RenderProcessRunner,
)
from ._long_visual_renderer import (
    LongVisualRenderer,
    LongVisualRenderError,
    LongVisualRenderRequest,
)

_NEUTRAL_PASSTHROUGH = frozenset({"stock_video", "photo", "non_editorial_clip"})
_BROWSER_ROLES = {
    "fullscreen_transition": "chapter",
    "hero_title": "hero_title",
    "identity_card": "identity_card",
    "visual_effect": "visual_effect",
}
STOCK_PLACEMENT_DURATION_TOLERANCE_SEC = 1.0 / 30.0


@dataclass(frozen=True, slots=True)
class FacePlacementRequest:
    run_id: str
    command_id: str
    episode_id: str
    cut_id: str
    event_id: str
    t0: float
    t1: float
    target_width: int
    target_height: int
    source_width: int
    source_height: int
    max_width_ratio: float = 0.24
    protected_bottom_ratio: float = 0.84


@dataclass(frozen=True, slots=True)
class FaceSafePlacement:
    x_ratio: float
    y_ratio: float
    width_ratio: float
    height_ratio: float
    avoids_faces: bool

    def __post_init__(self) -> None:
        values = (self.x_ratio, self.y_ratio, self.width_ratio, self.height_ratio)
        if (
            not all(math.isfinite(value) for value in values)
            or self.x_ratio < 0
            or self.y_ratio < 0
            or self.width_ratio <= 0
            or self.height_ratio <= 0
            or self.x_ratio + self.width_ratio > 1
            or self.y_ratio + self.height_ratio > 0.84
            or self.width_ratio > 0.24
            or not self.avoids_faces
        ):
            raise ValueError("person inset placement is not face-safe and compact")


@dataclass(frozen=True, slots=True)
class PersonInsetCompositeRequest:
    render_identity: str
    source_path: Path
    target_width: int
    target_height: int
    duration_sec: float
    placement: FaceSafePlacement
    require_alpha: bool = True
    animation: str = "slide_fade"


@dataclass(frozen=True, slots=True)
class PersonInsetCompositeResult:
    path: Path
    width: int
    height: int
    duration_sec: float
    has_alpha: bool
    animated: bool


class PersonInsetCompositorPort(Protocol):
    """External media composition seam."""

    def composite(self, request: PersonInsetCompositeRequest) -> PersonInsetCompositeResult: ...


FfmpegProcessResult = RenderProcessResult
FfmpegRunner = RenderProcessRunner


class FfmpegPersonInsetCompositor:
    """Apply the fixed alpha/animation contract through an injected ffmpeg runner."""

    def __init__(
        self,
        *,
        output_root: str | Path,
        runner: FfmpegRunner,
        probe: GeneratedMediaProbePort,
    ) -> None:
        self._output_root = Path(output_root).resolve()
        self._runner = runner
        self._probe = probe

    def composite(self, request: PersonInsetCompositeRequest) -> PersonInsetCompositeResult:
        source = Path(request.source_path).resolve(strict=True)
        if (
            not source.is_file()
            or not request.render_identity.strip()
            or not request.require_alpha
            or request.animation != "slide_fade"
            or request.target_width <= 0
            or request.target_height <= 0
            or not math.isfinite(request.duration_sec)
            or request.duration_sec <= 0
        ):
            raise ValueError("person inset composition request is invalid")
        identity = hashlib.sha256(
            repr(
                (
                    request.render_identity,
                    str(source),
                    request.target_width,
                    request.target_height,
                    request.duration_sec,
                    request.placement,
                )
            ).encode("utf-8")
        ).hexdigest()[:24]
        self._output_root.mkdir(parents=True, exist_ok=True)
        output = self._output_root / f"person-inset-{identity}.mov"
        inset_width = round(request.target_width * request.placement.width_ratio)
        x = round(request.target_width * request.placement.x_ratio)
        y = round(request.target_height * request.placement.y_ratio)
        travel = round(request.target_width * 0.035)
        filter_graph = (
            f"[0:v]null[base];"
            f"[1:v]scale={inset_width}:-2,format=rgba,"
            "fade=t=in:st=0:d=0.28:alpha=1[inset];"
            f"[base][inset]overlay=x='{x}+{travel}*(1-min(t/0.35\\,1))':"
            f"y={y}:format=auto[out]"
        )
        with tempfile.TemporaryDirectory(
            prefix=".person-inset-",
            dir=self._output_root,
        ) as workspace_text:
            staged_output = Path(workspace_text) / "encoded.mov"
            arguments = (
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                (
                    f"color=c=black@0.0:s={request.target_width}x{request.target_height}:"
                    f"r=30:d={request.duration_sec:.6f},format=rgba"
                ),
                "-loop",
                "1",
                "-i",
                str(source),
                "-filter_complex",
                filter_graph,
                "-map",
                "[out]",
                "-t",
                f"{request.duration_sec:.6f}",
                "-an",
                "-c:v",
                "prores_ks",
                "-profile:v",
                "4",
                "-pix_fmt",
                "yuva444p12le",
                "-movflags",
                "+faststart",
                str(staged_output),
            )
            try:
                result = self._runner.run(
                    arguments,
                    cwd=None,
                    timeout_sec=max(30.0, request.duration_sec * 10.0),
                )
            except Exception as exc:
                raise ValueError("ffmpeg person inset process failed") from exc
            if result.returncode != 0 or not staged_output.is_file():
                raise ValueError(f"ffmpeg person inset composition failed: {result.stderr.strip()}")
            try:
                probe = self._probe.inspect(staged_output)
            except Exception as exc:
                raise ValueError("person inset media probe failed") from exc
            if (
                probe.codec_name != "prores"
                or probe.pixel_format != "yuva444p12le"
                or probe.width != request.target_width
                or probe.height != request.target_height
                or not math.isclose(
                    probe.duration_sec,
                    request.duration_sec,
                    rel_tol=0,
                    abs_tol=(1 / 24) + 1e-6,
                )
                or not probe.has_alpha
            ):
                raise ValueError("person inset media probe violates Resolve alpha contract")
            os.replace(staged_output, output)
        return PersonInsetCompositeResult(
            path=output,
            width=probe.width,
            height=probe.height,
            duration_sec=probe.duration_sec,
            has_alpha=probe.has_alpha,
            animated=True,
        )


@dataclass(frozen=True, slots=True)
class LongVisualMediaAdapters:
    """Private composition result for the two Resolve-compatible render Adapters."""

    title_renderer: LongVisualRenderer
    person_inset_compositor: FfmpegPersonInsetCompositor


def build_long_visual_media_adapters(
    *,
    workspace_root: str | Path,
    render_output_root: str | Path,
    inset_output_root: str | Path,
    runtime: PinnedHyperFramesRuntime,
    runner: RenderProcessRunner,
) -> LongVisualMediaAdapters:
    """Wire all generated Long media to one process seam and one strict probe."""

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
        ),
        person_inset_compositor=FfmpegPersonInsetCompositor(
            output_root=inset_output_root,
            runner=runner,
            probe=probe,
        ),
    )


class FacialSafePlacementPort(Protocol):
    """External talking-head face-placement seam."""

    def place(self, request: FacePlacementRequest) -> FaceSafePlacement: ...


class LongDerivedAssetBuilder:
    """Resolve or render every current instruction behind one managed Interface."""

    def __init__(
        self,
        *,
        store: ActiveAssetStore,
        title_renderer: LongVisualRenderer,
        compositor: PersonInsetCompositorPort,
        face_placement: FacialSafePlacementPort,
    ) -> None:
        self._store = store
        self._title_renderer = title_renderer
        self._compositor = compositor
        self._face_placement = face_placement

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
            elif instruction.implementation_kind == "person_inset":
                built = self._render_person_inset(request, instruction)
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
        expected_kind = {
            "stock_video": AssetKind.STOCK,
            "photo": AssetKind.PHOTO,
            "non_editorial_clip": AssetKind.NON_EDITORIAL_CLIP,
        }[instruction.implementation_kind]
        if resolution.record.kind is not expected_kind:
            return None
        if instruction.implementation_kind == "stock_video" and (
            current_item.width is None
            or current_item.height is None
            or current_item.width <= current_item.height
        ):
            return None
        return BuiltComponentAsset(
            component_id=instruction.component_id,
            event_id=instruction.event_id,
            source_asset_ref=reference,
            final_asset_ref=reference,
            inspection_ref=reference,
            recipe_identity=None,
        )

    def _render_browser_visual(
        self,
        instruction: DerivedAssetInstruction,
    ) -> BuiltComponentAsset | None:
        recipe_identity = instruction.recipe_identity
        if recipe_identity is None or instruction.source_asset_ref is not None:
            return None
        expected_kind = {
            "fullscreen_transition": AssetKind.CHAPTER_RENDER,
            "hero_title": AssetKind.TITLE_RENDER,
            "identity_card": AssetKind.CONCEPT_RENDER,
            "visual_effect": AssetKind.CONCEPT_RENDER,
        }[instruction.implementation_kind]
        expected_suffix = (
            ".mp4" if instruction.implementation_kind == "fullscreen_transition" else ".mov"
        )
        try:
            resolution = self._store.find_exact_recipe(recipe_identity)
            if resolution is None:
                rendered = self._title_renderer.render(
                    LongVisualRenderRequest(
                        recipe_identity=recipe_identity,
                        event_id=instruction.event_id,
                        role=_BROWSER_ROLES[instruction.implementation_kind],  # type: ignore[arg-type]
                        display=instruction.display,
                        duration_sec=instruction.show_sec,
                        target_width=instruction.geometry.target_width,
                        target_height=instruction.geometry.target_height,
                        layout_identity=instruction.geometry.layout_identity,
                    )
                )
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
            or resolution.record.recipe_identity != recipe_identity
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

    def _render_person_inset(
        self,
        request: DerivedAssetBuildRequest,
        instruction: DerivedAssetInstruction,
    ) -> BuiltComponentAsset | None:
        reference = instruction.source_asset_ref
        recipe_identity = instruction.recipe_identity
        if reference is None or recipe_identity is None:
            return None
        try:
            request_item = WorkerSelectionCatalog(request.worker_catalog_items).item(reference)
            current_item = self._store.worker_selection_catalog().item(reference)
            source = self._store.resolve_worker_asset(reference)
            if (
                request_item != current_item
                or source.record.kind is not AssetKind.PHOTO
                or current_item.width is None
                or current_item.height is None
                or source.path is None
                or instruction.geometry.layout_identity != "person_inset:v1"
            ):
                return None
            resolution = self._store.find_exact_recipe(recipe_identity)
            if resolution is None:
                placement = self._face_placement.place(
                    FacePlacementRequest(
                        run_id=request.run_id,
                        command_id=request.command_id,
                        episode_id=request.episode_id,
                        cut_id=request.cut_id,
                        event_id=instruction.event_id,
                        t0=instruction.t0,
                        t1=instruction.t1,
                        target_width=instruction.geometry.target_width,
                        target_height=instruction.geometry.target_height,
                        source_width=current_item.width,
                        source_height=current_item.height,
                    )
                )
                rendered = self._compositor.composite(
                    PersonInsetCompositeRequest(
                        render_identity=recipe_identity,
                        source_path=source.path,
                        target_width=instruction.geometry.target_width,
                        target_height=instruction.geometry.target_height,
                        duration_sec=instruction.show_sec,
                        placement=placement,
                    )
                )
                if (
                    not Path(rendered.path).is_file()
                    or Path(rendered.path).suffix.lower() != ".mov"
                    or rendered.width != instruction.geometry.target_width
                    or rendered.height != instruction.geometry.target_height
                    or not math.isclose(
                        rendered.duration_sec,
                        instruction.show_sec,
                        rel_tol=0,
                        abs_tol=1e-6,
                    )
                    or not rendered.has_alpha
                    or not rendered.animated
                ):
                    return None
                resolution = self._store.publish(
                    ActiveAssetPublication(
                        source_path=rendered.path,
                        kind=AssetKind.COMPOSITE,
                        recipe_identity=recipe_identity,
                    )
                )
        except (ActiveAssetStoreError, ValueError, OSError):
            return None
        if (
            resolution.record.kind is not AssetKind.COMPOSITE
            or resolution.record.recipe_identity != recipe_identity
            or resolution.path is None
            or resolution.path.suffix.lower() != ".mov"
        ):
            return None
        return BuiltComponentAsset(
            component_id=instruction.component_id,
            event_id=instruction.event_id,
            source_asset_ref=reference,
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
