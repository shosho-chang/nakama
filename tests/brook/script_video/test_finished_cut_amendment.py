"""一般化的 amendment 變換必須與釘死的歷史腳本**鑄出完全相同的 plan**。

ADR-066 對這項 follow-up 的驗收標準寫得很明確：**plan equality**，不是 preview byte
equality——「a 1 GiB preview render is not a reproducibility contract」。

20260805 林之晨 的 runtime 資料已經不在磁碟上，沒辦法直接重放那兩次歷史 amendment。
所以改成**等價性證明**：拿同一份合成 Release 同時餵給 `amendments/operations/` 底下
那兩支釘死的腳本與 `_amendment.py`，斷言兩邊的 plan 一模一樣。那兩支腳本是當初真的
產出 current Release 的東西，journal 用 SHA-256 釘著它們。
"""

from __future__ import annotations

import importlib.util
from dataclasses import asdict
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._amendment import (
    AmendmentError,
    ReplaceComponentAssets,
    SuppressComponents,
    amendment_plan,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    ReleaseArtifact,
    _rehydrate_finished_cut_release,
    _rehydrate_release_projected_component,
)

OPERATIONS = (
    Path(__file__).resolve().parents[3]
    / "agents/brook/script_video/finished_cut_production/amendments/operations"
)

# 兩支歷史腳本各自的 plan 身分（identity 的 `operation` 字串 ＋ plan_id 前綴）。
# 一般化之後新的 amendment 用 canonical 值；重放歷史時把這兩組釘回去。
SUPPRESS_LABEL = "suppress_exact_release_components"
SUPPRESS_PREFIX = "plan-suppression-"
REPLACE_LABEL = "restore_fullscreen_transition_paper_hand_v4"
REPLACE_PREFIX = "plan-transition-v4-"

SUPPRESS_TARGETS = (
    "evt_k_shape_prices_inflation",
    "evt_agency_autonomy_title",
    "evt_future_values_deliberation",
    "evt_human_agency_definition",
    "evt_generalist_closing_title",
)
CHAPTER_TARGETS = (
    "evt_chapter_abundance",
    "evt_chapter_captivity",
    "evt_chapter_deliberation",
    "evt_chapter_k_shaped_future",
    "evt_chapter_self_actualization",
)


def _load(name: str):
    """把釘死的腳本當模組載進來。它們只有 module-level 常數，沒有 import 期副作用。"""
    spec = importlib.util.spec_from_file_location(f"_pinned_{name}", OPERATIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _component(event_id: str, index: int, triple: tuple[str, str, str], asset: str | None):
    # 走 rehydrate 不走 mint：`supporting_title` 已於 2026-08-29 退役，現役詞彙鑄不出來，
    # 但**歷史 Release receipt 本來就含退役值**（見 `_projection.py` docstring，
    # `_release.py` 的載入器也明文允許）。這份 fixture 代表的正是那樣一份已封存的收據。
    semantic, implementation, lane = triple
    return _rehydrate_release_projected_component(
        component_id=f"cmp_{event_id}",
        event_id=event_id,
        semantic_kind=semantic,
        implementation_kind=implementation,
        lane=lane,
        display=f"display {index}",
        t0=float(index * 10),
        t1=float(index * 10 + 3),
        asset_ref=asset,
    )


def _event(event_id: str, index: int, triple: tuple[str, str, str | None], asset: str | None):
    semantic, implementation, lane = triple
    return EventRecord(
        event_id=event_id,
        master_cue_ids=(f"cue-{index}",),
        text_hash=f"{index:064x}",
        intent=f"intent {index}",
        asset_ref=asset,
        text=f"text {index}",
        t0=float(index * 10),
        t1=float(index * 10 + 3),
        section_id=f"section-{index}",
        display=f"display {index}",
        semantic_kind=semantic,
        implementation_kind=implementation,
        lane=lane,
        intentional_aroll=semantic == "intentional_aroll",
    )


def _artifact(name: str) -> ReleaseArtifact:
    return ReleaseArtifact(path=f"{name}.mp4", bytes=1024, sha256="a" * 64, duration_sec=12.5)


# 現役投影詞彙是**三元組**，不是同一個字串重複三次（見 `_projection.py`）。
# supporting_title 已退役，只存在於歷史收據
SUPPORTING = ("supporting_title", "supporting_title", "supporting_title")
CHAPTER = ("chapter", "fullscreen_transition", "fullscreen_transition")
BROLL = ("b_roll", "stock_video", "b_roll")
AROLL = ("intentional_aroll", "intentional_aroll", None)


def _specs(*, suppressed: bool):
    """L04 的組成。`suppressed=False` 是 base（20 個 component，5 個 supporting_title）；
    `True` 是第一次 amendment 之後（15 個 component，那 5 個已轉成 intentional A-roll）。

    歷史上兩支腳本吃的是**不同**的 Release：suppress 吃 base，replace 吃 suppress 的結果
    （journal 記著 release-8ca1a6eb → release-22a0424 → release-af65a1d7）。
    """
    rows = []
    for event_id in SUPPRESS_TARGETS:
        rows.append((event_id, AROLL if suppressed else SUPPORTING, None, not suppressed))
    for event_id in CHAPTER_TARGETS:
        rows.append((event_id, CHAPTER, "asset://tr/v3", True))
    for i in range(10):
        rows.append((f"evt_broll_{i:02d}", BROLL, f"asset://broll/{i:02d}", True))
    return rows


def _release(
    *,
    suppressed: bool = False,
    release_id: str = "release-8ca1a6eb97ad9facf7702155",
    plan_id: str = "plan-1410680187a940c1b3a1503fc44d3",
):
    rows = _specs(suppressed=suppressed)
    components = tuple(
        _component(event_id, i, triple, asset)
        for i, (event_id, triple, asset, has_component) in enumerate(rows)
        if has_component
    )
    events = tuple(
        _event(event_id, i, triple, asset) for i, (event_id, triple, asset, _has) in enumerate(rows)
    )
    return _rehydrate_finished_cut_release(
        release_id=release_id,
        episode_id="20260805 林之晨",
        cut_id="long3-fresh-20260828-r4",
        format="long",
        command_id="command-fixture",
        run_id="run-fixture",
        editorial_master_id="em-fixture",
        winner_id="winner-fixture",
        tight_cut_id="tight-fixture",
        director_acceptance_id="acc-director",
        dp_acceptance_id="acc-dp",
        visual_acceptance_id="acc-visual",
        materialization_plan_id=plan_id,
        events=events,
        preview=_artifact("preview"),
        subtitle=_artifact("subtitle"),
        transaction_receipt_id="txn-fixture",
        rollback_ref="rollback-fixture",
        components=components,
    )


def _release_after_suppression():
    return _release(
        suppressed=True,
        release_id="release-22a0424136727bb41527ff15",
        plan_id="plan-suppression-e8080c9bbb1bcbbd4",
    )


# ── 等價性：一般化 vs 釘死的歷史腳本 ────────────────────────────────────────


def test_suppression_matches_the_pinned_operation_exactly():
    release = _release()
    pinned = _load("suppress_l04_supporting_titles")._suppression_plan(release)
    general = amendment_plan(
        release,
        SuppressComponents(SUPPRESS_TARGETS),
        label=SUPPRESS_LABEL,
        plan_prefix=SUPPRESS_PREFIX,
    )
    assert general.plan_id == pinned.plan_id
    assert asdict(general) == asdict(pinned)


def test_asset_replacement_matches_the_pinned_operation_exactly():
    release = _release_after_suppression()
    replacements = {event_id: "asset://transition/v4" for event_id in CHAPTER_TARGETS}
    module = _load("restore_l04_fullscreen_transitions")
    pinned = module._replacement_plan(release, replacements)
    general = amendment_plan(
        release,
        ReplaceComponentAssets(replacements),
        label=REPLACE_LABEL,
        plan_prefix=REPLACE_PREFIX,
    )
    assert general.plan_id == pinned.plan_id
    assert asdict(general) == asdict(pinned)


# ── 語意權威一個位元都不動 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "operation",
    [
        SuppressComponents(SUPPRESS_TARGETS),
        ReplaceComponentAssets({e: "asset://transition/v4" for e in CHAPTER_TARGETS}),
    ],
)
def test_amendment_never_mints_semantic_authority(operation):
    """amendment 是機械式的：整條 AcceptedStage 鏈原封不動地沿用。"""
    release = (
        _release_after_suppression()
        if isinstance(operation, ReplaceComponentAssets)
        else _release()
    )
    plan = amendment_plan(release, operation)
    assert plan.director_acceptance_id == release.director_acceptance_id
    assert plan.dp_acceptance_id == release.dp_acceptance_id
    assert plan.visual_acceptance_id == release.visual_acceptance_id
    assert plan.run_id == release.run_id
    assert plan.command_id == release.command_id
    assert plan.plan_id != release.materialization_plan_id


def test_suppression_converts_targets_to_intentional_aroll_and_drops_components():
    release = _release()
    plan = amendment_plan(release, SuppressComponents(SUPPRESS_TARGETS))
    assert len(plan.components) == 15
    assert not [c for c in plan.components if c.event_id in set(SUPPRESS_TARGETS)]
    suppressed = [e for e in plan.events if e.event_id in set(SUPPRESS_TARGETS)]
    assert len(suppressed) == 5
    for event in suppressed:
        assert event.intentional_aroll is True
        assert event.lane is None
        assert event.asset_ref is None
        assert event.visual_placement is None
    # 沒被指名的 event 一個字都不能動。
    untouched = {e.event_id: e for e in plan.events}
    for original in release.events:
        if original.event_id not in set(SUPPRESS_TARGETS):
            assert untouched[original.event_id] == original


def test_replacement_changes_only_the_asset_ref():
    release = _release_after_suppression()
    replacements = {CHAPTER_TARGETS[0]: "asset://transition/v4"}
    plan = amendment_plan(release, ReplaceComponentAssets(replacements))
    assert plan.events == release.events  # 換素材不動 events
    assert len(plan.components) == len(release.components)
    by_event = {c.event_id: c for c in plan.components}
    changed = by_event[CHAPTER_TARGETS[0]]
    original = next(c for c in release.components if c.event_id == CHAPTER_TARGETS[0])
    assert changed.asset_ref == "asset://transition/v4"
    assert (changed.t0, changed.t1, changed.lane, changed.display) == (
        original.t0,
        original.t1,
        original.lane,
        original.display,
    )
    for component in release.components:
        if component.event_id not in replacements:
            assert by_event[component.event_id] == component


# ── 決定性與 fail-loud ──────────────────────────────────────────────────────


def test_same_operation_on_same_release_is_deterministic():
    release = _release()
    op = SuppressComponents(SUPPRESS_TARGETS)
    assert amendment_plan(release, op).plan_id == amendment_plan(release, op).plan_id


def test_different_assets_give_different_plan_ids():
    release = _release_after_suppression()
    a = amendment_plan(release, ReplaceComponentAssets({CHAPTER_TARGETS[0]: "asset://tr/v4"}))
    b = amendment_plan(release, ReplaceComponentAssets({CHAPTER_TARGETS[0]: "asset://tr/v5"}))
    assert a.plan_id != b.plan_id


def test_partially_suppressing_a_retired_lane_is_refused():
    """只收掉 5 個 supporting_title 裡的 3 個，剩下 2 個會留在 plan 裡——而
    supporting_title 已經退役，`_mint_materialization_plan` 本來就不該放行。
    這不是 amendment 的 bug，是退役詞彙的正確結果：要收就整組收。"""
    release = _release()
    with pytest.raises(ValueError, match="retired or unsupported"):
        amendment_plan(release, SuppressComponents(SUPPRESS_TARGETS[:3]))


def test_unknown_event_id_names_the_offender():
    release = _release()
    with pytest.raises(AmendmentError) as excinfo:
        amendment_plan(release, SuppressComponents(("evt_does_not_exist",)))
    message = str(excinfo.value)
    assert "evt_does_not_exist" in message
    assert release.release_id in message


def test_empty_operation_is_refused():
    with pytest.raises(AmendmentError, match="至少要指定一個"):
        amendment_plan(_release(), SuppressComponents(()))


def test_duplicate_event_id_is_refused():
    with pytest.raises(AmendmentError, match="重複"):
        amendment_plan(_release(), SuppressComponents((SUPPRESS_TARGETS[0],) * 2))
