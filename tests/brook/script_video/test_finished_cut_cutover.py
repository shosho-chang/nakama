from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._cutover import (
    AtomicDeploymentPointerAdapter,
    DeploymentSnapshot,
    FilesystemReleaseCutoverAdapter,
    GlobalCutover,
    GlobalCutoverError,
    GlobalCutoverJournal,
    PointerSnapshot,
    UnpublishedReleaseIndex,
)
from agents.brook.script_video.finished_cut_production._persistence import (
    AtomicCutoverJournalStore,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    FinishedCutRelease,
    MaterializationPlan,
    StagedReleaseCandidate,
    _mint_materialization_plan,
    _mint_staged_release_candidate,
)
from agents.brook.script_video.finished_cut_production._release import (
    FinishedCutReleaseLifecycle,
)
from agents.brook.script_video.finished_cut_production._resolve import (
    CommitReceipt,
    PreviewRender,
    ResolveTransactionManager,
    TimelineIdentity,
    TimelineSnapshot,
    TimelineWorkspace,
)

_CUT_ORDER = ("value-L01", "thesis-L02", "punch-L04")


class _TimelineAdapter:
    def __init__(self, event_log: list[str]) -> None:
        self.event_log = event_log
        self.timelines: dict[str, dict[str, object]] = {}
        self.compensations: list[str] = []
        self.fail_commit_cut: str | None = None

    def preflight_plan(self, plan: MaterializationPlan) -> None:
        return None

    def add_canonical(self, cut_id: str) -> TimelineIdentity:
        identity = TimelineIdentity(name=cut_id, uid=f"canonical-{cut_id}")
        self.timelines[identity.uid] = {
            "name": identity.name,
            "protected": f"master-{cut_id}",
            "visuals": [],
        }
        return identity

    def snapshot(self, timeline: TimelineIdentity) -> TimelineSnapshot:
        state = self.timelines[timeline.uid]
        return TimelineSnapshot(
            protected_fingerprint=str(state["protected"]),
            full_fingerprint=repr(state),
        )

    def duplicate(
        self,
        canonical: TimelineIdentity,
        *,
        transaction_id: str,
    ) -> TimelineWorkspace:
        original = self.timelines[canonical.uid]
        backup = TimelineIdentity(
            name=f"__backup__{transaction_id}",
            uid=canonical.uid,
        )
        work = TimelineIdentity(name=canonical.name, uid=f"work-{transaction_id}")
        original["name"] = backup.name
        self.timelines[work.uid] = deepcopy(original) | {"name": work.name}
        return TimelineWorkspace(canonical=canonical, work=work, backup=backup)

    def apply_plan(self, work: TimelineIdentity, plan: MaterializationPlan) -> None:
        visuals = self.timelines[work.uid]["visuals"]
        assert isinstance(visuals, list)
        visuals.append(plan.plan_id)

    def render_preview(self, work: TimelineIdentity, output: Path) -> PreviewRender:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"preview:{work.uid}".encode())
        return PreviewRender(
            path=output,
            duration_sec=600.0,
            video_codec="h264",
            audio_codec="aac",
        )

    def rollback(self, workspace: TimelineWorkspace) -> None:
        self.timelines.pop(workspace.work.uid, None)
        self.timelines[workspace.backup.uid]["name"] = workspace.canonical.name

    def commit(
        self,
        workspace: TimelineWorkspace,
        *,
        transaction_id: str,
        cut_id: str,
        retain_backup: bool,
    ) -> CommitReceipt:
        if cut_id == self.fail_commit_cut:
            raise RuntimeError(f"injected commit failure:{cut_id}")
        self.event_log.append(f"commit:{cut_id}")
        return CommitReceipt(
            transaction_id=transaction_id,
            cut_id=cut_id,
            work_uid=workspace.work.uid,
            transaction_receipt_id=f"receipt-{cut_id}",
            rollback_ref=f"timeline:{workspace.backup.uid}",
            backup_retained=retain_backup,
        )

    def compensate(
        self,
        workspace: TimelineWorkspace,
        receipt: CommitReceipt,
    ) -> None:
        self.event_log.append(f"compensate:{receipt.cut_id}")
        self.compensations.append(receipt.cut_id)
        self.rollback(workspace)


class _RecordingSealer:
    def __init__(
        self,
        lifecycle: FinishedCutReleaseLifecycle,
        event_log: list[str],
    ) -> None:
        self.lifecycle = lifecycle
        self.event_log = event_log
        self.fail_cut: str | None = None
        self.discarded_release_ids: list[str] = []

    def seal_candidate(
        self,
        candidate: StagedReleaseCandidate,
    ) -> FinishedCutRelease:
        if candidate.cut_id == self.fail_cut:
            raise RuntimeError(f"injected seal failure:{candidate.cut_id}")
        self.event_log.append(f"seal:{candidate.cut_id}")
        return self.lifecycle.seal_candidate(candidate)

    def discard_unpublished(
        self,
        releases: tuple[FinishedCutRelease, ...],
    ) -> None:
        self.event_log.append("discard-releases")
        self.discarded_release_ids.extend(release.release_id for release in releases)


class _CurrentIndex:
    def __init__(self, event_log: list[str]) -> None:
        self.event_log = event_log
        self.current = PointerSnapshot(index_id="old-current")
        self.publish_calls = 0
        self.discarded: list[str] = []
        self.fail_build = False
        self.fail_publish = False

    def snapshot_pointer(self) -> PointerSnapshot:
        return self.current

    def inspect_pointer(self) -> PointerSnapshot:
        return self.current

    def build_index(
        self,
        releases: tuple[FinishedCutRelease, ...],
    ) -> UnpublishedReleaseIndex:
        if self.fail_build:
            raise RuntimeError("injected index failure")
        self.event_log.append("build-index")
        return UnpublishedReleaseIndex(
            index_id="index-v3-new",
            episode_id=releases[0].episode_id,
            release_ids=tuple(release.release_id for release in releases),
        )

    def publish_pointer(self, index: UnpublishedReleaseIndex) -> None:
        self.event_log.append("publish-pointer")
        self.publish_calls += 1
        self.current = PointerSnapshot(index_id=index.index_id)
        if self.fail_publish:
            raise RuntimeError("injected pointer failure")

    def restore_pointer(self, snapshot: PointerSnapshot) -> None:
        self.event_log.append("restore-pointer")
        self.current = snapshot

    def discard_index(self, index: UnpublishedReleaseIndex) -> None:
        self.event_log.append("discard-index")
        self.discarded.append(index.index_id)


class _Deployment:
    def __init__(self, event_log: list[str]) -> None:
        self.event_log = event_log
        self.current = DeploymentSnapshot(deployment_id="old-deployment")
        self.fail_activate = False

    def snapshot_deployment(self) -> DeploymentSnapshot:
        return self.current

    def inspect_deployment(self) -> DeploymentSnapshot:
        return self.current

    def activate(self, deployment_id: str) -> None:
        self.event_log.append("activate")
        self.current = DeploymentSnapshot(deployment_id=deployment_id)
        if self.fail_activate:
            raise RuntimeError("injected activate failure")

    def restore_deployment(self, snapshot: DeploymentSnapshot) -> None:
        self.event_log.append("restore-deployment")
        self.current = snapshot


class _JournalStore:
    def __init__(self) -> None:
        self.journals: dict[str, GlobalCutoverJournal] = {}
        self.crash_before_committed_count: int | None = None
        self.crash_before_compensated_count: int | None = None
        self.crash_before_pointer_published = False

    def load(self, cutover_id: str) -> GlobalCutoverJournal | None:
        return self.journals.get(cutover_id)

    def save(self, journal: GlobalCutoverJournal) -> None:
        if len(journal.committed_transaction_ids) == self.crash_before_committed_count:
            self.crash_before_committed_count = None
            raise KeyboardInterrupt("injected process crash before journal save")
        if len(journal.compensated_transaction_ids) == self.crash_before_compensated_count:
            self.crash_before_compensated_count = None
            raise KeyboardInterrupt("injected rollback crash before journal save")
        if journal.pointer_published and self.crash_before_pointer_published:
            self.crash_before_pointer_published = False
            raise KeyboardInterrupt("injected pointer crash before journal save")
        self.journals[journal.cutover_id] = journal


def _plan(cut_id: str) -> MaterializationPlan:
    event = EventRecord(
        event_id=f"event-{cut_id}",
        master_cue_ids=(f"cue-{cut_id}",),
        text_hash=hashlib.sha256(cut_id.encode()).hexdigest(),
        intent=f"visual intent for {cut_id}",
        asset_ref=f"asset:{cut_id}",
        visual_status="approved",
    )
    return _mint_materialization_plan(
        plan_id=f"plan-{cut_id}",
        run_id=f"run-{cut_id}",
        command_id=f"approved-{cut_id}",
        episode_id="episode-001",
        cut_id=cut_id,
        format="long",
        director_acceptance_id=f"director-{cut_id}",
        dp_acceptance_id=f"dp-{cut_id}",
        visual_acceptance_id=f"visual-{cut_id}",
        events=(event,),
    )


def _preview_probe(_path: Path) -> dict[str, object]:
    return {
        "duration_sec": 600.0,
        "video_codec": "h264",
        "audio_codec": "aac",
    }


def _prepared_candidates(
    tmp_path: Path,
    event_log: list[str],
) -> tuple[
    ResolveTransactionManager,
    _TimelineAdapter,
    FinishedCutReleaseLifecycle,
    tuple[StagedReleaseCandidate, ...],
]:
    adapter = _TimelineAdapter(event_log)
    manager = ResolveTransactionManager(adapter)
    lifecycle = FinishedCutReleaseLifecycle(
        tmp_path,
        transactions=manager,
        preview_probe=_preview_probe,
    )
    candidates: list[StagedReleaseCandidate] = []
    for cut_id in _CUT_ORDER:
        subtitle = tmp_path / "highlights" / "srt" / f"{cut_id}.srt"
        subtitle.parent.mkdir(parents=True, exist_ok=True)
        subtitle.write_text(f"subtitle:{cut_id}", encoding="utf-8")
        transaction = manager.prepare(
            _plan(cut_id),
            canonical=adapter.add_canonical(cut_id),
            preview_path=tmp_path / "highlights" / "preview" / f"{cut_id}.mp4",
            subtitle_path=subtitle,
        )
        candidates.append(
            lifecycle.stage_candidate(
                _plan(cut_id),
                editorial_master_id="master-001",
                winner_id=f"winner-{cut_id}",
                tight_cut_id=f"tight-{cut_id}",
                transaction_id=transaction.transaction_id,
                preview_path=transaction.preview.path,
                subtitle_path=subtitle,
            )
        )
    return manager, adapter, lifecycle, tuple(candidates)


def test_production_adapters_publish_one_v3_pointer_after_three_commits(
    tmp_path: Path,
) -> None:
    event_log: list[str] = []
    manager, _timeline, lifecycle, candidates = _prepared_candidates(tmp_path, event_log)
    current_path = tmp_path / "highlights" / "review" / "finished_review_manifest_current.json"
    current_path.parent.mkdir(parents=True, exist_ok=True)
    old_pointer = b'{"schema":"nakama.finished_cut_review_manifest.v1"}\n'
    current_path.write_bytes(old_pointer)
    deployment_path = tmp_path / "runtime" / "deployment" / "current.json"
    deployment_path.parent.mkdir(parents=True, exist_ok=True)
    deployment_path.write_bytes(
        b'{"deployment_id":"legacy-v1","schema":"nakama.finished-cut-deployment-pointer.v1"}\n'
    )
    releases = FilesystemReleaseCutoverAdapter(
        lifecycle=lifecycle,
        episode_id="episode-001",
        rollback_root=tmp_path / "runtime" / "cutovers" / "pointer-snapshots",
    )
    deployment = AtomicDeploymentPointerAdapter(
        deployment_path,
        target_deployment_id="finished-cut-production-v1",
    )
    cutover = GlobalCutover(
        resolve=manager,
        sealer=releases,
        current_index=releases,
        deployment=deployment,
        journals=AtomicCutoverJournalStore(tmp_path / "runtime" / "cutovers" / "journals"),
        fixed_cut_order=_CUT_ORDER,
    )

    result = cutover.run(
        "cutover-production",
        candidates=tuple(reversed(candidates)),
        target_deployment_id="finished-cut-production-v1",
    )

    assert result.status == "completed"
    assert event_log == [
        "commit:value-L01",
        "commit:thesis-L02",
        "commit:punch-L04",
    ]
    assert {release.release_id for release in lifecycle.inspect_current("episode-001")} == set(
        result.unpublished_index.release_ids
    )
    assert releases.inspect_pointer().index_id == result.unpublished_index.index_id
    assert deployment.inspect_deployment() == DeploymentSnapshot("finished-cut-production-v1")
    assert old_pointer in tuple(
        path.read_bytes()
        for path in (tmp_path / "runtime" / "cutovers" / "pointer-snapshots").glob("*.json")
    )


def test_production_adapters_restore_old_pointer_and_deployment_on_activation_failure(
    tmp_path: Path,
) -> None:
    event_log: list[str] = []
    manager, timeline, lifecycle, candidates = _prepared_candidates(tmp_path, event_log)
    current_path = tmp_path / "highlights" / "review" / "finished_review_manifest_current.json"
    current_path.parent.mkdir(parents=True, exist_ok=True)
    old_pointer = b'{"schema":"nakama.finished_cut_review_manifest.v1"}\n'
    current_path.write_bytes(old_pointer)
    deployment_path = tmp_path / "runtime" / "deployment" / "current.json"
    deployment_path.parent.mkdir(parents=True, exist_ok=True)
    old_deployment = (
        b'{"deployment_id":"legacy-v1","schema":"nakama.finished-cut-deployment-pointer.v1"}\n'
    )
    deployment_path.write_bytes(old_deployment)
    releases = FilesystemReleaseCutoverAdapter(
        lifecycle=lifecycle,
        episode_id="episode-001",
        rollback_root=tmp_path / "runtime" / "cutovers" / "pointer-snapshots",
    )
    durable_deployment = AtomicDeploymentPointerAdapter(
        deployment_path,
        target_deployment_id="finished-cut-production-v1",
    )

    class _FailAfterActivation:
        def snapshot_deployment(self):
            return durable_deployment.snapshot_deployment()

        def inspect_deployment(self):
            return durable_deployment.inspect_deployment()

        def activate(self, deployment_id: str) -> None:
            durable_deployment.activate(deployment_id)
            raise RuntimeError("injected activation health-check failure")

        def restore_deployment(self, snapshot: DeploymentSnapshot) -> None:
            durable_deployment.restore_deployment(snapshot)

    cutover = GlobalCutover(
        resolve=manager,
        sealer=releases,
        current_index=releases,
        deployment=_FailAfterActivation(),
        journals=AtomicCutoverJournalStore(tmp_path / "runtime" / "cutovers" / "journals"),
        fixed_cut_order=_CUT_ORDER,
    )

    with pytest.raises(GlobalCutoverError, match="activation health-check"):
        cutover.run(
            "cutover-production-rollback",
            candidates=candidates,
            target_deployment_id="finished-cut-production-v1",
        )

    assert current_path.read_bytes() == old_pointer
    assert deployment_path.read_bytes() == old_deployment
    assert timeline.compensations == ["punch-L04", "thesis-L02", "value-L01"]
    assert not tuple((tmp_path / "highlights" / "releases" / "v1").glob("*.json"))
    assert not tuple((tmp_path / "highlights" / "releases" / "index" / "v3").glob("*.json"))


def test_deployment_pointer_restart_finishes_exact_staged_activation(tmp_path: Path) -> None:
    path = tmp_path / "deployment" / "current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b'{"deployment_id":"legacy-v1","schema":"nakama.finished-cut-deployment-pointer.v1"}\n'
    )
    adapter = AtomicDeploymentPointerAdapter(
        path,
        target_deployment_id="finished-cut-production-v1",
    )
    staged = adapter._staging_path("finished-cut-production-v1")
    staged.write_bytes(
        b'{"deployment_id":"finished-cut-production-v1","schema":'
        b'"nakama.finished-cut-deployment-pointer.v1"}\n'
    )

    adapter.activate("finished-cut-production-v1")

    assert adapter.inspect_deployment() == DeploymentSnapshot("finished-cut-production-v1")
    assert not staged.exists()


def test_global_cutover_commits_fixed_order_and_publishes_pointer_last_once(
    tmp_path: Path,
) -> None:
    event_log: list[str] = []
    manager, _timeline, lifecycle, candidates = _prepared_candidates(tmp_path, event_log)
    current_index = _CurrentIndex(event_log)
    deployment = _Deployment(event_log)
    journal_store = _JournalStore()
    cutover = GlobalCutover(
        resolve=manager,
        sealer=_RecordingSealer(lifecycle, event_log),
        current_index=current_index,
        deployment=deployment,
        journals=journal_store,
        fixed_cut_order=_CUT_ORDER,
    )
    assert {
        manager.inspect_transaction(candidate.preview_ready_transaction_id)["status"]
        for candidate in candidates
    } == {"preview_ready"}

    result = cutover.run(
        "cutover-001",
        candidates=tuple(reversed(candidates)),
        target_deployment_id="finished-cut-v3",
    )

    assert result.status == "completed"
    assert event_log == [
        "commit:value-L01",
        "commit:thesis-L02",
        "commit:punch-L04",
        "seal:value-L01",
        "seal:thesis-L02",
        "seal:punch-L04",
        "build-index",
        "publish-pointer",
        "activate",
    ]
    assert current_index.publish_calls == 1
    assert current_index.current.index_id == "index-v3-new"
    assert deployment.current.deployment_id == "finished-cut-v3"


@pytest.mark.parametrize(
    ("fail_cut", "expected_compensations"),
    [
        ("thesis-L02", ["value-L01"]),
        ("punch-L04", ["thesis-L02", "value-L01"]),
    ],
)
def test_commit_failure_compensates_prior_cuts_in_reverse_order(
    tmp_path: Path,
    fail_cut: str,
    expected_compensations: list[str],
) -> None:
    event_log: list[str] = []
    manager, timeline, lifecycle, candidates = _prepared_candidates(tmp_path, event_log)
    timeline.fail_commit_cut = fail_cut
    current_index = _CurrentIndex(event_log)
    deployment = _Deployment(event_log)
    journals = _JournalStore()
    cutover = GlobalCutover(
        resolve=manager,
        sealer=_RecordingSealer(lifecycle, event_log),
        current_index=current_index,
        deployment=deployment,
        journals=journals,
        fixed_cut_order=_CUT_ORDER,
    )

    with pytest.raises(GlobalCutoverError, match="injected commit failure"):
        cutover.run(
            "cutover-commit-failure",
            candidates=candidates,
            target_deployment_id="finished-cut-v3",
        )

    assert timeline.compensations == expected_compensations
    assert current_index.current == PointerSnapshot(index_id="old-current")
    assert current_index.publish_calls == 0
    assert deployment.current == DeploymentSnapshot(deployment_id="old-deployment")
    assert journals.journals["cutover-commit-failure"].status == "rolled_back"


@pytest.mark.parametrize("failure_stage", ["seal", "index", "pointer", "activate"])
def test_post_commit_failure_restores_pointer_deployment_and_all_timelines(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    event_log: list[str] = []
    manager, timeline, lifecycle, candidates = _prepared_candidates(tmp_path, event_log)
    sealer = _RecordingSealer(lifecycle, event_log)
    current_index = _CurrentIndex(event_log)
    deployment = _Deployment(event_log)
    if failure_stage == "seal":
        sealer.fail_cut = "thesis-L02"
    elif failure_stage == "index":
        current_index.fail_build = True
    elif failure_stage == "pointer":
        current_index.fail_publish = True
    else:
        deployment.fail_activate = True
    journals = _JournalStore()
    cutover = GlobalCutover(
        resolve=manager,
        sealer=sealer,
        current_index=current_index,
        deployment=deployment,
        journals=journals,
        fixed_cut_order=_CUT_ORDER,
    )

    with pytest.raises(GlobalCutoverError, match=f"injected {failure_stage}"):
        cutover.run(
            f"cutover-{failure_stage}-failure",
            candidates=candidates,
            target_deployment_id="finished-cut-v3",
        )

    assert timeline.compensations == ["punch-L04", "thesis-L02", "value-L01"]
    assert current_index.current == PointerSnapshot(index_id="old-current")
    assert deployment.current == DeploymentSnapshot(deployment_id="old-deployment")
    assert journals.journals[f"cutover-{failure_stage}-failure"].status == "rolled_back"
    assert sealer.discarded_release_ids


def test_restart_reconciles_committed_transaction_without_double_commit(
    tmp_path: Path,
) -> None:
    event_log: list[str] = []
    manager, _timeline, lifecycle, candidates = _prepared_candidates(tmp_path, event_log)
    current_index = _CurrentIndex(event_log)
    deployment = _Deployment(event_log)
    journals = _JournalStore()
    journals.crash_before_committed_count = 1

    def cutover() -> GlobalCutover:
        return GlobalCutover(
            resolve=manager,
            sealer=_RecordingSealer(lifecycle, event_log),
            current_index=current_index,
            deployment=deployment,
            journals=journals,
            fixed_cut_order=_CUT_ORDER,
        )

    with pytest.raises(KeyboardInterrupt, match="injected process crash"):
        cutover().run(
            "cutover-restart",
            candidates=candidates,
            target_deployment_id="finished-cut-v3",
        )
    assert event_log == ["commit:value-L01"]

    result = cutover().run(
        "cutover-restart",
        candidates=candidates,
        target_deployment_id="finished-cut-v3",
    )

    assert result.status == "completed"
    assert event_log.count("commit:value-L01") == 1
    assert event_log.count("commit:thesis-L02") == 1
    assert event_log.count("commit:punch-L04") == 1
    assert current_index.publish_calls == 1


def test_restart_resumes_reverse_rollback_without_double_compensation(
    tmp_path: Path,
) -> None:
    event_log: list[str] = []
    manager, timeline, lifecycle, candidates = _prepared_candidates(tmp_path, event_log)
    timeline.fail_commit_cut = "punch-L04"
    current_index = _CurrentIndex(event_log)
    deployment = _Deployment(event_log)
    journals = _JournalStore()
    journals.crash_before_compensated_count = 1

    def cutover() -> GlobalCutover:
        return GlobalCutover(
            resolve=manager,
            sealer=_RecordingSealer(lifecycle, event_log),
            current_index=current_index,
            deployment=deployment,
            journals=journals,
            fixed_cut_order=_CUT_ORDER,
        )

    with pytest.raises(KeyboardInterrupt, match="injected rollback crash"):
        cutover().run(
            "cutover-rollback-restart",
            candidates=candidates,
            target_deployment_id="finished-cut-v3",
        )
    assert timeline.compensations == ["thesis-L02"]

    with pytest.raises(GlobalCutoverError, match="completed rollback"):
        cutover().run(
            "cutover-rollback-restart",
            candidates=candidates,
            target_deployment_id="finished-cut-v3",
        )

    assert timeline.compensations == ["thesis-L02", "value-L01"]
    assert journals.journals["cutover-rollback-restart"].status == "rolled_back"
    assert current_index.current == PointerSnapshot(index_id="old-current")
    assert deployment.current == DeploymentSnapshot(deployment_id="old-deployment")


def test_candidate_transaction_identity_mismatch_performs_no_cutover_operation(
    tmp_path: Path,
) -> None:
    event_log: list[str] = []
    manager, timeline, lifecycle, candidates = _prepared_candidates(tmp_path, event_log)
    first, second, third = candidates
    forged_first = _mint_staged_release_candidate(
        candidate_id="candidate-forged-transaction",
        episode_id=first.episode_id,
        cut_id=first.cut_id,
        format=first.format,
        command_id=first.command_id,
        run_id=first.run_id,
        editorial_master_id=first.editorial_master_id,
        winner_id=first.winner_id,
        tight_cut_id=first.tight_cut_id,
        director_acceptance_id=first.director_acceptance_id,
        dp_acceptance_id=first.dp_acceptance_id,
        visual_acceptance_id=first.visual_acceptance_id,
        materialization_plan=first.materialization_plan,
        preview=first.preview,
        subtitle=first.subtitle,
        preview_ready_transaction_id=second.preview_ready_transaction_id,
        components=first.components,
    )
    current_index = _CurrentIndex(event_log)
    deployment = _Deployment(event_log)
    journals = _JournalStore()
    cutover = GlobalCutover(
        resolve=manager,
        sealer=_RecordingSealer(lifecycle, event_log),
        current_index=current_index,
        deployment=deployment,
        journals=journals,
        fixed_cut_order=_CUT_ORDER,
    )

    with pytest.raises(GlobalCutoverError, match="exact preview_ready"):
        cutover.run(
            "cutover-wrong-transaction",
            candidates=(forged_first, second, third),
            target_deployment_id="finished-cut-v3",
        )

    assert timeline.event_log == []
    assert current_index.publish_calls == 0
    assert deployment.current == DeploymentSnapshot(deployment_id="old-deployment")
    assert journals.journals == {}


def test_restart_detects_already_published_pointer_and_does_not_publish_twice(
    tmp_path: Path,
) -> None:
    event_log: list[str] = []
    manager, _timeline, lifecycle, candidates = _prepared_candidates(tmp_path, event_log)
    current_index = _CurrentIndex(event_log)
    deployment = _Deployment(event_log)
    journals = _JournalStore()
    journals.crash_before_pointer_published = True

    def cutover() -> GlobalCutover:
        return GlobalCutover(
            resolve=manager,
            sealer=_RecordingSealer(lifecycle, event_log),
            current_index=current_index,
            deployment=deployment,
            journals=journals,
            fixed_cut_order=_CUT_ORDER,
        )

    with pytest.raises(KeyboardInterrupt, match="injected pointer crash"):
        cutover().run(
            "cutover-pointer-restart",
            candidates=candidates,
            target_deployment_id="finished-cut-v3",
        )
    assert current_index.publish_calls == 1

    result = cutover().run(
        "cutover-pointer-restart",
        candidates=candidates,
        target_deployment_id="finished-cut-v3",
    )

    assert result.status == "completed"
    assert current_index.publish_calls == 1
    assert event_log.count("build-index") == 1
    assert event_log.count("seal:value-L01") == 1
