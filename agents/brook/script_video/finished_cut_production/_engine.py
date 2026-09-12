"""Deep Finished Cut Production interface and its in-memory system adapter."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from ._assets import (
    AssetContractError,
    AssetResolver,
    InMemoryAssetResolver,
    WorkerSelectionCatalog,
)
from ._commands import (
    ApprovedCutCommand,
    CommandRejectedError,
    TargetedRevisionCommand,
    _is_authoritative_approved_cut,
    _is_authoritative_command_id,
)
from ._context import (
    CutSourceRange,
    EditorialCutContext,
    EditorialCutContextResolver,
    VisualPlacement,
)
from ._correction import (
    RunInspection,
    _merge_exact_event,
    _PreReleaseCorrection,
    _PreReleaseCorrectionError,
    _project_run_inspection,
    _select_correction,
)
from ._derived_assets import (
    BuiltComponentAsset,
    DerivedAssetBuilder,
    DerivedAssetBuildRequest,
    DerivedAssetBuildResult,
    DerivedAssetGeometry,
    DerivedAssetInstruction,
    readable_floor_sec,
)
from ._policy import (
    CutPolicyInput,
    FormatPolicy,
    LongV2Policy,
    ShortPolicy,
    StockVideoMetadata,
)
from ._projection import (
    _WORKER_PROJECTION_COMBINATIONS,
    ASSET_KIND_BY_IMPLEMENTATION,
    NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS,
    SOURCE_ASSET_KIND_BY_IMPLEMENTATION,
    _event_has_active_projection,
    _is_active_semantic_kind,
    layout_identity,
)
from ._records import (
    STAGE_RESPONSE_SCHEMA,
    AcceptedStage,
    ArtifactView,
    ComponentProposal,
    ComponentView,
    CutView,
    DirectorEventProposal,
    DPEventProposal,
    EventPlacementCandidates,
    EventRecord,
    EventView,
    FinishedCutInspection,
    FinishedCutRelease,
    ProjectedComponent,
    ReleaseArtifact,
    RequestScope,
    RunView,
    StageName,
    StageProposal,
    StageRequest,
    VisualEventProposal,
    _mint_accepted_stage,
    _mint_materialization_plan,
    _mint_projected_component,
    _ProductionRun,
    _seal_finished_cut_release,
)
from ._release import ReleaseLifecycleError
from ._semantic import (
    _DEFAULT_PARENT,
    InMemorySemanticAdapter,
    SemanticAdapter,
    SemanticDispatchOutcome,
)
from ._store import (
    ApprovedCutStore,
    CurrentReleaseIndex,
    InMemoryCurrentReleaseIndex,
    SemanticDispatchStoreError,
    _FilesystemProductionStore,
    _StoredRun,
)

_ALLOWED_PROJECTION = frozenset(_WORKER_PROJECTION_COMBINATIONS)
#: 三張表本來在這裡各自宣告一份（ADR-069 階段 1 漏掉的兩份就在這）。名單本體在
#: `_projection.VOCABULARY`——`source` 是 worker 挑進來的素材類別，`asset_kind` 是
#: 成品的類別，person_inset 兩者不同（挑 PHOTO、產 COMPOSITE）。
_ASSET_KIND_BY_IMPLEMENTATION = SOURCE_ASSET_KIND_BY_IMPLEMENTATION
_FINAL_ASSET_KIND_BY_IMPLEMENTATION = ASSET_KIND_BY_IMPLEMENTATION
_NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS = NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS


@dataclass(slots=True)
class _NeutralPassThroughBuilder:
    """Conservative default: resolve neutral media, never synthesize semantic bytes."""

    asset_resolver: AssetResolver

    def build(self, request: DerivedAssetBuildRequest) -> DerivedAssetBuildResult:
        if any(instruction.recipe_identity is not None for instruction in request.instructions):
            return DerivedAssetBuildResult(
                build_request_id=request.build_request_id,
                dp_acceptance_id=request.dp_acceptance_id,
                status="pending",
            )
        assets: list[BuiltComponentAsset] = []
        for instruction in request.instructions:
            if instruction.source_asset_ref is None:
                return DerivedAssetBuildResult(
                    build_request_id=request.build_request_id,
                    dp_acceptance_id=request.dp_acceptance_id,
                    status="failed",
                    error_code="neutral_source_missing",
                )
            self.asset_resolver.resolve_worker_asset(instruction.source_asset_ref)
            assets.append(
                BuiltComponentAsset(
                    component_id=instruction.component_id,
                    event_id=instruction.event_id,
                    source_asset_ref=instruction.source_asset_ref,
                    final_asset_ref=instruction.source_asset_ref,
                    inspection_ref=instruction.source_asset_ref,
                    recipe_identity=None,
                )
            )
        return DerivedAssetBuildResult(
            build_request_id=request.build_request_id,
            dp_acceptance_id=request.dp_acceptance_id,
            status="ready",
            assets=tuple(assets),
        )


class FinishedCutProduction:
    """Deep public interface over persistent production authority."""

    def __init__(
        self,
        *,
        store_root: str | Path,
        approved_cut_store: ApprovedCutStore,
        asset_resolver: AssetResolver,
        semantic_adapter: SemanticAdapter,
        derived_asset_builder: DerivedAssetBuilder | None = None,
        context_resolver: EditorialCutContextResolver | None = None,
        long_policy: FormatPolicy | None = None,
        short_policy: FormatPolicy | None = None,
        stock_video_metadata: Iterable[StockVideoMetadata] = (),
        current_release_index: CurrentReleaseIndex | None = None,
    ) -> None:
        self._store = _FilesystemProductionStore(store_root)
        self._approved_cut_store = approved_cut_store
        self._asset_resolver = asset_resolver
        self._semantic_adapter = semantic_adapter
        self._derived_asset_builder = derived_asset_builder or _NeutralPassThroughBuilder(
            asset_resolver
        )
        self._context_resolver = context_resolver
        self._long_policy = long_policy or LongV2Policy()
        self._short_policy = short_policy or ShortPolicy()
        self._stock_video_metadata = tuple(stock_video_metadata)
        self._current_release_index = current_release_index or InMemoryCurrentReleaseIndex()

    def advance(self, command_id: str) -> RunView:
        with self._store.command_lock(command_id):
            return self._advance_locked(command_id)

    def _advance_locked(self, command_id: str) -> RunView:
        if not _is_authoritative_command_id(command_id):
            raise CommandRejectedError(f"opaque authoritative command ID required: {command_id}")
        existing = self._store.load_run(command_id)
        if existing is not None:
            base_release = None
            if existing.base_release_id is not None:
                base_release = self._current_release_index.resolve_exact_current(
                    existing.base_release_id
                )
                if base_release is None:
                    raise CommandRejectedError(
                        f"targeted revision base is not exact current: {existing.base_release_id}"
                    )
            run = _RunState(
                command=existing.command,
                view=existing.view,
                worker_catalog=_live_catalog(self._asset_resolver, existing.worker_catalog),
                base_release=base_release,
                editorial_context=self._validate_stored_context(
                    existing.view.editorial_context,
                    existing.command,
                    base_release=base_release,
                ),
                format_policy=self._policy_for(existing.command.format),
                stock_video_metadata=self._stock_video_metadata,
                derived_asset_builder=self._derived_asset_builder,
                asset_resolver=self._asset_resolver,
            )
            aggregate = _PersistentAggregateContext(
                semantic_adapter=self._semantic_adapter,
                accepted_stages={
                    stage.acceptance_id: stage for stage in self._store.accepted_stages()
                },
            )
            result = _advance_existing(run, aggregate)
            if result != existing.view:
                self._store.save_run(
                    _StoredRun(
                        command=existing.command,
                        view=result,
                        worker_catalog=existing.worker_catalog,
                        base_release_id=existing.base_release_id,
                    )
                )
            return _public_run_view(result)
        command = (
            self._approved_cut_store.resolve(command_id)
            if command_id.startswith("approved-cut:")
            else self._store.load_targeted_revision(command_id)
        )
        if command is None:
            raise CommandRejectedError(f"authoritative command not found: {command_id}")
        if isinstance(command, ApprovedCutCommand) and not _is_authoritative_approved_cut(
            command, command_id
        ):
            raise CommandRejectedError(f"opaque authoritative command ID required: {command_id}")
        base_release = None
        events: tuple[EventRecord, ...] = ()
        feedback = None
        scope = "full_stage"
        event_id = None
        parent_acceptance_id = None
        if isinstance(command, TargetedRevisionCommand):
            base_release = self._current_release_index.resolve_exact_current(
                command.current_release_id
            )
            if base_release is None:
                raise CommandRejectedError(
                    f"targeted revision base is not exact current: {command.current_release_id}"
                )
            director = self._store.load_accepted(base_release.director_acceptance_id)
            if director is None:
                raise CommandRejectedError("targeted revision Director authority is unavailable")
            target = next(
                (event for event in director.events if event.event_id == command.event_id),
                None,
            )
            if target is None:
                raise CommandRejectedError("targeted revision event authority is unavailable")
            events = (target,)
            feedback = command.feedback
            scope = "event_retry"
            event_id = command.event_id
            parent_acceptance_id = director.acceptance_id
        editorial_context = self._resolve_context(command, base_release=base_release)
        run_id = f"run-{uuid4().hex}"
        request = StageRequest(
            run_id=run_id,
            request_id=f"request-{uuid4().hex}",
            command_id=command.command_id,
            episode_id=command.episode_id,
            cut_id=command.cut_id,
            format=command.format,
            stage="director",
            attempt=1,
            scope=scope,
            event_id=event_id,
            parent_acceptance_id=parent_acceptance_id,
            base_acceptance_id=(director.acceptance_id if base_release is not None else None),
            events=events,
            feedback=feedback,
            components=(
                tuple(
                    component
                    for component in director.components
                    if component.event_id == command.event_id
                )
                if isinstance(command, TargetedRevisionCommand)
                else ()
            ),
            editorial_context=editorial_context,
        )
        view = _ProductionRun(
            run_id=run_id,
            command_id=command.command_id,
            editorial_context=editorial_context,
            status="pending",
            outstanding_request=request,
        )
        worker_catalog = self._asset_resolver.worker_selection_catalog()
        self._store.create_run(
            command,
            view,
            worker_catalog,
            base_release_id=base_release.release_id if base_release is not None else None,
        )
        run = _RunState(
            command=command,
            view=view,
            worker_catalog=worker_catalog,
            base_release=base_release,
            editorial_context=editorial_context,
            format_policy=self._policy_for(command.format),
            stock_video_metadata=self._stock_video_metadata,
            derived_asset_builder=self._derived_asset_builder,
            asset_resolver=self._asset_resolver,
        )
        result = _advance_existing(
            run,
            _PersistentAggregateContext(
                semantic_adapter=self._semantic_adapter,
                accepted_stages={
                    stage.acceptance_id: stage for stage in self._store.accepted_stages()
                },
            ),
        )
        if result != view:
            self._store.save_run(
                _StoredRun(
                    command=command,
                    view=result,
                    worker_catalog=worker_catalog,
                    base_release_id=(base_release.release_id if base_release is not None else None),
                )
            )
        return _public_run_view(result)

    def request_revision(
        self,
        current_release_ref: str,
        event_id: str,
        feedback: str,
    ) -> str:
        with self._store.command_lock(current_release_ref):
            return self._request_revision_locked(current_release_ref, event_id, feedback)

    def _request_revision_locked(
        self,
        current_release_ref: str,
        event_id: str,
        feedback: str,
    ) -> str:
        release = self._current_release_index.resolve_exact_current(current_release_ref)
        if release is None:
            raise CommandRejectedError(f"release is not exact current: {current_release_ref}")
        if any(
            not _event_has_active_projection(
                semantic_kind=event.semantic_kind,
                implementation_kind=event.implementation_kind,
                lane=event.lane,
                intentional_aroll=event.intentional_aroll,
            )
            for event in release.events
            if event.semantic_kind
        ):
            raise CommandRejectedError(
                "historical Release is read-only after projection retirement"
            )
        if event_id not in {event.event_id for event in release.events}:
            raise CommandRejectedError(f"event is not in current release: {event_id}")
        normalized_feedback = feedback.strip()
        if not normalized_feedback:
            raise CommandRejectedError("targeted revision feedback is required")
        command_id = f"targeted-revision:{uuid4().hex}"
        self._store.save_targeted_revision(
            TargetedRevisionCommand(
                command_id=command_id,
                current_release_id=release.release_id,
                episode_id=release.episode_id,
                cut_id=release.cut_id,
                format=release.format,
                event_id=event_id,
                feedback=normalized_feedback,
            )
        )
        return command_id

    def retry_failed_dispatch(self, command_id: str) -> str:
        """Explicitly replace one terminal dispatch or rejected correction proposal."""

        with self._store.command_lock(command_id):
            if not _is_authoritative_command_id(command_id):
                raise CommandRejectedError(
                    f"opaque authoritative command ID required: {command_id}"
                )
            stored = self._store.load_run(command_id)
            if stored is None:
                raise CommandRejectedError(f"authoritative production run not found: {command_id}")
            view = stored.view
            request = view.outstanding_request
            if (
                view.status not in {"pending", "needs_review"}
                or request is None
                or view.materialization_plan is not None
                or view.derived_asset_request is not None
            ):
                raise CommandRejectedError("current run is not eligible for dispatch recovery")
            history = view.accepted_stage_history or view.accepted_stages
            correction_recovery = (
                view.correction is not None
                and view.status == "needs_review"
                and request.scope == "event_retry"
                and request.event_id == view.correction.event_id
            )
            if not correction_recovery and any(stage.stage == request.stage for stage in history):
                raise CommandRejectedError("failed stage already has AcceptedStage authority")
            try:
                outcome = self._semantic_adapter.outcome_for_request(request)
            except SemanticDispatchStoreError as error:
                raise CommandRejectedError(
                    "failed semantic request does not match durable dispatch"
                ) from error
            # Any terminal dispatch failure is recoverable, not just the first.
            # This command is only ever reached by a human asking for another try
            # (the CLI; the watcher never calls it), so capping it at attempt 1
            # did not bound runaway retries — it bounded how many times a person
            # is allowed to ask.  A targeted revision whose stage failed twice
            # then had no recovery at all: request_correction refuses revision
            # runs outright, and the watcher only picks up queued work.  That is
            # what stranded 20260805's six needs_review runs.
            failed_dispatch = (
                view.correction is None
                and outcome is not None
                and outcome.state == "failed"
                and outcome.proposal is None
            )
            recoverable_correction = (
                correction_recovery
                and outcome is not None
                and (
                    (outcome.state == "ready" and outcome.proposal is not None)
                    or (outcome.state in {"failed", "indeterminate"} and outcome.proposal is None)
                )
            )
            dispatch_state = self._semantic_adapter.dispatch_state(request.request_id)
            dispatch_is_recoverable = dispatch_state == "completed" or (
                dispatch_state == "claimed"
                and outcome is not None
                and outcome.state == "indeterminate"
                and outcome.proposal is None
            )
            if not dispatch_is_recoverable or not (failed_dispatch or recoverable_correction):
                raise CommandRejectedError(
                    "current request has no terminal dispatch failure or rejected correction"
                )
            base_release = None
            if stored.base_release_id is not None:
                base_release = self._current_release_index.resolve_exact_current(
                    stored.base_release_id
                )
                if base_release is None:
                    raise CommandRejectedError("dispatch recovery base is not exact current")
            run = _RunState(
                command=stored.command,
                view=view,
                worker_catalog=_live_catalog(self._asset_resolver, stored.worker_catalog),
                base_release=base_release,
                editorial_context=self._validate_stored_context(
                    view.editorial_context,
                    stored.command,
                    base_release=base_release,
                ),
                format_policy=self._policy_for(stored.command.format),
                stock_video_metadata=self._stock_video_metadata,
                derived_asset_builder=self._derived_asset_builder,
                asset_resolver=self._asset_resolver,
            )
            aggregate = _PersistentAggregateContext(
                semantic_adapter=self._semantic_adapter,
                accepted_stages={
                    stage.acceptance_id: stage for stage in self._store.accepted_stages()
                },
            )
            if not _request_base_is_current(run, request, aggregate):
                raise CommandRejectedError("failed semantic request is not exact current")
            request_id = f"request-{uuid4().hex}"
            retry = _retry_with_live_catalog(request, request_id, run.worker_catalog)
            self._store.save_run(
                _StoredRun(
                    command=stored.command,
                    view=replace(
                        view,
                        status="pending",
                        outstanding_request=retry,
                        policy_diagnostics=(),
                    ),
                    worker_catalog=stored.worker_catalog,
                    base_release_id=stored.base_release_id,
                )
            )
            return request_id

    def request_correction(
        self,
        command_id: str,
        stage: StageName,
        event_id: str,
        feedback: str,
    ) -> str:
        """Mint one exact-event retry from current pre-release authority."""

        with self._store.command_lock(command_id):
            return self._request_correction_locked(command_id, stage, event_id, feedback)

    def _request_correction_locked(
        self,
        command_id: str,
        stage: StageName,
        event_id: str,
        feedback: str,
    ) -> str:

        if not _is_authoritative_command_id(command_id):
            raise CommandRejectedError(f"opaque authoritative command ID required: {command_id}")
        stored = self._store.load_run(command_id)
        if stored is None:
            raise CommandRejectedError(f"authoritative production run not found: {command_id}")
        if isinstance(stored.command, TargetedRevisionCommand):
            raise CommandRejectedError(
                "pre-release correction is only available to fresh ApprovedCut runs"
            )
        view = stored.view
        # 舊行為：`MaterializationPlan` 一鑄出來就把修正窗口關掉。
        #
        # 那條規則在長片線上是**反的**：Resolve 的 timeline 與 preview 只在 plan 生出來
        # 之後才存在，也就是說**等修修看得到成品，窗口已經關了**。他 2026-09-10 的原話
        # 是「我希望長片也能快速改」——而唯一的出路是重新登錄整支，把已經付掉的語意
        # 工作再付一次。
        #
        # plan 是 run 內部的紀錄，重鑄一份不會動到任何已封存的東西：correction 本來就會
        # 把 `materialization_plan` 清成 None、run 退回 `pending`（見本函式結尾的
        # `save_run`），而新的 plan 依 `_materialization_paths` 會拿到**自己的** staging
        # 工作區與 Resolve transaction（兩者的身分都由 plan 決定），不會覆蓋舊的。
        #
        # 真正不能動的是**已經封存成 Release** 的 plan——那要走 `request_revision`，
        # 它會鑄新 run 並保留整條收據鏈。所以只擋這一種。
        plan = view.materialization_plan
        if plan is not None:
            sealed = [
                release
                for release in self._current_release_index.inspect_current(
                    stored.command.episode_id
                )
                if release.materialization_plan_id == plan.plan_id
            ]
            if sealed:
                raise CommandRejectedError(
                    "this MaterializationPlan is already sealed into current Release "
                    f"{sealed[0].release_id}; use request_revision to change a released cut"
                )
        if view.correction is not None:
            raise CommandRejectedError("another pre-release correction is still current")
        try:
            selection = _select_correction(
                view.accepted_stages,
                stage=stage,
                event_id=event_id,
                feedback=feedback,
            )
        except _PreReleaseCorrectionError as error:
            raise CommandRejectedError(str(error)) from error
        outstanding = view.outstanding_request
        dispatch_state = (
            self._semantic_adapter.dispatch_state(outstanding.request_id)
            if outstanding is not None
            else "unclaimed"
        )
        if dispatch_state != "unclaimed":
            raise CommandRejectedError("downstream semantic request was already claimed")
        if (
            view.derived_asset_request is not None
            and view.status != "pending"
            and stage != "dp"
            and not _derived_build_failed(view)
        ):
            raise CommandRejectedError("downstream derived-asset work is already current")
        target = next(event for event in selection.base.events if event.event_id == event_id)
        upstream = selection.current_prefix[-1] if selection.current_prefix else None
        request_id = f"request-{uuid4().hex}"
        dp_catalog = (
            _live_catalog(self._asset_resolver, stored.worker_catalog) if stage == "dp" else None
        )
        request = StageRequest(
            run_id=view.run_id,
            request_id=request_id,
            command_id=command_id,
            episode_id=stored.command.episode_id,
            cut_id=stored.command.cut_id,
            format=stored.command.format,
            stage=stage,
            attempt=selection.base.attempt + 1,
            scope="event_retry",
            event_id=event_id,
            parent_acceptance_id=(upstream.acceptance_id if upstream is not None else None),
            base_acceptance_id=selection.base.acceptance_id,
            events=(target,),
            placement_candidates=(
                _event_placement_candidates(view.editorial_context, (target,))
                if stage == "dp"
                else ()
            ),
            feedback=selection.correction.feedback,
            # 修正之所以被發出，常常正是因為素材不對。用登錄那一刻的快照等於
            # 「重挑永遠只能在同一批錯的素材裡重挑」——見 `_live_catalog`。
            worker_asset_refs=(
                tuple(item.reference for item in dp_catalog.items())
                if dp_catalog is not None
                else ()
            ),
            worker_catalog_items=(dp_catalog.items() if dp_catalog is not None else ()),
            components=tuple(
                component
                for component in selection.base.components
                if component.event_id == event_id
            ),
            built_components=tuple(
                component
                for component in selection.base.built_components
                if component.event_id == event_id
            ),
            editorial_context=view.editorial_context if stage == "director" else None,
        )
        history = view.accepted_stage_history or view.accepted_stages
        self._store.save_run(
            _StoredRun(
                command=stored.command,
                view=replace(
                    view,
                    status="pending",
                    outstanding_request=request,
                    accepted_stages=selection.current_prefix,
                    accepted_stage_history=history,
                    derived_asset_request=None,
                    materialization_plan=None,
                    correction=selection.correction,
                    policy_diagnostics=(),
                ),
                worker_catalog=stored.worker_catalog,
                base_release_id=stored.base_release_id,
            )
        )
        return request_id

    def inspect_run(self, command_id: str) -> RunInspection:
        """Project the exact current pre-release authority for one command."""

        if not _is_authoritative_command_id(command_id):
            raise CommandRejectedError(f"opaque authoritative command ID required: {command_id}")
        stored = self._store.load_run(command_id)
        if stored is None:
            raise CommandRejectedError(f"authoritative production run not found: {command_id}")
        view = stored.view
        current_visual = next(
            (stage for stage in view.accepted_stages if stage.stage == "visual_review"),
            None,
        )
        if current_visual is not None or (
            view.outstanding_request is not None
            and view.outstanding_request.stage == "visual_review"
        ):
            build_state = "ready"
        elif view.derived_asset_request is not None:
            build_state = "failed" if _derived_build_failed(view) else "pending"
        else:
            build_state = "not_started"
        return _project_run_inspection(
            run_id=view.run_id,
            command_id=view.command_id,
            episode_id=stored.command.episode_id,
            cut_id=stored.command.cut_id,
            format=stored.command.format,
            status=view.status,
            outstanding_request=view.outstanding_request,
            current_stages=view.accepted_stages,
            stage_history=getattr(view, "accepted_stage_history", view.accepted_stages),
            build_state=build_state,
            policy_diagnostics=getattr(view, "policy_diagnostics", ()),
        )

    def inspect_current(self, episode_id: str) -> FinishedCutInspection:
        return _current_inspection(episode_id, self._current_release_index)

    def _resolve_context(
        self,
        command: ApprovedCutCommand | TargetedRevisionCommand,
        *,
        base_release: FinishedCutRelease | None,
    ) -> EditorialCutContext:
        if self._context_resolver is None:
            raise CommandRejectedError("exact Editorial Cut Context is unavailable")
        if isinstance(command, ApprovedCutCommand):
            editorial_master_id = command.editorial_master_id
            tight_cut_id = command.tight_cut_id
        elif base_release is not None:
            editorial_master_id = base_release.editorial_master_id
            tight_cut_id = base_release.tight_cut_id
        else:
            raise CommandRejectedError("targeted revision has no exact current context")
        context = self._context_resolver.resolve(
            episode_id=command.episode_id,
            cut_id=command.cut_id,
            editorial_master_id=editorial_master_id,
            tight_cut_id=tight_cut_id,
        )
        if (
            context is None
            or context.episode_id != command.episode_id
            or context.cut_id != command.cut_id
            or context.format != command.format
            or context.editorial_master_id != editorial_master_id
            or context.tight_cut_id != tight_cut_id
        ):
            raise CommandRejectedError("exact Editorial Cut Context is unavailable")
        return context

    def _validate_stored_context(
        self,
        context: EditorialCutContext,
        command: ApprovedCutCommand | TargetedRevisionCommand,
        *,
        base_release: FinishedCutRelease | None,
    ) -> EditorialCutContext:
        if isinstance(command, ApprovedCutCommand):
            editorial_master_id = command.editorial_master_id
            tight_cut_id = command.tight_cut_id
        elif base_release is not None:
            editorial_master_id = base_release.editorial_master_id
            tight_cut_id = base_release.tight_cut_id
        else:
            raise CommandRejectedError("targeted revision has no exact current context")
        if (
            context.episode_id != command.episode_id
            or context.cut_id != command.cut_id
            or context.format != command.format
            or context.editorial_master_id != editorial_master_id
            or context.tight_cut_id != tight_cut_id
        ):
            raise CommandRejectedError("persisted Editorial Cut Context identity is invalid")
        return context

    def _policy_for(self, format: str) -> FormatPolicy:
        return self._long_policy if format == "long" else self._short_policy


@dataclass(slots=True)
class _RunState:
    command: ApprovedCutCommand | TargetedRevisionCommand
    view: _ProductionRun
    worker_catalog: WorkerSelectionCatalog
    base_release: FinishedCutRelease | None = None
    editorial_context: EditorialCutContext | None = None
    format_policy: FormatPolicy | None = None
    stock_video_metadata: tuple[StockVideoMetadata, ...] = ()
    derived_asset_builder: DerivedAssetBuilder | None = None
    asset_resolver: AssetResolver | None = None


def _derived_build_failed(view: RunView) -> bool:
    """衍生素材建置已經死在這裡——不是還在跑，也不是已經交給 visual_review。

    `request_correction` 原本用「有 derived_asset_request 且 status 不是 pending」
    當「下游工作還是 current」來擋上游修正。可是那個條件**正好就是建置失敗的條件**
    （見 `inspect_run` 的 build_state），於是唯一需要回上游修的情況被擋死了。

    20260721 punch-L03：hero01 的字卡 23 個字，撐到讀得完要 8.85 秒 > 8.0 秒上限，
    preflight 直接 `visual_placement_duration_exceeded`。長度是 Director 寫的，DP
    改不了；而 `retry-failed-dispatch` 要有 outstanding 的語意請求才動得了，這時
    沒有。整條 run 於是沒有任何合法出路，只剩「重新登錄整支」——而那正是這個檔案
    自己在 `_live_catalog` 裡記下的「蠻力，不是流程」。

    失敗的建置不是 current work，是死路；死路必須讓得開。
    """
    if view.derived_asset_request is None:
        return False
    if any(stage.stage == "visual_review" for stage in view.accepted_stages):
        return False
    outstanding = view.outstanding_request
    if outstanding is not None and outstanding.stage == "visual_review":
        return False
    return view.status == "needs_review"


def _retry_with_live_catalog(
    request: StageRequest,
    request_id: str,
    catalog: WorkerSelectionCatalog,
) -> StageRequest:
    """重試一站 DP 時，換上**現在**的素材目錄，不是失敗那一刻的那份。

    `_live_catalog` 已經讓每次載入 run 都重讀目錄，但 `retry_failed_dispatch` 造重試
    請求時是 `replace(request, request_id=…, attempt=…)`——只換這兩個欄位，於是舊請求
    那份 `worker_catalog_items` 原封不動被複製過去。活目錄只被拿去做 base 驗證，從來
    沒進到送出去的請求裡。

    20260721 punch-L03 的實測：登錄那一刻素材庫是空的（DP 還沒跑，沒人知道要買什麼），
    補買八支之後 retry，packet 的 `catalog` 仍然是 `[]`，DP 連一個 `asset_ref` 都引用
    不到。而 DP 的契約就是 `implement_current_events_using_only_catalog_references`
    ——只能選、不能買——所以空目錄等於整站無解。

    只在 DP 這一站換。Director 與 visual_review 的請求本來就不帶素材目錄，硬塞會讓
    `_worker_packet` 的 `worker_catalog_items` / `worker_asset_refs` 一致性檢查失效。
    """
    bumped = replace(request, request_id=request_id, attempt=request.attempt + 1)
    return _with_live_catalog(bumped, catalog)


def _with_live_catalog(request: StageRequest, catalog: WorkerSelectionCatalog) -> StageRequest:
    """把一個 DP 請求換上**現在**的素材目錄；其他站原樣回傳。

    請求一旦鑄好就把目錄凍在裡面，而 DP 的請求是在 `register_approved_cut` 那一刻
    鑄的——那時候 Director 還沒跑，沒有人知道要買什麼，目錄必然是空的。之後補買的
    素材再多，送出去的還是那份空快照。

    20260721 value-L02：素材庫已經有 52 支，packet 的 `catalog` 仍然是 `[]`。
    `retry-failed-dispatch` 那條路先前已經修過（見 `_retry_with_live_catalog`），
    但**第一次派發**走的是 `_advance_existing`，直接把儲存的請求原樣丟出去，同一個
    缺口在那裡還開著。

    只換 DP。Director 與 visual_review 的請求本來就不帶目錄，硬塞會讓
    `_worker_packet` 的 `worker_catalog_items` / `worker_asset_refs` 一致性檢查失效。
    """
    if request.stage != "dp":
        return request
    items = catalog.items()
    return replace(
        request,
        worker_asset_refs=tuple(item.reference for item in items),
        worker_catalog_items=items,
    )


def _live_catalog(
    resolver: AssetResolver | None,
    snapshot: WorkerSelectionCatalog,
) -> WorkerSelectionCatalog:
    """素材目錄一律**在用到的當下重讀**，不用登錄那一刻的快照。

    2026-09-09 punch-L03 的實測：目錄在 `register_approved_cut` 拍一次快照就再也不更新，
    而 DP 的契約是 `implement_current_events_using_only_catalog_references`——只能選、
    不能買——三個 stage 裡也沒有任何一站負責獲取。三件事疊起來就是：**登錄之後才買的
    素材，這支 cut 永遠看不到**。可是要買什麼，是 Director 跑完才知道的。

    實際後果：L2 的「邊際效應遞減」被迫配了 L1 的相機素材（不同的 beat）然後建置失敗；
    我補買了四支對的素材，發修正、retry、再發修正，DP 目錄始終是同一批 5 支 L1 剩菜。
    L1 之所以出得來，是因為 `authority.json` 裡有約 50 個 run——每買一批就重登錄一次，
    硬換一張新快照。那是蠻力，不是流程。

    **只把送出去的 packet 換成活的還不夠**：第一版修正只動了請求端，DP 於是看得到 25 支、
    也選了新買的那一支，但 `_events_for_acceptance` 收件時仍拿 `run.worker_catalog`
    去 `resolve_dp_reference`，快照裡沒有這支 → `AssetContractError` → `_leave_in_review`。
    沒有 diagnostic、沒有 policy_diagnostics，`advance` 立刻回 needs_review 什麼也不說，
    看起來就像「答案被吃掉了」。`_derived_asset_request` 帶給建置器的
    `worker_catalog_items` 也是同一份快照，會在下一站再爆一次。所以活目錄要放在
    **`_RunState` 建構的那一刻**，讓所有讀取點自然都是活的，而不是逐個站點加特例。

    重讀不會弄髒稽核：每個 StageRequest 都各自存下自己那份 `worker_catalog_items`，
    所以「這一次 DP 是在哪些素材裡挑的」照樣查得到，只是不再被第一次登錄綁死。
    """
    if resolver is None:
        return snapshot
    return resolver.worker_selection_catalog()


class _AggregateContext(Protocol):
    """Narrow authority seam used by the stage aggregate."""

    def dispatch(self, request: StageRequest) -> SemanticDispatchOutcome: ...

    def load_accepted(self, acceptance_id: str) -> AcceptedStage: ...

    def record_accepted(self, accepted: AcceptedStage) -> None: ...

    def mint_id(self, prefix: str) -> str: ...


@dataclass(slots=True)
class _PersistentAggregateContext:
    """Current typed worker port plus restored private acceptance authority."""

    semantic_adapter: SemanticAdapter
    accepted_stages: dict[str, AcceptedStage]

    def dispatch(self, request: StageRequest) -> SemanticDispatchOutcome:
        return self.semantic_adapter.dispatch(request)

    def load_accepted(self, acceptance_id: str) -> AcceptedStage:
        return self.accepted_stages[acceptance_id]

    def record_accepted(self, accepted: AcceptedStage) -> None:
        self.accepted_stages[accepted.acceptance_id] = accepted

    def mint_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid4().hex}"


class InMemoryProductionSystem:
    """Fake system adapter used to exercise the public interface end to end."""

    def __init__(
        self,
        *,
        approved_cuts: Iterable[ApprovedCutCommand] = (),
        asset_resolver: AssetResolver | None = None,
        semantic_adapter: SemanticAdapter | None = None,
    ) -> None:
        self._approved_cuts = {command.command_id: command for command in approved_cuts}
        self._asset_resolver = asset_resolver or InMemoryAssetResolver(())
        self._semantic_adapter = semantic_adapter or InMemorySemanticAdapter()
        self._targeted_revisions: dict[str, TargetedRevisionCommand] = {}
        self._runs: dict[str, _RunState] = {}
        self._accepted_stages: dict[str, AcceptedStage] = {}
        self._state_views: dict[str, dict[str, object]] = {}
        self._releases: dict[str, FinishedCutRelease] = {}
        self._current_releases: dict[str, tuple[FinishedCutRelease, ...]] = {}
        self._sequence = 0

    def _next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{uuid4().hex}"

    def dispatch(self, request: StageRequest) -> SemanticDispatchOutcome:
        return self._semantic_adapter.dispatch(request)

    def load_accepted(self, acceptance_id: str) -> AcceptedStage:
        return self._accepted_stages[acceptance_id]

    def record_accepted(self, accepted: AcceptedStage) -> None:
        self._accepted_stages[accepted.acceptance_id] = accepted

    def mint_id(self, prefix: str) -> str:
        return self._next_id(prefix)

    def respond(
        self,
        request: StageRequest,
        *,
        events: Iterable[EventRecord],
        parent_acceptance_id: str | None | object = _DEFAULT_PARENT,
        **overrides: Any,
    ) -> None:
        """Queue one fake worker proposal for an outstanding request."""

        if not isinstance(self._semantic_adapter, InMemorySemanticAdapter):
            raise TypeError("respond is available only with InMemorySemanticAdapter")
        self._semantic_adapter.respond(
            request,
            events=events,
            parent_acceptance_id=parent_acceptance_id,
            **overrides,
        )

    def replace_state_view(self, run_id: str, forged_view: dict[str, object]) -> None:
        """Simulate an external state.json edit; authority never reads this view."""

        self._state_views[run_id] = dict(forged_view)

    def install_current_release(
        self, view: _ProductionRun, *, release_id: str
    ) -> FinishedCutRelease:
        """Seed the fake current-index adapter from a plan produced by this module."""

        plan = view.materialization_plan
        run = self._runs.get(view.command_id)
        if plan is None or run is None or not isinstance(run.command, ApprovedCutCommand):
            raise ValueError("only a review-ready approved cut can seed fake current")
        artifact = ReleaseArtifact(path="fixture", bytes=0, sha256="0" * 64)
        release = _seal_finished_cut_release(
            release_id=release_id,
            episode_id=run.command.episode_id,
            cut_id=run.command.cut_id,
            format=run.command.format,
            command_id=run.command.command_id,
            run_id=view.run_id,
            editorial_master_id=run.command.editorial_master_id,
            winner_id=run.command.winner_id,
            tight_cut_id=run.command.tight_cut_id,
            director_acceptance_id=plan.director_acceptance_id,
            dp_acceptance_id=plan.dp_acceptance_id,
            visual_acceptance_id=plan.visual_acceptance_id,
            materialization_plan_id=plan.plan_id,
            events=plan.events,
            components=plan.components,
            preview=artifact,
            subtitle=artifact,
            transaction_receipt_id="fixture-committed-transaction",
            rollback_ref="fixture-rollback",
        )
        self._releases[release.release_id] = release
        self._current_releases[release.episode_id] = (release,)
        return release


def advance(command_id: str, *, system: InMemoryProductionSystem) -> _ProductionRun:
    """Idempotently advance one authoritative command."""

    existing = system._runs.get(command_id)
    if existing is not None:
        return _advance_existing(existing, system)
    command = system._approved_cuts.get(command_id) or system._targeted_revisions.get(command_id)
    if command is None:
        raise CommandRejectedError(f"authoritative command not found: {command_id}")
    run_id = system._next_id("run")
    worker_catalog = system._asset_resolver.worker_selection_catalog()
    base_release: FinishedCutRelease | None = None
    events: tuple[EventRecord, ...] = ()
    scope = "full_stage"
    event_id: str | None = None
    parent_acceptance_id: str | None = None
    base_acceptance_id: str | None = None
    feedback: str | None = None
    if isinstance(command, TargetedRevisionCommand):
        base_release = system._releases[command.current_release_id]
        director = system._accepted_stages[base_release.director_acceptance_id]
        events = tuple(event for event in director.events if event.event_id == command.event_id)
        scope = "event_retry"
        event_id = command.event_id
        parent_acceptance_id = director.acceptance_id
        base_acceptance_id = director.acceptance_id
        feedback = command.feedback
    request = StageRequest(
        run_id=run_id,
        request_id=system._next_id("request"),
        command_id=command.command_id,
        episode_id=command.episode_id,
        cut_id=command.cut_id,
        format=command.format,
        stage="director",
        attempt=1,
        scope=scope,
        event_id=event_id,
        parent_acceptance_id=parent_acceptance_id,
        base_acceptance_id=base_acceptance_id,
        events=events,
        feedback=feedback,
    )
    view = _ProductionRun(
        run_id=run_id,
        command_id=command.command_id,
        editorial_context=_in_memory_editorial_context(command, base_release=base_release),
        status="pending",
        outstanding_request=request,
    )
    system._runs[command_id] = _RunState(
        command=command,
        view=view,
        worker_catalog=worker_catalog,
        base_release=base_release,
    )
    return view


def _in_memory_editorial_context(
    command: ApprovedCutCommand | TargetedRevisionCommand,
    *,
    base_release: FinishedCutRelease | None,
) -> EditorialCutContext:
    """Create deterministic fake authority for the legacy in-memory test adapter only."""

    if isinstance(command, ApprovedCutCommand):
        editorial_master_id = command.editorial_master_id
        tight_cut_id = command.tight_cut_id
    elif base_release is not None:
        editorial_master_id = base_release.editorial_master_id
        tight_cut_id = base_release.tight_cut_id
    else:  # pragma: no cover - the caller resolves targeted base authority first
        raise CommandRejectedError("targeted revision has no exact current context")
    return EditorialCutContext(
        episode_id=command.episode_id,
        cut_id=command.cut_id,
        format=command.format,
        editorial_master_id=editorial_master_id,
        tight_cut_id=tight_cut_id,
        duration_sec=1.0,
        source_ranges=(CutSourceRange(0.0, 1.0),),
        cues=(),
    )


def request_revision(
    current_release_ref: str,
    event_id: str,
    feedback: str,
    *,
    system: InMemoryProductionSystem,
) -> str:
    """Mint one current-event revision command after exact-current validation."""

    release = system._releases.get(current_release_ref)
    if release is None or release not in system._current_releases.get(release.episode_id, ()):
        raise CommandRejectedError(f"release is not exact current: {current_release_ref}")
    if event_id not in {event.event_id for event in release.events}:
        raise CommandRejectedError(f"event is not in current release: {event_id}")
    if not feedback.strip():
        raise CommandRejectedError("targeted revision feedback is required")
    command_id = system._next_id("targeted-revision")
    system._targeted_revisions[command_id] = TargetedRevisionCommand(
        command_id=command_id,
        current_release_id=release.release_id,
        episode_id=release.episode_id,
        cut_id=release.cut_id,
        format=release.format,
        event_id=event_id,
        feedback=feedback.strip(),
    )
    return command_id


def inspect_current(
    episode_id: str,
    *,
    system: InMemoryProductionSystem,
) -> tuple[FinishedCutRelease, ...]:
    """Return only the exact current Release index for an episode."""

    return system._current_releases.get(episode_id, ())


def _advance_existing(run: _RunState, aggregate: _AggregateContext) -> _ProductionRun:
    request = run.view.outstanding_request
    if request is None and run.view.derived_asset_request is not None:
        return _advance_derived_build(run, aggregate)
    if (
        request is None
        and run.view.materialization_plan is None
        and run.view.accepted_stages
        and run.view.accepted_stages[-1].stage == "visual_review"
    ):
        return _advance_visual_checkpoint(run, aggregate)
    if request is None:
        return run.view
    # 送出去之前換上現在的素材目錄——請求是登錄那一刻鑄的，那時候櫃子必然是空的。
    request = _with_live_catalog(request, run.worker_catalog)
    if not _request_base_is_current(run, request, aggregate):
        return _leave_in_review(run, request)
    outcome = aggregate.dispatch(request)
    if outcome.state == "pending":
        return run.view
    if outcome.state in {"failed", "indeterminate"}:
        return _leave_in_review(run, request)
    proposal = outcome.proposal
    if proposal is None:  # pragma: no cover - SemanticDispatchOutcome enforces this invariant
        raise RuntimeError("ready semantic dispatch has no proposal")
    if (
        proposal.run_id != request.run_id
        or proposal.schema != STAGE_RESPONSE_SCHEMA
        or request.schema != STAGE_RESPONSE_SCHEMA
        or proposal.request_id != request.request_id
        or proposal.episode_id != request.episode_id
        or proposal.cut_id != request.cut_id
        or proposal.format != request.format
        or proposal.stage != request.stage
        or proposal.attempt != request.attempt
        or proposal.scope != request.scope
        or proposal.event_id != request.event_id
        or proposal.parent_acceptance_id != request.parent_acceptance_id
    ):
        return _leave_in_review(run, request)
    proposal_events = _events_for_acceptance(run, request, proposal)
    if proposal_events is None:
        return _leave_in_review(run, request)

    accepted_events = _merge_retry_events(
        run,
        request,
        proposal_events,
        aggregate,
    )
    accepted_components = _merge_retry_components(run, request, proposal, aggregate)
    accepted_built_components = _merge_retry_built_components(
        request,
        accepted_events,
        aggregate,
    )
    if accepted_built_components is None:
        return _leave_in_review(run, request)
    accepted = _mint_accepted_stage(
        acceptance_id=aggregate.mint_id("acceptance"),
        run_id=proposal.run_id,
        request_id=proposal.request_id,
        stage=proposal.stage,
        attempt=proposal.attempt,
        scope=proposal.scope,
        event_id=proposal.event_id,
        parent_acceptance_id=proposal.parent_acceptance_id,
        events=accepted_events,
        components=accepted_components,
        built_components=accepted_built_components,
    )
    aggregate.record_accepted(accepted)
    accepted_stages = run.view.accepted_stages + (accepted,)
    stage_history = (run.view.accepted_stage_history or run.view.accepted_stages) + (accepted,)
    if accepted.stage == "visual_review":
        run.view = replace(
            run.view,
            status=(
                "pending"
                if all(event.visual_status == "approved" for event in accepted.events)
                else "needs_review"
            ),
            outstanding_request=None,
            accepted_stages=accepted_stages,
            accepted_stage_history=stage_history,
            derived_asset_request=None,
            materialization_plan=None,
            correction=None,
            policy_diagnostics=(),
        )
        return run.view

    if accepted.stage == "dp" and run.editorial_context is not None:
        correction = run.view.correction
        first_downstream_run = (
            request.scope == "event_retry"
            and correction is not None
            and not correction.remaining_base_acceptance_ids
        )
        build_scope: RequestScope = "full_stage" if first_downstream_run else request.scope
        build_event_id = None if build_scope == "full_stage" else request.event_id
        build_events = accepted.events
        if build_scope == "event_retry":
            build_events = tuple(
                event for event in accepted.events if event.event_id == request.event_id
            )
        build_request = (
            None
            if build_scope == "event_retry"
            and all(event.intentional_aroll for event in build_events)
            else _derived_asset_request(
                run=run,
                accepted=accepted,
                events=build_events,
                scope=build_scope,
                event_id=build_event_id,
                aggregate=aggregate,
            )
        )
        next_correction = None if first_downstream_run else correction
        if build_request is not None and build_request.instructions:
            run.view = replace(
                run.view,
                status="pending",
                outstanding_request=None,
                accepted_stages=accepted_stages,
                accepted_stage_history=stage_history,
                derived_asset_request=build_request,
                materialization_plan=None,
                correction=next_correction,
                policy_diagnostics=(),
            )
            return run.view
        visual_retry = _visual_retry_context(
            run=run,
            scope=build_scope,
            event_id=build_event_id,
            correction=next_correction,
            aggregate=aggregate,
        )
        if visual_retry is None:
            return _leave_in_review_from_build(run)
        visual_base_acceptance_id, visual_attempt, visual_feedback, next_correction = visual_retry
        run.view = replace(
            run.view,
            status="pending",
            outstanding_request=_visual_request(
                run=run,
                accepted=accepted,
                events=build_events,
                built_components=(),
                aggregate=aggregate,
                scope=build_scope,
                event_id=build_event_id,
                base_acceptance_id=visual_base_acceptance_id,
                attempt=visual_attempt,
                feedback=visual_feedback,
            ),
            accepted_stages=accepted_stages,
            accepted_stage_history=stage_history,
            derived_asset_request=None,
            materialization_plan=None,
            correction=next_correction,
            policy_diagnostics=(),
        )
        return run.view

    next_stage = "dp" if accepted.stage == "director" else "visual_review"
    next_scope = request.scope
    next_event_id = request.event_id
    next_feedback = request.feedback
    next_base_acceptance_id = None
    correction = run.view.correction
    if request.scope == "event_retry" and correction is not None:
        if correction.remaining_base_acceptance_ids:
            next_base_acceptance_id = correction.remaining_base_acceptance_ids[0]
            correction = _PreReleaseCorrection(
                event_id=correction.event_id,
                feedback=correction.feedback,
                remaining_base_acceptance_ids=correction.remaining_base_acceptance_ids[1:],
            )
        else:
            next_scope = "full_stage"
            next_event_id = None
            next_feedback = None
            correction = None
    elif request.scope == "event_retry" and run.base_release is not None:
        next_base_acceptance_id = {
            "dp": run.base_release.dp_acceptance_id,
            "visual_review": run.base_release.visual_acceptance_id,
        }[next_stage]
    next_events = accepted.events
    next_components = accepted.components
    if next_scope == "event_retry":
        next_events = tuple(
            event for event in accepted.events if event.event_id == request.event_id
        )
        next_components = tuple(
            component for component in accepted.components if component.event_id == request.event_id
        )
    dp_catalog = run.worker_catalog if next_stage == "dp" else None
    next_request = StageRequest(
        run_id=request.run_id,
        request_id=aggregate.mint_id("request"),
        command_id=request.command_id,
        episode_id=request.episode_id,
        cut_id=request.cut_id,
        format=request.format,
        stage=next_stage,
        attempt=1,
        scope=next_scope,
        event_id=next_event_id,
        parent_acceptance_id=accepted.acceptance_id,
        base_acceptance_id=next_base_acceptance_id,
        events=next_events,
        placement_candidates=(
            _event_placement_candidates(run.editorial_context, next_events)
            if next_stage == "dp" and run.editorial_context is not None
            else ()
        ),
        feedback=next_feedback,
        worker_asset_refs=(
            tuple(item.reference for item in dp_catalog.items()) if dp_catalog is not None else ()
        ),
        worker_catalog_items=(dp_catalog.items() if dp_catalog is not None else ()),
        components=next_components,
        editorial_context=None,
    )
    run.view = replace(
        run.view,
        status="pending",
        outstanding_request=next_request,
        accepted_stages=accepted_stages,
        accepted_stage_history=stage_history,
        derived_asset_request=None,
        materialization_plan=None,
        correction=correction,
        policy_diagnostics=(),
    )
    return run.view


def _request_base_is_current(
    run: _RunState,
    request: StageRequest,
    aggregate: _AggregateContext,
) -> bool:
    if (
        request.run_id != run.view.run_id
        or request.command_id != run.command.command_id
        or request.episode_id != run.command.episode_id
        or request.cut_id != run.command.cut_id
        or request.format != run.command.format
    ):
        return False
    current = run.view.accepted_stages
    expected_order = ("director", "dp", "visual_review")
    if not _current_chain_is_exact(run):
        return False
    expected_stage = expected_order[len(current)] if len(current) < len(expected_order) else None
    if request.stage != expected_stage:
        return False
    expected_parent = current[-1].acceptance_id if current else None
    if request.parent_acceptance_id != expected_parent:
        if not (
            not current
            and run.base_release is not None
            and request.stage == "director"
            and request.parent_acceptance_id == run.base_release.director_acceptance_id
        ):
            return False
    if request.scope == "full_stage":
        return request.event_id is None and request.base_acceptance_id is None
    if request.event_id is None:
        return False
    if request.base_acceptance_id is None:
        return run.base_release is not None
    try:
        base = aggregate.load_accepted(request.base_acceptance_id)
    except KeyError:
        return False
    if base.stage != request.stage or request.event_id not in {
        event.event_id for event in base.events
    }:
        return False
    if run.view.correction is not None:
        history = run.view.accepted_stage_history or run.view.accepted_stages
        latest = next(
            (stage for stage in reversed(history) if stage.stage == request.stage),
            None,
        )
        return (
            base.run_id == run.view.run_id
            and latest is not None
            and base.acceptance_id == latest.acceptance_id
            and run.view.correction.event_id == request.event_id
        )
    if run.base_release is None:
        return False
    expected = {
        "director": run.base_release.director_acceptance_id,
        "dp": run.base_release.dp_acceptance_id,
        "visual_review": run.base_release.visual_acceptance_id,
    }[request.stage]
    return base.acceptance_id == expected


def _current_chain_is_exact(run: _RunState) -> bool:
    current = run.view.accepted_stages
    expected_order = ("director", "dp", "visual_review")
    if tuple(stage.stage for stage in current) != expected_order[: len(current)]:
        return False
    if any(stage.run_id != run.view.run_id for stage in current):
        return False
    expected_first_parent = (
        run.base_release.director_acceptance_id
        if run.base_release is not None and isinstance(run.command, TargetedRevisionCommand)
        else None
    )
    if current and current[0].parent_acceptance_id != expected_first_parent:
        return False
    if any(
        current[index].parent_acceptance_id != current[index - 1].acceptance_id
        for index in range(1, len(current))
    ):
        return False
    history = run.view.accepted_stage_history or current
    for stage in current:
        latest = next(
            (candidate for candidate in reversed(history) if candidate.stage == stage.stage),
            None,
        )
        if latest is None or latest != stage:
            return False
    return True


def _leave_in_review(run: _RunState, request: StageRequest) -> _ProductionRun:
    run.view = replace(
        run.view,
        status="needs_review",
        outstanding_request=request,
    )
    return run.view


def _derived_asset_request(
    *,
    run: _RunState,
    accepted: AcceptedStage,
    events: tuple[EventRecord, ...],
    scope: RequestScope,
    event_id: str | None,
    aggregate: _AggregateContext,
    build_request_id: str | None = None,
) -> DerivedAssetBuildRequest:
    width, height = (1920, 1080) if run.command.format == "long" else (1080, 1920)
    instructions: list[DerivedAssetInstruction] = []
    for event in events:
        if event.intentional_aroll:
            continue
        if event.lane is None:
            raise RuntimeError("accepted DP event has no component lane")
        if event.visual_placement is None:
            raise RuntimeError("accepted DP event has no Visual Placement authority")
        placement = event.visual_placement
        geometry = DerivedAssetGeometry(
            target_width=width,
            target_height=height,
            layout_identity=layout_identity(event.implementation_kind),
        )
        recipe_identity = None
        if event.implementation_kind not in _NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS:
            # Recipe identity 必須是**畫面的函數**，只放真的會改變輸出像素的欄位。
            #
            # 原本這裡還放了 run_id、dp_acceptance_id 與 event_id——三個都不影響渲染。
            # 後果是每次重跑（重新登錄、事件改名、修正後重建）都會為**完全一樣的
            # bytes** 鑄出一個新 identity：`find_exact_recipe` 永遠 miss，於是重新
            # render，然後 content-addressed store 因為 digest 早就在了而拒絕
            # 「content digest already has conflicting asset metadata」，被
            # `_render_browser_visual` 吞成 None，再被回報成 `derived_asset_mismatch`
            # ——沒有 diagnostic，看不出是哪一條指令、更看不出原因。
            #
            # 2026-09-09 punch-L03：把 `visual_effect` 事件改名成 hero 之後重跑，
            # 「其實就是重播」那張卡就是這樣卡死的。20260805 那 50 次重新登錄之所以
            # 那麼痛，同一個病根。
            #
            # 絕對時間也拿掉：渲染器只吃時長，兩張同字同版位同長度的卡本來就會產出
            # 一樣的 bytes，讓它們共用同一份媒體才是對的。
            recipe_payload = {
                "semantic_kind": event.semantic_kind,
                "implementation_kind": event.implementation_kind,
                "lane": event.lane,
                "display": event.display,
                "show_sec": round(placement.t1 - placement.t0, 6),
                "source_asset_ref": event.asset_ref,
                "geometry": {
                    "target_width": geometry.target_width,
                    "target_height": geometry.target_height,
                    "layout_identity": geometry.layout_identity,
                },
            }
            digest = hashlib.sha256(
                json.dumps(
                    recipe_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            recipe_identity = f"recipe-sha256:{digest}"
        instructions.append(
            DerivedAssetInstruction(
                component_id=f"component:{event.event_id}",
                event_id=event.event_id,
                semantic_kind=event.semantic_kind,
                implementation_kind=event.implementation_kind,
                lane=event.lane,
                display=event.display,
                t0=placement.t0,
                t1=placement.t1,
                source_asset_ref=event.asset_ref,
                geometry=geometry,
                recipe_identity=recipe_identity,
            )
        )
    return DerivedAssetBuildRequest(
        build_request_id=build_request_id or aggregate.mint_id("derived-build"),
        run_id=run.view.run_id,
        command_id=run.command.command_id,
        episode_id=run.command.episode_id,
        cut_id=run.command.cut_id,
        format=run.command.format,
        dp_acceptance_id=accepted.acceptance_id,
        scope=scope,
        event_id=event_id,
        instructions=tuple(instructions),
        worker_catalog_items=run.worker_catalog.items(),
    )


def _advance_derived_build(
    run: _RunState,
    aggregate: _AggregateContext,
) -> _ProductionRun:
    request = run.view.derived_asset_request
    if request is None or run.derived_asset_builder is None:
        return run.view
    if not _derived_request_is_current(run, request, aggregate):
        return _leave_in_review_from_build(run)
    result = run.derived_asset_builder.build(request)
    if not _derived_result_matches(request, result, asset_resolver=run.asset_resolver):
        run.view = replace(
            run.view,
            status="needs_review",
            outstanding_request=None,
            derived_asset_request=request,
        )
        return run.view
    if result.status != "ready":
        run.view = replace(
            run.view,
            status="needs_review",
            outstanding_request=None,
            derived_asset_request=request,
        )
        return run.view
    dp_accepted = next(
        stage
        for stage in run.view.accepted_stages
        if stage.acceptance_id == request.dp_acceptance_id
    )
    event_ids = {instruction.event_id for instruction in request.instructions}
    events = tuple(event for event in dp_accepted.events if event.event_id in event_ids)
    visual_retry = _visual_retry_context(
        run=run,
        scope=request.scope,
        event_id=request.event_id,
        correction=run.view.correction,
        aggregate=aggregate,
    )
    if visual_retry is None:
        return _leave_in_review_from_build(run)
    visual_base_acceptance_id, visual_attempt, visual_feedback, next_correction = visual_retry
    run.view = replace(
        run.view,
        status="pending",
        outstanding_request=_visual_request(
            run=run,
            accepted=dp_accepted,
            events=events,
            built_components=result.assets,
            aggregate=aggregate,
            scope=request.scope,
            event_id=request.event_id,
            base_acceptance_id=visual_base_acceptance_id,
            attempt=visual_attempt,
            feedback=visual_feedback,
        ),
        derived_asset_request=None,
        correction=next_correction,
    )
    return run.view


def _derived_request_is_current(
    run: _RunState,
    request: DerivedAssetBuildRequest,
    aggregate: _AggregateContext,
) -> bool:
    if request.scope not in {"full_stage", "event_retry"}:
        return False
    if not _current_chain_is_exact(run) or len(run.view.accepted_stages) != 2:
        return False
    dp = run.view.accepted_stages[-1]
    if dp.stage != "dp" or request.dp_acceptance_id != dp.acceptance_id:
        return False
    if request.scope == "full_stage":
        if request.event_id is not None or run.view.correction is not None:
            return False
        events = dp.events
    else:
        if request.event_id is None:
            return False
        events = tuple(event for event in dp.events if event.event_id == request.event_id)
        if len(events) != 1:
            return False
        if run.base_release is None and (
            run.view.correction is None
            or run.view.correction.event_id != request.event_id
            or not run.view.correction.remaining_base_acceptance_ids
        ):
            return False
        if (
            _visual_retry_context(
                run=run,
                scope=request.scope,
                event_id=request.event_id,
                correction=run.view.correction,
                aggregate=aggregate,
            )
            is None
        ):
            return False
    expected = _derived_asset_request(
        run=run,
        accepted=dp,
        events=events,
        scope=request.scope,
        event_id=request.event_id,
        aggregate=aggregate,
        build_request_id=request.build_request_id,
    )
    return request == expected


def _visual_retry_context(
    *,
    run: _RunState,
    scope: RequestScope,
    event_id: str | None,
    correction: _PreReleaseCorrection | None,
    aggregate: _AggregateContext,
) -> tuple[str | None, int, str | None, _PreReleaseCorrection | None] | None:
    if correction is not None and correction.remaining_base_acceptance_ids:
        base_id = correction.remaining_base_acceptance_ids[0]
        try:
            base = aggregate.load_accepted(base_id)
        except KeyError:
            return None
        history = run.view.accepted_stage_history or run.view.accepted_stages
        latest_visual = next(
            (stage for stage in reversed(history) if stage.stage == "visual_review"),
            None,
        )
        if (
            base.run_id != run.view.run_id
            or base.stage != "visual_review"
            or latest_visual is None
            or latest_visual.acceptance_id != base.acceptance_id
            or event_id is None
            or event_id != correction.event_id
            or event_id not in {event.event_id for event in base.events}
        ):
            return None
        return (
            base.acceptance_id,
            base.attempt + 1,
            correction.feedback,
            _PreReleaseCorrection(
                event_id=correction.event_id,
                feedback=correction.feedback,
                remaining_base_acceptance_ids=correction.remaining_base_acceptance_ids[1:],
            ),
        )
    if scope == "event_retry" and run.base_release is not None:
        try:
            base = aggregate.load_accepted(run.base_release.visual_acceptance_id)
        except KeyError:
            return None
        if base.stage != "visual_review" or event_id is None:
            return None
        return base.acceptance_id, base.attempt + 1, None, correction
    if scope == "event_retry":
        return None
    return None, 1, None, correction


# 每個字卡實作的版面版本。改設計就要 bump，否則新舊兩版會共用同一個 recipe
# 快取鍵，畫面改了卻拿到舊的渲染檔。
# - fullscreen_transition v4：滿版紙紋轉場（B2 定版）
# - hero_title v2：2026-09-08 從 ADR-066 自創的 compact_paper 單行藥丸，改回頻道
#   定版的 punch_card_wide tier1 + style:paper（錯位雙行紙卡、96px、手繪橘底線、
#   落在說話者負空間）。手冊：.claude/skills/longform-cut/SKILL.md「Hero 大字卡」
# 版位版本住在 _projection.LAYOUT_VERSIONS（唯一真相來源），這裡不再自己維護一份。


def _leave_in_review_from_build(run: _RunState) -> _ProductionRun:
    run.view = replace(
        run.view,
        status="needs_review",
        outstanding_request=None,
    )
    return run.view


def _advance_visual_checkpoint(
    run: _RunState,
    aggregate: _AggregateContext,
) -> _ProductionRun:
    if not _current_chain_is_exact(run) or len(run.view.accepted_stages) != 3:
        run.view = replace(run.view, status="needs_review", materialization_plan=None)
        return run.view
    visual = run.view.accepted_stages[-1]
    if visual.stage != "visual_review":
        return run.view
    if any(event.visual_status != "approved" for event in visual.events):
        if run.view.status == "needs_review":
            return run.view
        run.view = replace(run.view, status="needs_review")
        return run.view
    if run.editorial_context is not None and run.format_policy is not None:
        projected_components = _project_event_components(
            visual.events,
            visual.components,
            built_components=visual.built_components,
        )
        policy_decision = run.format_policy.validate(
            CutPolicyInput(
                context=run.editorial_context,
                components=projected_components,
                stock_video_metadata=run.stock_video_metadata,
            )
        )
        # `accepted_with_warnings` 照樣往下走：那些是品味與政策，不是壞成品。
        # 分級理由見 `_policy` 的模組 docstring（修修 2026-09-10 裁決）。
        if policy_decision.status == "needs_review":
            run.view = replace(
                run.view,
                status="needs_review",
                policy_diagnostics=policy_decision.diagnostics,
            )
            return run.view
        # 警告要跟著 plan 一起留在 view 上——收據裡看得到，修修在 timeline 上自己判斷。
        policy_notices = policy_decision.diagnostics
    else:
        projected_components = _project_components(visual)
        policy_notices = ()
    current_by_stage = {stage.stage: stage for stage in run.view.accepted_stages}
    plan = _mint_materialization_plan(
        plan_id=aggregate.mint_id("plan"),
        run_id=run.view.run_id,
        command_id=run.command.command_id,
        episode_id=run.command.episode_id,
        cut_id=run.command.cut_id,
        format=run.command.format,
        director_acceptance_id=current_by_stage["director"].acceptance_id,
        dp_acceptance_id=current_by_stage["dp"].acceptance_id,
        visual_acceptance_id=visual.acceptance_id,
        events=visual.events,
        components=projected_components,
        duration_sec=(
            run.editorial_context.duration_sec if run.editorial_context is not None else 0.0
        ),
    )
    run.view = replace(
        run.view,
        status="review_ready",
        materialization_plan=plan,
        policy_diagnostics=policy_notices,
    )
    return run.view


def _derived_result_matches(
    request: DerivedAssetBuildRequest,
    result: DerivedAssetBuildResult,
    *,
    asset_resolver: AssetResolver | None,
) -> bool:
    if (
        result.build_request_id != request.build_request_id
        or result.dp_acceptance_id != request.dp_acceptance_id
    ):
        return False
    if result.status != "ready":
        return True
    if asset_resolver is None:
        return False
    if len(result.assets) != len(request.instructions):
        return False
    for instruction, asset in zip(request.instructions, result.assets, strict=True):
        if (
            asset.component_id != instruction.component_id
            or asset.event_id != instruction.event_id
            or asset.source_asset_ref != instruction.source_asset_ref
            or asset.recipe_identity != instruction.recipe_identity
        ):
            return False
        if instruction.recipe_identity is None and (
            asset.final_asset_ref != instruction.source_asset_ref
        ):
            return False
        if instruction.implementation_kind == "person_inset" and (
            asset.final_asset_ref == instruction.source_asset_ref
        ):
            return False
        try:
            final = asset_resolver.resolve_active_asset(asset.final_asset_ref)
            if asset.inspection_ref is not None:
                asset_resolver.resolve_active_asset(asset.inspection_ref)
        except AssetContractError:
            return False
        if final.record.kind is not _FINAL_ASSET_KIND_BY_IMPLEMENTATION.get(
            instruction.implementation_kind
        ):
            return False
        # 生成素材只要求「它是某個配方算出來的」，不要求 store record 上的 identity
        # 就是這一個：兩個配方算出同樣的 bytes 時 store 會回既有那筆
        # （`ActiveAssetStore.publish` 的去重路徑），逐字相等在那條路上永遠不成立。
        # 建置器交出來的 `asset.recipe_identity` 已經在上面比對過，那才是「這一份
        # 媒體是為這條指令建的」的宣告。
        # 直通素材（stock/photo）沒有配方，record 也必須沒有——那是兩種不同的東西。
        if instruction.recipe_identity is None:
            if final.record.recipe_identity is not None:
                return False
        elif final.record.recipe_identity is None:
            return False
    return True


def _visual_request(
    *,
    run: _RunState,
    accepted: AcceptedStage,
    events: tuple[EventRecord, ...],
    built_components: tuple[BuiltComponentAsset, ...],
    aggregate: _AggregateContext,
    scope: RequestScope | None = None,
    event_id: str | None = None,
    base_acceptance_id: str | None = None,
    attempt: int = 1,
    feedback: str | None = None,
) -> StageRequest:
    current_feedback = (
        feedback
        if feedback is not None
        else (run.command.feedback if isinstance(run.command, TargetedRevisionCommand) else None)
    )
    return StageRequest(
        run_id=run.view.run_id,
        request_id=aggregate.mint_id("request"),
        command_id=run.command.command_id,
        episode_id=run.command.episode_id,
        cut_id=run.command.cut_id,
        format=run.command.format,
        stage="visual_review",
        attempt=attempt,
        scope=scope or accepted.scope,
        event_id=event_id if scope is not None else accepted.event_id,
        parent_acceptance_id=accepted.acceptance_id,
        base_acceptance_id=base_acceptance_id,
        events=events,
        feedback=current_feedback,
        built_components=built_components,
    )


def _events_for_acceptance(
    run: _RunState,
    request: StageRequest,
    proposal: StageProposal,
) -> tuple[EventRecord, ...] | None:
    if not proposal.events:
        return None
    ids = [event.event_id for event in proposal.events]
    if len(ids) != len(set(ids)):
        return None
    if request.scope == "event_retry":
        if ids != [request.event_id] or len(request.events) != 1:
            return None
    if not _proposal_components_are_valid(request, proposal):
        return None
    if request.stage == "director":
        if all(isinstance(event, DirectorEventProposal) for event in proposal.events):
            if run.editorial_context is None or proposal.components:
                return None
            derived_events: list[EventRecord] = []
            for event in proposal.events:
                if (
                    not event.event_id
                    or not event.master_cue_ids
                    or not event.intent.strip()
                    or not event.display.strip()
                    or not _is_active_semantic_kind(event.semantic_kind)
                    # `intentional_aroll` 與 semantic_kind 必須互相同意。DP 那一關
                    # 硬性要求「intentional_aroll 的事件其 semantic_kind 也是
                    # intentional_aroll」，所以 Director 交出 hero_title +
                    # intentional_aroll=true 這種組合時，DP 無論回什麼都會被拒——
                    # 錯在 Director，卻由 DP 反覆撞牆，而且沒有任何訊息說得出原因。
                    # 2026-09-08 蘇予昕 punch-L04 就是這樣連退 11 次。
                    or event.intentional_aroll != (event.semantic_kind == "intentional_aroll")
                ):
                    return None
                try:
                    anchor = run.editorial_context.derive_anchor(event.master_cue_ids)
                except ValueError:
                    return None
                derived_events.append(
                    EventRecord(
                        event_id=event.event_id,
                        master_cue_ids=anchor.master_cue_ids,
                        text_hash=anchor.text_hash,
                        intent=event.intent,
                        text=anchor.text,
                        t0=anchor.t0,
                        t1=anchor.t1,
                        section_id=anchor.section_id,
                        display=event.display,
                        semantic_kind=event.semantic_kind,
                        intentional_aroll=event.intentional_aroll,
                    )
                )
            accepted = tuple(derived_events)
            if request.scope == "event_retry":
                base = request.events[0]
                event = accepted[0]
                if event.master_cue_ids != base.master_cue_ids or event.text_hash != base.text_hash:
                    return None
            return accepted
        if run.editorial_context is not None:
            return None
        if not all(isinstance(event, EventRecord) for event in proposal.events):
            return None
        if not all(
            event.event_id
            and event.master_cue_ids
            and event.text_hash
            and event.intent
            and event.asset_ref is None
            and event.visual_status is None
            for event in proposal.events
        ):
            return None
        if request.scope == "event_retry":
            base = request.events[0]
            event = proposal.events[0]
            if event.master_cue_ids != base.master_cue_ids or event.text_hash != base.text_hash:
                return None
        return tuple(proposal.events)

    if request.stage == "dp" and all(
        isinstance(event, DPEventProposal) for event in proposal.events
    ):
        if proposal.components:
            return None
        expected = {event.event_id: event for event in request.events}
        if ids != list(expected):
            return None
        selected: list[EventRecord] = []
        for event in proposal.events:
            base = expected[event.event_id]
            if base.intentional_aroll:
                if (
                    base.semantic_kind != "intentional_aroll"
                    or event.implementation_kind != "intentional_aroll"
                    or event.lane is not None
                    or event.asset_ref is not None
                    or event.placement_cue_ids
                ):
                    return None
                placement = None
            else:
                if (
                    event.lane is None
                    or (base.semantic_kind, event.implementation_kind, event.lane)
                    not in _ALLOWED_PROJECTION
                ):
                    return None
                expected_asset_kind = _ASSET_KIND_BY_IMPLEMENTATION.get(event.implementation_kind)
                if expected_asset_kind is None:
                    if event.asset_ref is not None:
                        return None
                else:
                    if event.asset_ref is None:
                        return None
                    try:
                        item = run.worker_catalog.resolve_dp_reference(event.asset_ref)
                    except AssetContractError:
                        return None
                    if item.kind is not expected_asset_kind:
                        return None
                if run.editorial_context is None:
                    return None
                try:
                    placement = run.editorial_context.derive_visual_placement(
                        semantic_cue_ids=base.master_cue_ids,
                        placement_cue_ids=event.placement_cue_ids,
                        semantic_kind=base.semantic_kind,
                        min_show_sec=readable_floor_sec(event.implementation_kind, base.display),
                    )
                except ValueError:
                    return None
            selected.append(
                replace(
                    base,
                    implementation_kind=event.implementation_kind,
                    lane=event.lane,
                    asset_ref=event.asset_ref,
                    visual_status=None,
                    visual_placement=placement,
                )
            )
        return tuple(selected)

    if request.stage == "visual_review" and all(
        isinstance(event, VisualEventProposal) for event in proposal.events
    ):
        if proposal.components:
            return None
        expected = {event.event_id: event for event in request.events}
        if ids != list(expected) or any(
            event.status not in {"approved", "failed"} for event in proposal.events
        ):
            return None
        return tuple(
            replace(expected[event.event_id], visual_status=event.status)
            for event in proposal.events
        )

    if run.editorial_context is not None or not all(
        isinstance(event, EventRecord) for event in proposal.events
    ):
        return None
    expected = {event.event_id: event for event in request.events}
    if set(ids) != set(expected):
        return None
    for event in proposal.events:
        base = expected[event.event_id]
        if (
            event.master_cue_ids != base.master_cue_ids
            or event.text_hash != base.text_hash
            or event.intent != base.intent
        ):
            return None
        if request.stage == "dp":
            if not event.asset_ref or event.visual_status is not None:
                return None
            try:
                run.worker_catalog.resolve_dp_reference(event.asset_ref)
            except AssetContractError:
                return None
        elif event.asset_ref != base.asset_ref or event.visual_status != "approved":
            return None
    return tuple(proposal.events)


def _proposal_components_are_valid(
    request: StageRequest,
    proposal: StageProposal,
) -> bool:
    components = proposal.components
    component_ids = [component.component_id for component in components]
    if len(component_ids) != len(set(component_ids)):
        return False
    event_ids = {event.event_id for event in proposal.events}
    if any(
        not component.component_id
        or component.event_id not in event_ids
        or not component.semantic_kind.strip()
        or not component.implementation_kind.strip()
        or (
            component.semantic_kind,
            component.implementation_kind,
            component.lane,
        )
        not in _ALLOWED_PROJECTION
        or not component.display.strip()
        or not math.isfinite(component.t0)
        or not math.isfinite(component.t1)
        or component.t0 < 0
        or component.t0 >= component.t1
        for component in components
    ):
        return False
    if request.stage != "director" and components != request.components:
        return False
    if request.scope == "event_retry" and any(
        component.event_id != request.event_id for component in components
    ):
        return False
    return True


def _merge_retry_events(
    run: _RunState,
    request: StageRequest,
    proposal_events: tuple[EventRecord, ...],
    aggregate: _AggregateContext,
) -> tuple[EventRecord, ...]:
    if request.scope == "full_stage":
        return proposal_events
    if request.base_acceptance_id is not None and request.event_id is not None:
        base = aggregate.load_accepted(request.base_acceptance_id)
        if base.stage != request.stage:
            raise RuntimeError("event retry base stage is stale")
        return _merge_exact_event(
            base.events,
            proposal_events,
            event_id=request.event_id,
        )
    if run.base_release is None or request.event_id is None:
        raise RuntimeError("event retry has no current base release")
    acceptance_id = {
        "director": run.base_release.director_acceptance_id,
        "dp": run.base_release.dp_acceptance_id,
        "visual_review": run.base_release.visual_acceptance_id,
    }[request.stage]
    base = aggregate.load_accepted(acceptance_id)
    replacement = proposal_events[0]
    return tuple(
        replacement if event.event_id == request.event_id else event for event in base.events
    )


def _merge_retry_components(
    run: _RunState,
    request: StageRequest,
    proposal: StageProposal,
    aggregate: _AggregateContext,
) -> tuple[ComponentProposal, ...]:
    if request.scope == "full_stage":
        return proposal.components
    if request.base_acceptance_id is not None and request.event_id is not None:
        base = aggregate.load_accepted(request.base_acceptance_id)
        if base.stage != request.stage:
            raise RuntimeError("component retry base stage is stale")
        return _replace_component_group(
            base.components,
            proposal.components,
            event_id=request.event_id,
        )
    if run.base_release is None or request.event_id is None:
        raise RuntimeError("event retry has no current base release")
    acceptance_id = {
        "director": run.base_release.director_acceptance_id,
        "dp": run.base_release.dp_acceptance_id,
        "visual_review": run.base_release.visual_acceptance_id,
    }[request.stage]
    base = aggregate.load_accepted(acceptance_id)
    return _replace_component_group(
        base.components,
        proposal.components,
        event_id=request.event_id,
    )


def _replace_component_group(
    base: tuple[ComponentProposal, ...],
    replacement: tuple[ComponentProposal, ...],
    *,
    event_id: str,
) -> tuple[ComponentProposal, ...]:
    if any(component.event_id != event_id for component in replacement):
        raise RuntimeError("component retry returned another event")
    target_count = sum(component.event_id == event_id for component in base)
    if len(replacement) != target_count:
        raise RuntimeError("component retry changed the target component cardinality")
    merged: list[ComponentProposal] = []
    inserted = False
    for component in base:
        if component.event_id != event_id:
            merged.append(component)
        elif not inserted:
            merged.extend(replacement)
            inserted = True
    return tuple(merged)


def _merge_retry_built_components(
    request: StageRequest,
    accepted_events: tuple[EventRecord, ...],
    aggregate: _AggregateContext,
) -> tuple[BuiltComponentAsset, ...] | None:
    if request.stage != "visual_review":
        return ()
    expected_ids = tuple(event.event_id for event in accepted_events if not event.intentional_aroll)
    incoming_ids = tuple(component.event_id for component in request.built_components)
    if len(incoming_ids) != len(set(incoming_ids)):
        return None
    if request.scope == "full_stage":
        if not request.built_components and all(
            not event.semantic_kind for event in accepted_events
        ):
            return ()
        if set(incoming_ids) != set(expected_ids):
            return None
        incoming = {component.event_id: component for component in request.built_components}
        return tuple(incoming[event_id] for event_id in expected_ids)
    if request.base_acceptance_id is None or request.event_id is None:
        return None
    base = aggregate.load_accepted(request.base_acceptance_id)
    if base.stage != "visual_review":
        return None
    target = next(
        (event for event in accepted_events if event.event_id == request.event_id),
        None,
    )
    if target is None:
        return None
    if not target.semantic_kind and not request.built_components and not base.built_components:
        return ()
    expected_incoming = () if target.intentional_aroll else (request.event_id,)
    if incoming_ids != expected_incoming:
        return None
    replacement = {component.event_id: component for component in request.built_components}
    base_ids = tuple(component.event_id for component in base.built_components)
    expected_base_ids = tuple(
        event.event_id for event in base.events if not event.intentional_aroll
    )
    if base_ids != expected_base_ids:
        return None
    base_by_event = {component.event_id: component for component in base.built_components}
    merged: list[BuiltComponentAsset] = []
    for event_id in expected_ids:
        component = replacement.get(event_id, base_by_event.get(event_id))
        if component is None:
            return None
        merged.append(component)
    return tuple(merged)


def _project_components(accepted: AcceptedStage) -> tuple[ProjectedComponent, ...]:
    return _project_event_components(accepted.events, accepted.components)


def _project_event_components(
    events: tuple[EventRecord, ...],
    components: tuple[ComponentProposal, ...],
    *,
    built_components: tuple[BuiltComponentAsset, ...] = (),
) -> tuple[ProjectedComponent, ...]:
    if events and all(event.semantic_kind and event.implementation_kind for event in events):
        built_by_event = {component.event_id: component for component in built_components}
        expected_event_ids = {event.event_id for event in events if not event.intentional_aroll}
        if set(built_by_event) != expected_event_ids:
            raise RuntimeError("Visual request lacks exact built component assets")
        return tuple(
            _mint_projected_component(
                component_id=built_by_event[event.event_id].component_id,
                event_id=event.event_id,
                semantic_kind=event.semantic_kind,
                implementation_kind=event.implementation_kind,
                lane=event.lane,
                display=event.display,
                t0=_required_visual_placement(event).t0,
                t1=_required_visual_placement(event).t1,
                asset_ref=built_by_event[event.event_id].final_asset_ref,
            )
            for event in events
            if not event.intentional_aroll and event.lane is not None
        )
    built_by_event = {component.event_id: component for component in built_components}
    if {component.event_id for component in components} != set(built_by_event):
        if components:
            raise RuntimeError("component projection requires exact built component assets")
    return tuple(
        _mint_projected_component(
            component_id=component.component_id,
            event_id=component.event_id,
            semantic_kind=component.semantic_kind,
            implementation_kind=component.implementation_kind,
            lane=component.lane,
            display=component.display,
            t0=component.t0,
            t1=component.t1,
            asset_ref=built_by_event[component.event_id].final_asset_ref,
        )
        for component in components
    )


def _required_visual_placement(event: EventRecord) -> VisualPlacement:
    if event.visual_placement is None:
        raise RuntimeError("component projection requires Visual Placement authority")
    return event.visual_placement


def _event_placement_candidates(
    context: EditorialCutContext | None,
    events: tuple[EventRecord, ...],
) -> tuple[EventPlacementCandidates, ...]:
    if context is None:
        raise RuntimeError("DP placement candidates require current Editorial Cut Context")
    cue_by_id = {cue.cue_id: cue for cue in context.cues}
    candidates: list[EventPlacementCandidates] = []
    for event in events:
        if event.intentional_aroll:
            continue
        try:
            cues = tuple(cue_by_id[cue_id] for cue_id in event.master_cue_ids)
        except KeyError as error:
            raise RuntimeError("DP placement candidate is outside current context") from error
        candidates.append(EventPlacementCandidates(event_id=event.event_id, cues=cues))
    return tuple(candidates)


def _public_run_view(run: _ProductionRun) -> RunView:
    request = run.outstanding_request
    return RunView(
        run_id=run.run_id,
        command_id=run.command_id,
        status=run.status,
        current_stage=request.stage if request is not None else None,
        scope=request.scope if request is not None else None,
        event_id=request.event_id if request is not None else None,
    )


def _current_inspection(episode_id: str, index: CurrentReleaseIndex) -> FinishedCutInspection:
    """Project the exact current index for one episode.

    Shared so an inbound read-only adapter cannot drift from what
    ``FinishedCutProduction.inspect_current`` returns.
    """
    try:
        releases = index.inspect_current(episode_id)
    except ReleaseLifecycleError as error:
        if error.reason == "missing":
            return FinishedCutInspection(
                episode_id=episode_id,
                state="missing",
                error_code="current_release_missing",
            )
        return FinishedCutInspection(
            episode_id=episode_id,
            state="invalid",
            error_code="current_release_invalid",
        )
    if not releases:
        return FinishedCutInspection(
            episode_id=episode_id,
            state="missing",
            error_code="current_release_missing",
        )
    return FinishedCutInspection(
        episode_id=episode_id,
        state="ready",
        cuts=tuple(_cut_view(release) for release in releases),
    )


def _cut_view(release: FinishedCutRelease) -> CutView:
    return CutView(
        release_id=release.release_id,
        cut_id=release.cut_id,
        format=release.format,
        preview=_artifact_view(release.preview),
        subtitle=_artifact_view(release.subtitle),
        events=tuple(
            EventView(
                event_id=event.event_id,
                master_cue_ids=event.master_cue_ids,
                text=event.text,
                text_hash=event.text_hash,
                t0=event.t0,
                t1=event.t1,
                section_id=event.section_id,
                intent=event.intent,
                display=event.display,
                semantic_kind=event.semantic_kind,
                implementation_kind=event.implementation_kind,
                lane=event.lane,
                asset_ref=event.asset_ref,
                visual_status=event.visual_status,
                intentional_aroll=event.intentional_aroll,
            )
            for event in release.events
        ),
        components=tuple(
            ComponentView(
                component_id=component.component_id,
                event_id=component.event_id,
                semantic_kind=component.semantic_kind,
                implementation_kind=component.implementation_kind,
                lane=component.lane,
                display=component.display,
                t0=component.t0,
                t1=component.t1,
                asset_ref=component.asset_ref,
            )
            for component in release.components
        ),
    )


def _artifact_view(artifact: ReleaseArtifact) -> ArtifactView:
    return ArtifactView(
        reference=artifact.path,
        bytes=artifact.bytes,
        sha256=artifact.sha256,
        duration_sec=artifact.duration_sec,
        probe=artifact.probe,
    )
