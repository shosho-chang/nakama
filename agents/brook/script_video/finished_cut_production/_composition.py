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
from ._assets import AssetResolver
from ._codex_semantic import (
    CodexProcessRunner,
    CodexSemanticAdapter,
    SubprocessCodexProcessRunner,
)
from ._commands import CommandRejectedError
from ._correction import RunInspection
from ._derived_assets import DerivedAssetBuilder
from ._engine import FinishedCutProduction
from ._hyperframes_renderer import (
    PinnedHyperFramesRuntime,
    SubprocessRenderProcessRunner,
)
from ._materialization import MaterializationCoordinator, MaterializationError
from ._materialization_fusion import (
    ResolveCanonicalTimelineAuthority,
    VerifiedEditorialMasterContractCache,
)
from ._persistence import AtomicResolveTransactionStore
from ._plan_record import PlanRecordStore
from ._policy import FormatPolicy
from ._records import (
    FinishedCutInspection,
    StageName,
    StageRequest,
    Status,
)
from ._resolve import ResolveTransactionManager, TimelineIdentity
from ._resolve_davinci import (
    DaVinciResolveTimelineAdapter,
    FFprobeMediaProbe,
    MediaProbe,
    ResolveCutBinding,
    ResolveFacade,
    ResolveProjectBinding,
)
from ._resolve_fusion import (
    DaVinciResolveFacade,
    MediaIdentityResolver,
    ResolveDatabaseIdentity,
    ResolveProjectLocator,
    _synthetic_project_uid,
    connect_resolve_scripting,
    current_timeline_identities,
)
from ._semantic import DurableSemanticAdapter, SemanticAdapter
from ._store import (
    PlanRecordIndex,
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


@dataclass(frozen=True, slots=True)
class ProductionDependencies:
    """Internal Adapter selection for the production seams that actually vary."""

    asset_resolver: AssetResolver
    semantic_adapter: SemanticAdapter
    derived_asset_builder: DerivedAssetBuilder | None = None
    plan_records: PlanRecordIndex | None = None
    long_policy: FormatPolicy | None = None
    materialization: MaterializationCoordinator | None = None
    materialization_unavailable_reason: str = "resolve_materialization_not_connected"


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
    ) -> None:
        self._episode_id = episode_id
        self._authority = authority
        self._production = production
        self._semantic_adapter = semantic_adapter
        self._run_store_root = run_store_root
        self._materialization = materialization
        self._materialization_unavailable_reason = materialization_unavailable_reason

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
            plan_records=dependencies.plan_records,
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
        current_plan_ref: str,
        event_id: str,
        feedback: str,
    ) -> str:
        return self._production.request_revision(current_plan_ref, event_id, feedback)

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


class PlanRecordReader:
    """Read-only access to one episode's plan records, for Bridge and publish.

    Bridge must not compose semantic workers, renderers or Resolve just to read a
    reviewable cut, but it must also not re-derive the projection itself: the
    inspection here is the same one ``FinishedCutProduction.inspect_current``
    returns, so the two cannot drift.
    """

    def __init__(self, episode_root: str | Path) -> None:
        # 沒有交易與探測接縫的 `PlanRecordStore` 就是唯讀的：`stage` 會擋，
        # 讀取不需要任何外部依賴。
        self._records = PlanRecordStore(episode_root)

    def inspect_current(self, episode_id: str) -> FinishedCutInspection:
        return self._records.inspect(episode_id)


def build_plan_record_reader(episode_root: str | Path) -> PlanRecordReader:
    """Public read-only entry point for the finished-cut review surface."""
    return PlanRecordReader(episode_root)


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
    PlanRecordStore,
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
    records = PlanRecordStore(
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
        records=records,
        episode_root=episode_root,
    )
    return coordinator, records, transactions


RESOLVE_BINDING_SCHEMA = "nakama.finished_cut_resolve_binding.v1"


def build_resolve_configuration(payload: dict, episode_id: str) -> ProductionResolveConfiguration:
    """Compose an episode's Resolve authority from a name-bound binding document.

    The CLI's ``--resolve-config`` pins Timeline uids on purpose: a one-shot
    operator wants it to fail if the project moved underneath them.  An
    unattended watcher needs the opposite, because every committed transaction
    duplicate-swaps the canonical Timeline and changes its uid.  So this binds by
    Timeline **name** and resolves the uid here, against the live project.
    """
    if payload.get("schema") != RESOLVE_BINDING_SCHEMA:
        raise ValueError(f"Resolve binding schema must be {RESOLVE_BINDING_SCHEMA}")
    if payload.get("episode_id") != episode_id:
        raise ValueError("Resolve binding belongs to another episode")
    database = payload["database"]
    locator = ResolveProjectLocator(
        episode_id=episode_id,
        database=ResolveDatabaseIdentity(
            db_type=str(database["db_type"]),
            db_name=str(database["db_name"]),
            ip_address=database.get("ip_address"),
        ),
        folder=str(payload["folder"]),
        project_name=str(payload["project_name"]),
    )
    identities = {row.name: row.uid for row in current_timeline_identities(locator)}
    cuts: list[ResolveCutBinding] = []
    for row in payload["cuts"]:
        name = str(row["timeline_name"])
        uid = identities.get(name)
        if uid is None:
            raise ValueError(f"Resolve project has no Timeline named {name!r}")
        cuts.append(
            ResolveCutBinding(
                cut_id=str(row["cut_id"]),
                canonical=TimelineIdentity(name=name, uid=uid),
            )
        )
    return ProductionResolveConfiguration(
        locator=locator,
        binding=ResolveProjectBinding(
            episode_id=episode_id,
            project_name=locator.project_name,
            project_uid=_synthetic_project_uid(locator),
            cuts=tuple(cuts),
        ),
        editorial_master_content_hash=str(payload["editorial_master_content_hash"]),
        staging_root=Path(str(payload["staging_root"])),
    )


def build_production_application(
    paths: ProductionPaths,
    episode_id: str,
    *,
    process_runner: CodexProcessRunner | None = None,
    previewer: InspectionPreviewer | None = None,
    resolve_configuration: ProductionResolveConfiguration | None = None,
    resolve_ports: ProductionResolvePorts | None = None,
) -> FinishedCutProductionApplication:
    """Compose the sole production path through verified offline Long media Adapters."""

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
        records = PlanRecordStore(episode_root)
        materialization = None
        materialization_reason = "resolve_binding_not_configured"
    else:
        materialization, records, _transactions = (
            _build_resolve_materialization_composition(
                paths=paths,
                episode_id=episode_id,
                assets=assets,
                run_store_root=run_store_root,
                configuration=resolve_configuration,
                ports=resolve_ports or ProductionResolvePorts(),
            )
        )
        materialization_reason = None
    return FinishedCutProductionApplication.open(
        paths,
        episode_id=episode_id,
        dependencies=ProductionDependencies(
            asset_resolver=assets,
            semantic_adapter=semantic,
            derived_asset_builder=media,
            plan_records=records,
            materialization=materialization,
            materialization_unavailable_reason=(
                materialization_reason or "resolve_materialization_not_connected"
            ),
        ),
    )


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
        runtime=runtime,
        runner=process_runner,
    )
    return LongDerivedAssetBuilder(
        store=assets,
        title_renderer=adapters.title_renderer,
    )
