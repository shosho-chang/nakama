"""Production composition root for one Podcast episode."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Protocol

from ._active_store import ActiveAssetStore
from ._approved_cut import (
    ApprovedCutAuthority,
    ApprovedCutAuthorityContextResolver,
    ApprovedCutRegistration,
    ApprovedCutRegistrationError,
    EditorialMasterVerifier,
    FilesystemEditorialMasterVerifier,
)
from ._assets import AssetKind, AssetResolver, WorkerSelectionCatalog
from ._codex_semantic import (
    CodexProcessRunner,
    CodexSemanticAdapter,
    SubprocessCodexProcessRunner,
)
from ._commands import CommandRejectedError
from ._correction import RunInspection
from ._cutover import (
    AtomicDeploymentPointerAdapter,
    CutoverStatus,
    FilesystemReleaseCutoverAdapter,
    GlobalCutover,
    GlobalCutoverError,
    GlobalCutoverJournal,
)
from ._derived_assets import DerivedAssetBuilder
from ._engine import FinishedCutProduction, _current_inspection
from ._face_placement import (
    DeterministicFacialSafePlacement,
    FilesystemEditorialMasterVideoResolver,
    OpenCvHaarFaceDetector,
    OpenCvMasterFrameReader,
    PinnedOpenCvHaarModel,
    StoredRunFacePlacementContextResolver,
)
from ._hyperframes_renderer import (
    PinnedHyperFramesRuntime,
    SubprocessRenderProcessRunner,
)
from ._materialization import MaterializationCoordinator, MaterializationError
from ._materialization_fusion import (
    ResolveCanonicalTimelineAuthority,
    VerifiedEditorialMasterContractCache,
)
from ._persistence import AtomicCutoverJournalStore, AtomicResolveTransactionStore
from ._policy import FormatPolicy, StockVideoMetadata
from ._records import (
    FinishedCutInspection,
    StagedReleaseCandidate,
    StageName,
    StageRequest,
    Status,
)
from ._release import FinishedCutReleaseLifecycle, ReleaseLifecycleError
from ._resolve import ResolveTransactionManager
from ._resolve_davinci import (
    DaVinciResolveTimelineAdapter,
    FFprobeMediaProbe,
    MediaProbe,
    ResolveFacade,
    ResolveProjectBinding,
)
from ._resolve_fusion import (
    DaVinciResolveFacade,
    MediaIdentityResolver,
    ResolveProjectLocator,
    connect_resolve_scripting,
)
from ._semantic import DurableSemanticAdapter, SemanticAdapter
from ._store import (
    CurrentReleaseIndex,
    _FilesystemProductionStore,
    _FilesystemSemanticDispatchLedger,
)
from ._timeline_apply import PreRenderedAssetCatalog
from ._visual_assets import LongDerivedAssetBuilder, build_long_visual_media_adapters
from ._worker_packet import (
    InspectionPreviewer,
    ProductionWorkerPacketMaterializer,
    StagePacket,
    StoredAssetPreviewer,
    WorkerPacketScope,
)

CommandState = Literal[
    "registered",
    "pending",
    "needs_review",
    "review_ready",
    "preview_ready",
    "failed",
]


@dataclass(frozen=True, slots=True)
class ProductionPaths:
    """All durable roots needed to compose one production runtime."""

    runtime_root: Path
    episodes_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_root", Path(self.runtime_root).resolve())
        object.__setattr__(self, "episodes_root", Path(self.episodes_root).resolve())


@dataclass(frozen=True, slots=True)
class ProductionResolveConfiguration:
    """Exact episode-scoped Resolve authority supplied by the deployment layer."""

    locator: ResolveProjectLocator
    binding: ResolveProjectBinding
    editorial_master_content_hash: str
    staging_root: Path

    def __post_init__(self) -> None:
        if self.locator.episode_id != self.binding.episode_id:
            raise ValueError("Resolve locator and binding have different episode identity")
        if self.locator.project_name != self.binding.project_name:
            raise ValueError("Resolve locator and binding have different project identity")
        if not _sha256(self.editorial_master_content_hash):
            raise ValueError("Editorial Master content hash must be lowercase SHA-256")
        object.__setattr__(self, "staging_root", Path(self.staging_root).resolve())


@dataclass(frozen=True, slots=True)
class ProductionCutoverConfiguration:
    """Pinned three-cut order and deployment authority for one cutover."""

    fixed_cut_order: tuple[str, ...]
    target_deployment_id: str
    deployment_state_path: Path

    def __post_init__(self) -> None:
        if (
            len(self.fixed_cut_order) != 3
            or len(set(self.fixed_cut_order)) != 3
            or any(not _opaque_identity(cut_id) for cut_id in self.fixed_cut_order)
        ):
            raise ValueError("production cutover requires three exact unique cut identities")
        if not _opaque_identity(self.target_deployment_id):
            raise ValueError("production cutover target deployment identity is invalid")
        object.__setattr__(
            self,
            "deployment_state_path",
            Path(self.deployment_state_path).resolve(),
        )


class ResolveFacadeFactory(Protocol):
    def __call__(
        self,
        locator: ResolveProjectLocator,
        media_identity_resolver: MediaIdentityResolver,
    ) -> ResolveFacade: ...


@dataclass(frozen=True, slots=True)
class ProductionResolvePorts:
    """External Resolve and probe seams injected only at the composition boundary."""

    facade_factory: ResolveFacadeFactory | None = None
    media_probe: MediaProbe | None = None
    editorial_master_verifier: Callable[..., object] | None = None


class _ProductionCutoverCoordinator(Protocol):
    def run(
        self,
        cutover_id: str,
        candidates: tuple[StagedReleaseCandidate, ...],
    ) -> GlobalCutoverJournal: ...


class _ConfiguredGlobalCutover:
    def __init__(self, cutover: GlobalCutover, *, target_deployment_id: str) -> None:
        self._cutover = cutover
        self._target_deployment_id = target_deployment_id

    def run(
        self,
        cutover_id: str,
        candidates: tuple[StagedReleaseCandidate, ...],
    ) -> GlobalCutoverJournal:
        return self._cutover.run(
            cutover_id,
            candidates=candidates,
            target_deployment_id=self._target_deployment_id,
        )


@dataclass(frozen=True, slots=True)
class ProductionDependencies:
    """Internal Adapter selection for the production seams that actually vary."""

    asset_resolver: AssetResolver
    semantic_adapter: SemanticAdapter
    derived_asset_builder: DerivedAssetBuilder | None = None
    current_release_index: CurrentReleaseIndex | None = None
    long_policy: FormatPolicy | None = None
    short_policy: FormatPolicy | None = None
    stock_video_metadata: tuple[StockVideoMetadata, ...] = ()
    materialization: MaterializationCoordinator | None = None
    materialization_unavailable_reason: str = "resolve_materialization_not_connected"
    cutover: _ProductionCutoverCoordinator | None = None


@dataclass(frozen=True, slots=True)
class ProductionStatusView:
    """Typed, read-only status returned by the composition Interface and CLI."""

    command_id: str
    state: CommandState
    run_id: str | None = None
    current_stage: StageName | Literal["materialization"] | None = None
    scope: Literal["full_stage", "event_retry"] | None = None
    event_id: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class ProductionCutoverStatusView:
    """Narrow operator view of one completed or resumed global cutover."""

    cutover_id: str
    episode_id: str
    state: CutoverStatus
    release_ids: tuple[str, ...]
    manifest_id: str
    deployment_id: str


class FinishedCutProductionApplication:
    """Deep episode-scoped Interface over registration and production advancement."""

    def __init__(
        self,
        *,
        episode_id: str,
        authority: ApprovedCutAuthority,
        production: FinishedCutProduction,
        semantic_adapter: SemanticAdapter,
        run_store_root: Path,
        materialization: MaterializationCoordinator | None,
        materialization_unavailable_reason: str | None,
        cutover: _ProductionCutoverCoordinator | None = None,
    ) -> None:
        self._episode_id = episode_id
        self._authority = authority
        self._production = production
        self._semantic_adapter = semantic_adapter
        self._run_store_root = run_store_root
        self._materialization = materialization
        self._materialization_unavailable_reason = materialization_unavailable_reason
        self._cutover = cutover

    @classmethod
    def open(
        cls,
        paths: ProductionPaths,
        *,
        episode_id: str,
        dependencies: ProductionDependencies,
        master_verifier: EditorialMasterVerifier | None = None,
    ) -> FinishedCutProductionApplication:
        if not _opaque_identity(episode_id):
            raise ApprovedCutRegistrationError("production episode identity is invalid")
        authority = ApprovedCutAuthority(
            paths.runtime_root / "approved-cuts",
            master_verifier=(
                master_verifier
                if master_verifier is not None
                else FilesystemEditorialMasterVerifier(paths.episodes_root)
            ),
        )
        run_store_root = paths.runtime_root / "episodes" / episode_id / "runs"
        production = FinishedCutProduction(
            store_root=run_store_root,
            approved_cut_store=authority,
            asset_resolver=dependencies.asset_resolver,
            semantic_adapter=dependencies.semantic_adapter,
            derived_asset_builder=dependencies.derived_asset_builder,
            context_resolver=ApprovedCutAuthorityContextResolver(authority),
            long_policy=dependencies.long_policy,
            short_policy=dependencies.short_policy,
            stock_video_metadata=dependencies.stock_video_metadata,
            current_release_index=dependencies.current_release_index,
        )
        return cls(
            episode_id=episode_id,
            authority=authority,
            production=production,
            semantic_adapter=dependencies.semantic_adapter,
            run_store_root=run_store_root,
            materialization=dependencies.materialization,
            materialization_unavailable_reason=(
                dependencies.materialization_unavailable_reason
                if dependencies.materialization is None
                else None
            ),
            cutover=dependencies.cutover,
        )

    def register_approved_cut(self, registration: ApprovedCutRegistration) -> str:
        if registration.episode_id != self._episode_id:
            raise ApprovedCutRegistrationError(
                "ApprovedCut registration belongs to another production episode"
            )
        return self._authority.register(registration)

    def advance(self, command_id: str) -> ProductionStatusView:
        self._reject_cross_episode_approved_cut(command_id)
        view = self._production.advance(command_id)
        if view.status == "review_ready" and self._materialization is not None:
            try:
                prepared = self._materialization.prepare(command_id)
            except MaterializationError as error:
                return ProductionStatusView(
                    command_id=command_id,
                    state="needs_review",
                    run_id=view.run_id,
                    current_stage="materialization",
                    reason_code=error.reason_code,
                )
            return ProductionStatusView(
                command_id=command_id,
                state="preview_ready",
                run_id=prepared.run_id,
                current_stage="materialization",
            )
        return self.status(command_id)

    def request_revision(
        self,
        current_release_ref: str,
        event_id: str,
        feedback: str,
    ) -> str:
        return self._production.request_revision(current_release_ref, event_id, feedback)

    def inspect_run(self, command_id: str) -> RunInspection:
        self._reject_cross_episode_approved_cut(command_id)
        return self._production.inspect_run(command_id)

    def request_correction(
        self,
        command_id: str,
        stage: StageName,
        event_id: str,
        feedback: str,
    ) -> str:
        self._reject_cross_episode_approved_cut(command_id)
        return self._production.request_correction(command_id, stage, event_id, feedback)

    def retry_failed_dispatch(self, command_id: str) -> str:
        self._reject_cross_episode_approved_cut(command_id)
        return self._production.retry_failed_dispatch(command_id)

    def cutover(
        self,
        cutover_id: str,
        command_ids: tuple[str, ...],
    ) -> ProductionCutoverStatusView:
        if (
            len(command_ids) != 3
            or len(set(command_ids)) != 3
            or any(not _opaque_identity(command_id) for command_id in command_ids)
        ):
            raise GlobalCutoverError("cutover requires three exact unique command identities")
        if self._materialization is None or self._cutover is None:
            raise GlobalCutoverError("production cutover is not configured")
        candidates: list[StagedReleaseCandidate] = []
        for command_id in command_ids:
            self._reject_cross_episode_approved_cut(command_id)
            candidates.append(self._materialization.prepare(command_id).candidate)
        journal = self._cutover.run(cutover_id, tuple(candidates))
        if (
            journal.status != "completed"
            or journal.episode_id != self._episode_id
            or len(journal.releases) != 3
            or journal.unpublished_index is None
            or journal.unpublished_index.episode_id != self._episode_id
        ):
            raise GlobalCutoverError("global cutover did not return one completed episode index")
        return ProductionCutoverStatusView(
            cutover_id=journal.cutover_id,
            episode_id=journal.episode_id,
            state=journal.status,
            release_ids=tuple(release.release_id for release in journal.releases),
            manifest_id=journal.unpublished_index.index_id,
            deployment_id=journal.target_deployment_id,
        )

    def inspect_current(self) -> FinishedCutInspection:
        return self._production.inspect_current(self._episode_id)

    def status(self, command_id: str) -> ProductionStatusView:
        self._reject_cross_episode_approved_cut(command_id)
        stored = _FilesystemProductionStore(self._run_store_root).load_run(command_id)
        if stored is not None:
            request = stored.view.outstanding_request
            outcome = (
                self._semantic_adapter.outcome_for(request.request_id)
                if request is not None
                else None
            )
            return self._status_from_values(
                command_id=stored.view.command_id,
                run_id=stored.view.run_id,
                status=stored.view.status,
                current_stage=request.stage if request is not None else None,
                scope=request.scope if request is not None else None,
                event_id=request.event_id if request is not None else None,
                reason_code=(
                    outcome.reason_code
                    if stored.view.status == "needs_review" and outcome is not None
                    else None
                ),
            )
        if self._authority.resolve(command_id) is not None:
            return ProductionStatusView(command_id=command_id, state="registered")
        revision = _FilesystemProductionStore(self._run_store_root).load_targeted_revision(
            command_id
        )
        if revision is not None:
            return ProductionStatusView(command_id=command_id, state="registered")
        raise CommandRejectedError(f"authoritative command not found: {command_id}")

    def _reject_cross_episode_approved_cut(self, command_id: str) -> None:
        if not command_id.startswith("approved-cut:"):
            return
        command = self._authority.resolve(command_id)
        if command is not None and command.episode_id != self._episode_id:
            raise CommandRejectedError("ApprovedCut command belongs to another episode")

    def _status_from_values(
        self,
        *,
        command_id: str,
        run_id: str,
        status: Status,
        current_stage: StageName | None,
        scope: Literal["full_stage", "event_retry"] | None,
        event_id: str | None,
        reason_code: str | None,
    ) -> ProductionStatusView:
        if status == "review_ready":
            return ProductionStatusView(
                command_id=command_id,
                state="pending",
                run_id=run_id,
                current_stage="materialization",
                scope=scope,
                event_id=event_id,
                reason_code=self._materialization_unavailable_reason,
            )
        return ProductionStatusView(
            command_id=command_id,
            state=status,
            run_id=run_id,
            current_stage=current_stage,
            scope=scope,
            event_id=event_id,
            reason_code=reason_code,
        )


def _opaque_identity(value: object) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= 256
        and not any(character in value for character in "/\\{}[]\r\n\t")
    )


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


class _CurrentRequestPacketMaterializer:
    """Build one stage packet from the exact request scope, never a shared directory."""

    def __init__(
        self,
        *,
        asset_resolver: AssetResolver,
        previewer: InspectionPreviewer,
    ) -> None:
        self._asset_resolver = asset_resolver
        self._previewer = previewer

    def materialize(self, request: StageRequest) -> StagePacket:
        return ProductionWorkerPacketMaterializer(
            scope=WorkerPacketScope(
                run_id=request.run_id,
                episode_id=request.episode_id,
                cut_id=request.cut_id,
                format=request.format,
            ),
            asset_resolver=self._asset_resolver,
            previewer=self._previewer,
        ).materialize(request)


class _InspectionOnlyTransactions:
    def inspect_transaction(self, transaction_id: str):
        raise ReleaseLifecycleError(
            f"transaction inspection is unavailable during dark install: {transaction_id}"
        )


def _inspection_only_probe(_path: Path):
    raise ReleaseLifecycleError("preview probing is unavailable during dark install")


class CurrentReleaseReader:
    """Read-only exact-current access for an inbound adapter such as Bridge.

    Bridge must not compose semantic workers, renderers or Resolve just to read a
    reviewable Release, but it must also not re-derive the projection itself: the
    inspection here is the same one ``FinishedCutProduction.inspect_current``
    returns, so the two cannot drift.
    """

    def __init__(self, episode_root: str | Path) -> None:
        self._index = _EpisodeCurrentReleaseIndex(
            FinishedCutReleaseLifecycle(
                Path(episode_root),
                transactions=_InspectionOnlyTransactions(),
                preview_probe=_inspection_only_probe,
            ),
            episode_id="",
        )

    def inspect_current(self, episode_id: str) -> FinishedCutInspection:
        return _current_inspection(episode_id, self._index)


def build_current_release_reader(episode_root: str | Path) -> CurrentReleaseReader:
    """Public read-only entry point for the finished-cut review surface."""
    return CurrentReleaseReader(episode_root)


class _EpisodeCurrentReleaseIndex:
    def __init__(self, lifecycle: FinishedCutReleaseLifecycle, *, episode_id: str) -> None:
        self._lifecycle = lifecycle
        self._episode_id = episode_id

    def inspect_current(self, episode_id: str):
        return self._lifecycle.inspect_current(episode_id)

    def resolve_exact_current(self, release_id: str):
        try:
            releases = self._lifecycle.inspect_current(self._episode_id)
        except ReleaseLifecycleError as error:
            if error.reason == "missing":
                return None
            raise
        return next(
            (release for release in releases if release.release_id == release_id),
            None,
        )


class _ActiveStorePreRenderedCatalog(PreRenderedAssetCatalog):
    """Resolve final component references against the current Active Store on demand."""

    def __init__(self, assets: ActiveAssetStore) -> None:
        self._assets = assets

    def resolve(self, reference: str) -> Path:
        resolved = self._assets.resolve_active_asset(reference)
        if resolved.path is None:
            raise ValueError("Active Store asset has no materializable object")
        return resolved.path.resolve(strict=True)


class _ProductionMediaIdentityResolver:
    """Map Resolve media objects to verified Master or Active Store digests."""

    def __init__(
        self,
        *,
        editorial_master: VerifiedEditorialMasterContractCache,
        episode_id: str,
        editorial_master_content_hash: str,
        assets: ActiveAssetStore,
    ) -> None:
        self._editorial_master = editorial_master
        self._episode_id = episode_id
        self._editorial_master_content_hash = editorial_master_content_hash
        self._assets = assets

    def digest_for(self, media_pool_item: object) -> str:
        getter = getattr(media_pool_item, "GetClipProperty", None)
        if not callable(getter):
            raise ValueError("Resolve media object has no clip property Interface")
        properties = getter()
        if not isinstance(properties, dict):
            raise ValueError("Resolve media object properties are invalid")
        raw_path = properties.get("File Path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("Resolve media object path is missing")
        media_path = Path(raw_path).resolve(strict=True)
        contract = self._editorial_master.load(
            episode_id=self._episode_id,
            editorial_master_content_hash=self._editorial_master_content_hash,
        )
        if media_path == contract.master_media_path.resolve(strict=True):
            return contract.master_media_sha256
        digest = media_path.stem.lower()
        if not _sha256(digest):
            raise ValueError("Resolve media object is neither the Master nor content-addressed")
        resolved = self._assets.resolve_active_asset(f"asset-sha256:{digest}")
        if resolved.path is None or resolved.path.resolve(strict=True) != media_path:
            raise ValueError("Resolve media object differs from the Active Store object")
        return resolved.record.digest


def _default_resolve_facade_factory(
    locator: ResolveProjectLocator,
    media_identity_resolver: MediaIdentityResolver,
) -> ResolveFacade:
    return DaVinciResolveFacade(
        resolve=connect_resolve_scripting(),
        locator=locator,
        media_identity_resolver=media_identity_resolver,
    )


def _preview_probe_mapping(probe: MediaProbe, path: Path) -> dict[str, object]:
    result = probe.inspect(path)
    return {
        "duration_sec": result.duration_sec,
        "video_codec": result.video_codec,
        "audio_codec": result.audio_codec,
        "decode_ok": result.decode_ok,
        "offline_frame_count": result.offline_frame_count,
    }


def _build_resolve_materialization_composition(
    *,
    paths: ProductionPaths,
    episode_id: str,
    assets: ActiveAssetStore,
    run_store_root: Path,
    configuration: ProductionResolveConfiguration,
    ports: ProductionResolvePorts,
) -> tuple[
    MaterializationCoordinator,
    FinishedCutReleaseLifecycle,
    ResolveTransactionManager,
]:
    episode_root = (paths.episodes_root / episode_id).resolve()
    expected_staging = (episode_root / "highlights" / "staging" / "finished-cut").resolve()
    if (
        configuration.locator.episode_id != episode_id
        or configuration.binding.episode_id != episode_id
        or configuration.staging_root != expected_staging
    ):
        raise ValueError("Resolve configuration does not bind this exact episode staging root")
    cache = VerifiedEditorialMasterContractCache(
        episode_root=episode_root,
        cache_path=(
            paths.runtime_root
            / "verified-editorial-masters"
            / episode_id
            / f"{configuration.editorial_master_content_hash}.json"
        ),
        **(
            {"verifier": ports.editorial_master_verifier}
            if ports.editorial_master_verifier is not None
            else {}
        ),
    )
    media_identity = _ProductionMediaIdentityResolver(
        editorial_master=cache,
        episode_id=episode_id,
        editorial_master_content_hash=configuration.editorial_master_content_hash,
        assets=assets,
    )
    facade = (ports.facade_factory or _default_resolve_facade_factory)(
        configuration.locator,
        media_identity,
    )
    probe = ports.media_probe or FFprobeMediaProbe()
    timeline = DaVinciResolveTimelineAdapter(
        facade=facade,
        probe=probe,
        binding=configuration.binding,
        assets=_ActiveStorePreRenderedCatalog(assets),
    )
    transactions = ResolveTransactionManager(
        timeline,
        store=AtomicResolveTransactionStore(
            paths.runtime_root / "episodes" / episode_id / "resolve-transactions"
        ),
    )
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=lambda path: _preview_probe_mapping(probe, path),
    )
    coordinator = MaterializationCoordinator(
        run_store=_FilesystemProductionStore(run_store_root),
        canonical_authority=ResolveCanonicalTimelineAuthority(
            binding=configuration.binding,
            facade=facade,
            timeline_adapter=timeline,
            editorial_master=cache,
        ),
        assets=assets,
        transactions=transactions,
        releases=lifecycle,
        episode_root=episode_root,
    )
    return coordinator, lifecycle, transactions


def build_production_application(
    paths: ProductionPaths,
    episode_id: str,
    *,
    process_runner: CodexProcessRunner | None = None,
    previewer: InspectionPreviewer | None = None,
    resolve_configuration: ProductionResolveConfiguration | None = None,
    resolve_ports: ProductionResolvePorts | None = None,
    cutover_configuration: ProductionCutoverConfiguration | None = None,
) -> FinishedCutProductionApplication:
    """Compose the sole production path through verified offline Long media Adapters."""

    if cutover_configuration is not None and resolve_configuration is None:
        raise ValueError("production cutover requires exact Resolve configuration")
    episode_root = paths.episodes_root / episode_id
    assets = ActiveAssetStore.open(
        episode_root / "highlights" / "assets-v2",
        episode_id=episode_id,
    )
    run_store_root = paths.runtime_root / "episodes" / episode_id / "runs"
    media = _build_long_media_composition(
        paths=paths,
        episode_id=episode_id,
        assets=assets,
        run_store_root=run_store_root,
    )
    semantic = DurableSemanticAdapter(
        worker=CodexSemanticAdapter(
            process_runner=process_runner or SubprocessCodexProcessRunner(),
            packet_materializer=_CurrentRequestPacketMaterializer(
                asset_resolver=assets,
                previewer=previewer or StoredAssetPreviewer(),
            ),
        ),
        ledger=_FilesystemSemanticDispatchLedger(run_store_root),
    )
    if resolve_configuration is None:
        lifecycle = FinishedCutReleaseLifecycle(
            episode_root,
            transactions=_InspectionOnlyTransactions(),
            preview_probe=_inspection_only_probe,
        )
        materialization = None
        materialization_reason = "resolve_binding_not_configured"
        production_cutover = None
    else:
        materialization, lifecycle, transactions = _build_resolve_materialization_composition(
            paths=paths,
            episode_id=episode_id,
            assets=assets,
            run_store_root=run_store_root,
            configuration=resolve_configuration,
            ports=resolve_ports or ProductionResolvePorts(),
        )
        materialization_reason = None
        production_cutover = None
        if cutover_configuration is not None:
            bound_cut_ids = tuple(cut.cut_id for cut in resolve_configuration.binding.cuts)
            if len(bound_cut_ids) != 3 or set(bound_cut_ids) != set(
                cutover_configuration.fixed_cut_order
            ):
                raise ValueError("cutover order does not match three exact Resolve cut bindings")
            cutover_root = paths.runtime_root / "episodes" / episode_id / "cutovers"
            release_cutover = FilesystemReleaseCutoverAdapter(
                lifecycle=lifecycle,
                episode_id=episode_id,
                rollback_root=cutover_root / "pointer-snapshots",
            )
            production_cutover = _ConfiguredGlobalCutover(
                GlobalCutover(
                    resolve=transactions,
                    sealer=release_cutover,
                    current_index=release_cutover,
                    deployment=AtomicDeploymentPointerAdapter(
                        cutover_configuration.deployment_state_path,
                        target_deployment_id=(cutover_configuration.target_deployment_id),
                    ),
                    journals=AtomicCutoverJournalStore(cutover_root / "journals"),
                    fixed_cut_order=cutover_configuration.fixed_cut_order,
                ),
                target_deployment_id=cutover_configuration.target_deployment_id,
            )
    return FinishedCutProductionApplication.open(
        paths,
        episode_id=episode_id,
        dependencies=ProductionDependencies(
            asset_resolver=assets,
            semantic_adapter=semantic,
            derived_asset_builder=media,
            stock_video_metadata=_stock_video_metadata_from_catalog(
                assets.worker_selection_catalog()
            ),
            current_release_index=_EpisodeCurrentReleaseIndex(
                lifecycle,
                episode_id=episode_id,
            ),
            materialization=materialization,
            materialization_unavailable_reason=(
                materialization_reason or "resolve_materialization_not_connected"
            ),
            cutover=production_cutover,
        ),
    )


def _stock_video_metadata_from_catalog(
    catalog: WorkerSelectionCatalog,
) -> tuple[StockVideoMetadata, ...]:
    """Project exact neutral acquisition dimensions into Long policy input."""

    rows: list[StockVideoMetadata] = []
    references: set[str] = set()
    for item in catalog.items():
        if item.kind is not AssetKind.STOCK:
            continue
        if (
            type(item.width) is not int
            or item.width <= 0
            or type(item.height) is not int
            or item.height <= 0
            or item.reference in references
        ):
            raise ValueError("Stock Video catalog dimensions are not trustworthy")
        references.add(item.reference)
        rows.append(
            StockVideoMetadata(
                asset_ref=item.reference,
                native_width=item.width,
                native_height=item.height,
            )
        )
    return tuple(rows)


def _build_long_media_composition(
    *,
    paths: ProductionPaths,
    episode_id: str,
    assets: ActiveAssetStore,
    run_store_root: Path,
) -> LongDerivedAssetBuilder:
    repo_root = Path(__file__).resolve().parents[4]
    runtime_root = repo_root / "video" / "node_modules" / ".nakama-hyperframes" / "0.7.72"
    node = shutil.which("node.exe") or shutil.which("node")
    if node is None:
        raise ValueError("pinned Node runtime is unavailable for Long media composition")
    runtime = PinnedHyperFramesRuntime.verify(
        runtime_root=runtime_root,
        node_executable=node,
    )
    process_runner = SubprocessRenderProcessRunner()
    media_root = paths.runtime_root / "episodes" / episode_id / "derived-media"
    adapters = build_long_visual_media_adapters(
        workspace_root=media_root / "workspaces",
        render_output_root=media_root / "renders",
        inset_output_root=media_root / "person-insets",
        runtime=runtime,
        runner=process_runner,
    )
    face_model = PinnedOpenCvHaarModel.verify(Path(__file__).resolve().parent / "assets")
    placement = DeterministicFacialSafePlacement(
        context_resolver=StoredRunFacePlacementContextResolver(run_store_root),
        master_resolver=FilesystemEditorialMasterVideoResolver(
            paths.episodes_root,
            cache_root=paths.runtime_root / "verified-editorial-masters",
        ),
        frame_reader=OpenCvMasterFrameReader(),
        face_detector=OpenCvHaarFaceDetector(
            model_path=face_model.path,
            expected_model_sha256=face_model.sha256,
            expected_opencv_version=face_model.opencv_version,
        ),
    )
    return LongDerivedAssetBuilder(
        store=assets,
        title_renderer=adapters.title_renderer,
        compositor=adapters.person_inset_compositor,
        face_placement=placement,
    )
