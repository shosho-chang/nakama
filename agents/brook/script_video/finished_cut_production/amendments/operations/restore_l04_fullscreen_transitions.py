"""Restore L04 Big Title Transitions to the approved B2 paper_hand recipe."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
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
    ActiveAssetPublication,
    ActiveAssetStore,
)
from agents.brook.script_video.finished_cut_production._assets import AssetKind  # noqa: E402
from agents.brook.script_video.finished_cut_production._composition import (  # noqa: E402
    ProductionPaths,
    ProductionResolveConfiguration,
    ProductionResolvePorts,
    _build_resolve_materialization_composition,
)
from agents.brook.script_video.finished_cut_production._hyperframes_renderer import (  # noqa: E402
    PinnedHyperFramesRuntime,
    SubprocessRenderProcessRunner,
)
from agents.brook.script_video.finished_cut_production._long_visual_renderer import (  # noqa: E402
    LongVisualRenderRequest,
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
    TimelineIdentity,
)
from agents.brook.script_video.finished_cut_production._resolve_davinci import (  # noqa: E402
    ResolveCutBinding,
    ResolveProjectBinding,
)
from agents.brook.script_video.finished_cut_production._resolve_fusion import (  # noqa: E402
    ResolveDatabaseIdentity,
    ResolveProjectLocator,
)
from agents.brook.script_video.finished_cut_production._timeline_apply import (  # noqa: E402
    project_timeline_application,
)
from agents.brook.script_video.finished_cut_production._visual_assets import (  # noqa: E402
    build_long_visual_media_adapters,
)

EPISODE_ID = "20260805 林之晨"
CUT_ID = "long3-fresh-20260828-r4"
CURRENT_RELEASE_ID = "release-22a0424136727bb41527ff15"
EXPECTED_PREVIEW_SHA256 = "6071742d4233fb6c6824853e5c97582e64bfd1cfb4f9fd7e3ca0ab3a20075e89"
EDITORIAL_MASTER_CONTENT_HASH = "8e7c13c2c55bc0df0c05241cfd91a9bf5c6b484b58058dae42d2bfaa7576805b"
PROJECT_UID = "resolve-project:da7c1f4698b72f57a400f9a5196d0b4a136ea498236f3296b13c4fe272795231"
CANONICAL = TimelineIdentity(
    name="long3-fresh-20260828-r4-base",
    uid="fae04af1-00f2-4615-a4a3-9cbf458419a6",
)
EPISODES_ROOT = Path(r"G:\Footages")
EPISODE_ROOT = EPISODES_ROOT / EPISODE_ID
RUNTIME_ROOT = EPISODE_ROOT / "highlights" / "finished-cut-production-v1" / "runtime"
CURRENT_POINTER = EPISODE_ROOT / "highlights" / "review" / "finished_review_manifest_current.json"
ASSET_ROOT = EPISODE_ROOT / "highlights" / "assets-v2"
LANE_TRACKS = {
    "b_roll": 2,
    "hero_title": 3,
    "identity_card": 4,
    "fullscreen_transition": 6,
    "visual_effect": 7,
}


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


def _compose() -> tuple[FinishedCutReleaseLifecycle, ResolveTransactionManager]:
    paths = ProductionPaths(runtime_root=RUNTIME_ROOT, episodes_root=EPISODES_ROOT)
    assets = ActiveAssetStore.open(ASSET_ROOT, episode_id=EPISODE_ID)
    configuration = ProductionResolveConfiguration(
        locator=ResolveProjectLocator(
            episode_id=EPISODE_ID,
            database=ResolveDatabaseIdentity(db_type="Disk", db_name="Local Database"),
            folder="",
            project_name=EPISODE_ID,
        ),
        binding=ResolveProjectBinding(
            episode_id=EPISODE_ID,
            project_name=EPISODE_ID,
            project_uid=PROJECT_UID,
            cuts=(ResolveCutBinding(cut_id=CUT_ID, canonical=CANONICAL),),
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
    preview_path = (EPISODE_ROOT / release.preview.path).resolve(strict=True)
    if (
        release.cut_id != CUT_ID
        or release.preview.sha256 != EXPECTED_PREVIEW_SHA256
        or _sha256_file(preview_path) != EXPECTED_PREVIEW_SHA256
    ):
        raise RuntimeError("exact current L04 preview identity differs")
    return releases, release, pointer_bytes


def _recipe_identity(release: FinishedCutRelease, component) -> str:
    event = next(event for event in release.events if event.event_id == component.event_id)
    payload = {
        "run_id": release.run_id,
        "dp_acceptance_id": release.dp_acceptance_id,
        "event_id": event.event_id,
        "semantic_kind": event.semantic_kind,
        "implementation_kind": event.implementation_kind,
        "lane": event.lane,
        "display": event.display,
        "t0": component.t0,
        "t1": component.t1,
        "source_asset_ref": event.asset_ref,
        "geometry": {
            "target_width": 1920,
            "target_height": 1080,
            "layout_identity": "fullscreen_transition:v4",
        },
    }
    return f"recipe-sha256:{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


def _render_replacements(release: FinishedCutRelease) -> dict[str, str]:
    store = ActiveAssetStore.open(ASSET_ROOT, episode_id=EPISODE_ID)
    node = shutil.which("node.exe") or shutil.which("node")
    if node is None:
        raise RuntimeError("pinned Node runtime is unavailable")
    runtime = PinnedHyperFramesRuntime.verify(
        runtime_root=REPO_ROOT / "video" / "node_modules" / ".nakama-hyperframes" / "0.7.72",
        node_executable=node,
    )
    runner = SubprocessRenderProcessRunner()
    media_root = RUNTIME_ROOT / "episodes" / EPISODE_ID / "derived-media"
    renderer = build_long_visual_media_adapters(
        workspace_root=media_root / "workspaces",
        render_output_root=media_root / "renders",
        inset_output_root=media_root / "person-insets",
        runtime=runtime,
        runner=runner,
    ).title_renderer
    targets = tuple(
        component
        for component in release.components
        if component.implementation_kind == "fullscreen_transition"
    )
    if len(targets) != 5:
        raise RuntimeError("current L04 does not contain exactly five Fullscreen Transitions")
    replacements: dict[str, str] = {}
    for component in targets:
        recipe_identity = _recipe_identity(release, component)
        prior = store.find_exact_recipe(recipe_identity)
        if prior is None:
            rendered = renderer.render(
                LongVisualRenderRequest(
                    recipe_identity=recipe_identity,
                    event_id=component.event_id,
                    role="chapter",
                    display=component.display,
                    duration_sec=component.t1 - component.t0,
                    target_width=1920,
                    target_height=1080,
                    layout_identity="fullscreen_transition:v4",
                )
            )
            prior = store.publish(
                ActiveAssetPublication(
                    source_path=rendered.media.path,
                    kind=AssetKind.CHAPTER_RENDER,
                    visual_summary=component.display,
                    width=1920,
                    height=1080,
                    duration_sec=component.t1 - component.t0,
                    recipe_identity=recipe_identity,
                )
            )
        replacements[component.event_id] = prior.record.reference
    return replacements


def _replacement_plan(
    release: FinishedCutRelease,
    replacements: dict[str, str],
) -> MaterializationPlan:
    components = tuple(
        _mint_projected_component(
            component_id=component.component_id,
            event_id=component.event_id,
            semantic_kind=component.semantic_kind,
            implementation_kind=component.implementation_kind,
            lane=component.lane,
            display=component.display,
            t0=component.t0,
            t1=component.t1,
            asset_ref=replacements[component.event_id],
        )
        if component.event_id in replacements
        else component
        for component in release.components
    )
    if len(replacements) != 5 or len(components) != 15:
        raise RuntimeError("replacement component cardinality differs")
    identity = {
        "operation": "restore_fullscreen_transition_paper_hand_v4",
        "base_release_id": release.release_id,
        "base_plan_id": release.materialization_plan_id,
        "replacements": sorted(replacements.items()),
        "components": [asdict(component) for component in components],
    }
    plan_id = f"plan-transition-v4-{hashlib.sha256(_canonical_json(identity)).hexdigest()[:24]}"
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
        events=release.events,
        components=components,
    )


def _operation_paths(
    plan: MaterializationPlan,
    release: FinishedCutRelease,
) -> tuple[Path, Path]:
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
    adapter = transactions._adapter
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
        raise RuntimeError("Resolve work Timeline differs from the replacement plan")


def _summary(action: str, release: FinishedCutRelease, **facts: object) -> dict[str, object]:
    return {
        "action": action,
        "base_release_id": release.release_id,
        "base_preview_sha256": release.preview.sha256,
        **facts,
    }


def audit() -> dict[str, object]:
    lifecycle, transactions = _compose()
    _, release, _ = _exact_current(lifecycle)
    transactions._adapter.snapshot(CANONICAL)
    targets = [
        component.component_id
        for component in release.components
        if component.implementation_kind == "fullscreen_transition"
    ]
    if len(targets) != 5:
        raise RuntimeError("Fullscreen Transition target set differs")
    return _summary("audit", release, target_component_ids=targets)


def publish() -> dict[str, object]:
    lifecycle, transactions = _compose()
    old_releases, release, pointer_bytes = _exact_current(lifecycle)
    replacements = _render_replacements(release)
    plan = _replacement_plan(release, replacements)
    preview_path, subtitle_path = _operation_paths(plan, release)
    transaction = transactions.prepare(
        plan,
        canonical=CANONICAL,
        preview_path=preview_path,
        subtitle_path=subtitle_path,
    )
    if transaction.status != "preview_ready":
        raise RuntimeError("transition replacement transaction is not preview_ready")
    candidate = _candidate(
        lifecycle,
        transaction,
        plan,
        release,
        preview_path,
        subtitle_path,
    )
    committed = False
    pointer_published = False
    try:
        _verify_work_timeline(transactions, transaction, plan)
        if CURRENT_POINTER.read_bytes() != pointer_bytes:
            raise RuntimeError("current pointer changed before transition commit")
        transactions.commit(transaction.transaction_id, expected_cut_id=CUT_ID)
        committed = True
        new_release = lifecycle.seal_candidate(candidate)
        updated = tuple(
            new_release if item.release_id == CURRENT_RELEASE_ID else item for item in old_releases
        )
        lifecycle.publish_current(updated)
        pointer_published = True
        return _summary(
            "publish",
            release,
            plan_id=plan.plan_id,
            transaction_id=transaction.transaction_id,
            candidate_id=candidate.candidate_id,
            new_release_id=new_release.release_id,
            new_preview_sha256=new_release.preview.sha256,
            replacement_asset_refs=replacements,
        )
    except BaseException:
        if pointer_published:
            lifecycle.publish_current(old_releases)
        if committed:
            transactions.compensating_rollback(
                transaction.transaction_id,
                expected_cut_id=CUT_ID,
            )
        else:
            transactions._adapter.rollback(transaction.workspace)
            transactions._store.save(replace(transaction, status="rolled_back"))
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("audit", "publish"))
    args = parser.parse_args()
    operation = {"audit": audit, "publish": publish}[args.action]
    print(json.dumps(operation(), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
