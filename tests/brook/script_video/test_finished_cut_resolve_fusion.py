from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    _mint_materialization_plan,
    _mint_projected_component,
)
from agents.brook.script_video.finished_cut_production._resolve import (
    ResolveTransactionError,
    ResolveTransactionManager,
)
from agents.brook.script_video.finished_cut_production._resolve_davinci import (
    DaVinciResolveTimelineAdapter,
    MediaProbeResult,
    RenderRequest,
)
from agents.brook.script_video.finished_cut_production._resolve_fusion import (
    DaVinciResolveFacade,
    ResolveCutLocator,
    ResolveDatabaseIdentity,
    ResolveProjectLocator,
    connect_resolve_scripting,
    discover_resolve_project_binding,
)
from agents.brook.script_video.finished_cut_production._timeline_apply import (
    PreRenderedAsset,
    PreRenderedAssetCatalog,
    TimelinePlacement,
)


@dataclass
class _Timeline:
    name: str
    uid: str
    tracks: dict[tuple[str, int], list[object]] | None = None
    project: _Project | None = None
    delete_calls: list[tuple[tuple[object, ...], bool]] | None = None

    def GetName(self) -> str:
        return self.name

    def GetUniqueId(self) -> str:
        return self.uid

    def GetStartFrame(self) -> int:
        return 86_400

    def GetEndFrame(self) -> int:
        return 95_400

    def GetTrackCount(self, track_type: str) -> int:
        rows = self.tracks or {}
        return max((index for kind, index in rows if kind == track_type), default=0)

    def GetItemListInTrack(self, track_type: str, track_index: int) -> list[object]:
        return list((self.tracks or {}).get((track_type, track_index), []))

    def GetTrackName(self, track_type: str, track_index: int) -> str:
        return f"{track_type.upper()} {track_index}"

    def GetIsTrackEnabled(self, _track_type: str, _track_index: int) -> bool:
        return True

    def GetIsTrackLocked(self, _track_type: str, _track_index: int) -> bool:
        return False

    def AddTrack(self, track_type: str) -> bool:
        index = self.GetTrackCount(track_type) + 1
        if self.tracks is None:
            self.tracks = {}
        self.tracks[(track_type, index)] = []
        return True

    def GetSetting(self, key: str) -> str:
        assert key == "timelineFrameRate"
        return "30"

    def DuplicateTimeline(self, name: str) -> _Timeline:
        assert self.project is not None
        duplicate = _Timeline(
            name=name,
            uid=f"{self.uid}-duplicate-{len(self.project.timelines)}",
            tracks={key: list(value) for key, value in (self.tracks or {}).items()},
            project=self.project,
        )
        self.project.timelines.append(duplicate)
        return duplicate

    def SetName(self, name: str) -> bool:
        self.name = name
        return True

    def DeleteClips(self, items: list[object], ripple: bool = False) -> bool:
        if self.delete_calls is None:
            self.delete_calls = []
        self.delete_calls.append((tuple(items), ripple))
        for rows in (self.tracks or {}).values():
            for item in items:
                if item in rows:
                    rows.remove(item)
        return True


@dataclass
class _MediaPoolItem:
    identity: str
    path: Path | None = None
    resolution: str = "1920x1080"
    fps: str = "30"
    frames: str = "300"
    status: str = "Online"

    def GetClipProperty(self, key: str | None = None) -> object:
        properties = {
            "File Path": str(self.path) if self.path is not None else f"{self.identity}.mov",
            "Resolution": self.resolution,
            "FPS": self.fps,
            "Frames": self.frames,
            "Status": self.status,
        }
        return properties if key is None else properties.get(key, "")


class _FusionTool:
    def SaveSettings(self) -> dict[str, object]:
        return {"Inputs": {"StyledText": "章節標題", "Size": 0.08}}


class _FusionComp:
    def GetToolList(self, _selected_only: bool) -> dict[str, _FusionTool]:
        return {"Text1": _FusionTool()}


class _TimelineItem:
    def __init__(
        self,
        *,
        uid: str,
        track_type: str,
        track_index: int,
        media: _MediaPoolItem | None,
        properties: dict[str, object],
        fusion: bool = False,
        start: int = 86_430,
        end: int = 86_520,
        source_start: int | None = 30,
        source_end: int | None = 120,
    ) -> None:
        self.uid = uid
        self.track_type = track_type
        self.track_index = track_index
        self.media = media
        self.properties = properties
        self.fusion = fusion
        self.start = start
        self.end = end
        self.source_start = source_start
        self.source_end = source_end
        self.set_property_calls: list[tuple[str, object]] = []

    def GetUniqueId(self) -> str:
        return self.uid

    def GetName(self) -> str:
        if self.track_type == "subtitle" and isinstance(self.properties.get("Text"), str):
            return str(self.properties["Text"])
        return f"item-{self.uid}"

    def GetTrackTypeAndIndex(self) -> list[object]:
        return [self.track_type, self.track_index]

    def GetStart(self, _subframe_precision: bool = False) -> int:
        return self.start

    def GetEnd(self, _subframe_precision: bool = False) -> int:
        return self.end

    def GetSourceStartFrame(self) -> int | None:
        return self.source_start

    def GetSourceEndFrame(self) -> int | None:
        return self.source_end

    def GetProperty(self, _property_key: str | None = None) -> dict[str, object]:
        return self.properties

    def GetClipEnabled(self) -> bool:
        return True

    def GetMediaPoolItem(self) -> _MediaPoolItem | None:
        return self.media

    def GetFusionCompCount(self) -> int:
        return 1 if self.fusion else 0

    def GetFusionCompByIndex(self, _index: int) -> _FusionComp:
        return _FusionComp()

    def GetSourceAudioChannelMapping(self) -> str:
        return json.dumps({"audioChannels": 2}, sort_keys=True)

    def SetProperty(self, key: str, value: object) -> bool:
        self.set_property_calls.append((key, value))
        self.properties[key] = value
        return True


class _Project:
    def __init__(self, *, name: str, timelines: list[_Timeline]) -> None:
        self.name = name
        self.timelines = timelines
        for timeline in timelines:
            timeline.project = self
        self.media_pool = _MediaPool(self)
        self.current_timeline = timelines[0] if timelines else None
        self.render_events: list[str] = []
        self.render_settings: dict[str, object] = {}
        self.render_format: tuple[str, str] | None = None

    def GetName(self) -> str:
        return self.name

    def GetTimelineCount(self) -> int:
        return len(self.timelines)

    def GetTimelineByIndex(self, index: int) -> _Timeline:
        return self.timelines[index - 1]

    def GetMediaPool(self) -> _MediaPool:
        return self.media_pool

    def SetCurrentTimeline(self, timeline: _Timeline) -> bool:
        self.current_timeline = timeline
        return True

    def GetCurrentTimeline(self) -> _Timeline | None:
        return self.current_timeline

    def SetCurrentRenderFormatAndCodec(self, container: str, codec: str) -> bool:
        self.render_format = (container, codec)
        return True

    def SetCurrentRenderMode(self, mode: int) -> bool:
        self.render_events.append(f"mode:{mode}")
        return True

    def SetRenderSettings(self, settings: dict[str, object]) -> bool:
        self.render_settings = settings
        return True

    def AddRenderJob(self) -> str:
        self.render_events.append("add")
        return "render-job-1"

    def StartRendering(self, jobs: list[str], *, isInteractiveMode: bool) -> bool:
        assert jobs == ["render-job-1"]
        assert isInteractiveMode is False
        output = Path(str(self.render_settings["TargetDir"])) / (
            f"{self.render_settings['CustomName']}.mp4"
        )
        output.write_bytes(b"fresh-preview")
        self.render_events.append("start")
        return True

    def IsRenderingInProgress(self) -> bool:
        self.render_events.append("poll")
        return False

    def GetRenderJobStatus(self, job_id: str) -> dict[str, object]:
        assert job_id == "render-job-1"
        self.render_events.append("status")
        return {"JobStatus": "Complete", "CompletionPercentage": 100}

    def DeleteRenderJob(self, job_id: str) -> bool:
        assert job_id == "render-job-1"
        self.render_events.append("delete")
        return True


class _MediaPool:
    def __init__(self, project: _Project) -> None:
        self.project = project
        self.imported: list[_MediaPoolItem] = []
        self.append_specs: list[dict[str, object]] = []

    def DeleteTimelines(self, timelines: list[_Timeline]) -> bool:
        for timeline in timelines:
            self.project.timelines.remove(timeline)
        return True

    def ImportMedia(self, paths: list[str]) -> list[_MediaPoolItem]:
        media = [_MediaPoolItem(identity=Path(path).stem, path=Path(path)) for path in paths]
        self.imported.extend(media)
        return media

    def AppendToTimeline(self, specs: list[dict[str, object]]) -> list[_TimelineItem]:
        assert self.project.current_timeline is not None
        rows: list[_TimelineItem] = []
        for spec in specs:
            self.append_specs.append(spec)
            media = spec["mediaPoolItem"]
            assert isinstance(media, _MediaPoolItem)
            start = int(spec["recordFrame"])
            source_start = int(spec["startFrame"])
            source_end = int(spec["endFrame"])
            item = _TimelineItem(
                uid=f"appended-{len(self.append_specs)}",
                track_type="video",
                track_index=int(spec["trackIndex"]),
                media=media,
                properties={},
                start=start,
                end=start + (source_end - source_start),
                source_start=source_start,
                source_end=source_end,
            )
            tracks = self.project.current_timeline.tracks
            assert tracks is not None
            tracks[("video", item.track_index)].append(item)
            rows.append(item)
        return rows


class _ProjectManager:
    def __init__(
        self,
        project: _Project,
        *,
        database: dict[str, str] | None = None,
        folder: str = "Podcast",
    ) -> None:
        self.project = project
        self.save_calls = 0
        self.database = database or {"DbType": "Disk", "DbName": "Local Database"}
        self.folder = folder

    def GetCurrentDatabase(self) -> dict[str, str]:
        return self.database

    def GetCurrentFolder(self) -> str:
        return self.folder

    def GetCurrentProject(self) -> _Project:
        return self.project

    def SaveProject(self) -> bool:
        self.save_calls += 1
        return True


class _Resolve:
    def __init__(self, manager: _ProjectManager) -> None:
        self.manager = manager

    def GetProjectManager(self) -> _ProjectManager:
        return self.manager


class _DigestResolver:
    def __init__(self) -> None:
        self.identities: list[str] = []

    def digest_for(self, media_pool_item: object) -> str:
        assert isinstance(media_pool_item, _MediaPoolItem)
        self.identities.append(media_pool_item.identity)
        return hashlib.sha256(media_pool_item.identity.encode("utf-8")).hexdigest()


class _PreviewProbe:
    def inspect(self, path: Path) -> MediaProbeResult:
        return MediaProbeResult(
            path=path,
            duration_sec=300.0,
            video_codec="h264",
            audio_codec="aac",
            decode_ok=True,
            offline_frame_count=0,
        )


class _MissingDigestResolver(_DigestResolver):
    def digest_for(self, media_pool_item: object) -> str:
        super().digest_for(media_pool_item)
        return ""


class _OfflineMediaPool(_MediaPool):
    def ImportMedia(self, paths: list[str]) -> list[_MediaPoolItem]:
        media = super().ImportMedia(paths)
        media[0].status = "Offline"
        return media


class _AppendNoneMediaPool(_MediaPool):
    def AppendToTimeline(self, specs: list[dict[str, object]]) -> list[_TimelineItem]:
        self.append_specs.extend(specs)
        return [None]  # type: ignore[list-item]


class _WrongTrackMediaPool(_MediaPool):
    def AppendToTimeline(self, specs: list[dict[str, object]]) -> list[_TimelineItem]:
        rows = super().AppendToTimeline(specs)
        rows[0].track_index += 1
        return rows


class _VerticalStockMediaPool(_MediaPool):
    def ImportMedia(self, paths: list[str]) -> list[_MediaPoolItem]:
        media = super().ImportMedia(paths)
        media[0].resolution = "1080x1920"
        return media


def _locator() -> ResolveProjectLocator:
    return ResolveProjectLocator(
        episode_id="episode-1",
        database=ResolveDatabaseIdentity(db_type="Disk", db_name="Local Database"),
        folder="Podcast",
        project_name="episode-1",
    )


def _materialization_plan(asset_ref: str):
    event = EventRecord(
        event_id="event-hero",
        master_cue_ids=("cue-1",),
        text_hash="text-hash",
        intent="emphasize agency",
        text="真正重要的是選擇權",
        t0=2.0,
        t1=5.0,
        display="真正重要的是選擇權",
        semantic_kind="hero_title",
        implementation_kind="hero_title",
        lane="hero_title",
    )
    component = _mint_projected_component(
        component_id="component-hero",
        event_id=event.event_id,
        semantic_kind="hero_title",
        implementation_kind="hero_title",
        lane="hero_title",
        display=event.display,
        t0=event.t0,
        t1=event.t1,
        asset_ref=asset_ref,
    )
    return _mint_materialization_plan(
        plan_id="plan-1",
        run_id="run-1",
        command_id="approved-cut:0123456789abcdef0123456789abcdef",
        episode_id="episode-1",
        cut_id="punch-L04",
        format="long",
        director_acceptance_id="director-1",
        dp_acceptance_id="dp-1",
        visual_acceptance_id="visual-1",
        events=(event,),
        components=(component,),
    )


def test_live_inventory_builds_exact_project_and_cut_binding() -> None:
    project = _Project(
        name="episode-1",
        timelines=[
            _Timeline(name="Long 2", uid="timeline-l02-current"),
            _Timeline(name="Long 3", uid="timeline-l03-live"),
        ],
    )
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )

    binding = discover_resolve_project_binding(
        facade,
        cuts=(ResolveCutLocator(cut_id="punch-L04", timeline_name="Long 3"),),
    )

    assert binding.episode_id == "episode-1"
    assert binding.project_name == "episode-1"
    assert binding.project_uid.startswith("resolve-project:")
    assert binding.cuts[0].canonical.name == "Long 3"
    assert binding.cuts[0].canonical.uid == "timeline-l03-live"


def test_live_inventory_accepts_resolve_root_folder_identity() -> None:
    locator = ResolveProjectLocator(
        episode_id="episode-1",
        database=ResolveDatabaseIdentity(db_type="Disk", db_name="Local Database"),
        folder="",
        project_name="episode-1",
    )
    project = _Project(
        name="episode-1",
        timelines=[_Timeline(name="Long 3", uid="timeline-l03-live")],
    )
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project, folder="")),
        locator=locator,
        media_identity_resolver=_DigestResolver(),
    )

    assert [(identity.name, identity.uid) for identity in facade.timeline_identities()] == [
        ("Long 3", "timeline-l03-live")
    ]


def test_project_locator_rejects_non_string_folder_identity() -> None:
    with pytest.raises(ResolveTransactionError, match="folder"):
        ResolveProjectLocator(
            episode_id="episode-1",
            database=ResolveDatabaseIdentity(db_type="Disk", db_name="Local Database"),
            folder=object(),  # type: ignore[arg-type]
            project_name="episode-1",
        )


@pytest.mark.parametrize("folder", [" ", "\t", " Podcast "])
def test_project_locator_rejects_whitespace_ambiguous_folder_identity(folder: str) -> None:
    with pytest.raises(ResolveTransactionError, match="folder"):
        ResolveProjectLocator(
            episode_id="episode-1",
            database=ResolveDatabaseIdentity(db_type="Disk", db_name="Local Database"),
            folder=folder,
            project_name="episode-1",
        )


@pytest.mark.parametrize("folder", ["Podcast/Season", "Podcast\\Season"])
def test_project_locator_rejects_nested_path_as_folder_identity(folder: str) -> None:
    with pytest.raises(ResolveTransactionError, match="folder"):
        ResolveProjectLocator(
            episode_id="episode-1",
            database=ResolveDatabaseIdentity(db_type="Disk", db_name="Local Database"),
            folder=folder,
            project_name="episode-1",
        )


def test_lazy_connection_uses_exact_resolve_application_and_rejects_missing_host() -> None:
    resolve = object()
    requested: list[str] = []

    def scriptapp(application: str) -> object:
        requested.append(application)
        return resolve

    assert connect_resolve_scripting(scriptapp=scriptapp) is resolve
    assert requested == ["Resolve"]

    try:
        connect_resolve_scripting(scriptapp=lambda _application: None)
    except ResolveTransactionError as exc:
        assert "unavailable" in str(exc)
    else:  # pragma: no cover - the assertion above is the behavior
        raise AssertionError("missing Resolve host must fail closed")


def test_snapshot_covers_every_track_item_and_media_fact_without_rehashing_master() -> None:
    video = _TimelineItem(
        uid="video-1",
        track_type="video",
        track_index=1,
        media=_MediaPoolItem("editorial-master"),
        properties={
            "ZoomX": 1.1,
            "CropLeft": 4.0,
            "CompositeMode": 0,
            "RetimeProcess": 2,
        },
        fusion=True,
    )
    audio = _TimelineItem(
        uid="audio-1",
        track_type="audio",
        track_index=1,
        media=_MediaPoolItem("editorial-master-audio"),
        properties={"Volume": -3.0, "Pan": 0.0},
    )
    subtitle = _TimelineItem(
        uid="subtitle-1",
        track_type="subtitle",
        track_index=1,
        media=None,
        properties={"Text": "第一章"},
        source_start=None,
        source_end=None,
    )
    timeline = _Timeline(
        name="Long 3",
        uid="timeline-l03-live",
        tracks={
            ("video", 1): [video],
            ("audio", 1): [audio],
            ("subtitle", 1): [subtitle],
        },
    )
    resolver = _DigestResolver()
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(_Project(name="episode-1", timelines=[timeline]))),
        locator=_locator(),
        media_identity_resolver=resolver,
    )

    state = facade.timeline_state("timeline-l03-live")

    assert (state.start_frame, state.end_frame) == (86_400, 95_400)
    assert state.frame_rate == 30.0
    assert [(track.track_type, track.track_index, track.item_ids) for track in state.tracks] == [
        ("video", 1, ("video-1",)),
        ("audio", 1, ("audio-1",)),
        ("subtitle", 1, ("subtitle-1",)),
    ]
    assert [
        (item.track_type, item.source_in_frame, item.source_out_frame) for item in state.items
    ] == [
        ("video", 30, 120),
        ("audio", 30, 120),
        ("subtitle", 86_430, 86_520),
    ]
    video_properties = dict(state.items[0].properties)
    assert json.loads(str(video_properties["timeline_properties"])) == video.properties
    assert json.loads(str(video_properties["fusion_settings"])) == {
        "1": {"Text1": {"Inputs": {"Size": 0.08, "StyledText": "章節標題"}}}
    }
    audio_properties = dict(state.items[1].properties)
    assert json.loads(str(audio_properties["source_audio_mapping"])) == {"audioChannels": 2}
    assert resolver.identities == [
        "editorial-master",
        "editorial-master-audio",
    ]


def test_uid_bound_duplicate_rename_delete_and_save_reinventory_each_result() -> None:
    canonical = _Timeline(name="Long 3", uid="timeline-l03-live")
    project = _Project(name="episode-1", timelines=[canonical])
    manager = _ProjectManager(project)
    facade = DaVinciResolveFacade(
        resolve=_Resolve(manager),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )

    duplicate = facade.duplicate_timeline("timeline-l03-live", "__fcp_work__L03__tx-1")
    facade.rename_timeline("timeline-l03-live", "__fcp_backup__L03__tx-1")
    facade.rename_timeline(duplicate.uid, "Long 3")
    facade.delete_timeline(duplicate.uid)
    facade.rename_timeline("timeline-l03-live", "Long 3")
    facade.save_project()

    assert facade.timeline_identities() == (
        type(duplicate)(name="Long 3", uid="timeline-l03-live"),
    )
    assert manager.save_calls == 1


def test_clear_derived_lanes_only_removes_video_two_through_seven() -> None:
    protected_v1 = object()
    derived_v2 = object()
    derived_v7 = object()
    protected_audio = object()
    protected_subtitle = object()
    timeline = _Timeline(
        name="Long 3",
        uid="timeline-l03-live",
        tracks={
            ("video", 1): [protected_v1],
            ("video", 2): [derived_v2],
            ("video", 7): [derived_v7],
            ("audio", 1): [protected_audio],
            ("subtitle", 1): [protected_subtitle],
        },
    )
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(_Project(name="episode-1", timelines=[timeline]))),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )

    facade.clear_derived_lanes("timeline-l03-live")

    assert timeline.tracks == {
        ("video", 1): [protected_v1],
        ("video", 2): [],
        ("video", 7): [],
        ("audio", 1): [protected_audio],
        ("subtitle", 1): [protected_subtitle],
    }
    assert timeline.delete_calls == [((derived_v2,), False), ((derived_v7,), False)]


def test_append_places_verified_prerendered_hero_on_fixed_lane_without_implicit_crop(
    tmp_path: Path,
) -> None:
    media_path = tmp_path / "hero.mov"
    media_path.write_bytes(b"prores-hero")
    timeline = _Timeline(name="Long 3", uid="work-uid", tracks={("video", 1): []})
    project = _Project(name="episode-1", timelines=[timeline])
    resolver = _DigestResolver()
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=resolver,
    )

    facade.append_pre_rendered(
        "work-uid",
        TimelinePlacement(
            component_id="component-hero",
            event_id="event-hero",
            semantic_kind="hero_title",
            implementation_kind="hero_title",
            lane="hero_title",
            display="真正重要的是選擇權",
            t0=2.0,
            t1=5.0,
            source_path=media_path,
        ),
    )

    spec = project.media_pool.append_specs[0]
    assert spec["mediaType"] == 1
    assert spec["trackIndex"] == 3
    assert spec["recordFrame"] == 86_460
    assert spec["startFrame"] == 0
    assert spec["endFrame"] == 90
    appended = timeline.tracks[("video", 3)][0]
    assert isinstance(appended, _TimelineItem)
    assert appended.set_property_calls == []
    assert resolver.identities == ["hero", "hero"]


@pytest.mark.parametrize(
    ("lane", "implementation_kind", "track_index"),
    [
        ("b_roll", "stock_video", 2),
        ("hero_title", "hero_title", 3),
        ("identity_card", "identity_card", 4),
        ("fullscreen_transition", "fullscreen_transition", 6),
        ("visual_effect", "visual_effect", 7),
    ],
)
def test_each_typed_lane_has_one_fixed_video_track(
    tmp_path: Path,
    lane: str,
    implementation_kind: str,
    track_index: int,
) -> None:
    media_path = tmp_path / f"{lane}.mov"
    media_path.write_bytes(lane.encode("utf-8"))
    timeline = _Timeline(name="Long 3", uid="work-uid", tracks={("video", 1): []})
    project = _Project(name="episode-1", timelines=[timeline])
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )

    facade.append_pre_rendered(
        "work-uid",
        TimelinePlacement(
            component_id=f"component-{lane}",
            event_id=f"event-{lane}",
            semantic_kind=lane,
            implementation_kind=implementation_kind,
            lane=lane,  # type: ignore[arg-type]
            display=lane,
            t0=2.0,
            t1=5.0,
            source_path=media_path,
        ),
    )

    assert project.media_pool.append_specs[0]["trackIndex"] == track_index


def test_vertical_stock_media_is_rejected_instead_of_implicitly_cropped(
    tmp_path: Path,
) -> None:
    media_path = tmp_path / "vertical-stock.mp4"
    media_path.write_bytes(b"stock")
    timeline = _Timeline(name="Long 3", uid="work-uid", tracks={("video", 1): []})
    project = _Project(name="episode-1", timelines=[timeline])
    project.media_pool = _VerticalStockMediaPool(project)
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )

    with pytest.raises(ResolveTransactionError, match="native 16:9 landscape"):
        facade.append_pre_rendered(
            "work-uid",
            TimelinePlacement(
                component_id="component-stock",
                event_id="event-stock",
                semantic_kind="b_roll",
                implementation_kind="stock_video",
                lane="b_roll",
                display="",
                t0=2.0,
                t1=5.0,
                source_path=media_path,
            ),
        )

    assert project.media_pool.append_specs == []


def test_render_preview_uses_exact_h264_aac_job_and_reads_status_before_deleting(
    tmp_path: Path,
) -> None:
    timeline = _Timeline(name="Long 3", uid="work-uid", tracks={("video", 1): []})
    project = _Project(name="episode-1", timelines=[timeline])
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )
    output = tmp_path / "preview.mp4"

    rendered = facade.render_preview("work-uid", RenderRequest(output=output))

    assert rendered == output
    assert rendered.read_bytes() == b"fresh-preview"
    assert project.render_format == ("mp4", "H264")
    assert project.render_settings == {
        "SelectAllFrames": False,
        "MarkIn": 86_400,
        "MarkOut": 95_400,
        "TargetDir": str(tmp_path),
        "CustomName": "preview",
        "ExportVideo": True,
        "ExportAudio": True,
        "AudioCodec": "aac",
        "FormatWidth": 1920,
        "FormatHeight": 1080,
    }
    assert project.render_events == ["mode:1", "add", "start", "poll", "status", "delete"]


@pytest.mark.parametrize(
    ("database", "folder", "project_name"),
    [
        ({"DbType": "Disk", "DbName": "Wrong"}, "Podcast", "episode-1"),
        ({"DbType": "Disk", "DbName": "Local Database"}, "Wrong", "episode-1"),
        ({"DbType": "Disk", "DbName": "Local Database"}, "Podcast", "wrong-project"),
    ],
)
def test_wrong_database_folder_or_project_is_rejected_before_inventory(
    database: dict[str, str],
    folder: str,
    project_name: str,
) -> None:
    project = _Project(name=project_name, timelines=[_Timeline("Long 3", "uid-3")])
    manager = _ProjectManager(project, database=database, folder=folder)
    facade = DaVinciResolveFacade(
        resolve=_Resolve(manager),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )

    with pytest.raises(ResolveTransactionError):
        facade.timeline_identities()


def test_wrong_named_folder_is_rejected_before_root_binding_mutation() -> None:
    locator = ResolveProjectLocator(
        episode_id="episode-1",
        database=ResolveDatabaseIdentity(db_type="Disk", db_name="Local Database"),
        folder="",
        project_name="episode-1",
    )
    canonical = _Timeline(name="Long 3", uid="uid-3")
    project = _Project(name="episode-1", timelines=[canonical])
    manager = _ProjectManager(project, folder="Podcast")
    facade = DaVinciResolveFacade(
        resolve=_Resolve(manager),
        locator=locator,
        media_identity_resolver=_DigestResolver(),
    )

    with pytest.raises(ResolveTransactionError, match="folder"):
        facade.duplicate_timeline("uid-3", "work")

    assert [(timeline.name, timeline.uid) for timeline in project.timelines] == [
        ("Long 3", "uid-3")
    ]
    assert manager.save_calls == 0


def test_duplicate_returning_the_canonical_uid_is_rejected() -> None:
    class _SameUidTimeline(_Timeline):
        def DuplicateTimeline(self, name: str) -> _Timeline:
            assert self.project is not None
            duplicate = _Timeline(name=name, uid=self.uid, project=self.project)
            self.project.timelines.append(duplicate)
            return duplicate

    timeline = _SameUidTimeline(name="Long 3", uid="uid-3")
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(_Project(name="episode-1", timelines=[timeline]))),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )

    with pytest.raises(ResolveTransactionError, match="duplicate Timeline identity"):
        facade.duplicate_timeline("uid-3", "work")


def test_duplicate_name_collision_is_rejected_before_resolve_mutation() -> None:
    canonical = _Timeline(name="Long 3", uid="canonical")
    existing_work = _Timeline(name="work", uid="existing-work")
    project = _Project(name="episode-1", timelines=[canonical, existing_work])
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )
    before = facade.timeline_identities()

    with pytest.raises(ResolveTransactionError, match="already exists"):
        facade.duplicate_timeline("canonical", "work")

    assert facade.timeline_identities() == before


@pytest.mark.parametrize(
    ("media_pool_type", "resolver", "error"),
    [
        (_OfflineMediaPool, _DigestResolver(), "offline"),
        (_MediaPool, _MissingDigestResolver(), "no digest"),
        (_AppendNoneMediaPool, _DigestResolver(), "AppendToTimeline"),
        (_WrongTrackMediaPool, _DigestResolver(), "wrong track"),
    ],
)
def test_append_fails_closed_for_offline_missing_digest_none_or_wrong_track(
    tmp_path: Path,
    media_pool_type: type[_MediaPool],
    resolver: _DigestResolver,
    error: str,
) -> None:
    media_path = tmp_path / "hero.mov"
    media_path.write_bytes(b"hero")
    timeline = _Timeline(name="Long 3", uid="work", tracks={("video", 1): []})
    project = _Project(name="episode-1", timelines=[timeline])
    project.media_pool = media_pool_type(project)
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=resolver,
    )
    placement = TimelinePlacement(
        component_id="hero",
        event_id="event",
        semantic_kind="hero_title",
        implementation_kind="hero_title",
        lane="hero_title",
        display="title",
        t0=1.0,
        t1=2.0,
        source_path=media_path,
    )

    with pytest.raises(ResolveTransactionError, match=error):
        facade.append_pre_rendered("work", placement)


@pytest.mark.parametrize("failure", ["failed_job", "missing_output"])
def test_render_failure_never_returns_stale_or_missing_preview(
    tmp_path: Path,
    failure: str,
) -> None:
    class _RenderFailureProject(_Project):
        def StartRendering(self, jobs: list[str], *, isInteractiveMode: bool) -> bool:
            if failure == "missing_output":
                assert jobs == ["render-job-1"] and isInteractiveMode is False
                self.render_events.append("start")
                return True
            return super().StartRendering(jobs, isInteractiveMode=isInteractiveMode)

        def GetRenderJobStatus(self, job_id: str) -> dict[str, object]:
            if failure == "failed_job":
                self.render_events.append("status")
                return {"JobStatus": "Failed", "Error": "codec unavailable"}
            return super().GetRenderJobStatus(job_id)

    timeline = _Timeline(name="Long 3", uid="work", tracks={("video", 1): []})
    project = _RenderFailureProject(name="episode-1", timelines=[timeline])
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )

    with pytest.raises(ResolveTransactionError):
        facade.render_preview("work", RenderRequest(output=tmp_path / "failed.mp4"))

    assert project.render_events[-2:] == ["status", "delete"]
    assert not (tmp_path / "failed.mp4").exists()


def test_render_timeout_stops_job_and_deletes_queue_entry(tmp_path: Path) -> None:
    class _TimeoutProject(_Project):
        def IsRenderingInProgress(self) -> bool:
            self.render_events.append("poll")
            return True

        def StopRendering(self) -> None:
            self.render_events.append("stop")

    ticks = iter((0.0, 1.0))
    timeline = _Timeline(name="Long 3", uid="work", tracks={("video", 1): []})
    project = _TimeoutProject(name="episode-1", timelines=[timeline])
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
        monotonic=lambda: next(ticks),
        sleep=lambda _seconds: None,
        render_timeout_sec=0.5,
    )

    with pytest.raises(ResolveTransactionError, match="timed out"):
        facade.render_preview("work", RenderRequest(output=tmp_path / "timeout.mp4"))

    assert project.render_events[-3:] == ["poll", "stop", "delete"]


def test_full_typed_transaction_commits_and_compensates_to_exact_original_baseline(
    tmp_path: Path,
) -> None:
    master_video = _TimelineItem(
        uid="master-video",
        track_type="video",
        track_index=1,
        media=_MediaPoolItem("master-video"),
        properties={"ZoomX": 1.0, "CompositeMode": 0},
    )
    master_audio = _TimelineItem(
        uid="master-audio",
        track_type="audio",
        track_index=1,
        media=_MediaPoolItem("master-audio"),
        properties={"Volume": 0.0},
    )
    subtitle = _TimelineItem(
        uid="subtitle",
        track_type="subtitle",
        track_index=1,
        media=_MediaPoolItem("subtitle"),
        properties={"Text": "第一章"},
    )
    old_derived = _TimelineItem(
        uid="old-derived",
        track_type="video",
        track_index=2,
        media=_MediaPoolItem("old-derived"),
        properties={},
    )
    canonical = _Timeline(
        name="Long 3",
        uid="timeline-l03-live",
        tracks={
            ("video", 1): [master_video],
            ("video", 2): [old_derived],
            ("audio", 1): [master_audio],
            ("subtitle", 1): [subtitle],
        },
    )
    project = _Project(name="episode-1", timelines=[canonical])
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )
    binding = discover_resolve_project_binding(
        facade,
        cuts=(ResolveCutLocator(cut_id="punch-L04", timeline_name="Long 3"),),
    )
    hero = tmp_path / "hero.mov"
    hero.write_bytes(b"hero")
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=_PreviewProbe(),
        binding=binding,
        assets=PreRenderedAssetCatalog((PreRenderedAsset(reference="asset:hero", path=hero),)),
    )
    manager = ResolveTransactionManager(adapter)
    subtitle_path = tmp_path / "review.srt"
    subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\ntext\n", encoding="utf-8")

    transaction = manager.prepare(
        _materialization_plan("asset:hero"),
        canonical=binding.cuts[0].canonical,
        preview_path=tmp_path / "preview.mp4",
        subtitle_path=subtitle_path,
    )
    receipt = manager.commit(transaction.transaction_id, expected_cut_id="punch-L04")
    compensated = manager.compensating_rollback(
        transaction.transaction_id,
        expected_cut_id="punch-L04",
    )

    assert receipt.backup_retained is True
    assert compensated.status == "compensated"
    assert facade.timeline_identities() == (binding.cuts[0].canonical,)
    restored = facade.timeline_state(binding.cuts[0].canonical.uid)
    assert [item.item_id for item in restored.items] == [
        "master-video",
        "old-derived",
        "master-audio",
        "subtitle",
    ]


def test_typed_transaction_rejects_subtitle_drift_before_preview_ready(
    tmp_path: Path,
) -> None:
    class _SubtitleDriftFacade(DaVinciResolveFacade):
        def clear_derived_lanes(self, uid: str) -> None:
            super().clear_derived_lanes(uid)
            work = next(timeline for timeline in project.timelines if timeline.uid == uid)
            assert work.tracks is not None
            work.tracks[("subtitle", 1)].clear()

    canonical = _Timeline(
        name="Long 3",
        uid="timeline-l03-live",
        tracks={
            ("video", 1): [
                _TimelineItem(
                    uid="master-video",
                    track_type="video",
                    track_index=1,
                    media=_MediaPoolItem("master-video"),
                    properties={},
                )
            ],
            ("audio", 1): [
                _TimelineItem(
                    uid="master-audio",
                    track_type="audio",
                    track_index=1,
                    media=_MediaPoolItem("master-audio"),
                    properties={},
                )
            ],
            ("subtitle", 1): [
                _TimelineItem(
                    uid="subtitle",
                    track_type="subtitle",
                    track_index=1,
                    media=_MediaPoolItem("subtitle"),
                    properties={"Text": "第一章"},
                )
            ],
        },
    )
    project = _Project(name="episode-1", timelines=[canonical])
    facade = _SubtitleDriftFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )
    binding = discover_resolve_project_binding(
        facade,
        cuts=(ResolveCutLocator(cut_id="punch-L04", timeline_name="Long 3"),),
    )
    hero = tmp_path / "hero.mov"
    hero.write_bytes(b"hero")
    manager = ResolveTransactionManager(
        DaVinciResolveTimelineAdapter(
            facade=facade,
            probe=_PreviewProbe(),
            binding=binding,
            assets=PreRenderedAssetCatalog((PreRenderedAsset(reference="asset:hero", path=hero),)),
        )
    )
    subtitle_path = tmp_path / "review.srt"
    subtitle_path.write_text("subtitle", encoding="utf-8")

    with pytest.raises(ResolveTransactionError, match="protected"):
        manager.prepare(
            _materialization_plan("asset:hero"),
            canonical=binding.cuts[0].canonical,
            preview_path=tmp_path / "preview.mp4",
            subtitle_path=subtitle_path,
        )

    assert "add" not in project.render_events
    assert facade.timeline_identities() == (binding.cuts[0].canonical,)
    assert [item.item_id for item in facade.timeline_state("timeline-l03-live").items] == [
        "master-video",
        "master-audio",
        "subtitle",
    ]


def test_protected_fingerprint_covers_subtitle_track_metadata_but_not_derived_lanes() -> None:
    class _TrackMetadataTimeline(_Timeline):
        def GetTrackName(self, track_type: str, track_index: int) -> str:
            return self.track_names[(track_type, track_index)]

    timeline = _TrackMetadataTimeline(
        name="Long 3",
        uid="timeline-l03-live",
        tracks={
            ("video", 1): [],
            ("video", 2): [],
            ("audio", 1): [],
            ("subtitle", 1): [],
        },
    )
    timeline.track_names = {
        ("video", 1): "Editorial Master",
        ("video", 2): "B-roll",
        ("audio", 1): "Program Audio",
        ("subtitle", 1): "Chinese Subtitles",
    }
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(_Project(name="episode-1", timelines=[timeline]))),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )
    binding = discover_resolve_project_binding(
        facade,
        cuts=(ResolveCutLocator(cut_id="punch-L04", timeline_name="Long 3"),),
    )
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=_PreviewProbe(),
        binding=binding,
        assets=PreRenderedAssetCatalog(()),
    )
    baseline = adapter.snapshot(binding.cuts[0].canonical)

    timeline.track_names[("video", 2)] = "Rebuilt B-roll"
    derived_change = adapter.snapshot(binding.cuts[0].canonical)
    timeline.track_names[("subtitle", 1)] = "Lost Subtitle Contract"
    subtitle_change = adapter.snapshot(binding.cuts[0].canonical)

    assert derived_change.protected_fingerprint == baseline.protected_fingerprint
    assert derived_change.full_fingerprint != baseline.full_fingerprint
    assert subtitle_change.protected_fingerprint != baseline.protected_fingerprint


def test_protected_fingerprint_ignores_duplicate_minted_item_uids() -> None:
    video = _TimelineItem(
        uid="canonical-video",
        track_type="video",
        track_index=1,
        media=_MediaPoolItem("editorial-master"),
        properties={"ZoomX": 1.0},
    )
    timeline = _Timeline(
        name="Long 3",
        uid="timeline-l03-live",
        tracks={("video", 1): [video]},
    )
    project = _Project(name="episode-1", timelines=[timeline])
    facade = DaVinciResolveFacade(
        resolve=_Resolve(_ProjectManager(project)),
        locator=_locator(),
        media_identity_resolver=_DigestResolver(),
    )
    binding = discover_resolve_project_binding(
        facade,
        cuts=(ResolveCutLocator(cut_id="punch-L04", timeline_name="Long 3"),),
    )
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=_PreviewProbe(),
        binding=binding,
        assets=PreRenderedAssetCatalog(()),
    )
    baseline = adapter.snapshot(binding.cuts[0].canonical)

    video.uid = "duplicate-minted-video"
    duplicate = adapter.snapshot(binding.cuts[0].canonical)

    assert duplicate.protected_fingerprint == baseline.protected_fingerprint
    assert duplicate.full_fingerprint != baseline.full_fingerprint


def test_production_resolve_modules_do_not_import_old_execution_paths() -> None:
    module_root = Path(__file__).parents[3] / "agents" / "brook" / "script_video"
    source = "\n".join(
        (module_root / "finished_cut_production" / name).read_text(encoding="utf-8")
        for name in ("_resolve_fusion.py", "_resolve_davinci.py")
    )

    assert "run_short_" not in source
    assert "long_highlight_materializer" not in source
