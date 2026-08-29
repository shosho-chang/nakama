from pathlib import Path
from typing import cast

import pytest

from agents.brook.script_video.finished_cut_production._cutover import (
    DeploymentSnapshot,
    GlobalCutover,
    GlobalCutoverJournal,
    PointerSnapshot,
    UnpublishedReleaseIndex,
)
from agents.brook.script_video.finished_cut_production._persistence import (
    AtomicCutoverJournalStore,
    AtomicResolveTransactionStore,
    PersistenceError,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    ReleaseArtifact,
    _mint_materialization_plan,
    _mint_projected_component,
    _mint_staged_release_candidate,
    _seal_finished_cut_release,
)
from agents.brook.script_video.finished_cut_production._resolve import (
    CommitReceipt,
    PreviewRender,
    ResolveTransaction,
    ResolveTransactionManager,
    TimelineAdapter,
    TimelineIdentity,
    TimelineSnapshot,
    TimelineWorkspace,
)


class _DurableTimelineAdapter:
    def __init__(self, baseline: TimelineSnapshot) -> None:
        self.baseline = baseline
        self.compensated: list[str] = []

    def preflight_plan(self, plan: object) -> None:
        raise AssertionError("preflight is not used by this test")

    def snapshot(self, timeline: TimelineIdentity) -> TimelineSnapshot:
        return self.baseline

    def duplicate(self, *args: object, **kwargs: object) -> TimelineWorkspace:
        raise AssertionError("duplicate is not used by this test")

    def apply_plan(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("apply is not used by this test")

    def render_preview(self, *args: object, **kwargs: object) -> PreviewRender:
        raise AssertionError("render is not used by this test")

    def rollback(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("rollback is not used by this test")

    def commit(
        self,
        workspace: TimelineWorkspace,
        *,
        transaction_id: str,
        cut_id: str,
        retain_backup: bool,
    ) -> CommitReceipt:
        return CommitReceipt(
            transaction_id=transaction_id,
            cut_id=cut_id,
            work_uid=workspace.work.uid,
            transaction_receipt_id=f"receipt-{transaction_id}",
            rollback_ref=f"resolve:{workspace.backup.uid}:{transaction_id}",
            backup_retained=retain_backup,
        )

    def compensate(self, workspace: TimelineWorkspace, receipt: CommitReceipt) -> None:
        self.compensated.append(receipt.transaction_id)


class _RestartResolve:
    def __init__(self) -> None:
        self.statuses = {
            "resolve-1": "committed",
            "resolve-2": "preview_ready",
            "resolve-3": "preview_ready",
        }
        self.cut_ids = {
            "resolve-1": "value-L01",
            "resolve-2": "thesis-L02",
            "resolve-3": "punch-L04",
        }
        self.commit_calls: list[str] = []

    def inspect_transaction(self, transaction_id: str) -> dict[str, object]:
        return {
            "transaction_id": transaction_id,
            "cut_id": self.cut_ids[transaction_id],
            "status": self.statuses[transaction_id],
        }

    def commit(self, transaction_id: str, *, expected_cut_id: str) -> CommitReceipt:
        assert self.cut_ids[transaction_id] == expected_cut_id
        self.commit_calls.append(transaction_id)
        self.statuses[transaction_id] = "committed"
        return CommitReceipt(
            transaction_id=transaction_id,
            cut_id=expected_cut_id,
            work_uid=f"work-{transaction_id}",
            transaction_receipt_id=f"receipt-{transaction_id}",
            rollback_ref=f"backup-{transaction_id}",
            backup_retained=True,
        )

    def compensating_rollback(
        self,
        transaction_id: str,
        *,
        expected_cut_id: str,
    ) -> None:
        raise AssertionError("rollback is not expected in successful restart")


class _RestartSealer:
    def seal_candidate(self, candidate: object):
        assert hasattr(candidate, "materialization_plan")
        return _seal_finished_cut_release(
            release_id=f"release-{candidate.cut_id}",
            episode_id=candidate.episode_id,
            cut_id=candidate.cut_id,
            format=candidate.format,
            command_id=candidate.command_id,
            run_id=candidate.run_id,
            editorial_master_id=candidate.editorial_master_id,
            winner_id=candidate.winner_id,
            tight_cut_id=candidate.tight_cut_id,
            director_acceptance_id=candidate.director_acceptance_id,
            dp_acceptance_id=candidate.dp_acceptance_id,
            visual_acceptance_id=candidate.visual_acceptance_id,
            materialization_plan_id=candidate.materialization_plan.plan_id,
            events=candidate.materialization_plan.events,
            preview=candidate.preview,
            subtitle=candidate.subtitle,
            transaction_receipt_id=f"receipt-{candidate.preview_ready_transaction_id}",
            rollback_ref=f"backup-{candidate.preview_ready_transaction_id}",
            components=candidate.components,
        )

    def discard_unpublished(self, releases: tuple[object, ...]) -> None:
        raise AssertionError("discard is not expected in successful restart")


class _RestartCurrentIndex:
    def __init__(self) -> None:
        self.current = PointerSnapshot(index_id="old-index")

    def snapshot_pointer(self) -> PointerSnapshot:
        return self.current

    def inspect_pointer(self) -> PointerSnapshot:
        return self.current

    def build_index(self, releases: tuple[object, ...]) -> UnpublishedReleaseIndex:
        return UnpublishedReleaseIndex(
            index_id="new-index",
            episode_id="episode-001",
            release_ids=tuple(release.release_id for release in releases),
        )

    def publish_pointer(self, index: UnpublishedReleaseIndex) -> None:
        self.current = PointerSnapshot(index_id=index.index_id)

    def restore_pointer(self, snapshot: PointerSnapshot) -> None:
        self.current = snapshot

    def discard_index(self, index: UnpublishedReleaseIndex) -> None:
        raise AssertionError("discard is not expected in successful restart")


class _RestartDeployment:
    def __init__(self) -> None:
        self.current = DeploymentSnapshot(deployment_id="old-deployment")

    def snapshot_deployment(self) -> DeploymentSnapshot:
        return self.current

    def inspect_deployment(self) -> DeploymentSnapshot:
        return self.current

    def activate(self, deployment_id: str) -> None:
        self.current = DeploymentSnapshot(deployment_id=deployment_id)

    def restore_deployment(self, snapshot: DeploymentSnapshot) -> None:
        self.current = snapshot


def _transaction(*, status: str = "preview_ready") -> ResolveTransaction:
    canonical = TimelineIdentity(name="Episode Master", uid="canonical-uid")
    return ResolveTransaction(
        transaction_id="resolve-001",
        episode_id="episode-001",
        cut_id="value-L01",
        plan_id="plan-001",
        plan_fingerprint="a" * 64,
        status=status,  # type: ignore[arg-type]
        canonical=canonical,
        workspace=TimelineWorkspace(
            canonical=canonical,
            work=TimelineIdentity(name="Episode Master", uid="work-uid"),
            backup=TimelineIdentity(name="__backup__resolve-001", uid="canonical-uid"),
        ),
        baseline=TimelineSnapshot(
            protected_fingerprint="protected-001",
            full_fingerprint="full-001",
        ),
        preview=PreviewRender(
            path=Path("previews/value-L01.mp4"),
            duration_sec=481.5,
            video_codec="h264",
            audio_codec="aac",
        ),
        subtitle_path=Path("subtitles/value-L01.srt"),
    )


def test_preview_ready_transaction_survives_store_restart(tmp_path: Path) -> None:
    first_process = AtomicResolveTransactionStore(tmp_path / "transactions")
    expected = _transaction()
    first_process.save(expected)

    restarted_process = AtomicResolveTransactionStore(tmp_path / "transactions")

    assert restarted_process.load(expected.transaction_id) == expected


def test_restarted_transaction_manager_inspects_durable_preview_ready_state(
    tmp_path: Path,
) -> None:
    store = AtomicResolveTransactionStore(tmp_path / "transactions")
    expected = _transaction()
    store.save(expected)

    restarted_manager = ResolveTransactionManager(
        cast(TimelineAdapter, object()),
        store=AtomicResolveTransactionStore(tmp_path / "transactions"),
    )

    assert restarted_manager.inspect_transaction(expected.transaction_id) == {
        "transaction_id": expected.transaction_id,
        "cut_id": "value-L01",
        "status": "preview_ready",
        "transaction_receipt_id": None,
        "rollback_ref": None,
        "backup_retained": False,
    }


def test_global_cutover_journal_survives_store_restart(tmp_path: Path) -> None:
    expected = GlobalCutoverJournal(
        cutover_id="cutover-001",
        episode_id="episode-001",
        fixed_cut_order=("value-L01", "thesis-L02", "punch-L04"),
        candidate_ids=("candidate-1", "candidate-2", "candidate-3"),
        transaction_ids=("resolve-1", "resolve-2", "resolve-3"),
        target_deployment_id="finished-cut-v3",
        old_pointer=PointerSnapshot(index_id="old-index"),
        old_deployment=DeploymentSnapshot(deployment_id="old-deployment"),
        status="committing",
        committed_transaction_ids=("resolve-1",),
    )
    first_process = AtomicCutoverJournalStore(tmp_path / "cutovers")
    first_process.save(expected)

    restarted_process = AtomicCutoverJournalStore(tmp_path / "cutovers")

    assert restarted_process.load(expected.cutover_id) == expected


def test_cutover_restart_preserves_sealed_release_and_unpublished_index(
    tmp_path: Path,
) -> None:
    component = _mint_projected_component(
        component_id="component-hero",
        event_id="event-001",
        semantic_kind="hero_title",
        implementation_kind="hero_title",
        lane="hero_title",
        display="自主權",
        t0=12.0,
        t1=15.0,
        asset_ref="asset-sha256:" + "b" * 64,
    )
    release = _seal_finished_cut_release(
        release_id="release-001",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        command_id="command-001",
        run_id="run-001",
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        director_acceptance_id="director-001",
        dp_acceptance_id="dp-001",
        visual_acceptance_id="visual-001",
        materialization_plan_id="plan-001",
        events=(
            EventRecord(
                event_id="event-001",
                master_cue_ids=("cue-001",),
                text_hash="c" * 64,
                intent="support current argument",
                asset_ref=component.asset_ref,
                visual_status="approved",
            ),
        ),
        preview=ReleaseArtifact(
            path="previews/value-L01.mp4",
            bytes=1234,
            sha256="d" * 64,
            duration_sec=481.5,
            probe=(("video_codec", "h264"), ("audio_codec", "aac")),
        ),
        subtitle=ReleaseArtifact(
            path="subtitles/value-L01.srt",
            bytes=321,
            sha256="e" * 64,
        ),
        transaction_receipt_id="receipt-001",
        rollback_ref="resolve:backup-001",
        components=(component,),
    )
    expected = GlobalCutoverJournal(
        cutover_id="cutover-with-release",
        episode_id="episode-001",
        fixed_cut_order=("value-L01",),
        candidate_ids=("candidate-1",),
        transaction_ids=("resolve-1",),
        target_deployment_id="finished-cut-v3",
        old_pointer=PointerSnapshot(index_id="old-index"),
        old_deployment=DeploymentSnapshot(deployment_id="old-deployment"),
        status="pointer_published",
        committed_transaction_ids=("resolve-1",),
        releases=(release,),
        unpublished_index=UnpublishedReleaseIndex(
            index_id="index-v3-new",
            episode_id="episode-001",
            release_ids=(release.release_id,),
        ),
        pointer_published=True,
    )
    store = AtomicCutoverJournalStore(tmp_path / "cutovers")

    store.save(expected)

    assert AtomicCutoverJournalStore(tmp_path / "cutovers").load(expected.cutover_id) == expected


def test_committed_and_compensated_transaction_states_survive_manager_restarts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "transactions"
    initial = _transaction()
    AtomicResolveTransactionStore(root).save(initial)
    adapter = _DurableTimelineAdapter(initial.baseline)

    committing_process = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(root),
    )
    committing_process.commit(initial.transaction_id, expected_cut_id=initial.cut_id)

    committed_process = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(root),
    )
    assert committed_process.inspect_transaction(initial.transaction_id)["status"] == "committed"
    committed_process.compensating_rollback(
        initial.transaction_id,
        expected_cut_id=initial.cut_id,
    )

    compensated_process = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(root),
    )
    assert compensated_process.inspect_transaction(initial.transaction_id)["status"] == (
        "compensated"
    )
    assert adapter.compensated == [initial.transaction_id]


@pytest.mark.parametrize("failure_kind", ["partial", "corrupt"])
def test_durable_transaction_store_fails_closed_on_incomplete_or_corrupt_record(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    root = tmp_path / "transactions"
    root.mkdir(parents=True)
    if failure_kind == "partial":
        (root / ".resolve-broken.json.staging").write_text("{", encoding="utf-8")
    else:
        (root / "resolve-broken.json").write_text("{", encoding="utf-8")

    with pytest.raises(PersistenceError, match="incomplete|unreadable"):
        AtomicResolveTransactionStore(root).load("resolve-broken")


def test_transaction_store_rejects_checksum_tampering_without_historical_fallback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "transactions"
    store = AtomicResolveTransactionStore(root)
    expected = _transaction()
    store.save(expected)
    path = root / f"{expected.transaction_id}.json"
    current = path.read_text(encoding="utf-8")
    path.write_text(current.replace("episode-001", "episode-999"), encoding="utf-8")
    (root / "resolve-historical.json").write_text(current, encoding="utf-8")

    with pytest.raises(PersistenceError, match="checksum differs"):
        store.load(expected.transaction_id)


def _restart_candidate(cut_id: str, transaction_id: str):
    plan = _mint_materialization_plan(
        plan_id=f"plan-{cut_id}",
        run_id="run-001",
        command_id=f"command-{cut_id}",
        episode_id="episode-001",
        cut_id=cut_id,
        format="long",
        director_acceptance_id="director-001",
        dp_acceptance_id="dp-001",
        visual_acceptance_id="visual-001",
        events=(),
    )
    return _mint_staged_release_candidate(
        candidate_id=f"candidate-{cut_id}",
        episode_id="episode-001",
        cut_id=cut_id,
        format="long",
        command_id=plan.command_id,
        run_id=plan.run_id,
        editorial_master_id="master-001",
        winner_id=f"winner-{cut_id}",
        tight_cut_id=f"tight-{cut_id}",
        director_acceptance_id=plan.director_acceptance_id,
        dp_acceptance_id=plan.dp_acceptance_id,
        visual_acceptance_id=plan.visual_acceptance_id,
        materialization_plan=plan,
        preview=ReleaseArtifact(
            path=f"previews/{cut_id}.mp4",
            bytes=100,
            sha256="a" * 64,
            duration_sec=480.0,
        ),
        subtitle=ReleaseArtifact(
            path=f"subtitles/{cut_id}.srt",
            bytes=20,
            sha256="b" * 64,
        ),
        preview_ready_transaction_id=transaction_id,
    )


def test_persistent_cutover_restart_reconciles_committed_transaction_without_double_commit(
    tmp_path: Path,
) -> None:
    cuts = ("value-L01", "thesis-L02", "punch-L04")
    candidates = tuple(
        _restart_candidate(cut_id, f"resolve-{index}") for index, cut_id in enumerate(cuts, start=1)
    )
    journal_store = AtomicCutoverJournalStore(tmp_path / "cutovers")
    journal_store.save(
        GlobalCutoverJournal(
            cutover_id="cutover-restart",
            episode_id="episode-001",
            fixed_cut_order=cuts,
            candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
            transaction_ids=tuple(
                candidate.preview_ready_transaction_id for candidate in candidates
            ),
            target_deployment_id="finished-cut-v3",
            old_pointer=PointerSnapshot(index_id="old-index"),
            old_deployment=DeploymentSnapshot(deployment_id="old-deployment"),
            status="committing",
        )
    )
    resolve = _RestartResolve()
    cutover = GlobalCutover(
        resolve=resolve,
        sealer=_RestartSealer(),
        current_index=_RestartCurrentIndex(),
        deployment=_RestartDeployment(),
        journals=AtomicCutoverJournalStore(tmp_path / "cutovers"),
        fixed_cut_order=cuts,
    )

    result = cutover.run(
        "cutover-restart",
        candidates=candidates,
        target_deployment_id="finished-cut-v3",
    )

    assert result.status == "completed"
    assert resolve.commit_calls == ["resolve-2", "resolve-3"]
    assert AtomicCutoverJournalStore(tmp_path / "cutovers").load("cutover-restart") == result
