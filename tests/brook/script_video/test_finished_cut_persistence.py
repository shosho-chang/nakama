from pathlib import Path
from typing import cast

import pytest

from agents.brook.script_video.finished_cut_production._persistence import (
    AtomicResolveTransactionStore,
    PersistenceError,
)
from agents.brook.script_video.finished_cut_production._resolve import (
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

    def compensating_rollback(
        self,
        transaction_id: str,
        *,
        expected_cut_id: str,
    ) -> None:
        raise AssertionError("rollback is not expected in successful restart")


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
        # plan record 要記「鋪到了哪一條 timeline」，所以這個唯讀視圖把 work
        # 那一條交出來。以前發布線得自己掃交易目錄反查，而那條路要求交易
        # `status == "committed"`——全機器從來沒有一筆 commit 過。
        "timeline": {"name": "Episode Master", "uid": "work-uid"},
        "status": "preview_ready",
    }


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


