import ast
import hashlib
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._persistence import (
    AtomicResolveTransactionStore,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    MaterializationPlan,
    _mint_materialization_plan,
    _mint_projected_component,
)
from agents.brook.script_video.finished_cut_production._resolve import (
    ResolveTransactionError,
    ResolveTransactionManager,
    TimelineIdentity,
)
from agents.brook.script_video.finished_cut_production._resolve_davinci import (
    DaVinciResolveTimelineAdapter,
    FFprobeMediaProbe,
    FFprobeProcess,
    MediaProbeResult,
    RenderRequest,
    ResolveCutBinding,
    ResolveProjectBinding,
    ResolveProjectIdentity,
    ResolveTimelineItem,
    ResolveTimelineState,
)
from agents.brook.script_video.finished_cut_production._timeline_apply import (
    PreRenderedAsset,
    PreRenderedAssetCatalog,
    TimelineApplyError,
    project_timeline_application,
)


@dataclass
class _Timeline:
    name: str
    uid: str
    state: ResolveTimelineState


class _ResolveFacade:
    def __init__(self, binding: ResolveProjectBinding) -> None:
        self.binding = binding
        canonical = binding.cuts[0].canonical
        self._timelines = {
            canonical.uid: _Timeline(
                name=canonical.name,
                uid=canonical.uid,
                state=ResolveTimelineState(
                    start_frame=0,
                    end_frame=3000,
                    items=(
                        ResolveTimelineItem(
                            item_id="a-roll-video",
                            track_type="video",
                            track_index=1,
                            start_frame=0,
                            end_frame=3000,
                            source_in_frame=0,
                            source_out_frame=3000,
                            properties=(("composite", "normal"),),
                            media_digest="a" * 64,
                        ),
                        ResolveTimelineItem(
                            item_id="a-roll-audio",
                            track_type="audio",
                            track_index=1,
                            start_frame=0,
                            end_frame=3000,
                            source_in_frame=0,
                            source_out_frame=3000,
                            properties=(("gain", 0.0),),
                            media_digest="b" * 64,
                        ),
                    ),
                ),
            )
        }
        self.mutations: list[str] = []
        self.applied = []
        self.render_requests: list[RenderRequest] = []
        self.return_canonical_from_duplicate = False
        self.duplicate_calls = 0

    def project_identity(self) -> ResolveProjectIdentity:
        return ResolveProjectIdentity(
            episode_id=self.binding.episode_id,
            name=self.binding.project_name,
            uid=self.binding.project_uid,
        )

    def timeline_identities(self) -> tuple[TimelineIdentity, ...]:
        return tuple(
            TimelineIdentity(name=timeline.name, uid=timeline.uid)
            for timeline in self._timelines.values()
        )

    def timeline_state(self, uid: str) -> ResolveTimelineState:
        return deepcopy(self._timelines[uid].state)

    def duplicate_timeline(self, uid: str, name: str) -> TimelineIdentity:
        self.duplicate_calls += 1
        self.mutations.append(f"duplicate:{uid}")
        if self.return_canonical_from_duplicate:
            return TimelineIdentity(name=self._timelines[uid].name, uid=uid)
        duplicate_uid = f"work-{uid}"
        self._timelines[duplicate_uid] = _Timeline(
            name=name,
            uid=duplicate_uid,
            state=deepcopy(self._timelines[uid].state),
        )
        return TimelineIdentity(name=name, uid=duplicate_uid)

    def rename_timeline(self, uid: str, name: str) -> None:
        self.mutations.append(f"rename:{uid}:{name}")
        self._timelines[uid].name = name

    def delete_timeline(self, uid: str) -> None:
        self.mutations.append(f"delete:{uid}")
        del self._timelines[uid]

    def clear_derived_lanes(self, uid: str) -> None:
        self.mutations.append(f"clear-derived:{uid}")
        timeline = self._timelines[uid]
        timeline.state = ResolveTimelineState(
            start_frame=timeline.state.start_frame,
            end_frame=timeline.state.end_frame,
            items=tuple(
                item
                for item in timeline.state.items
                if item.track_type == "audio"
                or (item.track_type == "video" and item.track_index == 1)
            ),
        )

    def append_pre_rendered(self, uid: str, placement: object) -> None:
        self.mutations.append(f"append:{uid}")
        self.applied.append(placement)

    def select_timeline(self, uid: str) -> None:
        self.mutations.append(f"select:{uid}")

    def render_preview(self, uid: str, request: RenderRequest) -> Path:
        self.mutations.append(f"render:{uid}")
        self.render_requests.append(request)
        request.output.parent.mkdir(parents=True, exist_ok=True)
        request.output.write_bytes(b"preview")
        return request.output

    def save_project(self) -> None:
        self.mutations.append("save")


class _Probe:
    def __init__(self, result: MediaProbeResult | None = None) -> None:
        self.result = result
        self.inspected: list[Path] = []

    def inspect(self, path: Path) -> MediaProbeResult:
        self.inspected.append(path)
        if self.result is None:
            raise AssertionError("probe is not used by this test")
        return self.result


def _binding() -> ResolveProjectBinding:
    return ResolveProjectBinding(
        episode_id="episode-001",
        project_name="episode-001",
        project_uid="project-uid",
        cuts=(
            ResolveCutBinding(
                cut_id="value-L01",
                canonical=TimelineIdentity(name="Value L01", uid="canonical-uid"),
            ),
        ),
    )


def _plan(
    asset_refs: tuple[str, ...],
    *,
    cut_id: str = "value-L01",
    source_asset_refs: tuple[str, ...] | None = None,
) -> MaterializationPlan:
    definitions = (
        ("chapter", "fullscreen_transition", "fullscreen_transition", "新章節"),
        ("hero_title", "hero_title", "hero_title", "核心論點"),
        ("b_roll", "stock_video", "b_roll", "工作忙碌"),
        ("identity_card", "identity_card", "identity_card", "簡立峰博士"),
        ("visual_effect", "visual_effect", "visual_effect", "焦點強調"),
    )
    event_refs = source_asset_refs or asset_refs
    events = tuple(
        EventRecord(
            event_id=f"event-{index}",
            master_cue_ids=(f"cue-{index}",),
            text_hash=hashlib.sha256(f"quote-{index}".encode()).hexdigest(),
            intent=f"intent-{index}",
            asset_ref=asset_ref,
            visual_status="approved",
        )
        for index, asset_ref in enumerate(event_refs, start=1)
    )
    components = tuple(
        _mint_projected_component(
            component_id=f"component-{index}",
            event_id=events[index - 1].event_id,
            semantic_kind=semantic_kind,
            implementation_kind=implementation_kind,
            lane=lane,  # type: ignore[arg-type]
            display=display,
            t0=float(index * 10),
            t1=float(index * 10 + 3),
            asset_ref=asset_refs[index - 1],
        )
        for index, (semantic_kind, implementation_kind, lane, display) in enumerate(
            definitions,
            start=1,
        )
    )
    return _mint_materialization_plan(
        plan_id=f"plan-{cut_id}",
        run_id="run-001",
        command_id=f"command-{cut_id}",
        episode_id="episode-001",
        cut_id=cut_id,
        format="long",
        director_acceptance_id="director-001",
        dp_acceptance_id="dp-001",
        visual_acceptance_id="visual-001",
        events=events,
        components=components,
    )


def test_typed_components_project_to_distinct_derived_lanes_without_a_roll(
    tmp_path: Path,
) -> None:
    refs = tuple(f"asset-{index}" for index in range(1, 6))
    assets = []
    for index, reference in enumerate(refs, start=1):
        path = tmp_path / f"asset-{index}.mov"
        path.write_bytes(f"asset-{index}".encode())
        assets.append(PreRenderedAsset(reference=reference, path=path))
    application = project_timeline_application(
        _plan(refs),
        PreRenderedAssetCatalog(assets),
    )

    assert tuple((item.semantic_kind, item.lane) for item in application.placements) == (
        ("chapter", "fullscreen_transition"),
        ("hero_title", "hero_title"),
        ("b_roll", "b_roll"),
        ("identity_card", "identity_card"),
        ("visual_effect", "visual_effect"),
    )
    assert all(item.lane != "a_roll" for item in application.placements)


def test_timeline_projects_component_final_media_when_event_source_ref_differs(
    tmp_path: Path,
) -> None:
    final_refs = tuple(f"asset-final-{index}" for index in range(1, 6))
    source_refs = tuple(f"asset-source-{index}" for index in range(1, 6))
    assets = []
    for index, reference in enumerate(final_refs, start=1):
        path = tmp_path / f"final-{index}.mov"
        path.write_bytes(reference.encode())
        assets.append(PreRenderedAsset(reference=reference, path=path))

    application = project_timeline_application(
        _plan(final_refs, source_asset_refs=source_refs),
        PreRenderedAssetCatalog(assets),
    )

    assert tuple(item.source_path for item in application.placements) == tuple(
        asset.path.resolve() for asset in assets
    )


def test_missing_component_final_media_fails_projection_before_application(
    tmp_path: Path,
) -> None:
    final_refs = tuple(f"asset-final-{index}" for index in range(1, 6))
    available = []
    for index, reference in enumerate(final_refs[:-1], start=1):
        path = tmp_path / f"final-{index}.mov"
        path.write_bytes(reference.encode())
        available.append(PreRenderedAsset(reference=reference, path=path))

    with pytest.raises(TimelineApplyError, match="exact catalog"):
        project_timeline_application(
            _plan(final_refs),
            PreRenderedAssetCatalog(available),
        )


def test_missing_final_media_fails_preflight_before_any_resolve_mutation(
    tmp_path: Path,
) -> None:
    final_refs = tuple(f"asset-final-{index}" for index in range(1, 6))
    available = []
    for index, reference in enumerate(final_refs[:-1], start=1):
        path = tmp_path / f"final-{index}.mov"
        path.write_bytes(reference.encode())
        available.append(PreRenderedAsset(reference=reference, path=path))
    binding = _binding()
    facade = _ResolveFacade(binding)
    manager = ResolveTransactionManager(
        DaVinciResolveTimelineAdapter(
            facade=facade,
            probe=_Probe(),
            binding=binding,
            assets=PreRenderedAssetCatalog(available),
        )
    )

    with pytest.raises(ResolveTransactionError, match="preflight"):
        manager.prepare(
            _plan(final_refs),
            canonical=binding.cuts[0].canonical,
            preview_path=tmp_path / "preview.mp4",
            subtitle_path=tmp_path / "subtitle.srt",
        )

    assert facade.duplicate_calls == 0
    assert facade.mutations == []


def test_davinci_adapter_applies_every_typed_component_only_to_duplicate_work(
    tmp_path: Path,
) -> None:
    refs = tuple(f"asset-{index}" for index in range(1, 6))
    assets = []
    for index, reference in enumerate(refs, start=1):
        path = tmp_path / f"asset-{index}.mov"
        path.write_bytes(f"asset-{index}".encode())
        assets.append(PreRenderedAsset(reference=reference, path=path))
    binding = _binding()
    facade = _ResolveFacade(binding)
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=_Probe(),
        binding=binding,
        assets=PreRenderedAssetCatalog(assets),
    )
    canonical = binding.cuts[0].canonical
    original = adapter.snapshot(canonical)

    workspace = adapter.duplicate(canonical, transaction_id="resolve-001")
    adapter.apply_plan(workspace.work, _plan(refs))

    assert adapter.snapshot(workspace.backup) == original
    assert workspace.work.uid != canonical.uid
    assert [placement.lane for placement in facade.applied] == [
        "fullscreen_transition",
        "hero_title",
        "b_roll",
        "identity_card",
        "visual_effect",
    ]
    assert all("a-roll" not in entry for entry in facade.mutations)


def test_davinci_preview_uses_h264_aac_render_and_checked_probe(tmp_path: Path) -> None:
    refs = tuple(f"asset-{index}" for index in range(1, 6))
    assets = []
    for index, reference in enumerate(refs, start=1):
        path = tmp_path / f"asset-{index}.mov"
        path.write_bytes(f"asset-{index}".encode())
        assets.append(PreRenderedAsset(reference=reference, path=path))
    output = tmp_path / "preview" / "value-L01.mp4"
    binding = _binding()
    facade = _ResolveFacade(binding)
    probe = _Probe(
        MediaProbeResult(
            path=output,
            duration_sec=481.5,
            video_codec="h264",
            audio_codec="aac",
            decode_ok=True,
            offline_frame_count=0,
        )
    )
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=probe,
        binding=binding,
        assets=PreRenderedAssetCatalog(assets),
    )
    workspace = adapter.duplicate(
        binding.cuts[0].canonical,
        transaction_id="resolve-render",
    )
    adapter.apply_plan(workspace.work, _plan(refs))

    preview = adapter.render_preview(workspace.work, output)

    assert preview.path == output
    assert preview.video_codec == "h264"
    assert preview.audio_codec == "aac"
    assert preview.duration_sec == 481.5
    assert facade.render_requests == [
        RenderRequest(
            output=output,
            container="mp4",
            video_codec="H264",
            export_audio=True,
            audio_codec="aac",
        )
    ]
    assert probe.inspected == [output]


def test_davinci_committed_transaction_retains_backup_and_can_compensate(
    tmp_path: Path,
) -> None:
    refs = tuple(f"asset-{index}" for index in range(1, 6))
    assets = []
    for index, reference in enumerate(refs, start=1):
        path = tmp_path / f"asset-{index}.mov"
        path.write_bytes(f"asset-{index}".encode())
        assets.append(PreRenderedAsset(reference=reference, path=path))
    binding = _binding()
    facade = _ResolveFacade(binding)
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=_Probe(),
        binding=binding,
        assets=PreRenderedAssetCatalog(assets),
    )
    canonical = binding.cuts[0].canonical
    baseline = adapter.snapshot(canonical)
    workspace = adapter.duplicate(canonical, transaction_id="resolve-compensate")
    adapter.apply_plan(workspace.work, _plan(refs))

    receipt = adapter.commit(
        workspace,
        transaction_id="resolve-compensate",
        cut_id="value-L01",
        retain_backup=True,
    )

    assert receipt.backup_retained is True
    assert workspace.backup in facade.timeline_identities()

    adapter.compensate(workspace, receipt)

    assert adapter.snapshot(canonical) == baseline
    assert workspace.work not in facade.timeline_identities()
    assert canonical in facade.timeline_identities()


def test_wrong_commit_or_compensation_identity_performs_no_resolve_mutation(
    tmp_path: Path,
) -> None:
    refs = tuple(f"asset-{index}" for index in range(1, 6))
    assets = []
    for index, reference in enumerate(refs, start=1):
        path = tmp_path / f"asset-{index}.mov"
        path.write_bytes(f"asset-{index}".encode())
        assets.append(PreRenderedAsset(reference=reference, path=path))
    binding = _binding()
    facade = _ResolveFacade(binding)
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=_Probe(),
        binding=binding,
        assets=PreRenderedAssetCatalog(assets),
    )
    workspace = adapter.duplicate(
        binding.cuts[0].canonical,
        transaction_id="resolve-exact",
    )
    before_wrong_commit = tuple(facade.mutations)

    with pytest.raises(ResolveTransactionError, match="exact cut transaction"):
        adapter.commit(
            workspace,
            transaction_id="resolve-wrong",
            cut_id="value-L01",
            retain_backup=True,
        )

    assert tuple(facade.mutations) == before_wrong_commit
    receipt = adapter.commit(
        workspace,
        transaction_id="resolve-exact",
        cut_id="value-L01",
        retain_backup=True,
    )
    before_wrong_compensation = tuple(facade.mutations)

    with pytest.raises(ResolveTransactionError, match="exact transaction backup"):
        adapter.compensate(
            workspace,
            replace(receipt, transaction_id="resolve-wrong"),
        )

    assert tuple(facade.mutations) == before_wrong_compensation


def test_failed_preview_probe_rolls_back_duplicate_and_restores_canonical(
    tmp_path: Path,
) -> None:
    refs = tuple(f"asset-{index}" for index in range(1, 6))
    assets = []
    for index, reference in enumerate(refs, start=1):
        path = tmp_path / f"asset-{index}.mov"
        path.write_bytes(f"asset-{index}".encode())
        assets.append(PreRenderedAsset(reference=reference, path=path))
    output = tmp_path / "preview" / "value-L01.mp4"
    binding = _binding()
    facade = _ResolveFacade(binding)
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=_Probe(
            MediaProbeResult(
                path=output,
                duration_sec=481.5,
                video_codec="h264",
                audio_codec="aac",
                decode_ok=False,
                offline_frame_count=0,
            )
        ),
        binding=binding,
        assets=PreRenderedAssetCatalog(assets),
    )
    manager = ResolveTransactionManager(adapter)

    with pytest.raises(ResolveTransactionError, match="decode probe contract"):
        manager.prepare(
            _plan(refs),
            canonical=binding.cuts[0].canonical,
            preview_path=output,
            subtitle_path=tmp_path / "subtitles" / "value-L01.srt",
        )

    assert facade.timeline_identities() == (binding.cuts[0].canonical,)


def test_duplicate_uid_must_be_distinct_before_any_timeline_is_cleared_or_appended(
    tmp_path: Path,
) -> None:
    binding = _binding()
    facade = _ResolveFacade(binding)
    facade.return_canonical_from_duplicate = True
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=_Probe(),
        binding=binding,
        assets=PreRenderedAssetCatalog(()),
    )
    canonical = binding.cuts[0].canonical
    original_state = facade.timeline_state(canonical.uid)

    with pytest.raises(ResolveTransactionError, match="distinct UID"):
        adapter.duplicate(canonical, transaction_id="resolve-bad-duplicate")

    assert facade.timeline_identities() == (canonical,)
    assert facade.timeline_state(canonical.uid) == original_state
    assert not any(entry.startswith(("clear-derived", "append")) for entry in facade.mutations)


class _FFprobeRunner:
    def __init__(self, results: tuple[FFprobeProcess, ...]) -> None:
        self.results = list(results)
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def run(self, arguments: tuple[str, ...], *, timeout_sec: float) -> FFprobeProcess:
        self.calls.append((arguments, timeout_sec))
        return self.results.pop(0)


def test_ffprobe_adapter_returns_stream_and_decode_contract(tmp_path: Path) -> None:
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"preview")
    runner = _FFprobeRunner(
        (
            FFprobeProcess(
                returncode=0,
                stdout=(
                    '{"format":{"duration":"481.5"},"streams":['
                    '{"codec_type":"video","codec_name":"h264"},'
                    '{"codec_type":"audio","codec_name":"aac"}]}'
                ),
                stderr="",
            ),
            FFprobeProcess(returncode=0, stdout="", stderr=""),
        )
    )

    result = FFprobeMediaProbe(runner=runner).inspect(preview)

    assert result == MediaProbeResult(
        path=preview,
        duration_sec=481.5,
        video_codec="h264",
        audio_codec="aac",
        decode_ok=True,
        offline_frame_count=0,
    )
    assert len(runner.calls) == 2
    assert runner.calls[0][0][0] == "ffprobe"
    assert runner.calls[0][0][-1] == str(preview)
    assert runner.calls[0][1] == 30.0
    assert runner.calls[1][0][0] == "ffmpeg"
    assert runner.calls[1][1] == 120.0


def test_production_adapter_transaction_restarts_commit_and_compensate_from_store(
    tmp_path: Path,
) -> None:
    refs = tuple(f"asset-{index}" for index in range(1, 6))
    assets = []
    for index, reference in enumerate(refs, start=1):
        path = tmp_path / f"asset-{index}.mov"
        path.write_bytes(f"asset-{index}".encode())
        assets.append(PreRenderedAsset(reference=reference, path=path))
    output = tmp_path / "preview" / "value-L01.mp4"
    binding = _binding()
    facade = _ResolveFacade(binding)
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=_Probe(
            MediaProbeResult(
                path=output,
                duration_sec=481.5,
                video_codec="h264",
                audio_codec="aac",
            )
        ),
        binding=binding,
        assets=PreRenderedAssetCatalog(assets),
    )
    store_root = tmp_path / "transactions"
    first_process = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(store_root),
    )
    transaction = first_process.prepare(
        _plan(refs),
        canonical=binding.cuts[0].canonical,
        preview_path=output,
        subtitle_path=tmp_path / "subtitles" / "value-L01.srt",
    )

    committing_process = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(store_root),
    )
    committing_process.commit(transaction.transaction_id, expected_cut_id="value-L01")
    compensating_process = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(store_root),
    )
    assert compensating_process.inspect_transaction(transaction.transaction_id)["status"] == (
        "committed"
    )
    compensating_process.compensating_rollback(
        transaction.transaction_id,
        expected_cut_id="value-L01",
    )

    final_process = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(store_root),
    )
    assert final_process.inspect_transaction(transaction.transaction_id)["status"] == (
        "compensated"
    )
    assert facade.timeline_identities() == (binding.cuts[0].canonical,)


def test_transaction_persistence_failure_rolls_back_open_resolve_workspace(
    tmp_path: Path,
) -> None:
    refs = tuple(f"asset-{index}" for index in range(1, 6))
    assets = []
    for index, reference in enumerate(refs, start=1):
        path = tmp_path / f"asset-{index}.mov"
        path.write_bytes(f"asset-{index}".encode())
        assets.append(PreRenderedAsset(reference=reference, path=path))
    output = tmp_path / "preview" / "value-L01.mp4"
    binding = _binding()
    facade = _ResolveFacade(binding)
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=_Probe(
            MediaProbeResult(
                path=output,
                duration_sec=481.5,
                video_codec="h264",
                audio_codec="aac",
            )
        ),
        binding=binding,
        assets=PreRenderedAssetCatalog(assets),
    )
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_bytes(b"file")
    manager = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(blocked_parent / "transactions"),
    )

    with pytest.raises(ResolveTransactionError, match="persistence failed"):
        manager.prepare(
            _plan(refs),
            canonical=binding.cuts[0].canonical,
            preview_path=output,
            subtitle_path=tmp_path / "subtitles" / "value-L01.srt",
        )

    assert facade.timeline_identities() == (binding.cuts[0].canonical,)


def test_production_timeline_modules_do_not_import_short_form_execution_paths() -> None:
    module_root = (
        Path(__file__).parents[3] / "agents" / "brook" / "script_video" / "finished_cut_production"
    )
    imported_names: list[str] = []
    for filename in ("_timeline_apply.py", "_resolve_davinci.py"):
        tree = ast.parse((module_root / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_names.append(node.module)

    assert not any("run_short_" in name for name in imported_names)


def test_render_requires_exact_retained_backup_before_selecting_work_timeline(
    tmp_path: Path,
) -> None:
    output = tmp_path / "preview.mp4"
    binding = _binding()
    facade = _ResolveFacade(binding)
    adapter = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=_Probe(
            MediaProbeResult(
                path=output,
                duration_sec=481.5,
                video_codec="h264",
                audio_codec="aac",
            )
        ),
        binding=binding,
        assets=PreRenderedAssetCatalog(()),
    )
    workspace = adapter.duplicate(
        binding.cuts[0].canonical,
        transaction_id="resolve-no-backup",
    )
    facade.delete_timeline(workspace.backup.uid)
    before = tuple(facade.mutations)

    with pytest.raises(ResolveTransactionError, match="retained backup"):
        adapter.render_preview(workspace.work, output)

    assert tuple(facade.mutations) == before
