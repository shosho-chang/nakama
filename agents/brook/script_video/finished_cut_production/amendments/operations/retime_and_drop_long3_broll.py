"""Exact episode-local L04 B-roll retime + duplicate drop.

修修 2026-08-29 在 finished review 給的兩筆意見：

1. ``evt_k_investment_broll`` 出得太早——要等來賓真的講到投資才顯示，移到 1:49
2. ``evt_adult_game_challenge_broll`` 和 3:05 那支是**同一個 asset digest**，重複

第 2 點只能拿掉，不能換：候選池 8 支裡有 7 支已經用在本片其他位置，唯一沒用過
的是「學生用筆電學習」，配不上「任務式體驗與成就感」。修修 2026-08-29 明確裁決
拿掉，並接受由此產生的 b-roll 空窗（約 130 秒，超過 policy 的 75 秒上限）。

同 suppress_l04_supporting_titles：重用已封存的 Release 語意權威與 Resolve
duplicate/apply transaction，不派任何語意 worker、不重建 asset。``prepare`` 停在
preview_ready 的 Candidate；``publish`` commit、seal，最後才換 v3 current pointer。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RESOLVE_MODULES = Path(
    r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"
)
if str(RESOLVE_MODULES) not in sys.path:
    sys.path.insert(0, str(RESOLVE_MODULES))

from agents.brook.script_video.finished_cut_production._active_store import (  # noqa: E402
    ActiveAssetStore,
)
from agents.brook.script_video.finished_cut_production._composition import (  # noqa: E402
    ProductionPaths,
    ProductionResolveConfiguration,
    ProductionResolvePorts,
    _build_resolve_materialization_composition,
)
from agents.brook.script_video.finished_cut_production._records import (  # noqa: E402
    FinishedCutRelease,
    MaterializationPlan,
    StagedReleaseCandidate,
    _mint_materialization_plan,
    _mint_projected_component,
)
from agents.brook.script_video.finished_cut_production._release import (  # noqa: E402
    FinishedCutReleaseLifecycle,
)
from agents.brook.script_video.finished_cut_production._resolve import (  # noqa: E402
    ResolveTransaction,
    ResolveTransactionManager,
)
from agents.brook.script_video.finished_cut_production._resolve_davinci import (  # noqa: E402
    ResolveCutBinding,
    ResolveProjectBinding,
)
from agents.brook.script_video.finished_cut_production._resolve_fusion import (  # noqa: E402
    ResolveDatabaseIdentity,
    ResolveProjectLocator,
    current_timeline_identities,
)
from agents.brook.script_video.finished_cut_production._timeline_apply import (  # noqa: E402
    project_timeline_application,
)

EPISODE_ID = "20260805 林之晨"
CUT_ID = "long3-fresh-20260828-r4"
CURRENT_RELEASE_ID = "release-af65a1d7a2ac611eb78be493"
EXPECTED_PREVIEW_SHA256 = "728520b58acb6d2bba27480d13f6b9c81c982a2e757908de14c8135422abb4aa"
EDITORIAL_MASTER_CONTENT_HASH = (
    "8e7c13c2c55bc0df0c05241cfd91a9bf5c6b484b58058dae42d2bfaa7576805b"
)
PROJECT_UID = (
    "resolve-project:da7c1f4698b72f57a400f9a5196d0b4a136ea498236f3296b13c4fe272795231"
)
CANONICAL_NAME = "long3-fresh-20260828-r4-base"

RETIME_EVENT_ID = "evt_k_investment_broll"
RETIME_T0 = 109.0
DROP_EVENT_ID = "evt_adult_game_challenge_broll"
DUPLICATE_OF_EVENT_ID = "evt_virtual_experience_broll"

LANE_TRACKS = {
    "b_roll": 2,
    "hero_title": 3,
    "identity_card": 4,
    "supporting_title": 5,
    "fullscreen_transition": 6,
    "visual_effect": 7,
}

EPISODES_ROOT = Path(r"G:\Footages")
EPISODE_ROOT = EPISODES_ROOT / EPISODE_ID
RUNTIME_ROOT = EPISODE_ROOT / "highlights" / "finished-cut-production-v1" / "runtime"
CURRENT_POINTER = EPISODE_ROOT / "highlights" / "review" / "finished_review_manifest_current.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _locator() -> ResolveProjectLocator:
    return ResolveProjectLocator(
        episode_id=EPISODE_ID,
        database=ResolveDatabaseIdentity(db_type="Disk", db_name="Local Database"),
        folder="",
        project_name=EPISODE_ID,
    )


def _canonical():
    """Resolve the canonical Timeline live; a committed transaction changes its uid."""
    locator = _locator()
    matches = [row for row in current_timeline_identities(locator) if row.name == CANONICAL_NAME]
    if len(matches) != 1:
        raise RuntimeError(f"Resolve 專案裡找不到唯一的 timeline「{CANONICAL_NAME}」")
    return matches[0]


def _compose() -> tuple[FinishedCutReleaseLifecycle, ResolveTransactionManager]:
    paths = ProductionPaths(runtime_root=RUNTIME_ROOT, episodes_root=EPISODES_ROOT)
    assets = ActiveAssetStore.open(
        EPISODE_ROOT / "highlights" / "assets-v2",
        episode_id=EPISODE_ID,
    )
    configuration = ProductionResolveConfiguration(
        locator=_locator(),
        binding=ResolveProjectBinding(
            episode_id=EPISODE_ID,
            project_name=EPISODE_ID,
            project_uid=PROJECT_UID,
            cuts=(ResolveCutBinding(cut_id=CUT_ID, canonical=_canonical()),),
        ),
        editorial_master_content_hash=EDITORIAL_MASTER_CONTENT_HASH,
        staging_root=EPISODE_ROOT / "highlights" / "staging" / "finished-cut",
    )
    _, lifecycle, transactions = _build_resolve_materialization_composition(
        paths=paths,
        episode_id=EPISODE_ID,
        assets=assets,
        run_store_root=RUNTIME_ROOT / "episodes" / EPISODE_ID / "runs",
        configuration=configuration,
        ports=ProductionResolvePorts(),
    )
    return lifecycle, transactions


def _exact_current(
    lifecycle: FinishedCutReleaseLifecycle,
) -> tuple[tuple[FinishedCutRelease, ...], FinishedCutRelease, bytes]:
    pointer_bytes = CURRENT_POINTER.read_bytes()
    releases = lifecycle.inspect_current(EPISODE_ID)
    matches = [release for release in releases if release.release_id == CURRENT_RELEASE_ID]
    if len(matches) != 1:
        raise RuntimeError("exact current L04 Release differs")
    release = matches[0]
    if release.cut_id != CUT_ID or release.format != "long":
        raise RuntimeError("exact current L04 cut identity differs")
    preview_path = (EPISODE_ROOT / release.preview.path).resolve(strict=True)
    if (
        release.preview.sha256 != EXPECTED_PREVIEW_SHA256
        or _sha256_file(preview_path) != EXPECTED_PREVIEW_SHA256
    ):
        raise RuntimeError("exact current L04 preview bytes differ")
    return releases, release, pointer_bytes


def _amendment_plan(release: FinishedCutRelease) -> MaterializationPlan:
    if len(release.components) != 15 or len(release.events) != 20:
        raise RuntimeError("base cardinality differs")

    dropped = [c for c in release.components if c.event_id == DROP_EVENT_ID]
    retimed = [c for c in release.components if c.event_id == RETIME_EVENT_ID]
    duplicate_of = [c for c in release.components if c.event_id == DUPLICATE_OF_EVENT_ID]
    if len(dropped) != 1 or len(retimed) != 1 or len(duplicate_of) != 1:
        raise RuntimeError("target component set differs")
    # 只在它確實是重複時才拿掉——若上游已經換過素材，這支就不該再動手。
    if dropped[0].asset_ref != duplicate_of[0].asset_ref:
        raise RuntimeError("drop target is no longer a duplicate; refusing to remove it")

    span = retimed[0].t1 - retimed[0].t0
    retime_event = next(e for e in release.events if e.event_id == RETIME_EVENT_ID)
    new_t1 = RETIME_T0 + span
    if not (retime_event.t0 <= RETIME_T0 and new_t1 <= retime_event.t1):
        raise RuntimeError("retimed placement leaves its Semantic Evidence Range")

    # ProjectedComponent 只能由核心鑄造（dataclasses.replace 會被權限守衛擋下），
    # 所以退休的那支用過濾移除，改時間的那支重新鑄造。
    components = tuple(
        _mint_projected_component(
            component_id=component.component_id,
            event_id=component.event_id,
            semantic_kind=component.semantic_kind,
            implementation_kind=component.implementation_kind,
            lane=component.lane,
            display=component.display,
            t0=RETIME_T0,
            t1=new_t1,
            asset_ref=component.asset_ref,
        )
        if component.event_id == RETIME_EVENT_ID
        else component
        for component in release.components
        if component.event_id != DROP_EVENT_ID
    )
    if len(components) != 14:
        raise RuntimeError("retained component cardinality differs")

    events = tuple(
        replace(
            event,
            semantic_kind="intentional_aroll",
            implementation_kind="intentional_aroll",
            lane=None,
            asset_ref=None,
            intentional_aroll=True,
            visual_placement=None,
        )
        if event.event_id == DROP_EVENT_ID
        else event
        for event in release.events
    )
    if len(events) != 20 or sum(event.intentional_aroll for event in events) != 6:
        raise RuntimeError("intentional A-roll event conversion differs")

    identity = {
        "operation": "retime_and_drop_exact_release_components",
        "base_release_id": release.release_id,
        "base_plan_id": release.materialization_plan_id,
        "retimed": {"event_id": RETIME_EVENT_ID, "t0": RETIME_T0, "t1": new_t1},
        "dropped_component_ids": sorted(component.component_id for component in dropped),
        "retained_components": [asdict(component) for component in components],
    }
    plan_id = f"plan-retime-drop-{hashlib.sha256(_canonical_json(identity)).hexdigest()[:22]}"
    return _mint_materialization_plan(
        plan_id=plan_id,
        run_id=release.run_id,
        command_id=release.command_id,
        episode_id=release.episode_id,
        cut_id=release.cut_id,
        format=release.format,
        director_acceptance_id=release.director_acceptance_id,
        dp_acceptance_id=release.dp_acceptance_id,
        visual_acceptance_id=release.visual_acceptance_id,
        events=events,
        components=components,
    )


def _operation_paths(plan: MaterializationPlan, release: FinishedCutRelease) -> tuple[Path, Path]:
    identity = hashlib.sha256(
        _canonical_json(
            {
                "plan_id": plan.plan_id,
                "base_release_id": release.release_id,
                "base_preview_sha256": release.preview.sha256,
            }
        )
    ).hexdigest()[:24]
    workspace = EPISODE_ROOT / "highlights" / "staging" / "finished-cut" / identity
    return workspace / "preview.mp4", (EPISODE_ROOT / release.subtitle.path).resolve(strict=True)


def _candidate(
    lifecycle: FinishedCutReleaseLifecycle,
    transaction: ResolveTransaction,
    plan: MaterializationPlan,
    release: FinishedCutRelease,
    preview_path: Path,
    subtitle_path: Path,
) -> StagedReleaseCandidate:
    return lifecycle.stage_candidate(
        plan,
        editorial_master_id=release.editorial_master_id,
        winner_id=release.winner_id,
        tight_cut_id=release.tight_cut_id,
        transaction_id=transaction.transaction_id,
        preview_path=preview_path,
        subtitle_path=subtitle_path,
    )


def _verify_work_timeline(
    transactions: ResolveTransactionManager,
    transaction: ResolveTransaction,
    plan: MaterializationPlan,
) -> None:
    adapter = transactions._adapter  # exact episode-local internal operation
    application = project_timeline_application(plan, adapter._assets)
    state = adapter._facade.timeline_state(transaction.workspace.work.uid)
    if state.frame_rate is None:
        raise RuntimeError("Resolve work Timeline frame rate is unavailable")
    expected = sorted(
        (
            LANE_TRACKS[placement.lane],
            round(placement.t0 * state.frame_rate),
            round(placement.t1 * state.frame_rate),
            placement.source_path.stem.lower(),
        )
        for placement in application.placements
    )
    actual = sorted(
        (
            item.track_index,
            item.start_frame - state.start_frame,
            item.end_frame - state.start_frame,
            item.media_digest.lower(),
        )
        for item in state.items
        if item.track_type == "video" and item.track_index in range(2, 8)
    )
    if actual != expected:
        raise RuntimeError("Resolve work Timeline retained component identity or timing differs")
    if len(actual) != 14:
        raise RuntimeError("Resolve work Timeline overlay cardinality differs")


def _rollback_preview_ready(
    transactions: ResolveTransactionManager,
    transaction: ResolveTransaction,
) -> None:
    transactions._adapter.rollback(transaction.workspace)
    transactions._store.save(replace(transaction, status="rolled_back"))


def _summary(
    *,
    action: str,
    plan: MaterializationPlan,
    release: FinishedCutRelease,
    transaction: ResolveTransaction | None = None,
    candidate: StagedReleaseCandidate | None = None,
    new_release: FinishedCutRelease | None = None,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "action": action,
        "episode_id": EPISODE_ID,
        "cut_id": CUT_ID,
        "base_release_id": release.release_id,
        "base_preview_sha256": release.preview.sha256,
        "plan_id": plan.plan_id,
        "components": len(plan.components),
    }
    if transaction is not None:
        summary["transaction_id"] = transaction.transaction_id
        summary["transaction_status"] = transaction.status
    if candidate is not None:
        summary["candidate_preview_sha256"] = candidate.preview.sha256
        summary["candidate_duration_sec"] = candidate.preview.duration_sec
    if new_release is not None:
        summary["new_release_id"] = new_release.release_id
    return summary


def audit() -> dict[str, object]:
    lifecycle, transactions = _compose()
    _, release, _ = _exact_current(lifecycle)
    plan = _amendment_plan(release)
    transactions._adapter.preflight_plan(plan)
    transactions._adapter.snapshot(_canonical())
    return _summary(action="audit", plan=plan, release=release)


def prepare() -> dict[str, object]:
    lifecycle, transactions = _compose()
    _, release, pointer_bytes = _exact_current(lifecycle)
    plan = _amendment_plan(release)
    preview_path, subtitle_path = _operation_paths(plan, release)
    transaction: ResolveTransaction | None = None
    try:
        transaction = transactions.prepare(
            plan,
            canonical=_canonical(),
            preview_path=preview_path,
            subtitle_path=subtitle_path,
        )
        if transaction.status != "preview_ready":
            raise RuntimeError("amendment transaction is not preview_ready")
        candidate = _candidate(
            lifecycle, transaction, plan, release, preview_path, subtitle_path
        )
        _verify_work_timeline(transactions, transaction, plan)
        if CURRENT_POINTER.read_bytes() != pointer_bytes:
            raise RuntimeError("current pointer changed during preview preparation")
        if candidate.preview.sha256 == release.preview.sha256:
            raise RuntimeError("amended preview bytes unexpectedly equal the base preview")
        return _summary(
            action="prepare",
            plan=plan,
            release=release,
            transaction=transaction,
            candidate=candidate,
        )
    except BaseException:
        if transaction is not None and transaction.status == "preview_ready":
            _rollback_preview_ready(transactions, transaction)
        raise


def publish() -> dict[str, object]:
    lifecycle, transactions = _compose()
    old_releases, release, pointer_bytes = _exact_current(lifecycle)
    plan = _amendment_plan(release)
    preview_path, subtitle_path = _operation_paths(plan, release)
    transaction = transactions.prepare(
        plan,
        canonical=_canonical(),
        preview_path=preview_path,
        subtitle_path=subtitle_path,
    )
    if transaction.status != "preview_ready":
        raise RuntimeError("amendment transaction is not ready to publish")
    candidate = _candidate(lifecycle, transaction, plan, release, preview_path, subtitle_path)
    committed = False
    pointer_published = False
    try:
        _verify_work_timeline(transactions, transaction, plan)
        if CURRENT_POINTER.read_bytes() != pointer_bytes:
            raise RuntimeError("current pointer changed before amendment commit")
        transactions.commit(transaction.transaction_id, expected_cut_id=CUT_ID)
        committed = True
        new_release = lifecycle.seal_candidate(candidate)
        if CURRENT_POINTER.read_bytes() != pointer_bytes:
            raise RuntimeError("current pointer changed before pointer-last publication")
        updated = tuple(
            new_release if item.release_id == CURRENT_RELEASE_ID else item for item in old_releases
        )
        lifecycle.publish_current(updated)
        pointer_published = True
        current = lifecycle.inspect_current(EPISODE_ID)
        exact = [item for item in current if item.cut_id == CUT_ID and item.format == "long"]
        if len(exact) != 1 or exact[0] != new_release:
            raise RuntimeError("pointer-last current Release verification failed")
        return _summary(
            action="publish",
            plan=plan,
            release=release,
            transaction=transactions._store.load(transaction.transaction_id),
            candidate=candidate,
            new_release=new_release,
        )
    except BaseException:
        if pointer_published:
            lifecycle.publish_current(old_releases)
        if committed:
            transactions.compensating_rollback(
                transaction.transaction_id, expected_cut_id=CUT_ID
            )
        else:
            _rollback_preview_ready(transactions, transaction)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("audit", "prepare", "publish"))
    args = parser.parse_args()
    operation = {"audit": audit, "prepare": prepare, "publish": publish}[args.action]
    print(json.dumps(operation(), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
