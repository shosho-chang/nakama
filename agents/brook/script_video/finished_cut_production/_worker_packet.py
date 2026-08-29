"""Exact-current stage packet materialization for production semantic workers."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

from ._assets import AssetContractError, AssetKind, AssetResolver, ResolvedAsset
from ._policy import (
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
from ._projection import _WORKER_PROJECTION_COMBINATIONS
from ._records import EventRecord, StageName, StageRequest

_ASSET_REFERENCE_RE = re.compile(r"^asset-sha256:[0-9a-f]{64}$")
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class NamedMedia:
    logical_name: str
    mime_type: str
    inspection_kind: str
    bytes: bytes
    inspection_ref: str
    for_asset_ref: str


@dataclass(frozen=True, slots=True)
class StagePacket:
    stage: StageName
    payload: dict[str, JsonValue]
    media: tuple[NamedMedia, ...] = ()
    format_policy: dict[str, JsonValue] | None = None


class WorkerPacketMaterializer(Protocol):
    def materialize(self, request: StageRequest) -> StagePacket: ...


_CANONICAL_PROJECTION_COMBINATIONS = (
    *_WORKER_PROJECTION_COMBINATIONS,
    ("intentional_aroll", "intentional_aroll", None),
)


class WorkerPacketError(ValueError):
    """A worker packet cannot be proven to belong to its current scope."""


@dataclass(frozen=True, slots=True)
class WorkerPacketScope:
    run_id: str
    episode_id: str
    cut_id: str
    format: Literal["long", "short"]


@dataclass(frozen=True, slots=True)
class WorkerPacketLimits:
    max_packet_json_bytes: int = 2 * 1024 * 1024
    max_media_item_bytes: int = 16 * 1024 * 1024
    max_total_media_bytes: int = 64 * 1024 * 1024
    max_media_items: int = 64

    def __post_init__(self) -> None:
        if (
            min(
                self.max_packet_json_bytes,
                self.max_media_item_bytes,
                self.max_total_media_bytes,
                self.max_media_items,
            )
            <= 0
        ):
            raise WorkerPacketError("worker packet size limits must be positive")


@dataclass(frozen=True, slots=True)
class InspectionPreview:
    mime_type: Literal["image/png", "image/jpeg", "video/mp4"]
    inspection_kind: Literal["preview_frame", "contact_sheet", "preview_clip"]
    bytes: bytes


class InspectionPreviewer(Protocol):
    def preview(self, resolution: ResolvedAsset) -> InspectionPreview: ...


@dataclass(frozen=True, slots=True)
class MediaPreviewProcessResult:
    returncode: int


class MediaPreviewProcessRunner(Protocol):
    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_sec: float,
    ) -> MediaPreviewProcessResult: ...


class SubprocessMediaPreviewProcessRunner:
    """Run the fixed media-preview argv without a command shell."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_sec: float,
    ) -> MediaPreviewProcessResult:
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_sec,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise WorkerPacketError("inspection preview process failed") from error
        return MediaPreviewProcessResult(returncode=completed.returncode)


class StoredAssetPreviewer:
    """Read one already-resolved Active Asset Store object into a bounded bytes-only preview."""

    def __init__(
        self,
        *,
        max_bytes: int = 16 * 1024 * 1024,
        process_runner: MediaPreviewProcessRunner | None = None,
        ffmpeg_executable: str = "ffmpeg",
        timeout_sec: float = 30.0,
    ) -> None:
        if max_bytes <= 0 or timeout_sec <= 0:
            raise WorkerPacketError("stored preview limits must be positive")
        if not ffmpeg_executable.strip():
            raise WorkerPacketError("ffmpeg executable must not be empty")
        self._max_bytes = max_bytes
        self._process_runner = process_runner or SubprocessMediaPreviewProcessRunner()
        self._ffmpeg_executable = ffmpeg_executable
        self._timeout_sec = timeout_sec

    def preview(self, resolution: ResolvedAsset) -> InspectionPreview:
        path = resolution.path
        if path is None:
            raise WorkerPacketError("resolved Active Asset Store object has no readable media")
        if resolution.record.extension in {".mov", ".mp4"}:
            return self._render_video_preview(path)
        try:
            if not path.is_file() or path.stat().st_size > self._max_bytes:
                raise WorkerPacketError(
                    "resolved media is missing or exceeds its preview size limit"
                )
            content = path.read_bytes()
        except OSError as error:
            raise WorkerPacketError("resolved Active Asset Store media is unreadable") from error
        preview_type = {
            ".png": ("image/png", "preview_frame"),
            ".jpg": ("image/jpeg", "preview_frame"),
            ".jpeg": ("image/jpeg", "preview_frame"),
        }.get(resolution.record.extension)
        if preview_type is None:
            raise WorkerPacketError("resolved media type has no inspection preview contract")
        mime_type, inspection_kind = preview_type
        return InspectionPreview(mime_type, inspection_kind, content)

    def _render_video_preview(self, path: Path) -> InspectionPreview:
        if not path.is_file():
            raise WorkerPacketError("resolved media is missing or exceeds its preview size limit")
        with tempfile.TemporaryDirectory(prefix="finished-cut-inspection-") as workspace_text:
            workspace = Path(workspace_text)
            output_name = "preview.png"
            result = self._process_runner.run(
                (
                    self._ffmpeg_executable,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-i",
                    str(path),
                    "-vf",
                    "thumbnail=100,scale=1280:-2:force_original_aspect_ratio=decrease",
                    "-frames:v",
                    "1",
                    "-compression_level",
                    "6",
                    "-y",
                    output_name,
                ),
                cwd=workspace,
                timeout_sec=self._timeout_sec,
            )
            if result.returncode != 0:
                raise WorkerPacketError("inspection preview process failed")
            output = workspace / output_name
            try:
                if not output.is_file() or output.stat().st_size > self._max_bytes:
                    raise WorkerPacketError(
                        "resolved media is missing or exceeds its preview size limit"
                    )
                content = output.read_bytes()
            except OSError as error:
                raise WorkerPacketError("inspection preview output is unreadable") from error
            preview = InspectionPreview("image/png", "preview_frame", content)
            if not _valid_inspection_preview(preview):
                raise WorkerPacketError("inspection preview process produced malformed media")
            return preview


class ProductionWorkerPacketMaterializer:
    """Project one core-created current request onto its stage allowlist."""

    def __init__(
        self,
        *,
        scope: WorkerPacketScope,
        asset_resolver: AssetResolver,
        previewer: InspectionPreviewer,
        limits: WorkerPacketLimits = WorkerPacketLimits(),
    ) -> None:
        self._scope = scope
        self._asset_resolver = asset_resolver
        self._previewer = previewer
        self._limits = limits

    def materialize(self, request: StageRequest) -> StagePacket:
        if (
            request.run_id,
            request.episode_id,
            request.cut_id,
            request.format,
        ) != (
            self._scope.run_id,
            self._scope.episode_id,
            self._scope.cut_id,
            self._scope.format,
        ):
            raise WorkerPacketError("request does not belong to the current worker scope")
        if request.stage == "director":
            context = request.editorial_context
            if context is None or (
                context.episode_id,
                context.cut_id,
                context.format,
            ) != (request.episode_id, request.cut_id, request.format):
                raise WorkerPacketError("Director request lacks its current editorial context")
            return self._finish(request, StagePacket(stage="director", payload={}, media=()))
        if request.editorial_context is not None:
            raise WorkerPacketError("editorial context is visible only to the Director")
        if request.stage == "visual_review":
            return self._visual_packet(request)
        if request.stage != "dp":
            raise WorkerPacketError("unsupported worker stage")
        if tuple(item.reference for item in request.worker_catalog_items) != (
            request.worker_asset_refs
        ):
            raise WorkerPacketError("DP catalog does not match its current references")
        catalog: list[dict[str, str | int | float | None]] = []
        for item in request.worker_catalog_items:
            try:
                resolution = self._asset_resolver.resolve_worker_asset(item.reference)
            except AssetContractError as error:
                raise WorkerPacketError("DP catalog asset is unavailable") from error
            record = resolution.record
            if (
                record.reference != item.reference
                or record.kind != item.kind
                or record.kind
                not in {AssetKind.STOCK, AssetKind.PHOTO, AssetKind.NON_EDITORIAL_CLIP}
                or record.visual_summary != item.visual_summary
                or record.width != item.width
                or record.height != item.height
                or record.duration_sec != item.duration_sec
                or record.recipe_identity is not None
            ):
                raise WorkerPacketError("DP catalog metadata differs from the Active Asset Store")
            catalog.append(
                {
                    "reference": item.reference,
                    "kind": item.kind.value,
                    "visual_summary": item.visual_summary,
                    "width": item.width,
                    "height": item.height,
                    "duration_sec": item.duration_sec,
                }
            )
        return self._finish(
            request,
            StagePacket(stage="dp", payload={"catalog": catalog}, media=()),
        )

    def _visual_packet(self, request: StageRequest) -> StagePacket:
        expected_events = {
            event.event_id: event for event in request.events if not event.intentional_aroll
        }
        if len(request.built_components) != len(expected_events):
            raise WorkerPacketError("Visual build does not cover the current events")
        rows: list[dict[str, str]] = []
        media: list[NamedMedia] = []
        observed_event_ids: list[str] = []
        for index, built in enumerate(request.built_components, start=1):
            inspection_ref = built.inspection_ref or built.final_asset_ref
            if (
                built.event_id not in expected_events
                or _ASSET_REFERENCE_RE.fullmatch(built.final_asset_ref) is None
                or _ASSET_REFERENCE_RE.fullmatch(inspection_ref) is None
            ):
                raise WorkerPacketError("Visual build is not bound to a current event and asset")
            try:
                final_resolution = self._asset_resolver.resolve_active_asset(built.final_asset_ref)
                inspection_resolution = (
                    final_resolution
                    if inspection_ref == built.final_asset_ref
                    else self._asset_resolver.resolve_active_asset(inspection_ref)
                )
            except AssetContractError as error:
                raise WorkerPacketError("Visual asset is unavailable") from error
            if (
                final_resolution.record.reference != built.final_asset_ref
                or inspection_resolution.record.reference != inspection_ref
            ):
                raise WorkerPacketError("Visual asset resolution differs from the current build")
            preview = self._previewer.preview(inspection_resolution)
            if not _valid_inspection_preview(preview):
                raise WorkerPacketError("Visual worker received a malformed inspection preview")
            if len(preview.bytes) > self._limits.max_media_item_bytes:
                raise WorkerPacketError("Visual inspection preview exceeds its size limit")
            extension = {
                "image/png": "png",
                "image/jpeg": "jpg",
                "video/mp4": "mp4",
            }[preview.mime_type]
            rows.append(
                {
                    "component_id": built.component_id,
                    "event_id": built.event_id,
                    "final_asset_ref": built.final_asset_ref,
                    "inspection_ref": inspection_ref,
                }
            )
            media.append(
                NamedMedia(
                    logical_name=f"component-{index:04d}.{extension}",
                    mime_type=preview.mime_type,
                    inspection_kind=preview.inspection_kind,
                    bytes=preview.bytes,
                    inspection_ref=inspection_ref,
                    for_asset_ref=built.final_asset_ref,
                )
            )
            observed_event_ids.append(built.event_id)
        if tuple(observed_event_ids) != tuple(expected_events):
            raise WorkerPacketError("Visual build order differs from the current events")
        return self._finish(
            request,
            StagePacket(
                stage="visual_review",
                payload={"built_components": rows},
                media=tuple(media),
            ),
        )

    def _finish(self, request: StageRequest, packet: StagePacket) -> StagePacket:
        packet = replace(
            packet,
            format_policy=expected_format_policy(request.format, request.stage),
        )
        if len(packet.media) > self._limits.max_media_items:
            raise WorkerPacketError("worker packet exceeds its media item limit")
        if sum(len(item.bytes) for item in packet.media) > self._limits.max_total_media_bytes:
            raise WorkerPacketError("worker packet exceeds its total media size limit")
        if any(_is_legacy_or_absolute(value) for value in _visible_request_strings(request)):
            raise WorkerPacketError("worker packet contains a legacy or absolute path")
        encoded = json.dumps(
            worker_packet_document(request, packet),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > self._limits.max_packet_json_bytes:
            raise WorkerPacketError("worker packet exceeds its JSON size limit")
        return packet


def _valid_inspection_preview(preview: InspectionPreview) -> bool:
    if preview.mime_type == "image/png":
        return preview.inspection_kind in {"preview_frame", "contact_sheet"} and (
            preview.bytes.startswith(b"\x89PNG\r\n\x1a\n")
        )
    if preview.mime_type == "image/jpeg":
        return (
            preview.inspection_kind in {"preview_frame", "contact_sheet"}
            and preview.bytes.startswith(b"\xff\xd8")
            and preview.bytes.endswith(b"\xff\xd9")
        )
    if preview.mime_type == "video/mp4":
        return (
            preview.inspection_kind == "preview_clip"
            and len(preview.bytes) >= 12
            and preview.bytes[4:8] == b"ftyp"
        )
    return False


def _visible_request_strings(request: StageRequest) -> tuple[str, ...]:
    values = [
        request.schema,
        request.run_id,
        request.request_id,
        request.episode_id,
        request.cut_id,
        request.stage,
        request.scope,
    ]
    values.extend(
        value
        for value in (
            request.event_id,
            request.parent_acceptance_id,
            request.feedback,
        )
        if value is not None
    )
    context = request.editorial_context
    if context is not None:
        values.extend(
            (
                context.episode_id,
                context.cut_id,
                context.format,
                context.editorial_master_id,
                context.tight_cut_id,
            )
        )
        values.extend(getattr(context, "editorial_feedback", ()))
        for cue in context.cues:
            values.extend((cue.cue_id, cue.text, cue.section_id))
        for section in context.sections:
            values.extend((section.section_id, section.chapter_title))
            if section.transition_title is not None:
                values.append(section.transition_title)
    for event in request.events:
        values.extend(
            (
                event.event_id,
                *event.master_cue_ids,
                event.text_hash,
                event.intent,
                event.text,
                event.display,
                event.semantic_kind,
                event.implementation_kind,
            )
        )
        values.extend(
            value for value in (event.section_id, event.lane, event.asset_ref) if value is not None
        )
    for item in request.worker_catalog_items:
        values.extend((item.reference, item.kind.value, item.visual_summary))
    for built in request.built_components:
        values.extend(
            value
            for value in (
                built.component_id,
                built.event_id,
                built.final_asset_ref,
                built.inspection_ref,
            )
            if value is not None
        )
    return tuple(values)


def _is_legacy_or_absolute(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    lowered = normalized.lower()
    return (
        "visual-pipeline" in lowered
        or lowered.startswith("file://")
        or lowered.startswith("//")
        or lowered.startswith("/")
        or re.match(r"^[a-z]:/", lowered) is not None
    )


def _current_event_packet(
    event: EventRecord,
    *,
    include_dp: bool,
    final_asset_ref: str | None = None,
) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {
        "event_id": event.event_id,
        "master_cue_ids": list(event.master_cue_ids),
        "text_hash": event.text_hash,
        "intent": event.intent,
        "text": event.text,
        "t0": event.t0,
        "t1": event.t1,
        "section_id": event.section_id,
        "display": event.display,
        "semantic_kind": event.semantic_kind,
        "intentional_aroll": event.intentional_aroll,
    }
    if include_dp:
        values.update(
            {
                "implementation_kind": event.implementation_kind,
                "lane": event.lane,
                "asset_ref": event.asset_ref,
            }
        )
        if event.intentional_aroll:
            if event.visual_placement is not None or final_asset_ref is not None:
                raise WorkerPacketError("intentional A-roll cannot carry Visual Placement")
            values["visual_placement"] = None
        else:
            placement = event.visual_placement
            if placement is None or final_asset_ref is None:
                raise WorkerPacketError(
                    "visual review event requires placement bound to its final asset"
                )
            values["visual_placement"] = {
                "placement_cue_ids": list(placement.placement_cue_ids),
                "t0": placement.t0,
                "t1": placement.t1,
                "section_id": placement.section_id,
                "final_asset_ref": final_asset_ref,
            }
    return values


def worker_packet_document(request: StageRequest, packet: StagePacket) -> dict[str, JsonValue]:
    context = request.editorial_context
    editorial_context: JsonValue = None
    if context is not None:
        editorial_context = {
            "episode_id": context.episode_id,
            "cut_id": context.cut_id,
            "format": context.format,
            "editorial_master_id": context.editorial_master_id,
            "tight_cut_id": context.tight_cut_id,
            "editorial_feedback": list(context.editorial_feedback),
            "duration_sec": context.duration_sec,
            "source_ranges": [
                {"t0": source_range.t0, "t1": source_range.t1}
                for source_range in context.source_ranges
            ],
            "cues": [
                {
                    "cue_id": cue.cue_id,
                    "text": cue.text,
                    "t0": cue.t0,
                    "t1": cue.t1,
                    "section_id": cue.section_id,
                }
                for cue in context.cues
            ],
            "sections": [
                {
                    "section_id": section.section_id,
                    "chapter_title": section.chapter_title,
                    "t0": section.t0,
                    "transition_before": section.transition_before,
                    "transition_title": section.transition_title,
                }
                for section in context.sections
            ],
        }
    current_event: JsonValue = None
    if request.scope == "event_retry":
        if (
            request.event_id is None
            or len(request.events) != 1
            or request.events[0].event_id != request.event_id
        ):
            raise WorkerPacketError("targeted Director request must contain its one current event")
        event = request.events[0]
        current_event = {
            "event_id": event.event_id,
            "master_cue_ids": list(event.master_cue_ids),
            "text_hash": event.text_hash,
            "intent": event.intent,
            "text": event.text,
            "t0": event.t0,
            "t1": event.t1,
            "section_id": event.section_id,
            "display": event.display,
            "semantic_kind": event.semantic_kind,
            "intentional_aroll": event.intentional_aroll,
        }
    current_events: list[JsonValue] = []
    if request.stage in {"dp", "visual_review"}:
        final_refs = {
            component.event_id: component.final_asset_ref for component in request.built_components
        }
        current_events = [
            _current_event_packet(
                event,
                include_dp=request.stage == "visual_review",
                final_asset_ref=final_refs.get(event.event_id),
            )
            for event in request.events
        ]
    return {
        "request": {
            "schema": request.schema,
            "run_id": request.run_id,
            "request_id": request.request_id,
            "episode_id": request.episode_id,
            "cut_id": request.cut_id,
            "format": request.format,
            "stage": request.stage,
            "attempt": request.attempt,
            "scope": request.scope,
            "event_id": request.event_id,
            "parent_acceptance_id": request.parent_acceptance_id,
            "feedback": request.feedback,
            "editorial_context": editorial_context,
            "current_event": current_event,
            "current_events": current_events,
        },
        "format_policy": packet.format_policy,
        "stage_input": packet.payload,
        "media": [
            {
                "logical_name": media.logical_name,
                "mime_type": media.mime_type,
                "inspection_kind": media.inspection_kind,
                "inspection_ref": media.inspection_ref,
                "for_asset_ref": media.for_asset_ref,
            }
            for media in packet.media
        ],
    }


def expected_format_policy(
    format: Literal["long", "short"],
    stage: StageName,
) -> dict[str, JsonValue]:
    projection_combinations: list[JsonValue] = [
        {
            "semantic_kind": semantic_kind,
            "implementation_kind": implementation_kind,
            "lane": lane,
        }
        for semantic_kind, implementation_kind, lane in _CANONICAL_PROJECTION_COMBINATIONS
    ]
    stage_instruction = {
        "director": "author_semantic_events_from_current_context",
        "dp": "implement_current_events_using_only_catalog_references",
        "visual_review": "judge_each_final_rendered_component_from_inspection_bytes",
    }[stage]
    if format == "short":
        return {
            "policy_id": "short_v1",
            "stage": stage,
            "stage_instruction": stage_instruction,
            "projection_combinations": projection_combinations,
            "constraints": {
                "duration_max_sec": SHORT_MAX_DURATION_SEC,
                "title_like_max_cards": SHORT_MAX_TITLE_LIKE_CARDS,
            },
        }
    return {
        "policy_id": "long_v2",
        "stage": stage,
        "stage_instruction": stage_instruction,
        "projection_combinations": projection_combinations,
        "editorial_brief": {
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
        },
        "constraints": {
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
        },
    }
