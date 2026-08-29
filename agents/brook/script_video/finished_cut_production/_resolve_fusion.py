"""Concrete DaVinci Resolve scripting facade for Finished Cut Production."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ._resolve import ResolveTransactionError, TimelineIdentity
from ._resolve_davinci import (
    RenderRequest,
    ResolveCutBinding,
    ResolveProjectBinding,
    ResolveProjectIdentity,
    ResolveTimelineItem,
    ResolveTimelineState,
    ResolveTimelineTrack,
)
from ._timeline_apply import TimelinePlacement

_DERIVED_VIDEO_TRACKS = range(2, 8)
_LANE_TRACKS = {
    "b_roll": 2,
    "hero_title": 3,
    "identity_card": 4,
    "fullscreen_transition": 6,
    "visual_effect": 7,
}


@dataclass(frozen=True, slots=True)
class ResolveDatabaseIdentity:
    db_type: str
    db_name: str
    ip_address: str | None = None

    def __post_init__(self) -> None:
        if not self.db_type.strip() or not self.db_name.strip():
            raise ResolveTransactionError("Resolve database identity is incomplete")


@dataclass(frozen=True, slots=True)
class ResolveProjectLocator:
    episode_id: str
    database: ResolveDatabaseIdentity
    folder: str
    project_name: str

    def __post_init__(self) -> None:
        _project_folder_identity(self.folder)
        if not self.episode_id.strip() or not self.project_name.strip():
            raise ResolveTransactionError("Resolve project locator is incomplete")


@dataclass(frozen=True, slots=True)
class ResolveCutLocator:
    cut_id: str
    timeline_name: str

    def __post_init__(self) -> None:
        if not self.cut_id.strip() or not self.timeline_name.strip():
            raise ResolveTransactionError("Resolve cut locator is incomplete")


class MediaIdentityResolver(Protocol):
    """Resolve a cached media-object digest without hashing source media."""

    def digest_for(self, media_pool_item: object) -> str: ...


def connect_resolve_scripting(
    *,
    scriptapp: Callable[[str], object | None] | None = None,
) -> object:
    """Lazily connect to the installed Resolve scripting host."""

    if scriptapp is None:
        try:
            module = importlib.import_module("DaVinciResolveScript")
        except ImportError as exc:
            raise ResolveTransactionError("Resolve scripting module is unavailable") from exc
        candidate = getattr(module, "scriptapp", None)
        if not callable(candidate):
            raise ResolveTransactionError("Resolve scriptapp connector is unavailable")
        scriptapp = candidate
    try:
        resolve = scriptapp("Resolve")
    except Exception as exc:
        raise ResolveTransactionError("Resolve scripting connection failed") from exc
    if resolve is None:
        raise ResolveTransactionError("Resolve scripting host is unavailable")
    return resolve


def current_timeline_identities(
    locator: ResolveProjectLocator, *, resolve: object | None = None
) -> tuple[TimelineIdentity, ...]:
    """List the project's Timelines as they exist right now.

    Every committed transaction duplicate-swaps the canonical Timeline, so its
    uid changes each time.  A uid written into a config file is therefore stale
    from the first revision onwards; callers bind by Timeline **name** and
    resolve the uid here, at job time.
    """
    facade = DaVinciResolveFacade(
        resolve=resolve if resolve is not None else connect_resolve_scripting(),
        locator=locator,
    )
    return facade.timeline_identities()


class DaVinciResolveFacade:
    """Hide Resolve's dynamic object graph behind the typed Resolve facade Interface."""

    def __init__(
        self,
        *,
        resolve: object,
        locator: ResolveProjectLocator,
        # Only materialization resolves media identity.  Listing Timelines does
        # not, so a read-only caller may omit it (see current_timeline_identities).
        media_identity_resolver: MediaIdentityResolver | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        render_timeout_sec: float = 1_200.0,
    ) -> None:
        self._resolve = resolve
        self._locator = locator
        self._media_identity_resolver = media_identity_resolver
        self._monotonic = monotonic
        self._sleep = sleep
        self._render_timeout_sec = render_timeout_sec
        if not math.isfinite(render_timeout_sec) or render_timeout_sec <= 0:
            raise ResolveTransactionError("Resolve render timeout is invalid")

    def project_identity(self) -> ResolveProjectIdentity:
        self._current_project()
        return ResolveProjectIdentity(
            episode_id=self._locator.episode_id,
            name=self._locator.project_name,
            uid=_synthetic_project_uid(self._locator),
        )

    def timeline_identities(self) -> tuple[TimelineIdentity, ...]:
        identities = [row[0] for row in self._timeline_objects()]
        if len({row.name for row in identities}) != len(identities) or len(
            {row.uid for row in identities}
        ) != len(identities):
            raise ResolveTransactionError("Resolve Timeline inventory is ambiguous")
        return tuple(identities)

    def timeline_state(self, uid: str) -> ResolveTimelineState:
        timeline = self._timeline_by_uid(uid)
        start_frame = _required_int(timeline, "GetStartFrame")
        end_frame = _required_int(timeline, "GetEndFrame")
        if end_frame <= start_frame:
            raise ResolveTransactionError("Resolve Timeline range is invalid")
        tracks: list[ResolveTimelineTrack] = []
        items: list[ResolveTimelineItem] = []
        for track_type in ("video", "audio", "subtitle"):
            count = _required_nonnegative_int(timeline, "GetTrackCount", track_type)
            for track_index in range(1, count + 1):
                raw_items = _required_call(
                    timeline,
                    "GetItemListInTrack",
                    track_type,
                    track_index,
                )
                if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
                    raise ResolveTransactionError("Resolve Timeline track items are invalid")
                track_items = tuple(
                    self._snapshot_item(item, track_type=track_type, track_index=track_index)
                    for item in raw_items
                )
                tracks.append(
                    ResolveTimelineTrack(
                        track_type=track_type,  # type: ignore[arg-type]
                        track_index=track_index,
                        name=_required_string(
                            timeline,
                            "GetTrackName",
                            track_type,
                            track_index,
                        ),
                        enabled=_required_bool(
                            timeline,
                            "GetIsTrackEnabled",
                            track_type,
                            track_index,
                        ),
                        locked=_required_bool(
                            timeline,
                            "GetIsTrackLocked",
                            track_type,
                            track_index,
                        ),
                        item_ids=tuple(item.item_id for item in track_items),
                    )
                )
                items.extend(track_items)
        if len({item.item_id for item in items}) != len(items):
            raise ResolveTransactionError("Resolve Timeline item identity is ambiguous")
        return ResolveTimelineState(
            start_frame=start_frame,
            end_frame=end_frame,
            items=tuple(items),
            tracks=tuple(tracks),
            frame_rate=_positive_number(
                _required_call(timeline, "GetSetting", "timelineFrameRate"),
                label="Timeline frame rate",
            ),
        )

    def duplicate_timeline(self, uid: str, name: str) -> TimelineIdentity:
        if not isinstance(name, str) or not name.strip():
            raise ResolveTransactionError("Resolve duplicate Timeline name is incomplete")
        if any(identity.name == name for identity in self.timeline_identities()):
            raise ResolveTransactionError("Resolve duplicate Timeline name already exists")
        source = self._timeline_by_uid(uid)
        duplicate = _required_call(source, "DuplicateTimeline", name)
        identity = TimelineIdentity(
            name=_required_string(duplicate, "GetName"),
            uid=_required_string(duplicate, "GetUniqueId"),
        )
        if identity.name != name or identity.uid == uid:
            raise ResolveTransactionError("Resolve duplicate Timeline identity is invalid")
        matches = [row for row in self.timeline_identities() if row == identity]
        if len(matches) != 1:
            raise ResolveTransactionError("Resolve duplicate Timeline is missing from inventory")
        return identity

    def rename_timeline(self, uid: str, name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ResolveTransactionError("Resolve Timeline rename is incomplete")
        occupied = [
            row for row in self.timeline_identities() if row.name == name and row.uid != uid
        ]
        if occupied:
            raise ResolveTransactionError("Resolve Timeline rename target already exists")
        timeline = self._timeline_by_uid(uid)
        _require_true_call(timeline, "SetName", name)
        matches = [
            identity
            for identity in self.timeline_identities()
            if identity.uid == uid and identity.name == name
        ]
        if len(matches) != 1:
            raise ResolveTransactionError("Resolve Timeline rename did not persist exactly")

    def delete_timeline(self, uid: str) -> None:
        timeline = self._timeline_by_uid(uid)
        project = self._current_project()
        media_pool = _required_call(project, "GetMediaPool")
        _require_true_call(media_pool, "DeleteTimelines", [timeline])
        if any(identity.uid == uid for identity in self.timeline_identities()):
            raise ResolveTransactionError("Resolve Timeline deletion did not persist")

    def save_project(self) -> None:
        manager = self._current_project_manager()
        _require_true_call(manager, "SaveProject")

    def clear_derived_lanes(self, uid: str) -> None:
        timeline = self._timeline_by_uid(uid)
        video_count = _required_nonnegative_int(timeline, "GetTrackCount", "video")
        for track_index in _DERIVED_VIDEO_TRACKS:
            if track_index > video_count:
                break
            raw_items = _required_call(
                timeline,
                "GetItemListInTrack",
                "video",
                track_index,
            )
            if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
                raise ResolveTransactionError("Resolve derived lane items are invalid")
            if raw_items:
                _require_true_call(timeline, "DeleteClips", list(raw_items), False)
                remaining = _required_call(
                    timeline,
                    "GetItemListInTrack",
                    "video",
                    track_index,
                )
                if remaining != []:
                    raise ResolveTransactionError("Resolve derived lane did not clear exactly")

    def append_pre_rendered(self, uid: str, placement: TimelinePlacement) -> None:
        try:
            source_path = Path(placement.source_path).resolve(strict=True)
        except OSError as exc:
            raise ResolveTransactionError("pre-rendered media is missing") from exc
        if not source_path.is_file():
            raise ResolveTransactionError("pre-rendered media is not a regular file")
        try:
            track_index = _LANE_TRACKS[placement.lane]
        except KeyError as exc:
            raise ResolveTransactionError("pre-rendered placement lane is unsupported") from exc
        timeline = self._timeline_by_uid(uid)
        project = self._current_project()
        media_pool = _required_call(project, "GetMediaPool")
        imported = _required_call(media_pool, "ImportMedia", [str(source_path)])
        if (
            not isinstance(imported, Sequence)
            or isinstance(imported, (str, bytes))
            or len(imported) != 1
            or imported[0] is None
        ):
            raise ResolveTransactionError("Resolve media import did not return one exact item")
        media_pool_item = imported[0]
        properties = _required_mapping(media_pool_item, "GetClipProperty")
        actual_path = _media_path(properties)
        if actual_path != source_path:
            raise ResolveTransactionError("Resolve imported media path does not match final asset")
        _assert_online_media(properties)
        width, height = _media_resolution(properties)
        if placement.implementation_kind == "stock_video":
            if width <= height or width * 9 != height * 16:
                raise ResolveTransactionError("Stock media is not native 16:9 landscape")
        elif (width, height) != (1920, 1080):
            raise ResolveTransactionError("derived media is not a pre-rendered 1920x1080 canvas")
        source_fps = _positive_number(properties.get("FPS"), label="media FPS")
        source_frames = _positive_int(properties.get("Frames"), label="media frame count")
        try:
            digest = self._media_identity_resolver.digest_for(media_pool_item)
        except Exception as exc:
            raise ResolveTransactionError("Resolve media identity lookup failed") from exc
        if not _is_sha256(digest):
            raise ResolveTransactionError("Resolve media identity resolver returned no digest")
        timeline_fps = _positive_number(
            _required_call(timeline, "GetSetting", "timelineFrameRate"),
            label="Timeline frame rate",
        )
        duration_sec = placement.t1 - placement.t0
        if (
            not math.isfinite(placement.t0)
            or not math.isfinite(placement.t1)
            or placement.t0 < 0
            or duration_sec <= 0
        ):
            raise ResolveTransactionError("pre-rendered placement range is invalid")
        source_duration_frames = round(duration_sec * source_fps)
        record_duration_frames = round(duration_sec * timeline_fps)
        if source_duration_frames <= 0 or source_duration_frames > source_frames:
            raise ResolveTransactionError("pre-rendered media is shorter than its placement")
        timeline_start = _required_int(timeline, "GetStartFrame")
        record_frame = timeline_start + round(placement.t0 * timeline_fps)
        current_tracks = _required_nonnegative_int(timeline, "GetTrackCount", "video")
        while current_tracks < track_index:
            _require_true_call(timeline, "AddTrack", "video")
            updated = _required_nonnegative_int(timeline, "GetTrackCount", "video")
            if updated != current_tracks + 1:
                raise ResolveTransactionError("Resolve did not add the requested derived lane")
            current_tracks = updated
        _require_true_call(project, "SetCurrentTimeline", timeline)
        selected = _required_call(project, "GetCurrentTimeline")
        if _required_string(selected, "GetUniqueId") != uid:
            raise ResolveTransactionError("Resolve selected a different Timeline before append")
        appended = _required_call(
            media_pool,
            "AppendToTimeline",
            [
                {
                    "mediaPoolItem": media_pool_item,
                    "mediaType": 1,
                    "trackIndex": track_index,
                    "recordFrame": record_frame,
                    "startFrame": 0,
                    "endFrame": source_duration_frames,
                }
            ],
        )
        if (
            not isinstance(appended, Sequence)
            or isinstance(appended, (str, bytes))
            or len(appended) != 1
            or appended[0] is None
        ):
            raise ResolveTransactionError("Resolve AppendToTimeline did not return one item")
        item = appended[0]
        actual_track = _required_call(item, "GetTrackTypeAndIndex")
        if list(actual_track) != ["video", track_index]:
            raise ResolveTransactionError("Resolve appended media to the wrong track")
        actual_start = _required_int(item, "GetStart", False)
        actual_end = _required_int(item, "GetEnd", False)
        actual_source_start = _required_int(item, "GetSourceStartFrame")
        actual_source_end = _required_int(item, "GetSourceEndFrame")
        actual_media_pool_item = _required_call(item, "GetMediaPoolItem")
        actual_media_properties = _required_mapping(actual_media_pool_item, "GetClipProperty")
        _assert_online_media(actual_media_properties)
        try:
            actual_digest = self._media_identity_resolver.digest_for(actual_media_pool_item)
        except Exception as exc:
            raise ResolveTransactionError("Resolve appended media identity lookup failed") from exc
        same_media = (
            _media_path(actual_media_properties) == source_path
            and actual_digest.lower() == digest.lower()
        )
        if (
            actual_start != record_frame
            or actual_end != record_frame + record_duration_frames
            or actual_source_start != 0
            or actual_source_end
            not in {
                source_duration_frames - 1,
                source_duration_frames,
            }
            or not same_media
        ):
            raise ResolveTransactionError(
                "Resolve appended media range or identity drifted: "
                f"actual=({actual_start},{actual_end},{actual_source_start},"
                f"{actual_source_end},{same_media}); "
                f"expected=({record_frame},{record_frame + record_duration_frames},0,"
                f"{source_duration_frames - 1}|{source_duration_frames},True)"
            )

    def select_timeline(self, uid: str) -> None:
        timeline = self._timeline_by_uid(uid)
        project = self._current_project()
        _require_true_call(project, "SetCurrentTimeline", timeline)
        selected = _required_call(project, "GetCurrentTimeline")
        if _required_string(selected, "GetUniqueId") != uid:
            raise ResolveTransactionError("Resolve current Timeline does not match requested UID")

    def render_preview(self, uid: str, request: RenderRequest) -> Path:
        if (
            request.container != "mp4"
            or request.video_codec != "H264"
            or request.export_audio is not True
            or request.audio_codec != "aac"
        ):
            raise ResolveTransactionError("Resolve preview render request is not H.264/AAC")
        output = Path(request.output).resolve()
        if output.suffix.lower() != ".mp4" or output.exists():
            raise ResolveTransactionError("Resolve preview output must be a fresh exact MP4 path")
        output.parent.mkdir(parents=True, exist_ok=True)
        timeline = self._timeline_by_uid(uid)
        self.select_timeline(uid)
        project = self._current_project()
        _require_true_call(project, "SetCurrentRenderFormatAndCodec", "mp4", "H264")
        _require_true_call(project, "SetCurrentRenderMode", 1)
        settings = {
            "SelectAllFrames": False,
            "MarkIn": _required_int(timeline, "GetStartFrame"),
            "MarkOut": _required_int(timeline, "GetEndFrame"),
            "TargetDir": str(output.parent),
            "CustomName": output.stem,
            "ExportVideo": True,
            "ExportAudio": True,
            "AudioCodec": "aac",
            "FormatWidth": 1920,
            "FormatHeight": 1080,
        }
        _require_true_call(project, "SetRenderSettings", settings)
        job_id = _required_string(project, "AddRenderJob")
        try:
            try:
                _require_true_keyword_call(
                    project,
                    "StartRendering",
                    [job_id],
                    isInteractiveMode=False,
                )
                deadline = self._monotonic() + self._render_timeout_sec
                while _required_bool(project, "IsRenderingInProgress"):
                    if self._monotonic() >= deadline:
                        stop = getattr(project, "StopRendering", None)
                        if callable(stop):
                            stop()
                        raise ResolveTransactionError("Resolve preview render timed out")
                    self._sleep(0.25)
                status = _required_mapping(project, "GetRenderJobStatus", job_id)
                if status.get("JobStatus") != "Complete":
                    raise ResolveTransactionError("Resolve preview render job did not complete")
                if not output.is_file():
                    raise ResolveTransactionError(
                        "Resolve preview render did not create exact output"
                    )
            finally:
                _require_true_call(project, "DeleteRenderJob", job_id)
        except BaseException:
            try:
                output.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                raise ResolveTransactionError(
                    "Resolve failed preview could not be removed safely"
                ) from cleanup_exc
            raise
        return output

    def _snapshot_item(
        self,
        item: object,
        *,
        track_type: str,
        track_index: int,
    ) -> ResolveTimelineItem:
        actual_track = _required_call(item, "GetTrackTypeAndIndex")
        if (
            not isinstance(actual_track, Sequence)
            or isinstance(actual_track, (str, bytes))
            or len(actual_track) != 2
            or actual_track[0] != track_type
            or actual_track[1] != track_index
        ):
            raise ResolveTransactionError("Resolve Timeline item reports the wrong track")
        start_frame = _required_int(item, "GetStart", False)
        end_frame = _required_int(item, "GetEnd", False)
        if track_type == "subtitle":
            source_in = start_frame
            source_out = end_frame
        else:
            source_in = _required_int(item, "GetSourceStartFrame")
            source_out = _required_int(item, "GetSourceEndFrame")
        if end_frame <= start_frame or source_out < source_in:
            raise ResolveTransactionError("Resolve Timeline item range is invalid")
        timeline_properties = _required_mapping(item, "GetProperty")
        properties: list[tuple[str, str | int | float | bool | None]] = [
            ("clip_enabled", _required_bool(item, "GetClipEnabled")),
            ("timeline_properties", _canonical_json(timeline_properties)),
        ]
        if track_type == "subtitle":
            properties.append(("Text", _required_string(item, "GetName")))
        fusion_count = _required_nonnegative_int(item, "GetFusionCompCount")
        fusion_rows: dict[str, object] = {}
        for index in range(1, fusion_count + 1):
            composition = _required_call(item, "GetFusionCompByIndex", index)
            tools = _required_mapping(composition, "GetToolList", False)
            tool_rows: dict[str, object] = {}
            for name, tool in tools.items():
                if not isinstance(name, str) or not name.strip():
                    raise ResolveTransactionError("Resolve Fusion tool identity is invalid")
                tool_rows[name] = _required_mapping(tool, "SaveSettings")
            fusion_rows[str(index)] = tool_rows
        properties.append(("fusion_settings", _canonical_json(fusion_rows)))
        if track_type == "audio":
            audio_mapping = _required_string(item, "GetSourceAudioChannelMapping")
            try:
                parsed_audio = json.loads(audio_mapping)
            except json.JSONDecodeError as exc:
                raise ResolveTransactionError("Resolve audio mapping is invalid") from exc
            properties.append(("source_audio_mapping", _canonical_json(parsed_audio)))
        if track_type == "subtitle":
            media_digest = hashlib.sha256(b"resolve-subtitle-item").hexdigest()
        else:
            media_pool_item = _required_call(item, "GetMediaPoolItem")
            try:
                media_digest = self._media_identity_resolver.digest_for(media_pool_item)
            except Exception as exc:
                raise ResolveTransactionError("Resolve media identity lookup failed") from exc
        if not _is_sha256(media_digest):
            raise ResolveTransactionError("Resolve media identity resolver returned no digest")
        return ResolveTimelineItem(
            item_id=_required_string(item, "GetUniqueId"),
            track_type=track_type,  # type: ignore[arg-type]
            track_index=track_index,
            start_frame=start_frame,
            end_frame=end_frame,
            source_in_frame=source_in,
            source_out_frame=source_out,
            properties=tuple(properties),
            media_digest=media_digest.lower(),
        )

    def _timeline_objects(self) -> tuple[tuple[TimelineIdentity, object], ...]:
        project = self._current_project()
        count = _required_nonnegative_int(project, "GetTimelineCount")
        rows: list[tuple[TimelineIdentity, object]] = []
        for index in range(1, count + 1):
            timeline = _required_call(project, "GetTimelineByIndex", index)
            rows.append(
                (
                    TimelineIdentity(
                        name=_required_string(timeline, "GetName"),
                        uid=_required_string(timeline, "GetUniqueId"),
                    ),
                    timeline,
                )
            )
        identities = [identity for identity, _timeline in rows]
        if len({row.name for row in identities}) != len(identities) or len(
            {row.uid for row in identities}
        ) != len(identities):
            raise ResolveTransactionError("Resolve Timeline inventory is ambiguous")
        return tuple(rows)

    def _timeline_by_uid(self, uid: str) -> object:
        if not isinstance(uid, str) or not uid.strip():
            raise ResolveTransactionError("Resolve Timeline UID is incomplete")
        matches = [
            timeline for identity, timeline in self._timeline_objects() if identity.uid == uid
        ]
        if len(matches) != 1:
            raise ResolveTransactionError("Resolve Timeline UID is not exact")
        return matches[0]

    def _current_project(self) -> object:
        manager = self._current_project_manager()
        project = _required_call(manager, "GetCurrentProject")
        if _required_string(project, "GetName") != self._locator.project_name:
            raise ResolveTransactionError("Resolve current project does not match locator")
        return project

    def _current_project_manager(self) -> object:
        manager = _required_call(self._resolve, "GetProjectManager")
        raw_database = _required_call(manager, "GetCurrentDatabase")
        if not isinstance(raw_database, dict):
            raise ResolveTransactionError("Resolve current database identity is invalid")
        actual_database = ResolveDatabaseIdentity(
            db_type=_mapping_string(raw_database, "DbType"),
            db_name=_mapping_string(raw_database, "DbName"),
            ip_address=_optional_mapping_string(raw_database, "IpAddress"),
        )
        if actual_database != self._locator.database:
            raise ResolveTransactionError("Resolve current database does not match locator")
        if _required_project_folder(manager, "GetCurrentFolder") != self._locator.folder:
            raise ResolveTransactionError("Resolve current project folder does not match locator")
        return manager


def discover_resolve_project_binding(
    facade: DaVinciResolveFacade,
    *,
    cuts: tuple[ResolveCutLocator, ...],
) -> ResolveProjectBinding:
    """Bind cuts only from the current live Timeline inventory."""

    if not cuts or len({cut.cut_id for cut in cuts}) != len(cuts):
        raise ResolveTransactionError("Resolve cut discovery is incomplete or ambiguous")
    identities = facade.timeline_identities()
    bindings: list[ResolveCutBinding] = []
    for cut in cuts:
        matches = [row for row in identities if row.name == cut.timeline_name]
        if len(matches) != 1:
            raise ResolveTransactionError("Resolve cut Timeline is not exact in live inventory")
        bindings.append(ResolveCutBinding(cut_id=cut.cut_id, canonical=matches[0]))
    project = facade.project_identity()
    return ResolveProjectBinding(
        episode_id=project.episode_id,
        project_name=project.name,
        project_uid=project.uid,
        cuts=tuple(bindings),
    )


def _synthetic_project_uid(locator: ResolveProjectLocator) -> str:
    encoded = json.dumps(
        {
            "database": {
                "db_name": locator.database.db_name,
                "db_type": locator.database.db_type,
                "ip_address": locator.database.ip_address,
            },
            "folder": locator.folder,
            "project_name": locator.project_name,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"resolve-project:{hashlib.sha256(encoded).hexdigest()}"


def _required_call(target: object, method_name: str, *arguments: object) -> object:
    method = getattr(target, method_name, None)
    if not callable(method):
        raise ResolveTransactionError(f"Resolve method is unavailable: {method_name}")
    try:
        value = method(*arguments)
    except Exception as exc:
        raise ResolveTransactionError(f"Resolve method failed: {method_name}") from exc
    if value is None:
        raise ResolveTransactionError(f"Resolve method returned no value: {method_name}")
    return value


def _require_true_call(target: object, method_name: str, *arguments: object) -> None:
    value = _required_call(target, method_name, *arguments)
    if value is not True:
        raise ResolveTransactionError(f"Resolve mutation was rejected: {method_name}")


def _require_true_keyword_call(
    target: object,
    method_name: str,
    *arguments: object,
    **keywords: object,
) -> None:
    method = getattr(target, method_name, None)
    if not callable(method):
        raise ResolveTransactionError(f"Resolve method is unavailable: {method_name}")
    try:
        value = method(*arguments, **keywords)
    except Exception as exc:
        raise ResolveTransactionError(f"Resolve method failed: {method_name}") from exc
    if value is not True:
        raise ResolveTransactionError(f"Resolve mutation was rejected: {method_name}")


def _required_string(target: object, method_name: str, *arguments: object) -> str:
    value = _required_call(target, method_name, *arguments)
    if not isinstance(value, str) or not value.strip():
        raise ResolveTransactionError(f"Resolve method returned no identity: {method_name}")
    return value.strip()


def _required_project_folder(target: object, method_name: str) -> str:
    try:
        return _project_folder_identity(_required_call(target, method_name))
    except ResolveTransactionError as exc:
        raise ResolveTransactionError(
            f"Resolve method returned no identity: {method_name}"
        ) from exc


def _project_folder_identity(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or any(separator in value for separator in ("/", "\\"))
    ):
        raise ResolveTransactionError("Resolve project folder identity is invalid")
    return value


def _required_nonnegative_int(target: object, method_name: str, *arguments: object) -> int:
    value = _required_call(target, method_name, *arguments)
    if type(value) is not int or value < 0:
        raise ResolveTransactionError(f"Resolve method returned invalid count: {method_name}")
    return value


def _required_int(target: object, method_name: str, *arguments: object) -> int:
    value = _required_call(target, method_name, *arguments)
    if type(value) is not int:
        raise ResolveTransactionError(f"Resolve method returned invalid frame: {method_name}")
    return value


def _required_bool(target: object, method_name: str, *arguments: object) -> bool:
    value = _required_call(target, method_name, *arguments)
    if type(value) is not bool:
        raise ResolveTransactionError(f"Resolve method returned invalid status: {method_name}")
    return value


def _required_mapping(
    target: object,
    method_name: str,
    *arguments: object,
) -> Mapping[object, object]:
    value = _required_call(target, method_name, *arguments)
    if not isinstance(value, Mapping):
        raise ResolveTransactionError(f"Resolve method returned invalid mapping: {method_name}")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        _json_compatible(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_compatible(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ResolveTransactionError("Resolve snapshot contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_compatible(item) for item in value]
    raise ResolveTransactionError("Resolve snapshot contains an unsupported value")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _media_path(properties: Mapping[object, object]) -> Path:
    value = properties.get("File Path")
    if not isinstance(value, str) or not value.strip():
        raise ResolveTransactionError("Resolve media has no online file path")
    try:
        return Path(value).resolve(strict=True)
    except OSError as exc:
        raise ResolveTransactionError("Resolve media file is offline") from exc


def _assert_online_media(properties: Mapping[object, object]) -> None:
    for key in ("Status", "Media Status", "Offline"):
        value = properties.get(key)
        normalized = str(value).strip().casefold() if value is not None else ""
        if normalized in {"offline", "missing", "media offline", "true", "1"}:
            raise ResolveTransactionError("Resolve media item is offline")


def _media_resolution(properties: Mapping[object, object]) -> tuple[int, int]:
    value = properties.get("Resolution")
    if not isinstance(value, str):
        raise ResolveTransactionError("Resolve media resolution is missing")
    normalized = value.lower().replace(" ", "")
    parts = normalized.split("x")
    if len(parts) != 2:
        raise ResolveTransactionError("Resolve media resolution is invalid")
    try:
        width, height = (int(part) for part in parts)
    except ValueError as exc:
        raise ResolveTransactionError("Resolve media resolution is invalid") from exc
    if width <= 0 or height <= 0:
        raise ResolveTransactionError("Resolve media resolution is invalid")
    return width, height


def _positive_number(value: object, *, label: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ResolveTransactionError(f"Resolve {label} is invalid") from exc
    if not math.isfinite(number) or number <= 0:
        raise ResolveTransactionError(f"Resolve {label} is invalid")
    return number


def _positive_int(value: object, *, label: str) -> int:
    number = _positive_number(value, label=label)
    if not number.is_integer():
        raise ResolveTransactionError(f"Resolve {label} is invalid")
    return int(number)


def _mapping_string(value: dict[object, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ResolveTransactionError(f"Resolve database is missing {key}")
    return item.strip()


def _optional_mapping_string(value: dict[object, object], key: str) -> str | None:
    item = value.get(key)
    if item in (None, ""):
        return None
    if not isinstance(item, str) or not item.strip():
        raise ResolveTransactionError(f"Resolve database has invalid {key}")
    return item.strip()
