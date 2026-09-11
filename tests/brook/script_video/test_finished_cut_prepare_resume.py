"""交易做完之後再失敗，那個 run 以前就永遠結不了帳。

`_transaction_id` 把 canonical 的名字與 UID 算進去，而交易成功那一刻 canonical
就換人了——work 頂上原名、原本那條改名成 `__fcp_backup__…`。於是同一個 plan
重跑 `prepare` 必然算出另一個 id、`load` 必然落空，然後從**已經套用過**的
timeline 再 duplicate 一次，把衍生軌疊第二層。

20260721 punch-L03 就卡在這裡：Resolve 那一邊全部做完（V2/V3/V6 就位、
preview 1.28GB 已輸出），只差 `materialization.json` 沒寫成，因為 preview 探測
拿輸出檔的長度去比 ApprovedCut 的浮點秒數和——中間隔著 timeline 的量化誤差，
它管不到。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._materialization import (
    CanonicalTimelineInspection,
    _preview_matches_timeline,
)
from agents.brook.script_video.finished_cut_production._persistence import (
    AtomicResolveTransactionStore,
)
from agents.brook.script_video.finished_cut_production._resolve import (
    PreviewRender,
    ResolveTransaction,
    TimelineIdentity,
    TimelineSnapshot,
    TimelineWorkspace,
    _InMemoryResolveTransactionStore,
)
from agents.brook.script_video.finished_cut_production._resolve_davinci import (
    ResolveTimelineState,
)

FPS = 30.0
# 真實數字：timeline 16347 格、ApprovedCut source_ranges 的浮點和 544.865s、
# 輸出檔 544.917333s（16347 格影像 ＋ 一個 AAC frame 的容器尾巴）。
TIMELINE_FRAMES = 16347
CONTEXT_SEC = 544.8649999999993
PREVIEW_SEC = 544.917333


def _inspection(*, frames: int = TIMELINE_FRAMES, fps: float = FPS):
    return CanonicalTimelineInspection(
        episode_id="20260721 呂冠緯",
        cut_id="punch-L03",
        canonical=TimelineIdentity(name="長1", uid="c1a06201"),
        editorial_master_content_hash="c" * 64,
        editorial_master_media_sha256="d" * 64,
        timeline_frame_rate=fps,
        editorial_master_frame_rate=fps,
        editorial_master_duration_sec=4000.0,
        state=ResolveTimelineState(start_frame=0, end_frame=frames, items=()),
        baseline=TimelineSnapshot(protected_fingerprint="a" * 64, full_fingerprint="b" * 64),
    )


def _old_rule(measured_sec: float, expected_sec: float, fps: float) -> bool:
    """舊判準：輸出檔長度對著 context 的浮點秒數和比，容忍一格。"""
    return abs(round(measured_sec * fps) - round(expected_sec * fps)) <= 1


def test_the_real_punch_l03_preview_passes_only_against_the_timeline():
    assert not _old_rule(PREVIEW_SEC, CONTEXT_SEC, FPS)
    assert _preview_matches_timeline(PREVIEW_SEC, _inspection())


def test_an_exact_preview_passes():
    assert _preview_matches_timeline(TIMELINE_FRAMES / FPS, _inspection())


@pytest.mark.parametrize("drift_frames", [2, -2, 30])
def test_a_preview_that_really_drifted_is_rejected(drift_frames):
    """容忍度沒有被放寬——兩格照擋。"""
    assert not _preview_matches_timeline(
        (TIMELINE_FRAMES + drift_frames) / FPS, _inspection()
    )


@pytest.mark.parametrize(
    "duration,frames,fps",
    [
        (float("nan"), TIMELINE_FRAMES, FPS),
        (PREVIEW_SEC, TIMELINE_FRAMES, 0.0),
        (PREVIEW_SEC, 0, FPS),
    ],
)
def test_a_nonsense_measurement_never_passes(duration, frames, fps):
    assert not _preview_matches_timeline(duration, _inspection(frames=frames, fps=fps))


def _transaction(
    *,
    transaction_id: str = "resolve-f2856f0bb8c4e61a167a2e47",
    plan_id: str = "plan-33c3c6ddda344a80afc7f12c81c098b4",
    fingerprint: str = "08f9c69a",
    cut_id: str = "punch-L03",
) -> ResolveTransaction:
    canonical = TimelineIdentity(name="長1", uid="479c480b")
    work = TimelineIdentity(name="長1", uid="c1a06201")
    return ResolveTransaction(
        transaction_id=transaction_id,
        episode_id="20260721 呂冠緯",
        cut_id=cut_id,
        plan_id=plan_id,
        plan_fingerprint=fingerprint,
        status="preview_ready",
        canonical=canonical,
        workspace=TimelineWorkspace(
            canonical=canonical,
            work=work,
            backup=TimelineIdentity(name="__fcp_backup__punch-L03__x", uid="479c480b"),
        ),
        baseline=TimelineSnapshot(protected_fingerprint="a" * 64, full_fingerprint="b" * 64),
        preview=PreviewRender(
            path=Path("preview.mp4"),
            duration_sec=PREVIEW_SEC,
            video_codec="h264",
            audio_codec="aac",
        ),
        subtitle_path=Path("review.srt"),
    )


def _key(transaction: ResolveTransaction) -> dict[str, str]:
    return {
        "episode_id": transaction.episode_id,
        "cut_id": transaction.cut_id,
        "plan_id": transaction.plan_id,
        "plan_fingerprint": transaction.plan_fingerprint,
    }


@pytest.fixture(params=["memory", "atomic"])
def store(request, tmp_path):
    if request.param == "memory":
        return _InMemoryResolveTransactionStore()
    return AtomicResolveTransactionStore(tmp_path / "resolve-transactions")


def test_a_prepared_transaction_is_found_by_its_plan_not_its_id(store):
    transaction = _transaction()
    store.save(transaction)
    found = store.find_for_plan(**_key(transaction))
    assert found is not None
    assert found.transaction_id == transaction.transaction_id


def test_a_changed_plan_never_resumes_the_old_transaction(store):
    store.save(_transaction())
    assert store.find_for_plan(**{**_key(_transaction()), "plan_fingerprint": "different"}) is None
    assert store.find_for_plan(**{**_key(_transaction()), "plan_id": "plan-other"}) is None


def test_another_cut_is_not_mistaken_for_this_one(store):
    store.save(_transaction())
    assert store.find_for_plan(**{**_key(_transaction()), "cut_id": "story-L02"}) is None


def test_an_empty_store_returns_nothing(store):
    assert store.find_for_plan(**_key(_transaction())) is None


def test_two_transactions_for_one_plan_are_never_guessed_between(store):
    """同一個 plan 竟然有兩筆交易時不猜——回 None，讓上層照原本的路撞該撞的錯。"""
    store.save(_transaction())
    store.save(_transaction(transaction_id="resolve-000000000000000000000001"))
    assert store.find_for_plan(**_key(_transaction())) is None
