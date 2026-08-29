from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    MaterializationPlan,
    _mint_materialization_plan,
)
from agents.brook.script_video.finished_cut_production._resolve import (
    CommitReceipt,
    PreviewRender,
    ResolveTransactionError,
    ResolveTransactionManager,
    TimelineIdentity,
    TimelineSnapshot,
    TimelineWorkspace,
)


class _InMemoryTimelineAdapter:
    def __init__(self) -> None:
        self.timelines: dict[str, dict[str, object]] = {
            "canonical-uid": {
                "name": "Episode Master",
                "protected": "editorial-master-and-audio",
                "visuals": [],
            }
        }
        self.applied_uids: list[str] = []
        self.duplicate_calls = 0
        self.fail_apply = False
        self.rolled_back: list[str] = []
        self.preview_video_codec = "h264"
        self.commit_calls: list[str] = []
        self.compensation_calls: list[str] = []
        self.corrupt_compensation = False
        self.corrupt_rollback = False

    def preflight_plan(self, plan: MaterializationPlan) -> None:
        return None

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
        self.duplicate_calls += 1
        original = self.timelines[canonical.uid]
        backup = TimelineIdentity(
            name=f"__backup__{transaction_id}",
            uid=canonical.uid,
        )
        work = TimelineIdentity(
            name=canonical.name,
            uid=f"work-{transaction_id}",
        )
        original["name"] = backup.name
        self.timelines[work.uid] = deepcopy(original) | {"name": work.name}
        return TimelineWorkspace(canonical=canonical, work=work, backup=backup)

    def apply_plan(self, work: TimelineIdentity, plan: MaterializationPlan) -> None:
        if self.fail_apply:
            raise RuntimeError("injected apply failure")
        self.applied_uids.append(work.uid)
        visuals = self.timelines[work.uid]["visuals"]
        assert isinstance(visuals, list)
        visuals.append(plan.plan_id)

    def render_preview(self, work: TimelineIdentity, output: Path) -> PreviewRender:
        assert work.uid in self.timelines
        return PreviewRender(
            path=output,
            duration_sec=10.0,
            video_codec=self.preview_video_codec,
            audio_codec="aac",
        )

    def rollback(self, workspace: TimelineWorkspace) -> None:
        self.rolled_back.append(workspace.work.uid)
        self.timelines.pop(workspace.work.uid, None)
        self.timelines[workspace.backup.uid]["name"] = workspace.canonical.name
        if self.corrupt_rollback:
            visuals = self.timelines[workspace.backup.uid]["visuals"]
            assert isinstance(visuals, list)
            visuals.append("corrupted-after-rollback")

    def commit(
        self,
        workspace: TimelineWorkspace,
        *,
        transaction_id: str,
        cut_id: str,
        retain_backup: bool,
    ) -> CommitReceipt:
        self.commit_calls.append(transaction_id)
        return CommitReceipt(
            transaction_id=transaction_id,
            cut_id=cut_id,
            work_uid=workspace.work.uid,
            transaction_receipt_id=f"receipt-{transaction_id}",
            rollback_ref=f"timeline:{workspace.backup.uid}",
            backup_retained=retain_backup,
        )

    def compensate(
        self,
        workspace: TimelineWorkspace,
        receipt: CommitReceipt,
    ) -> None:
        assert receipt.backup_retained
        self.compensation_calls.append(receipt.transaction_id)
        self.timelines.pop(workspace.work.uid, None)
        self.timelines[workspace.backup.uid]["name"] = workspace.canonical.name
        if self.corrupt_compensation:
            visuals = self.timelines[workspace.backup.uid]["visuals"]
            assert isinstance(visuals, list)
            visuals.append("corrupted-after-restore")


def _plan(cut_id: str = "value-L01") -> MaterializationPlan:
    event = EventRecord(
        event_id="event-001",
        master_cue_ids=("cue-001",),
        text_hash=hashlib.sha256(b"current quote").hexdigest(),
        intent="support the current argument",
        asset_ref="asset:stock-001",
        visual_status="approved",
    )
    return _mint_materialization_plan(
        plan_id=f"plan-{cut_id}",
        run_id="run-001",
        command_id=f"approved-{cut_id}",
        episode_id="episode-001",
        cut_id=cut_id,
        format="long",
        director_acceptance_id="director-001",
        dp_acceptance_id="dp-001",
        visual_acceptance_id="visual-001",
        events=(event,),
    )


def test_prepare_applies_plan_only_to_duplicate_work_timeline(tmp_path: Path) -> None:
    adapter = _InMemoryTimelineAdapter()
    manager = ResolveTransactionManager(adapter)
    canonical = TimelineIdentity(name="Episode Master", uid="canonical-uid")
    original = deepcopy(adapter.timelines[canonical.uid])

    transaction = manager.prepare(
        _plan(),
        canonical=canonical,
        preview_path=tmp_path / "preview.mp4",
        subtitle_path=tmp_path / "review.srt",
    )

    assert transaction.status == "preview_ready"
    assert transaction.workspace is not None
    assert transaction.workspace.work.uid != canonical.uid
    assert adapter.applied_uids == [transaction.workspace.work.uid]
    assert adapter.timelines[canonical.uid]["protected"] == original["protected"]
    assert adapter.timelines[canonical.uid]["visuals"] == original["visuals"]
    assert adapter.timelines[transaction.workspace.work.uid]["visuals"] == [transaction.plan_id]


def test_apply_failure_rolls_back_duplicate_and_restores_canonical(tmp_path: Path) -> None:
    adapter = _InMemoryTimelineAdapter()
    adapter.fail_apply = True
    manager = ResolveTransactionManager(adapter)
    canonical = TimelineIdentity(name="Episode Master", uid="canonical-uid")

    with pytest.raises(ResolveTransactionError, match="injected apply failure"):
        manager.prepare(
            _plan(),
            canonical=canonical,
            preview_path=tmp_path / "preview.mp4",
            subtitle_path=tmp_path / "review.srt",
        )

    assert len(adapter.rolled_back) == 1
    assert adapter.rolled_back[0] not in adapter.timelines
    assert adapter.timelines[canonical.uid] == {
        "name": canonical.name,
        "protected": "editorial-master-and-audio",
        "visuals": [],
    }


def test_probe_failure_rolls_back_duplicate_work(tmp_path: Path) -> None:
    adapter = _InMemoryTimelineAdapter()
    adapter.preview_video_codec = "vp9"
    manager = ResolveTransactionManager(adapter)

    with pytest.raises(ResolveTransactionError, match="not H.264"):
        manager.prepare(
            _plan(),
            canonical=TimelineIdentity(name="Episode Master", uid="canonical-uid"),
            preview_path=tmp_path / "preview.mp4",
            subtitle_path=tmp_path / "review.srt",
        )

    assert len(adapter.rolled_back) == 1
    assert set(adapter.timelines) == {"canonical-uid"}


def test_commit_retains_backup_and_exposes_release_receipt(tmp_path: Path) -> None:
    adapter = _InMemoryTimelineAdapter()
    manager = ResolveTransactionManager(adapter)
    transaction = manager.prepare(
        _plan(),
        canonical=TimelineIdentity(name="Episode Master", uid="canonical-uid"),
        preview_path=tmp_path / "preview.mp4",
        subtitle_path=tmp_path / "review.srt",
    )

    receipt = manager.commit(
        transaction.transaction_id,
        expected_cut_id="value-L01",
    )

    assert receipt.backup_retained is True
    assert receipt.rollback_ref == "timeline:canonical-uid"
    assert adapter.commit_calls == [transaction.transaction_id]
    assert manager.inspect_transaction(transaction.transaction_id) == {
        "transaction_id": transaction.transaction_id,
        "cut_id": "value-L01",
        "status": "committed",
        "transaction_receipt_id": receipt.transaction_receipt_id,
        "rollback_ref": receipt.rollback_ref,
        "backup_retained": True,
    }


def test_committed_transaction_can_compensate_from_retained_backup(tmp_path: Path) -> None:
    adapter = _InMemoryTimelineAdapter()
    manager = ResolveTransactionManager(adapter)
    transaction = manager.prepare(
        _plan(),
        canonical=TimelineIdentity(name="Episode Master", uid="canonical-uid"),
        preview_path=tmp_path / "preview.mp4",
        subtitle_path=tmp_path / "review.srt",
    )
    manager.commit(transaction.transaction_id, expected_cut_id="value-L01")

    compensated = manager.compensating_rollback(
        transaction.transaction_id,
        expected_cut_id="value-L01",
    )

    assert compensated.status == "compensated"
    assert adapter.compensation_calls == [transaction.transaction_id]
    assert set(adapter.timelines) == {"canonical-uid"}
    assert adapter.timelines["canonical-uid"]["name"] == "Episode Master"


@pytest.mark.parametrize(
    ("transaction_id", "cut_id"),
    [
        ("wrong-transaction", "value-L01"),
        (None, "wrong-cut"),
    ],
)
def test_wrong_transaction_or_cut_identity_cannot_commit(
    tmp_path: Path,
    transaction_id: str | None,
    cut_id: str,
) -> None:
    adapter = _InMemoryTimelineAdapter()
    manager = ResolveTransactionManager(adapter)
    transaction = manager.prepare(
        _plan(),
        canonical=TimelineIdentity(name="Episode Master", uid="canonical-uid"),
        preview_path=tmp_path / "preview.mp4",
        subtitle_path=tmp_path / "review.srt",
    )

    with pytest.raises(ResolveTransactionError, match="identity"):
        manager.commit(
            transaction_id or transaction.transaction_id,
            expected_cut_id=cut_id,
        )

    assert adapter.commit_calls == []
    assert manager.inspect_transaction(transaction.transaction_id)["status"] == "preview_ready"


def test_prepare_retry_reuses_exact_preview_ready_transaction(tmp_path: Path) -> None:
    adapter = _InMemoryTimelineAdapter()
    manager = ResolveTransactionManager(adapter)
    canonical = TimelineIdentity(name="Episode Master", uid="canonical-uid")
    plan = _plan()
    arguments = {
        "canonical": canonical,
        "preview_path": tmp_path / "preview.mp4",
        "subtitle_path": tmp_path / "review.srt",
    }

    first = manager.prepare(plan, **arguments)
    second = manager.prepare(plan, **arguments)

    assert second == first
    assert adapter.duplicate_calls == 1
    assert adapter.applied_uids == [first.workspace.work.uid]


def test_compensation_requires_exact_original_timeline_snapshot(tmp_path: Path) -> None:
    adapter = _InMemoryTimelineAdapter()
    manager = ResolveTransactionManager(adapter)
    transaction = manager.prepare(
        _plan(),
        canonical=TimelineIdentity(name="Episode Master", uid="canonical-uid"),
        preview_path=tmp_path / "preview.mp4",
        subtitle_path=tmp_path / "review.srt",
    )
    manager.commit(transaction.transaction_id, expected_cut_id="value-L01")
    adapter.corrupt_compensation = True

    with pytest.raises(ResolveTransactionError, match="original Timeline snapshot"):
        manager.compensating_rollback(
            transaction.transaction_id,
            expected_cut_id="value-L01",
        )

    assert manager.inspect_transaction(transaction.transaction_id)["status"] == "rollback_failed"


def test_prepare_failure_requires_exact_original_timeline_rollback(tmp_path: Path) -> None:
    adapter = _InMemoryTimelineAdapter()
    adapter.preview_video_codec = "vp9"
    adapter.corrupt_rollback = True
    manager = ResolveTransactionManager(adapter)

    with pytest.raises(ResolveTransactionError, match="rollback failed"):
        manager.prepare(
            _plan(),
            canonical=TimelineIdentity(name="Episode Master", uid="canonical-uid"),
            preview_path=tmp_path / "preview.mp4",
            subtitle_path=tmp_path / "review.srt",
        )
