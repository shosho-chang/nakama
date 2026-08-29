from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import agents.brook.script_video.finished_cut_production._materialization as materialization_module
from agents.brook.script_video.finished_cut_production._assets import (
    AssetContractError,
    AssetKind,
    AssetRecord,
    CompactAssetReceipt,
    ResolvedAsset,
    WorkerSelectionCatalog,
)
from agents.brook.script_video.finished_cut_production._commands import ApprovedCutCommand
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
)
from agents.brook.script_video.finished_cut_production._materialization import (
    CanonicalTimelineInspection,
    MaterializationCoordinator,
    MaterializationError,
    MaterializationPreparation,
)
from agents.brook.script_video.finished_cut_production._persistence import (
    AtomicResolveTransactionStore,
)
from agents.brook.script_video.finished_cut_production._records import (
    MaterializationPlan,
    _mint_materialization_plan,
    _mint_projected_component,
    _ProductionRun,
)
from agents.brook.script_video.finished_cut_production._release import (
    FinishedCutReleaseLifecycle,
    ReleaseLifecycleError,
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
from agents.brook.script_video.finished_cut_production._resolve_davinci import (
    ResolveTimelineItem,
    ResolveTimelineState,
    ResolveTimelineTrack,
)
from agents.brook.script_video.finished_cut_production._store import (
    _FilesystemProductionStore,
)

_LIN_EDITORIAL_MASTER_CONTENT_HASH = (
    "8e7c13c2c55bc0df0c05241cfd91a9bf5c6b484b58058dae42d2bfaa7576805b"
)
_LIN_EDITORIAL_MASTER_MEDIA_SHA256 = (
    "39b8db8b82eb114447b9ea4e877899bc505ff1a64788fd62e78b90ee8902ec80"
)


class _MissingRunStore:
    def load_run(self, command_id: str) -> None:
        assert command_id == "approved-cut:" + "a" * 32
        return None


class _RunStore:
    def __init__(self, run: object) -> None:
        self.run = run

    def load_run(self, command_id: str) -> object:
        return self.run


class _UnexpectedDependency:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"preflight dependency must not be touched: {name}")


class _CanonicalAuthority:
    def __init__(self, inspections: tuple[CanonicalTimelineInspection, ...]) -> None:
        self.inspections = inspections
        self.calls: list[tuple[str, str, str]] = []

    def inspect(
        self,
        *,
        episode_id: str,
        cut_id: str,
        editorial_master_content_hash: str,
    ) -> tuple[CanonicalTimelineInspection, ...]:
        self.calls.append((episode_id, cut_id, editorial_master_content_hash))
        return self.inspections


class _AssetResolver:
    def __init__(self, assets: dict[str, ResolvedAsset | Exception]) -> None:
        self.assets = assets
        self.calls: list[str] = []

    def resolve_active_asset(self, reference: str) -> ResolvedAsset:
        self.calls.append(reference)
        value = self.assets.get(reference, AssetContractError("missing"))
        if isinstance(value, Exception):
            raise value
        return value


class _FailingTransactions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def prepare(self, plan: object, **arguments: object) -> object:
        self.calls.append({"plan": plan, **arguments})
        raise ResolveTransactionError("injected stop after subtitle staging")


class _TimelineAdapter:
    def __init__(self, root: Path, *, baseline: TimelineSnapshot) -> None:
        self.root = root
        self.baseline = baseline
        self.preflights = 0
        self.duplicates = 0
        self.applies = 0
        self.renders = 0
        self.rollbacks = 0

    def preflight_plan(self, plan: MaterializationPlan) -> None:
        self.preflights += 1

    def snapshot(self, timeline: TimelineIdentity) -> TimelineSnapshot:
        return self.baseline

    def duplicate(
        self,
        canonical: TimelineIdentity,
        *,
        transaction_id: str,
    ) -> TimelineWorkspace:
        self.duplicates += 1
        return TimelineWorkspace(
            canonical,
            TimelineIdentity(canonical.name, f"work-{transaction_id}"),
            TimelineIdentity(f"backup-{canonical.name}", canonical.uid),
        )

    def apply_plan(self, work: TimelineIdentity, plan: MaterializationPlan) -> None:
        self.applies += 1

    def render_preview(self, work: TimelineIdentity, output: Path) -> PreviewRender:
        self.renders += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"preview")
        return PreviewRender(output, 480.0, "h264", "aac")

    def rollback(self, workspace: TimelineWorkspace) -> None:
        self.rollbacks += 1

    def commit(
        self,
        workspace: TimelineWorkspace,
        *,
        transaction_id: str,
        cut_id: str,
        retain_backup: bool,
    ) -> CommitReceipt:
        raise AssertionError("materialization must never commit")

    def compensate(self, workspace: TimelineWorkspace, receipt: CommitReceipt) -> None:
        raise AssertionError("materialization must never compensate")


class _CommittingTimelineAdapter(_TimelineAdapter):
    def commit(
        self,
        workspace: TimelineWorkspace,
        *,
        transaction_id: str,
        cut_id: str,
        retain_backup: bool,
    ) -> CommitReceipt:
        assert retain_backup is True
        return CommitReceipt(
            transaction_id=transaction_id,
            cut_id=cut_id,
            work_uid=workspace.work.uid,
            transaction_receipt_id=f"receipt-{transaction_id}",
            rollback_ref=f"backup-{transaction_id}",
            backup_retained=True,
        )


class _BadPreviewAdapter(_TimelineAdapter):
    def __init__(
        self,
        root: Path,
        *,
        baseline: TimelineSnapshot,
        failure: str,
    ) -> None:
        super().__init__(root, baseline=baseline)
        self.failure = failure

    def render_preview(self, work: TimelineIdentity, output: Path) -> PreviewRender:
        self.renders += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"bad preview")
        if self.failure == "offline":
            raise ResolveTransactionError("preview contains offline frames")
        if self.failure == "codec":
            return PreviewRender(output, 480.0, "prores", "aac")
        return PreviewRender(output, 479.0, "h264", "aac")


class _FailingReleaseLifecycle:
    def stage_candidate(self, *args: object, **kwargs: object) -> object:
        raise ReleaseLifecycleError("injected crash before Candidate persistence")


def _context(**changes: object) -> EditorialCutContext:
    values: dict[str, object] = {
        "episode_id": "episode-1",
        "cut_id": "punch-L04",
        "format": "long",
        "editorial_master_id": "b" * 64,
        "tight_cut_id": "tight-L04",
        "duration_sec": 480.0,
        "source_ranges": (CutSourceRange(100.0, 580.0),),
        "cues": (CueAnchor("cue-1", "第一段", 0.0, 2.0, "section-1"),),
        "sections": (CanonicalSection("section-1", "第一章", 0.0),),
    }
    values.update(changes)
    return EditorialCutContext(**values)  # type: ignore[arg-type]


def _plan(**changes: object) -> MaterializationPlan:
    values: dict[str, object] = {
        "plan_id": "plan-1",
        "run_id": "run-1",
        "command_id": "approved-cut:" + "a" * 32,
        "episode_id": "episode-1",
        "cut_id": "punch-L04",
        "format": "long",
        "director_acceptance_id": "accepted-director",
        "dp_acceptance_id": "accepted-dp",
        "visual_acceptance_id": "accepted-visual",
        "events": (),
        "components": (),
    }
    values.update(changes)
    return _mint_materialization_plan(**values)  # type: ignore[arg-type]


def _component_plan(
    asset_ref: str | None, *, implementation_kind: str = "hero_title"
) -> MaterializationPlan:
    stock = implementation_kind == "stock_video"
    component = _mint_projected_component(
        component_id="component-1",
        event_id="event-1",
        semantic_kind="b_roll" if stock else "hero_title",
        implementation_kind=implementation_kind,
        lane="b_roll" if stock else "hero_title",
        display="下一個黃金年代",
        t0=4.0,
        t1=8.0,
        asset_ref=asset_ref,
    )
    return _plan(components=(component,))


def _resolved_asset(
    path: Path,
    *,
    kind: AssetKind = AssetKind.TITLE_RENDER,
    width: int = 1920,
    height: int = 1080,
) -> ResolvedAsset:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if kind is AssetKind.STOCK:
        receipt = CompactAssetReceipt(
            origin="neutral_acquisition",
            media_sha256=digest,
            media_bytes=path.stat().st_size,
            source_class="licensed_stock",
            provider="pexels",
            provider_item_id="123",
            source_url="https://www.pexels.com/video/123/",
            license="Pexels license: https://www.pexels.com/license/",
            acquired_at="2026-08-28T00:00:00Z",
            forensic_receipt_ref="forensic-sha256:" + "f" * 64,
        )
        recipe_identity = None
        duration_sec = 10.0
    else:
        receipt = CompactAssetReceipt(
            origin="current_generated",
            media_sha256=digest,
            media_bytes=path.stat().st_size,
        )
        recipe_identity = "recipe-1"
        duration_sec = None
    return ResolvedAsset(
        AssetRecord(
            digest=digest,
            extension=path.suffix,
            kind=kind,
            visual_summary="neutral stock" if kind is AssetKind.STOCK else None,
            width=width,
            height=height,
            duration_sec=duration_sec,
            recipe_identity=recipe_identity,
            compact_receipt=receipt,
        ),
        path,
    )


def _stored(
    *,
    plan: MaterializationPlan | None = None,
    context: EditorialCutContext | None = None,
    command: ApprovedCutCommand | None = None,
    run_id: str = "run-1",
) -> object:
    command = command or ApprovedCutCommand(
        command_id="approved-cut:" + "a" * 32,
        episode_id="episode-1",
        cut_id="punch-L04",
        format="long",
        editorial_master_id="b" * 64,
        winner_id="winner-L04",
        tight_cut_id="tight-L04",
    )
    return SimpleNamespace(
        command=command,
        view=SimpleNamespace(
            run_id=run_id,
            command_id=command.command_id,
            status="review_ready",
            materialization_plan=plan or _plan(),
            editorial_context=context or _context(),
        ),
    )


def _canonical() -> CanonicalTimelineInspection:
    start = 86_400
    end = start + 480 * 30
    master = "b" * 64
    video = ResolveTimelineItem(
        item_id="video-1",
        track_type="video",
        track_index=1,
        start_frame=start,
        end_frame=end,
        source_in_frame=100 * 30,
        source_out_frame=580 * 30,
        properties=(),
        media_digest=master,
    )
    audio = replace(video, item_id="audio-1", track_type="audio")
    subtitle = ResolveTimelineItem(
        item_id="subtitle-1",
        track_type="subtitle",
        track_index=1,
        start_frame=start,
        end_frame=start + 2 * 30,
        source_in_frame=0,
        source_out_frame=2 * 30,
        properties=(("Text", "第一段"),),
        media_digest="c" * 64,
    )
    items = (video, audio, subtitle)
    tracks = (
        ResolveTimelineTrack("video", 1, "V1", True, True, ("video-1",)),
        ResolveTimelineTrack("audio", 1, "A1", True, True, ("audio-1",)),
        ResolveTimelineTrack("audio", 2, "A2", True, True, ()),
        ResolveTimelineTrack("subtitle", 1, "S1", True, True, ("subtitle-1",)),
    )
    return CanonicalTimelineInspection(
        episode_id="episode-1",
        cut_id="punch-L04",
        canonical=TimelineIdentity("Long 3", "timeline-L04"),
        editorial_master_content_hash="b" * 64,
        editorial_master_media_sha256="b" * 64,
        timeline_frame_rate=30.0,
        editorial_master_frame_rate=30.0,
        editorial_master_duration_sec=3_600.0,
        state=ResolveTimelineState(start, end, items, tracks),
        baseline=TimelineSnapshot("protected", "full"),
    )


def _split_identity_canonical() -> CanonicalTimelineInspection:
    inspection = _canonical()
    inspection = _replace_item(
        inspection,
        "video-1",
        media_digest=_LIN_EDITORIAL_MASTER_MEDIA_SHA256,
    )
    inspection = _replace_item(
        inspection,
        "audio-1",
        media_digest=_LIN_EDITORIAL_MASTER_MEDIA_SHA256,
    )
    return replace(
        inspection,
        editorial_master_content_hash=_LIN_EDITORIAL_MASTER_CONTENT_HASH,
        editorial_master_media_sha256=_LIN_EDITORIAL_MASTER_MEDIA_SHA256,
    )


def _replace_item(
    inspection: CanonicalTimelineInspection,
    item_id: str,
    **changes: object,
) -> CanonicalTimelineInspection:
    state = inspection.state
    items = tuple(
        replace(item, **changes) if item.item_id == item_id else item for item in state.items
    )
    return replace(inspection, state=replace(state, items=items))


def test_missing_production_run_fails_before_any_materialization_side_effect(
    tmp_path: Path,
) -> None:
    coordinator = MaterializationCoordinator(
        run_store=_MissingRunStore(),
        canonical_authority=_UnexpectedDependency(),
        assets=_UnexpectedDependency(),
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError, match="ProductionRun is missing") as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == "production_run_missing"
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.parametrize(
    ("status", "plan", "reason_code"),
    [
        ("needs_review", object(), "production_run_not_review_ready"),
        ("review_ready", None, "materialization_plan_missing"),
    ],
)
def test_run_requires_review_ready_exact_plan_before_preflight(
    tmp_path: Path,
    status: str,
    plan: object | None,
    reason_code: str,
) -> None:
    command_id = "approved-cut:" + "a" * 32
    stored = SimpleNamespace(
        command=SimpleNamespace(command_id=command_id),
        view=SimpleNamespace(
            command_id=command_id,
            status=status,
            materialization_plan=plan,
        ),
    )
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_UnexpectedDependency(),
        assets=_UnexpectedDependency(),
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare(command_id)

    assert raised.value.reason_code == reason_code
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.parametrize(
    "stored",
    [
        _stored(plan=_plan(command_id="approved-cut:" + "c" * 32)),
        _stored(plan=_plan(run_id="run-other")),
        _stored(plan=_plan(episode_id="episode-other")),
        _stored(plan=_plan(cut_id="value-L01")),
        _stored(plan=_plan(format="short")),
        _stored(context=_context(episode_id="episode-other")),
        _stored(context=_context(cut_id="value-L01")),
        _stored(context=_context(format="short")),
        _stored(context=_context(editorial_master_id="c" * 64)),
        _stored(context=_context(tight_cut_id="tight-other")),
    ],
)
def test_plan_context_and_command_must_be_one_exact_authority_chain(
    tmp_path: Path,
    stored: object,
) -> None:
    command_id = "approved-cut:" + "a" * 32
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_UnexpectedDependency(),
        assets=_UnexpectedDependency(),
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare(command_id)

    assert raised.value.reason_code == "authority_chain_mismatch"
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.parametrize(
    ("inspections", "reason_code"),
    [
        ((), "canonical_timeline_unknown"),
        (
            (_canonical(), replace(_canonical(), canonical=TimelineIdentity("Other", "uid-2"))),
            "canonical_timeline_ambiguous",
        ),
    ],
)
def test_exact_uid_binding_rejects_unknown_or_ambiguous_canonical_before_assets(
    tmp_path: Path,
    inspections: tuple[CanonicalTimelineInspection, ...],
    reason_code: str,
) -> None:
    authority = _CanonicalAuthority(inspections)
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(_stored()),
        canonical_authority=authority,
        assets=_UnexpectedDependency(),
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == reason_code
    assert authority.calls == [("episode-1", "punch-L04", "b" * 64)]
    assert list(tmp_path.rglob("*")) == []


def test_receipt_content_identity_and_master_media_digest_are_distinct_authorities(
    tmp_path: Path,
) -> None:
    context = _context(editorial_master_id=_LIN_EDITORIAL_MASTER_CONTENT_HASH)
    command = ApprovedCutCommand(
        command_id="approved-cut:" + "a" * 32,
        episode_id="episode-1",
        cut_id="punch-L04",
        format="long",
        editorial_master_id=_LIN_EDITORIAL_MASTER_CONTENT_HASH,
        winner_id="winner-L04",
        tight_cut_id="tight-L04",
    )
    inspection = _split_identity_canonical()
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(_stored(context=context, command=command)),
        canonical_authority=_CanonicalAuthority((inspection,)),
        assets=_UnexpectedDependency(),
        transactions=_FailingTransactions(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == "resolve_prepare_failed"


@pytest.mark.parametrize(
    ("inspection", "reason_code"),
    [
        (
            replace(
                _split_identity_canonical(),
                editorial_master_content_hash=_LIN_EDITORIAL_MASTER_MEDIA_SHA256,
            ),
            "editorial_master_content_identity_mismatch",
        ),
        (
            replace(
                _split_identity_canonical(),
                editorial_master_media_sha256=_LIN_EDITORIAL_MASTER_CONTENT_HASH,
            ),
            "editorial_master_media_drift",
        ),
        (
            _replace_item(
                _split_identity_canonical(),
                "video-1",
                media_digest=_LIN_EDITORIAL_MASTER_CONTENT_HASH,
            ),
            "editorial_master_media_drift",
        ),
    ],
)
def test_content_hash_and_media_sha_cannot_be_swapped_or_forged(
    tmp_path: Path,
    inspection: CanonicalTimelineInspection,
    reason_code: str,
) -> None:
    context = _context(editorial_master_id=_LIN_EDITORIAL_MASTER_CONTENT_HASH)
    command = ApprovedCutCommand(
        command_id="approved-cut:" + "a" * 32,
        episode_id="episode-1",
        cut_id="punch-L04",
        format="long",
        editorial_master_id=_LIN_EDITORIAL_MASTER_CONTENT_HASH,
        winner_id="winner-L04",
        tight_cut_id="tight-L04",
    )
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(_stored(context=context, command=command)),
        canonical_authority=_CanonicalAuthority((inspection,)),
        assets=_UnexpectedDependency(),
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == reason_code


def test_source_ranges_must_fit_inside_the_verified_master_duration(tmp_path: Path) -> None:
    inspection = replace(_canonical(), editorial_master_duration_sec=579.0)
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(_stored()),
        canonical_authority=_CanonicalAuthority((inspection,)),
        assets=_UnexpectedDependency(),
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == "source_range_outside_editorial_master"


@pytest.mark.parametrize(
    ("inspection", "reason_code"),
    [
        (replace(_canonical(), episode_id="episode-other"), "canonical_identity_mismatch"),
        (replace(_canonical(), cut_id="value-L01"), "canonical_identity_mismatch"),
        (replace(_canonical(), timeline_frame_rate=29.97), "frame_rate_drift"),
        (
            replace(
                _canonical(),
                state=replace(_canonical().state, end_frame=_canonical().state.end_frame + 2),
            ),
            "timeline_duration_drift",
        ),
        (
            _replace_item(_canonical(), "video-1", media_digest="d" * 64),
            "editorial_master_media_drift",
        ),
        (
            _replace_item(_canonical(), "audio-1", media_digest="d" * 64),
            "editorial_master_media_drift",
        ),
        (_replace_item(_canonical(), "video-1", source_in_frame=3_001), "source_range_drift"),
        (_replace_item(_canonical(), "audio-1", end_frame=100_799), "source_range_drift"),
        (
            replace(
                _canonical(),
                state=replace(
                    _canonical().state,
                    items=tuple(
                        item for item in _canonical().state.items if item.track_type != "audio"
                    ),
                ),
            ),
            "protected_track_drift",
        ),
        (_replace_item(_canonical(), "subtitle-1", end_frame=86_461), "subtitle_contract_drift"),
        (
            _replace_item(_canonical(), "subtitle-1", properties=(("Text", "錯字"),)),
            "subtitle_contract_drift",
        ),
        (
            replace(
                _canonical(),
                state=replace(
                    _canonical().state,
                    tracks=tuple(
                        replace(track, enabled=False) if track.track_type == "subtitle" else track
                        for track in _canonical().state.tracks
                    ),
                ),
            ),
            "protected_track_drift",
        ),
    ],
)
def test_editorial_master_base_drift_fails_before_asset_or_timeline_mutation(
    tmp_path: Path,
    inspection: CanonicalTimelineInspection,
    reason_code: str,
) -> None:
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(_stored()),
        canonical_authority=_CanonicalAuthority((inspection,)),
        assets=_UnexpectedDependency(),
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == reason_code
    assert list(tmp_path.rglob("*")) == []


def test_every_component_requires_an_exact_active_store_final_asset(tmp_path: Path) -> None:
    missing_ref = "asset-sha256:" + "e" * 64
    resolver = _AssetResolver({})
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(_stored(plan=_component_plan(missing_ref))),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=resolver,
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == "final_asset_unavailable"
    assert resolver.calls == [missing_ref]


def test_active_store_reference_and_object_digest_cannot_be_forged(tmp_path: Path) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"actual title bytes")
    resolved = _resolved_asset(asset)
    forged_ref = "asset-sha256:" + "e" * 64
    resolver = _AssetResolver({forged_ref: resolved})
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(_stored(plan=_component_plan(forged_ref))),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=resolver,
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == "final_asset_identity_mismatch"


def test_assetless_component_and_changed_active_object_fail_closed(tmp_path: Path) -> None:
    assetless = MaterializationCoordinator(
        run_store=_RunStore(_stored(plan=_component_plan(None))),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({}),
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )
    with pytest.raises(MaterializationError) as absent:
        assetless.prepare("approved-cut:" + "a" * 32)
    assert absent.value.reason_code == "final_asset_unavailable"

    asset = tmp_path / "changed.mov"
    asset.write_bytes(b"original")
    resolved = _resolved_asset(asset)
    asset.write_bytes(b"changed")
    changed = MaterializationCoordinator(
        run_store=_RunStore(_stored(plan=_component_plan(resolved.record.reference))),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )
    with pytest.raises(MaterializationError) as forged:
        changed.prepare("approved-cut:" + "a" * 32)
    assert forged.value.reason_code == "final_asset_identity_mismatch"


def test_vertical_stock_is_rejected_before_timeline_mutation(tmp_path: Path) -> None:
    asset = tmp_path / "stock.mp4"
    asset.write_bytes(b"vertical stock")
    resolved = _resolved_asset(asset, kind=AssetKind.STOCK, width=1080, height=1920)
    resolver = _AssetResolver({resolved.record.reference: resolved})
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(
            _stored(
                plan=_component_plan(
                    resolved.record.reference,
                    implementation_kind="stock_video",
                )
            )
        ),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=resolver,
        transactions=_UnexpectedDependency(),
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == "stock_not_landscape_16_9"


def test_current_cues_create_deterministic_utf8_srt_before_resolve_prepare(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    transactions = _FailingTransactions()
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(_stored(plan=_component_plan(resolved.record.reference))),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=transactions,
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == "resolve_prepare_failed"
    assert len(transactions.calls) == 1
    subtitle_path = transactions.calls[0]["subtitle_path"]
    assert isinstance(subtitle_path, Path)
    expected = "1\n00:00:00,000 --> 00:00:02,000\n第一段\n".encode()
    assert subtitle_path.read_bytes() == expected
    assert not subtitle_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert transactions.calls[0]["expected_baseline"] == _canonical().baseline


def test_conflicting_staged_srt_is_not_overwritten_or_sent_to_resolve(tmp_path: Path) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    transactions = _FailingTransactions()
    first = MaterializationCoordinator(
        run_store=_RunStore(_stored(plan=_component_plan(resolved.record.reference))),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=transactions,
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )
    with pytest.raises(MaterializationError):
        first.prepare("approved-cut:" + "a" * 32)
    subtitle_path = transactions.calls[0]["subtitle_path"]
    assert isinstance(subtitle_path, Path)
    subtitle_path.write_bytes(b"1\n00:00:00,000 --> 00:00:03,000\nwrong\n")
    transactions.calls.clear()

    with pytest.raises(MaterializationError) as raised:
        first.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == "subtitle_staging_conflict"
    assert transactions.calls == []
    assert subtitle_path.read_bytes().endswith(b"wrong\n")


def test_success_stages_one_preview_ready_candidate_and_reopens_idempotently(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    plan = _component_plan(resolved.record.reference)
    stored = _stored(plan=plan)
    authority = _CanonicalAuthority((_canonical(),))
    assets = _AssetResolver({resolved.record.reference: resolved})
    adapter = _TimelineAdapter(tmp_path, baseline=_canonical().baseline)
    transaction_root = tmp_path / "transactions"

    def open_coordinator() -> MaterializationCoordinator:
        manager = ResolveTransactionManager(
            adapter,
            store=AtomicResolveTransactionStore(transaction_root),
        )
        lifecycle = FinishedCutReleaseLifecycle(
            tmp_path,
            transactions=manager,
            preview_probe=lambda path: {
                "duration_sec": 480.0,
                "video_codec": "h264",
                "audio_codec": "aac",
                "decode_ok": True,
                "offline_frame_count": 0,
            },
        )
        return MaterializationCoordinator(
            run_store=_RunStore(stored),
            canonical_authority=authority,
            assets=assets,
            transactions=manager,
            releases=lifecycle,
            episode_root=tmp_path,
        )

    first = open_coordinator().prepare("approved-cut:" + "a" * 32)
    journal_path = next((tmp_path / "highlights" / "staging").rglob("materialization.json"))
    journal_bytes = journal_path.read_bytes()
    second = open_coordinator().prepare("approved-cut:" + "a" * 32)

    assert isinstance(first, MaterializationPreparation)
    assert first == second
    assert first.status == "preview_ready"
    assert first.candidate.preview_ready_transaction_id == first.transaction_id
    assert (adapter.duplicates, adapter.applies, adapter.renders) == (1, 1, 1)
    assert journal_path.read_bytes() == journal_bytes
    assert not (
        tmp_path / "highlights" / "review" / "finished_review_manifest_current.json"
    ).exists()
    with pytest.raises(ReleaseLifecycleError, match="missing"):
        FinishedCutReleaseLifecycle(
            tmp_path,
            transactions=ResolveTransactionManager(
                adapter,
                store=AtomicResolveTransactionStore(transaction_root),
            ),
            preview_probe=lambda _: {"duration_sec": 480.0},
        ).inspect_current("episode-1")


def test_reopen_uses_exact_preview_ready_journal_before_live_canonical_lookup(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    plan = _component_plan(resolved.record.reference)
    stored = _stored(plan=plan)
    adapter = _TimelineAdapter(tmp_path, baseline=_canonical().baseline)
    transaction_root = tmp_path / "transactions"

    first_manager = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(transaction_root),
    )
    first = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=first_manager,
        releases=FinishedCutReleaseLifecycle(
            tmp_path,
            transactions=first_manager,
            preview_probe=lambda _: {"duration_sec": 480.0},
        ),
        episode_root=tmp_path,
    ).prepare("approved-cut:" + "a" * 32)

    reopened_manager = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(transaction_root),
    )
    reopened = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_UnexpectedDependency(),  # type: ignore[arg-type]
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=reopened_manager,
        releases=FinishedCutReleaseLifecycle(
            tmp_path,
            transactions=reopened_manager,
            preview_probe=lambda _: {"duration_sec": 480.0},
        ),
        episode_root=tmp_path,
    ).prepare("approved-cut:" + "a" * 32)

    assert reopened == first
    assert (adapter.duplicates, adapter.applies, adapter.renders) == (1, 1, 1)


def test_reopen_accepts_exact_committed_transaction_without_live_canonical_lookup(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    plan = _component_plan(resolved.record.reference)
    stored = _stored(plan=plan)
    adapter = _CommittingTimelineAdapter(tmp_path, baseline=_canonical().baseline)
    transaction_root = tmp_path / "transactions"
    first_manager = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(transaction_root),
    )
    first = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=first_manager,
        releases=FinishedCutReleaseLifecycle(
            tmp_path,
            transactions=first_manager,
            preview_probe=lambda _: {"duration_sec": 480.0},
        ),
        episode_root=tmp_path,
    ).prepare("approved-cut:" + "a" * 32)
    first_manager.commit(first.transaction_id, expected_cut_id=plan.cut_id)

    reopened_manager = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(transaction_root),
    )
    reopened = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_UnexpectedDependency(),  # type: ignore[arg-type]
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=reopened_manager,
        releases=FinishedCutReleaseLifecycle(
            tmp_path,
            transactions=reopened_manager,
            preview_probe=lambda _: {"duration_sec": 480.0},
        ),
        episode_root=tmp_path,
    ).prepare("approved-cut:" + "a" * 32)

    assert reopened == first
    assert reopened_manager.inspect_transaction(first.transaction_id)["status"] == "committed"
    assert (adapter.duplicates, adapter.applies, adapter.renders) == (1, 1, 1)


def test_multi_range_editorial_base_reaches_transaction_preparation(tmp_path: Path) -> None:
    context = _context(source_ranges=(CutSourceRange(100.0, 340.0), CutSourceRange(500.0, 740.0)))
    base = _canonical()
    start = base.state.start_frame
    first_end = start + 240 * 30
    master = context.editorial_master_id
    rows: list[ResolveTimelineItem] = []
    for track_type in ("video", "audio"):
        track_prefix = "v" if track_type == "video" else "a"
        rows.extend(
            (
                ResolveTimelineItem(
                    f"{track_prefix}-1",
                    track_type,  # type: ignore[arg-type]
                    1,
                    start,
                    first_end,
                    100 * 30,
                    340 * 30,
                    (),
                    master,
                ),
                ResolveTimelineItem(
                    f"{track_prefix}-2",
                    track_type,  # type: ignore[arg-type]
                    1,
                    first_end,
                    base.state.end_frame,
                    500 * 30,
                    740 * 30,
                    (),
                    master,
                ),
            )
        )
    subtitle = next(item for item in base.state.items if item.track_type == "subtitle")
    state = replace(
        base.state,
        items=(*rows, subtitle),
        tracks=(
            ResolveTimelineTrack("video", 1, "V1", True, True, ("v-1", "v-2")),
            ResolveTimelineTrack("audio", 1, "A1", True, True, ("a-1", "a-2")),
            ResolveTimelineTrack("subtitle", 1, "S1", True, True, ("subtitle-1",)),
        ),
    )
    inspection = replace(base, state=state)
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    transactions = _FailingTransactions()
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(
            _stored(plan=_component_plan(resolved.record.reference), context=context)
        ),
        canonical_authority=_CanonicalAuthority((inspection,)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=transactions,
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == "resolve_prepare_failed"
    assert len(transactions.calls) == 1


def test_baseline_change_after_read_only_inspection_has_zero_timeline_mutations(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    adapter = _TimelineAdapter(
        tmp_path,
        baseline=TimelineSnapshot("changed", "changed"),
    )
    manager = ResolveTransactionManager(adapter)
    lifecycle = FinishedCutReleaseLifecycle(
        tmp_path,
        transactions=manager,
        preview_probe=lambda _: {"duration_sec": 480.0},
    )
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(_stored(plan=_component_plan(resolved.record.reference))),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=manager,
        releases=lifecycle,
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == "resolve_prepare_failed"
    assert (adapter.duplicates, adapter.applies, adapter.renders) == (0, 0, 0)


@pytest.mark.parametrize(
    ("failure", "reason_code"),
    [
        ("codec", "resolve_prepare_failed"),
        ("offline", "resolve_prepare_failed"),
        ("duration", "preview_probe_failed"),
    ],
)
def test_preview_codec_duration_and_offline_fail_without_candidate(
    tmp_path: Path,
    failure: str,
    reason_code: str,
) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    adapter = _BadPreviewAdapter(
        tmp_path,
        baseline=_canonical().baseline,
        failure=failure,
    )
    manager = ResolveTransactionManager(adapter)
    lifecycle = FinishedCutReleaseLifecycle(
        tmp_path,
        transactions=manager,
        preview_probe=lambda _: {"duration_sec": 480.0},
    )
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(_stored(plan=_component_plan(resolved.record.reference))),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=manager,
        releases=lifecycle,
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == reason_code
    assert not list((tmp_path / "highlights" / "staging").rglob("materialization.json"))


def test_restart_after_preview_ready_before_candidate_does_not_render_twice(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    plan = _component_plan(resolved.record.reference)
    stored = _stored(plan=plan)
    adapter = _TimelineAdapter(tmp_path, baseline=_canonical().baseline)
    transaction_root = tmp_path / "transactions"

    first_manager = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(transaction_root),
    )
    first = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=first_manager,
        releases=_FailingReleaseLifecycle(),  # type: ignore[arg-type]
        episode_root=tmp_path,
    )
    with pytest.raises(MaterializationError) as crashed:
        first.prepare("approved-cut:" + "a" * 32)
    assert crashed.value.reason_code == "candidate_staging_failed"

    second_manager = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(transaction_root),
    )
    recovered = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=second_manager,
        releases=FinishedCutReleaseLifecycle(
            tmp_path,
            transactions=second_manager,
            preview_probe=lambda _: {"duration_sec": 480.0},
        ),
        episode_root=tmp_path,
    ).prepare("approved-cut:" + "a" * 32)

    assert recovered.status == "preview_ready"
    assert (adapter.duplicates, adapter.renders) == (1, 1)


def test_restart_after_journal_write_failure_reuses_preview_ready_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    stored = _stored(plan=_component_plan(resolved.record.reference))
    adapter = _TimelineAdapter(tmp_path, baseline=_canonical().baseline)
    transaction_root = tmp_path / "transactions"
    original_writer = materialization_module._write_materialization_journal

    def fail_once(path: Path, payload: dict[str, object]) -> None:
        raise MaterializationError("injected journal failure", reason_code="journal_crash")

    monkeypatch.setattr(materialization_module, "_write_materialization_journal", fail_once)
    manager = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(transaction_root),
    )
    lifecycle = FinishedCutReleaseLifecycle(
        tmp_path,
        transactions=manager,
        preview_probe=lambda _: {"duration_sec": 480.0},
    )
    first = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=manager,
        releases=lifecycle,
        episode_root=tmp_path,
    )
    with pytest.raises(MaterializationError, match="injected journal failure"):
        first.prepare("approved-cut:" + "a" * 32)

    monkeypatch.setattr(
        materialization_module,
        "_write_materialization_journal",
        original_writer,
    )
    reopened_manager = ResolveTransactionManager(
        adapter,
        store=AtomicResolveTransactionStore(transaction_root),
    )
    recovered = MaterializationCoordinator(
        run_store=_RunStore(stored),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=reopened_manager,
        releases=FinishedCutReleaseLifecycle(
            tmp_path,
            transactions=reopened_manager,
            preview_probe=lambda _: {"duration_sec": 480.0},
        ),
        episode_root=tmp_path,
    ).prepare("approved-cut:" + "a" * 32)

    assert recovered.status == "preview_ready"
    assert (adapter.duplicates, adapter.renders) == (1, 1)


def test_reopened_filesystem_run_store_supplies_exact_plan_and_context(tmp_path: Path) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    plan = _component_plan(resolved.record.reference)
    context = _context()
    command = ApprovedCutCommand(
        command_id="approved-cut:" + "a" * 32,
        episode_id="episode-1",
        cut_id="punch-L04",
        format="long",
        editorial_master_id=context.editorial_master_id,
        winner_id="winner-L04",
        tight_cut_id=context.tight_cut_id,
    )
    store_root = tmp_path / "production-runs"
    _FilesystemProductionStore(store_root).create_run(
        command,
        _ProductionRun(
            run_id="run-1",
            command_id=command.command_id,
            editorial_context=context,
            status="review_ready",
            outstanding_request=None,
            materialization_plan=plan,
        ),
        WorkerSelectionCatalog(()),
    )
    transactions = _FailingTransactions()
    reopened = MaterializationCoordinator(
        run_store=_FilesystemProductionStore(store_root),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=transactions,
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )

    with pytest.raises(MaterializationError) as raised:
        reopened.prepare(command.command_id)

    assert raised.value.reason_code == "resolve_prepare_failed"
    assert len(transactions.calls) == 1


def test_incomplete_candidate_journal_fails_before_transaction_reentry(tmp_path: Path) -> None:
    asset = tmp_path / "title.mov"
    asset.write_bytes(b"title")
    resolved = _resolved_asset(asset)
    transactions = _FailingTransactions()
    coordinator = MaterializationCoordinator(
        run_store=_RunStore(_stored(plan=_component_plan(resolved.record.reference))),
        canonical_authority=_CanonicalAuthority((_canonical(),)),
        assets=_AssetResolver({resolved.record.reference: resolved}),
        transactions=transactions,
        releases=_UnexpectedDependency(),
        episode_root=tmp_path,
    )
    with pytest.raises(MaterializationError):
        coordinator.prepare("approved-cut:" + "a" * 32)
    subtitle_path = transactions.calls[0]["subtitle_path"]
    assert isinstance(subtitle_path, Path)
    (subtitle_path.parent / ".materialization.json.staging").write_bytes(b"partial")
    transactions.calls.clear()

    with pytest.raises(MaterializationError) as raised:
        coordinator.prepare("approved-cut:" + "a" * 32)

    assert raised.value.reason_code == "materialization_journal_incomplete"
    assert transactions.calls == []
