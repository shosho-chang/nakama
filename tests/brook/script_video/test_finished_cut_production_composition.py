import hashlib
from pathlib import Path

import pytest

from agents.brook.script_video.editorial_master import EditorialMasterSelection
from agents.brook.script_video.finished_cut_production._approved_cut import (
    ApprovedCutAuthority,
    ApprovedCutRegistration,
    VerifiedEditorialMaster,
)
from agents.brook.script_video.finished_cut_production._assets import WorkerSelectionCatalog
from agents.brook.script_video.finished_cut_production._composition import (
    ProductionCutoverConfiguration,
    ProductionPaths,
    ProductionResolveConfiguration,
    ProductionResolvePorts,
    build_production_application,
)
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
)
from agents.brook.script_video.finished_cut_production._materialization import (
    MaterializationCoordinator,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    _mint_materialization_plan,
    _ProductionRun,
)
from agents.brook.script_video.finished_cut_production._resolve import (
    TimelineIdentity,
)
from agents.brook.script_video.finished_cut_production._resolve_davinci import (
    MediaProbeResult,
    ResolveCutBinding,
    ResolveProjectBinding,
    ResolveProjectIdentity,
    ResolveTimelineItem,
    ResolveTimelineState,
    ResolveTimelineTrack,
)
from agents.brook.script_video.finished_cut_production._resolve_fusion import (
    ResolveDatabaseIdentity,
    ResolveProjectLocator,
)
from agents.brook.script_video.finished_cut_production._store import (
    _FilesystemProductionStore,
)


class _RegistrationMasterVerifier:
    def verify(self, *, episode_id: str, editorial_master_id: str) -> VerifiedEditorialMaster:
        return VerifiedEditorialMaster(episode_id, editorial_master_id, 1_200.0)


class _PreviewProbe:
    def __init__(self, duration_sec: float) -> None:
        self.duration_sec = duration_sec

    def inspect(self, path: Path) -> MediaProbeResult:
        return MediaProbeResult(
            path=path,
            duration_sec=self.duration_sec,
            video_codec="h264",
            audio_codec="aac",
            decode_ok=True,
            offline_frame_count=0,
        )


class _ResolveFacade:
    def __init__(
        self,
        *,
        project: ResolveProjectIdentity,
        canonical: TimelineIdentity,
        state: ResolveTimelineState,
    ) -> None:
        self.project = project
        self.identities = {canonical.uid: canonical}
        self.states = {canonical.uid: state}
        self.duplicates = 0
        self.applies = 0
        self.renders = 0
        self.saves = 0

    def project_identity(self) -> ResolveProjectIdentity:
        return self.project

    def timeline_identities(self) -> tuple[TimelineIdentity, ...]:
        return tuple(self.identities.values())

    def timeline_state(self, uid: str) -> ResolveTimelineState:
        return self.states[uid]

    def duplicate_timeline(self, uid: str, name: str) -> TimelineIdentity:
        self.duplicates += 1
        duplicate = TimelineIdentity(name, f"work-{self.duplicates}")
        self.identities[duplicate.uid] = duplicate
        self.states[duplicate.uid] = self.states[uid]
        return duplicate

    def rename_timeline(self, uid: str, name: str) -> None:
        self.identities[uid] = TimelineIdentity(name, uid)

    def delete_timeline(self, uid: str) -> None:
        self.identities.pop(uid)
        self.states.pop(uid)

    def clear_derived_lanes(self, uid: str) -> None:
        assert uid in self.identities
        self.applies += 1

    def append_pre_rendered(self, uid: str, placement: object) -> None:
        raise AssertionError(f"A-roll plan must not append derived media: {uid} {placement}")

    def select_timeline(self, uid: str) -> None:
        assert uid in self.identities

    def render_preview(self, uid: str, request) -> Path:
        assert uid in self.identities
        self.renders += 1
        request.output.parent.mkdir(parents=True, exist_ok=True)
        request.output.write_bytes(b"preview")
        return request.output

    def save_project(self) -> None:
        self.saves += 1


def _registration() -> ApprovedCutRegistration:
    return ApprovedCutRegistration(
        episode_id="episode-1",
        cut_id="long-3",
        format="long",
        editorial_master_id="a" * 64,
        winner_id="winner-long-3",
        tight_cut_id="tight-long-3",
        source_ranges=(CutSourceRange(120.0, 660.0),),
        cues=(
            CueAnchor("cue-1", "第一句", 0.0, 270.0, "section-1"),
            CueAnchor("cue-2", "第二句", 270.0, 540.0, "section-1"),
        ),
        sections=(CanonicalSection("section-1", "第一章", 0.0),),
        human_approved=True,
        approved_by="human:shosho",
        approved_at="2026-08-28T10:00:00+08:00",
    )


def _canonical_state(master_sha256: str) -> ResolveTimelineState:
    items = (
        ResolveTimelineItem("v1", "video", 1, 0, 16_200, 3_600, 19_800, (), master_sha256),
        ResolveTimelineItem("a1", "audio", 1, 0, 16_200, 3_600, 19_800, (), master_sha256),
        ResolveTimelineItem(
            "s1",
            "subtitle",
            1,
            0,
            8_100,
            0,
            8_100,
            (("Text", "第一句"),),
            master_sha256,
        ),
        ResolveTimelineItem(
            "s2",
            "subtitle",
            1,
            8_100,
            16_200,
            8_100,
            16_200,
            (("Text", "第二句"),),
            master_sha256,
        ),
    )
    return ResolveTimelineState(
        start_frame=0,
        end_frame=16_200,
        items=items,
        tracks=(
            ResolveTimelineTrack("video", 1, "V1", True, True, ("v1",)),
            ResolveTimelineTrack("audio", 1, "A1", True, True, ("a1",)),
            ResolveTimelineTrack("subtitle", 1, "S1", True, True, ("s1", "s2")),
        ),
        frame_rate=30.0,
    )


def test_resolve_configuration_rejects_cross_episode_binding_before_connect(
    tmp_path: Path,
) -> None:
    locator = ResolveProjectLocator(
        episode_id="episode-1",
        database=ResolveDatabaseIdentity("Disk", "Local Database"),
        folder="Podcasts",
        project_name="Episode Project",
    )
    binding = ResolveProjectBinding(
        episode_id="episode-2",
        project_name="Episode Project",
        project_uid="resolve-project:exact",
        cuts=(
            ResolveCutBinding(
                cut_id="long-3",
                canonical=TimelineIdentity("Long 3", "timeline-long-3"),
            ),
        ),
    )

    with pytest.raises(ValueError, match="episode identity"):
        ProductionResolveConfiguration(
            locator=locator,
            binding=binding,
            editorial_master_content_hash="a" * 64,
            staging_root=tmp_path / "episode-1/highlights/staging/finished-cut",
        )


def test_resolve_configuration_accepts_exact_root_folder_identity(tmp_path: Path) -> None:
    locator = ResolveProjectLocator(
        episode_id="episode-1",
        database=ResolveDatabaseIdentity("Disk", "Local Database"),
        folder="",
        project_name="Episode Project",
    )
    binding = ResolveProjectBinding(
        episode_id="episode-1",
        project_name="Episode Project",
        project_uid="resolve-project:exact",
        cuts=(
            ResolveCutBinding(
                cut_id="long-3",
                canonical=TimelineIdentity("Long 3 Base", "timeline-long-3-base"),
            ),
        ),
    )

    configuration = ProductionResolveConfiguration(
        locator=locator,
        binding=binding,
        editorial_master_content_hash="a" * 64,
        staging_root=tmp_path / "episode-1/highlights/staging/finished-cut",
    )

    assert configuration.locator.folder == ""


def test_factory_wires_private_materialization_only_from_exact_resolve_configuration(
    tmp_path: Path,
) -> None:
    paths = ProductionPaths(tmp_path / "runtime", tmp_path / "episodes")
    episode_root = paths.episodes_root / "episode-1"
    locator = ResolveProjectLocator(
        episode_id="episode-1",
        database=ResolveDatabaseIdentity("Disk", "Local Database"),
        folder="Podcasts",
        project_name="Episode Project",
    )
    binding = ResolveProjectBinding(
        episode_id="episode-1",
        project_name="Episode Project",
        project_uid="resolve-project:exact",
        cuts=(
            ResolveCutBinding(
                "long-3",
                TimelineIdentity("Long 3", "timeline-long-3"),
            ),
        ),
    )
    facade_calls = 0

    def facade_factory(_locator, _media_identity_resolver):
        nonlocal facade_calls
        facade_calls += 1
        return object()

    application = build_production_application(
        paths,
        "episode-1",
        resolve_configuration=ProductionResolveConfiguration(
            locator=locator,
            binding=binding,
            editorial_master_content_hash="a" * 64,
            staging_root=episode_root / "highlights" / "staging" / "finished-cut",
        ),
        resolve_ports=ProductionResolvePorts(
            facade_factory=facade_factory,
            media_probe=object(),
        ),
    )

    assert isinstance(application._materialization, MaterializationCoordinator)
    assert application._materialization_unavailable_reason is None
    assert facade_calls == 1


def test_factory_wires_global_cutover_only_from_three_exact_cut_bindings(
    tmp_path: Path,
) -> None:
    paths = ProductionPaths(tmp_path / "runtime", tmp_path / "episodes")
    episode_root = paths.episodes_root / "episode-1"
    cuts = tuple(
        ResolveCutBinding(
            cut_id=f"long-{position}",
            canonical=TimelineIdentity(
                f"Long {position} clean base",
                f"timeline-long-{position}",
            ),
        )
        for position in range(1, 4)
    )
    resolve_configuration = ProductionResolveConfiguration(
        locator=ResolveProjectLocator(
            episode_id="episode-1",
            database=ResolveDatabaseIdentity("Disk", "Local Database"),
            folder="",
            project_name="Episode Project",
        ),
        binding=ResolveProjectBinding(
            episode_id="episode-1",
            project_name="Episode Project",
            project_uid="resolve-project:exact",
            cuts=cuts,
        ),
        editorial_master_content_hash="a" * 64,
        staging_root=episode_root / "highlights" / "staging" / "finished-cut",
    )
    deployment_path = paths.runtime_root / "deployment" / "current.json"
    deployment_path.parent.mkdir(parents=True, exist_ok=True)
    deployment_path.write_bytes(
        b'{"deployment_id":"legacy-v1","schema":"nakama.finished-cut-deployment-pointer.v1"}\n'
    )

    application = build_production_application(
        paths,
        "episode-1",
        resolve_configuration=resolve_configuration,
        resolve_ports=ProductionResolvePorts(
            facade_factory=lambda _locator, _resolver: object(),
            media_probe=object(),
        ),
        cutover_configuration=ProductionCutoverConfiguration(
            fixed_cut_order=("long-1", "long-2", "long-3"),
            target_deployment_id="finished-cut-production-v1",
            deployment_state_path=deployment_path,
        ),
    )

    assert application._cutover is not None


@pytest.mark.parametrize(
    ("live_project_uid", "expected_state"),
    [
        ("resolve-project:exact", "preview_ready"),
        ("resolve-project:wrong", "needs_review"),
        ("resolve-project:unused", "pending"),
    ],
)
def test_review_ready_run_prepares_candidate_without_publishing_current(
    tmp_path: Path,
    live_project_uid: str,
    expected_state: str,
) -> None:
    paths = ProductionPaths(tmp_path / "runtime", tmp_path / "episodes")
    episode_root = paths.episodes_root / "episode-1"
    authority = ApprovedCutAuthority(
        paths.runtime_root / "approved-cuts",
        master_verifier=_RegistrationMasterVerifier(),
    )
    command_id = authority.register(_registration())
    command = authority.resolve(command_id)
    assert command is not None
    context = authority.resolve_context(
        episode_id="episode-1",
        cut_id="long-3",
        editorial_master_id="a" * 64,
        tight_cut_id="tight-long-3",
    )
    assert context is not None
    event = EventRecord(
        event_id="event-1",
        master_cue_ids=("cue-1", "cue-2"),
        text_hash=hashlib.sha256("第一句第二句".encode()).hexdigest(),
        intent="保留完整 A-roll",
        visual_status="approved",
        text="第一句第二句",
        t0=0.0,
        t1=540.0,
        section_id="section-1",
        display="保留講者原畫面",
        semantic_kind="intentional_aroll",
        intentional_aroll=True,
        implementation_kind="intentional_aroll",
    )
    plan = _mint_materialization_plan(
        plan_id="plan-1",
        run_id="run-1",
        command_id=command_id,
        episode_id="episode-1",
        cut_id="long-3",
        format="long",
        director_acceptance_id="director-1",
        dp_acceptance_id="dp-1",
        visual_acceptance_id="visual-1",
        events=(event,),
    )
    run_store_root = paths.runtime_root / "episodes" / "episode-1" / "runs"
    _FilesystemProductionStore(run_store_root).create_run(
        command,
        _ProductionRun(
            run_id="run-1",
            command_id=command_id,
            editorial_context=context,
            status="review_ready",
            outstanding_request=None,
            materialization_plan=plan,
        ),
        WorkerSelectionCatalog(()),
    )

    master_path = episode_root / "editorial-master" / "v1" / "master.mp4"
    master_path.parent.mkdir(parents=True)
    master_path.write_bytes(b"master")
    master_sha256 = hashlib.sha256(master_path.read_bytes()).hexdigest()
    canonical = TimelineIdentity("Long 3", "timeline-long-3")
    facade = _ResolveFacade(
        project=ResolveProjectIdentity("episode-1", "Episode Project", live_project_uid),
        canonical=canonical,
        state=_canonical_state(master_sha256),
    )

    def verify_master(
        root: str | Path,
        *,
        expected_episode_id: str | None = None,
        expected_content_hash: str | None = None,
    ) -> EditorialMasterSelection:
        assert Path(root).resolve() == episode_root.resolve()
        assert expected_episode_id == "episode-1"
        assert expected_content_hash == "a" * 64
        receipt = {
            "episode_id": "episode-1",
            "content_hash": "a" * 64,
            "artifacts": {
                "media": {
                    "sha256": master_sha256,
                    "bytes": master_path.stat().st_size,
                }
            },
            "project": {"name": "Episode Project"},
            "timeline": {
                "name": "Editorial Master",
                "uid": "editorial-master-uid",
                "fps": 30.0,
                "duration_sec": 1_200.0,
            },
        }
        return EditorialMasterSelection(
            video_path=master_path,
            srt_path=master_path.with_suffix(".srt"),
            snapshot_path=master_path.with_suffix(".json"),
            receipt_path=episode_root / "editorial-master" / "v1" / "EDITORIAL-MASTER.json",
            receipt=receipt,
        )

    configuration = ProductionResolveConfiguration(
        locator=ResolveProjectLocator(
            episode_id="episode-1",
            database=ResolveDatabaseIdentity("Disk", "Local Database"),
            folder="Podcasts",
            project_name="Episode Project",
        ),
        binding=ResolveProjectBinding(
            episode_id="episode-1",
            project_name="Episode Project",
            project_uid="resolve-project:exact",
            cuts=(ResolveCutBinding("long-3", canonical),),
        ),
        editorial_master_content_hash="a" * 64,
        staging_root=episode_root / "highlights" / "staging" / "finished-cut",
    )
    ports = ProductionResolvePorts(
        facade_factory=lambda _locator, _resolver: facade,
        media_probe=_PreviewProbe(540.0),
        editorial_master_verifier=verify_master,
    )
    application = (
        build_production_application(paths, "episode-1")
        if expected_state == "pending"
        else build_production_application(
            paths,
            "episode-1",
            resolve_configuration=configuration,
            resolve_ports=ports,
        )
    )

    status = application.advance(command_id)
    if expected_state == "pending":
        assert status.state == "pending"
        assert status.reason_code == "resolve_binding_not_configured"
        assert (facade.duplicates, facade.applies, facade.renders) == (0, 0, 0)
        return
    if expected_state == "needs_review":
        assert status.state == "needs_review"
        assert status.reason_code == "resolve_project_identity_mismatch"
        assert (facade.duplicates, facade.applies, facade.renders) == (0, 0, 0)
        assert not tuple((episode_root / "highlights" / "staging").rglob("materialization.json"))
        return
    reopened_status = build_production_application(
        paths,
        "episode-1",
        resolve_configuration=configuration,
        resolve_ports=ports,
    ).advance(command_id)

    assert status.state == expected_state
    assert reopened_status == status
    assert status.current_stage == "materialization"
    assert (facade.duplicates, facade.applies, facade.renders) == (1, 1, 1)
    assert len(tuple((episode_root / "highlights" / "staging").rglob("materialization.json"))) == 1
    assert not (
        episode_root / "highlights" / "review" / "finished_review_manifest_current.json"
    ).exists()
