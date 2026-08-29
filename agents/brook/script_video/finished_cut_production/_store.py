"""Private persistent authority store for Finished Cut Production."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import uuid4

from ._assets import AssetKind, WorkerCatalogItem, WorkerSelectionCatalog
from ._commands import ApprovedCutCommand, Format, TargetedRevisionCommand
from ._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
    _mint_visual_placement,
)
from ._correction import _PreReleaseCorrection
from ._derived_assets import (
    BuiltComponentAsset,
    DerivedAssetBuildRequest,
    DerivedAssetGeometry,
    DerivedAssetInstruction,
)
from ._policy import PolicyDiagnostic, PolicyDiagnosticCode
from ._records import (
    AcceptedStage,
    ComponentLane,
    ComponentProposal,
    DirectorEventProposal,
    DPEventProposal,
    EventPlacementCandidates,
    EventRecord,
    FinishedCutRelease,
    MaterializationPlan,
    ProjectedComponent,
    RequestScope,
    StageName,
    StageProposal,
    StageRequest,
    Status,
    VisualEventProposal,
    _mint_accepted_stage,
    _mint_materialization_plan,
    _mint_projected_component,
    _ProductionRun,
)
from ._semantic import SemanticDispatchOutcome, SemanticRequestState

_STORE_SCHEMA = "nakama.finished-cut-production-store.v1"
_SEMANTIC_DISPATCH_SCHEMA = "nakama.finished-cut-semantic-dispatch.v1"
_CORE_REQUEST_ID_RE = re.compile(r"^request-[0-9a-f]{32}$")


class ProductionStoreError(RuntimeError):
    """The private authority store is missing, malformed, or incomplete."""


class SemanticDispatchStoreError(RuntimeError):
    """The private semantic claim/outcome authority is inconsistent."""


class _FilesystemSemanticDispatchLedger:
    """One-file-per-request durable claim ledger with atomic exclusive creation."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root) / "semantic-dispatch"
        self._root.mkdir(parents=True, exist_ok=True)

    def claim(self, request: StageRequest) -> bool:
        path = self._path_for(request.request_id)
        document = {
            "schema": _SEMANTIC_DISPATCH_SCHEMA,
            "state": "claimed",
            "request": _request_to_dict(request),
            "outcome": None,
        }
        try:
            self._write_exclusive(path, document)
        except FileExistsError:
            self.load(request)
            return False
        return True

    def load(self, request: StageRequest) -> SemanticDispatchOutcome | None:
        document = self._read(request.request_id)
        if document is None:
            return None
        if document["request"] != _request_to_dict(request):
            raise SemanticDispatchStoreError(
                "semantic request ID is already claimed by a different request"
            )
        return self._outcome_from_document(request.request_id, document)

    def outcome_for(self, request_id: str) -> SemanticDispatchOutcome | None:
        document = self._read(request_id)
        if document is None:
            return None
        return self._outcome_from_document(request_id, document)

    def dispatch_state(self, request_id: str) -> SemanticRequestState:
        document = self._read(request_id)
        if document is None:
            return "unclaimed"
        return "claimed" if document["state"] == "claimed" else "completed"

    def complete(
        self,
        request: StageRequest,
        outcome: SemanticDispatchOutcome,
    ) -> None:
        if outcome.request_id != request.request_id or outcome.state == "pending":
            raise SemanticDispatchStoreError("semantic dispatch outcome is not terminal/current")
        path = self._path_for(request.request_id)
        document = self._read(request.request_id)
        if document is None or document["request"] != _request_to_dict(request):
            raise SemanticDispatchStoreError("semantic dispatch claim is missing or mismatched")
        if document["state"] == "completed":
            if document["outcome"] == _outcome_to_dict(outcome):
                return
            raise SemanticDispatchStoreError("semantic dispatch already has another outcome")
        completed = {
            "schema": _SEMANTIC_DISPATCH_SCHEMA,
            "state": "completed",
            "request": document["request"],
            "outcome": _outcome_to_dict(outcome),
        }
        self._atomic_write(path, completed)

    def _outcome_from_document(
        self,
        request_id: str,
        document: dict[str, object],
    ) -> SemanticDispatchOutcome:
        if document["state"] == "claimed":
            return SemanticDispatchOutcome(
                request_id=request_id,
                state="indeterminate",
                reason_code="semantic_dispatch_indeterminate",
                diagnostic="request was claimed without a durable terminal outcome",
            )
        outcome = _outcome_from_dict(document["outcome"])
        if outcome.request_id != request_id:
            raise SemanticDispatchStoreError("semantic dispatch outcome belongs to another request")
        return outcome

    def _read(self, request_id: str) -> dict[str, object] | None:
        path = self._path_for(request_id)
        if not path.exists():
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SemanticDispatchStoreError("semantic dispatch ledger is unreadable") from error
        if (
            not isinstance(document, dict)
            or document.get("schema") != _SEMANTIC_DISPATCH_SCHEMA
            or document.get("state") not in {"claimed", "completed"}
            or not isinstance(document.get("request"), dict)
            or set(document) != {"schema", "state", "request", "outcome"}
            or (document["state"] == "claimed" and document["outcome"] is not None)
            or (document["state"] == "completed" and not isinstance(document["outcome"], dict))
        ):
            raise SemanticDispatchStoreError("semantic dispatch ledger contract is invalid")
        return document

    def _path_for(self, request_id: str) -> Path:
        if _CORE_REQUEST_ID_RE.fullmatch(request_id) is None:
            raise SemanticDispatchStoreError("semantic request ID is not core-created")
        return self._root / f"{request_id}.json"

    @staticmethod
    def _encoded(document: dict[str, object]) -> bytes:
        return (
            json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")

    def _write_exclusive(self, path: Path, document: dict[str, object]) -> None:
        with path.open("xb") as handle:
            handle.write(self._encoded(document))
            handle.flush()
            os.fsync(handle.fileno())

    def _atomic_write(self, path: Path, document: dict[str, object]) -> None:
        temporary = self._root / f".{path.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(self._encoded(document))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class ApprovedCutStore(Protocol):
    """Upstream authority seam that resolves only opaque approved-cut IDs."""

    def resolve(self, command_id: str) -> ApprovedCutCommand | None: ...


class InMemoryApprovedCutStore:
    """Fixture adapter for the upstream approved-cut authority seam."""

    def __init__(self, commands: Iterable[ApprovedCutCommand]) -> None:
        self._commands = {command.command_id: command for command in commands}

    def resolve(self, command_id: str) -> ApprovedCutCommand | None:
        return self._commands.get(command_id)


class CurrentReleaseIndex(Protocol):
    """Exact-current Release seam used by inspection and targeted feedback."""

    def resolve_exact_current(self, release_id: str) -> FinishedCutRelease | None: ...

    def inspect_current(self, episode_id: str) -> tuple[FinishedCutRelease, ...]: ...


class InMemoryCurrentReleaseIndex:
    """Fixture adapter that never resolves a historical Release."""

    def __init__(self) -> None:
        self._current_by_episode: dict[str, tuple[FinishedCutRelease, ...]] = {}

    def publish(self, releases: Iterable[FinishedCutRelease]) -> None:
        current = tuple(releases)
        if not current or len({release.episode_id for release in current}) != 1:
            raise ValueError("current Release fixture must contain exactly one episode")
        self._current_by_episode[current[0].episode_id] = current

    def resolve_exact_current(self, release_id: str) -> FinishedCutRelease | None:
        return next(
            (
                release
                for releases in self._current_by_episode.values()
                for release in releases
                if release.release_id == release_id
            ),
            None,
        )

    def inspect_current(self, episode_id: str) -> tuple[FinishedCutRelease, ...]:
        return self._current_by_episode.get(episode_id, ())


@dataclass(frozen=True, slots=True)
class _StoredRun:
    command: ApprovedCutCommand | TargetedRevisionCommand
    view: _ProductionRun
    worker_catalog: WorkerSelectionCatalog
    base_release_id: str | None = None


class _FilesystemProductionStore:
    """Atomic, reopenable store hidden behind FinishedCutProduction."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._path = self._root / "authority.json"

    @contextmanager
    def command_lock(self, command_id: str) -> Iterator[None]:
        """Serialize one command's claim/check/write transition across processes."""

        try:
            from filelock import FileLock, Timeout
        except ImportError as error:  # pragma: no cover - declared core dependency
            raise ProductionStoreError(
                "cross-process production command locking requires filelock"
            ) from error
        lock_root = self._root / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        if not command_id:
            raise ProductionStoreError("production command lock requires an identity")
        try:
            with FileLock(str(lock_root / "authority-json.lock")).acquire(timeout=30):
                yield
        except Timeout as error:
            raise ProductionStoreError("timed out acquiring production command lock") from error

    def load_run(self, command_id: str) -> _StoredRun | None:
        payload = self._read_payload()
        row = payload["runs"].get(command_id)
        return None if row is None else _run_from_row(row)

    def create_run(
        self,
        command: ApprovedCutCommand | TargetedRevisionCommand,
        view: _ProductionRun,
        worker_catalog: WorkerSelectionCatalog,
        *,
        base_release_id: str | None = None,
    ) -> None:
        payload = self._read_payload()
        if command.command_id in payload["runs"]:
            raise ProductionStoreError("ProductionRun already exists")
        payload["runs"][command.command_id] = {
            "command": _command_to_dict(command),
            "command_kind": _command_kind(command),
            "view": _view_to_dict(view),
            "worker_catalog": _catalog_to_list(worker_catalog),
            "base_release_id": base_release_id,
        }
        self._atomic_write(payload)

    def save_run(self, run: _StoredRun) -> None:
        payload = self._read_payload()
        if run.command.command_id not in payload["runs"]:
            raise ProductionStoreError("ProductionRun does not exist")
        payload["runs"][run.command.command_id] = {
            "command": _command_to_dict(run.command),
            "command_kind": _command_kind(run.command),
            "view": _view_to_dict(run.view),
            "worker_catalog": _catalog_to_list(run.worker_catalog),
            "base_release_id": run.base_release_id,
        }
        self._atomic_write(payload)

    def save_targeted_revision(self, command: TargetedRevisionCommand) -> None:
        payload = self._read_payload()
        revisions = payload["targeted_revisions"]
        if command.command_id in revisions:
            raise ProductionStoreError("TargetedRevision command already exists")
        revisions[command.command_id] = _targeted_revision_to_dict(command)
        self._atomic_write(payload)

    def load_targeted_revision(self, command_id: str) -> TargetedRevisionCommand | None:
        payload = self._read_payload()
        row = payload["targeted_revisions"].get(command_id)
        return _targeted_revision_from_dict(row) if row is not None else None

    def accepted_stages(self) -> tuple[AcceptedStage, ...]:
        # Decode straight from the payload already in hand.  Resolving each run
        # through ``load_run`` re-read and re-parsed the whole authority store
        # once per run, so this cost one parse per run plus one, every call.
        #
        # Decode *only* the accepted stages, too.  Building the whole view also
        # rebuilt each run's derived-asset instructions, and those re-run the
        # active-projection contract — so one historical run holding a since
        # retired projection (20260805 has two `supporting_title` instructions
        # from before that lane was retired) made every later lookup raise, and
        # with it every revision.  History is allowed to contain retired
        # projections; only building from them is forbidden.
        accepted: list[AcceptedStage] = []
        for row in self._read_payload()["runs"].values():
            view = row["view"]
            if not isinstance(view, dict):
                raise ProductionStoreError("persisted ProductionRun view is invalid")
            stages = view.get("accepted_stage_history") or view["accepted_stages"]
            if not isinstance(stages, list):
                raise ProductionStoreError("persisted AcceptedStage collection is invalid")
            accepted.extend(_accepted_from_dict(stage) for stage in stages)
        return tuple(accepted)

    def load_accepted(self, acceptance_id: str) -> AcceptedStage | None:
        return next(
            (stage for stage in self.accepted_stages() if stage.acceptance_id == acceptance_id),
            None,
        )

    def _read_payload(self) -> dict[str, object]:
        if not self._path.exists():
            return {"schema": _STORE_SCHEMA, "runs": {}, "targeted_revisions": {}}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProductionStoreError("authority store is unreadable") from error
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != _STORE_SCHEMA
            or not isinstance(payload.get("runs"), dict)
            or not isinstance(payload.get("targeted_revisions"), dict)
            or set(payload) != {"schema", "runs", "targeted_revisions"}
        ):
            raise ProductionStoreError("authority store contract is invalid")
        return payload

    def _atomic_write(self, payload: dict[str, object]) -> None:
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        temporary = self._root / f".{self._path.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


def _view_to_dict(view: _ProductionRun) -> dict[str, object]:
    request = view.outstanding_request
    return {
        "run_id": view.run_id,
        "command_id": view.command_id,
        "editorial_context": _context_to_dict(view.editorial_context),
        "status": view.status,
        "outstanding_request": _request_to_dict(request) if request is not None else None,
        "accepted_stages": [_accepted_to_dict(stage) for stage in view.accepted_stages],
        "accepted_stage_history": [
            _accepted_to_dict(stage) for stage in view.accepted_stage_history
        ],
        "derived_asset_request": (
            _derived_build_request_to_dict(view.derived_asset_request)
            if view.derived_asset_request is not None
            else None
        ),
        "materialization_plan": (
            _plan_to_dict(view.materialization_plan)
            if view.materialization_plan is not None
            else None
        ),
        "correction": (
            _correction_to_dict(view.correction) if view.correction is not None else None
        ),
        "policy_diagnostics": [
            _policy_diagnostic_to_dict(diagnostic) for diagnostic in view.policy_diagnostics
        ],
    }


def _request_to_dict(request: StageRequest) -> dict[str, object]:
    return {
        "run_id": request.run_id,
        "request_id": request.request_id,
        "command_id": request.command_id,
        "episode_id": request.episode_id,
        "cut_id": request.cut_id,
        "format": request.format,
        "stage": request.stage,
        "attempt": request.attempt,
        "scope": request.scope,
        "event_id": request.event_id,
        "parent_acceptance_id": request.parent_acceptance_id,
        "base_acceptance_id": request.base_acceptance_id,
        "events": [_event_to_dict(event) for event in request.events],
        "placement_candidates": [
            {
                "event_id": candidate.event_id,
                "cues": [
                    {
                        "cue_id": cue.cue_id,
                        "text": cue.text,
                        "t0": cue.t0,
                        "t1": cue.t1,
                        "section_id": cue.section_id,
                    }
                    for cue in candidate.cues
                ],
            }
            for candidate in request.placement_candidates
        ],
        "feedback": request.feedback,
        "worker_asset_refs": list(request.worker_asset_refs),
        "worker_catalog_items": [
            {
                "reference": item.reference,
                "kind": item.kind.value,
                "visual_summary": item.visual_summary,
                "width": item.width,
                "height": item.height,
                "duration_sec": item.duration_sec,
            }
            for item in request.worker_catalog_items
        ],
        "components": [_component_proposal_to_dict(component) for component in request.components],
        "built_components": [
            _built_component_to_dict(component) for component in request.built_components
        ],
        "editorial_context": (
            _context_to_dict(request.editorial_context)
            if request.editorial_context is not None
            else None
        ),
        "schema": request.schema,
    }


def _view_from_dict(value: object) -> _ProductionRun:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted ProductionRun view is invalid")
    try:
        request_value = value["outstanding_request"]
        request = _request_from_dict(request_value) if request_value is not None else None
        accepted_value = value["accepted_stages"]
        if not isinstance(accepted_value, list):
            raise ProductionStoreError("persisted AcceptedStage collection is invalid")
        history_value = value.get("accepted_stage_history", accepted_value)
        if not isinstance(history_value, list):
            raise ProductionStoreError("persisted AcceptedStage history is invalid")
        diagnostics_value = value.get("policy_diagnostics", [])
        if not isinstance(diagnostics_value, list):
            raise ProductionStoreError("persisted policy diagnostics are invalid")
        plan_value = value["materialization_plan"]
        derived_request_value = value["derived_asset_request"]
        return _ProductionRun(
            run_id=str(value["run_id"]),
            command_id=str(value["command_id"]),
            editorial_context=_context_from_dict(value["editorial_context"]),
            status=cast(Status, value["status"]),
            outstanding_request=request,
            accepted_stages=tuple(_accepted_from_dict(item) for item in accepted_value),
            accepted_stage_history=tuple(_accepted_from_dict(item) for item in history_value),
            derived_asset_request=(
                _derived_build_request_from_dict(derived_request_value)
                if derived_request_value is not None
                else None
            ),
            materialization_plan=(_plan_from_dict(plan_value) if plan_value is not None else None),
            correction=(
                _correction_from_dict(value["correction"])
                if value.get("correction") is not None
                else None
            ),
            policy_diagnostics=tuple(
                _policy_diagnostic_from_dict(item) for item in diagnostics_value
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted ProductionRun view fields are invalid") from error


def _approved_cut_to_dict(command: ApprovedCutCommand) -> dict[str, str]:
    return {
        "command_id": command.command_id,
        "episode_id": command.episode_id,
        "cut_id": command.cut_id,
        "format": command.format,
        "editorial_master_id": command.editorial_master_id,
        "winner_id": command.winner_id,
        "tight_cut_id": command.tight_cut_id,
    }


def _targeted_revision_to_dict(command: TargetedRevisionCommand) -> dict[str, str]:
    return {
        "command_id": command.command_id,
        "current_release_id": command.current_release_id,
        "episode_id": command.episode_id,
        "cut_id": command.cut_id,
        "format": command.format,
        "event_id": command.event_id,
        "feedback": command.feedback,
    }


def _command_to_dict(
    command: ApprovedCutCommand | TargetedRevisionCommand,
) -> dict[str, str]:
    if isinstance(command, ApprovedCutCommand):
        return _approved_cut_to_dict(command)
    return _targeted_revision_to_dict(command)


def _command_kind(command: ApprovedCutCommand | TargetedRevisionCommand) -> str:
    return "approved_cut" if isinstance(command, ApprovedCutCommand) else "targeted_revision"


def _run_from_row(value: object) -> _StoredRun:
    """Decode one persisted ProductionRun row without re-reading the store."""
    if not isinstance(value, dict) or set(value) != {
        "command",
        "command_kind",
        "view",
        "worker_catalog",
        "base_release_id",
    }:
        raise ProductionStoreError("persisted ProductionRun row is invalid")
    command_kind = value["command_kind"]
    if command_kind == "approved_cut":
        command: ApprovedCutCommand | TargetedRevisionCommand = _approved_cut_from_dict(
            value["command"]
        )
    elif command_kind == "targeted_revision":
        command = _targeted_revision_from_dict(value["command"])
    else:
        raise ProductionStoreError("persisted ProductionRun command kind is invalid")
    return _StoredRun(
        command=command,
        view=_view_from_dict(value["view"]),
        worker_catalog=_catalog_from_list(value["worker_catalog"]),
        base_release_id=cast(str | None, value["base_release_id"]),
    )


def _approved_cut_from_dict(value: object) -> ApprovedCutCommand:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted ApprovedCut command is invalid")
    try:
        return ApprovedCutCommand(
            command_id=str(value["command_id"]),
            episode_id=str(value["episode_id"]),
            cut_id=str(value["cut_id"]),
            format=cast(Format, value["format"]),
            editorial_master_id=str(value["editorial_master_id"]),
            winner_id=str(value["winner_id"]),
            tight_cut_id=str(value["tight_cut_id"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted ApprovedCut command fields are invalid") from error


def _targeted_revision_from_dict(value: object) -> TargetedRevisionCommand:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted TargetedRevision command is invalid")
    try:
        return TargetedRevisionCommand(
            command_id=str(value["command_id"]),
            current_release_id=str(value["current_release_id"]),
            episode_id=str(value["episode_id"]),
            cut_id=str(value["cut_id"]),
            format=cast(Format, value["format"]),
            event_id=str(value["event_id"]),
            feedback=str(value["feedback"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError(
            "persisted TargetedRevision command fields are invalid"
        ) from error


def _catalog_to_list(catalog: WorkerSelectionCatalog) -> list[dict[str, object]]:
    return [
        {
            "reference": item.reference,
            "kind": item.kind.value,
            "visual_summary": item.visual_summary,
            "width": item.width,
            "height": item.height,
            "duration_sec": item.duration_sec,
        }
        for item in catalog.items()
    ]


def _catalog_from_list(value: object) -> WorkerSelectionCatalog:
    if not isinstance(value, list):
        raise ProductionStoreError("persisted Worker Selection Catalog is invalid")
    try:
        return WorkerSelectionCatalog(
            WorkerCatalogItem(
                reference=str(item["reference"]),
                kind=AssetKind(item["kind"]),
                visual_summary=cast(str | None, item["visual_summary"]),
                width=cast(int | None, item["width"]),
                height=cast(int | None, item["height"]),
                duration_sec=cast(float | None, item["duration_sec"]),
            )
            for item in value
            if isinstance(item, dict)
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError(
            "persisted Worker Selection Catalog fields are invalid"
        ) from error


def _derived_geometry_to_dict(geometry: DerivedAssetGeometry) -> dict[str, object]:
    return {
        "target_width": geometry.target_width,
        "target_height": geometry.target_height,
        "layout_identity": geometry.layout_identity,
    }


def _derived_geometry_from_dict(value: object) -> DerivedAssetGeometry:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted derived asset geometry is invalid")
    try:
        return DerivedAssetGeometry(
            target_width=int(value["target_width"]),
            target_height=int(value["target_height"]),
            layout_identity=str(value["layout_identity"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted derived asset geometry fields are invalid") from error


def _derived_instruction_to_dict(instruction: DerivedAssetInstruction) -> dict[str, object]:
    return {
        "component_id": instruction.component_id,
        "event_id": instruction.event_id,
        "semantic_kind": instruction.semantic_kind,
        "implementation_kind": instruction.implementation_kind,
        "lane": instruction.lane,
        "display": instruction.display,
        "t0": instruction.t0,
        "t1": instruction.t1,
        "source_asset_ref": instruction.source_asset_ref,
        "geometry": _derived_geometry_to_dict(instruction.geometry),
        "recipe_identity": instruction.recipe_identity,
    }


def _derived_instruction_from_dict(value: object) -> DerivedAssetInstruction:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted derived asset instruction is invalid")
    try:
        return DerivedAssetInstruction(
            component_id=str(value["component_id"]),
            event_id=str(value["event_id"]),
            semantic_kind=str(value["semantic_kind"]),
            implementation_kind=str(value["implementation_kind"]),
            lane=cast(ComponentLane, value["lane"]),
            display=str(value["display"]),
            t0=float(value["t0"]),
            t1=float(value["t1"]),
            source_asset_ref=cast(str | None, value["source_asset_ref"]),
            geometry=_derived_geometry_from_dict(value["geometry"]),
            recipe_identity=cast(str | None, value["recipe_identity"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError(
            "persisted derived asset instruction fields are invalid"
        ) from error


def _derived_build_request_to_dict(request: DerivedAssetBuildRequest) -> dict[str, object]:
    return {
        "build_request_id": request.build_request_id,
        "run_id": request.run_id,
        "command_id": request.command_id,
        "episode_id": request.episode_id,
        "cut_id": request.cut_id,
        "format": request.format,
        "dp_acceptance_id": request.dp_acceptance_id,
        "scope": request.scope,
        "event_id": request.event_id,
        "instructions": [
            _derived_instruction_to_dict(instruction) for instruction in request.instructions
        ],
        "worker_catalog_items": [
            {
                "reference": item.reference,
                "kind": item.kind.value,
                "visual_summary": item.visual_summary,
                "width": item.width,
                "height": item.height,
                "duration_sec": item.duration_sec,
            }
            for item in request.worker_catalog_items
        ],
    }


def _derived_build_request_from_dict(value: object) -> DerivedAssetBuildRequest:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted derived asset build request is invalid")
    try:
        catalog = _catalog_from_list(value["worker_catalog_items"])
        return DerivedAssetBuildRequest(
            build_request_id=str(value["build_request_id"]),
            run_id=str(value["run_id"]),
            command_id=str(value["command_id"]),
            episode_id=str(value["episode_id"]),
            cut_id=str(value["cut_id"]),
            format=cast(Format, value["format"]),
            dp_acceptance_id=str(value["dp_acceptance_id"]),
            scope=cast(RequestScope, value["scope"]),
            event_id=cast(str | None, value["event_id"]),
            instructions=tuple(
                _derived_instruction_from_dict(item) for item in value["instructions"]
            ),
            worker_catalog_items=catalog.items(),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError(
            "persisted derived asset build request fields are invalid"
        ) from error


def _built_component_to_dict(component: BuiltComponentAsset) -> dict[str, object]:
    return {
        "component_id": component.component_id,
        "event_id": component.event_id,
        "source_asset_ref": component.source_asset_ref,
        "final_asset_ref": component.final_asset_ref,
        "inspection_ref": component.inspection_ref,
        "recipe_identity": component.recipe_identity,
    }


def _built_component_from_dict(value: object) -> BuiltComponentAsset:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted built component is invalid")
    try:
        return BuiltComponentAsset(
            component_id=str(value["component_id"]),
            event_id=str(value["event_id"]),
            source_asset_ref=cast(str | None, value["source_asset_ref"]),
            final_asset_ref=str(value["final_asset_ref"]),
            inspection_ref=cast(str | None, value["inspection_ref"]),
            recipe_identity=cast(str | None, value["recipe_identity"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted built component fields are invalid") from error


def _request_from_dict(value: object) -> StageRequest:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted current request is invalid")
    try:
        events_value = value["events"]
        if not isinstance(events_value, list):
            raise ProductionStoreError("persisted current request events are invalid")
        return StageRequest(
            run_id=str(value["run_id"]),
            request_id=str(value["request_id"]),
            command_id=str(value["command_id"]),
            episode_id=str(value["episode_id"]),
            cut_id=str(value["cut_id"]),
            format=cast(Format, value["format"]),
            stage=cast(StageName, value["stage"]),
            attempt=int(value["attempt"]),
            scope=cast(RequestScope, value["scope"]),
            event_id=cast(str | None, value["event_id"]),
            parent_acceptance_id=cast(str | None, value["parent_acceptance_id"]),
            base_acceptance_id=cast(str | None, value.get("base_acceptance_id")),
            events=tuple(_event_from_dict(event) for event in events_value),
            placement_candidates=tuple(
                EventPlacementCandidates(
                    event_id=str(item["event_id"]),
                    cues=tuple(
                        CueAnchor(
                            cue_id=str(cue["cue_id"]),
                            text=str(cue["text"]),
                            t0=float(cue["t0"]),
                            t1=float(cue["t1"]),
                            section_id=cast(str | None, cue["section_id"]),
                        )
                        for cue in item["cues"]
                    ),
                )
                for item in value.get("placement_candidates", [])
            ),
            feedback=cast(str | None, value["feedback"]),
            worker_asset_refs=tuple(str(item) for item in value["worker_asset_refs"]),
            worker_catalog_items=_catalog_from_list(value["worker_catalog_items"]).items(),
            components=tuple(_component_proposal_from_dict(item) for item in value["components"]),
            built_components=tuple(
                _built_component_from_dict(item) for item in value["built_components"]
            ),
            editorial_context=(
                _context_from_dict(value["editorial_context"])
                if value["editorial_context"] is not None
                else None
            ),
            schema=str(value["schema"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted current request fields are invalid") from error


def _event_to_dict(event: EventRecord) -> dict[str, object]:
    placement = event.visual_placement
    return {
        "event_id": event.event_id,
        "master_cue_ids": list(event.master_cue_ids),
        "text_hash": event.text_hash,
        "intent": event.intent,
        "asset_ref": event.asset_ref,
        "visual_status": event.visual_status,
        "text": event.text,
        "t0": event.t0,
        "t1": event.t1,
        "section_id": event.section_id,
        "display": event.display,
        "semantic_kind": event.semantic_kind,
        "intentional_aroll": event.intentional_aroll,
        "implementation_kind": event.implementation_kind,
        "lane": event.lane,
        "visual_placement": (
            {
                "placement_cue_ids": list(placement.placement_cue_ids),
                "t0": placement.t0,
                "t1": placement.t1,
                "section_id": placement.section_id,
            }
            if placement is not None
            else None
        ),
    }


def _event_from_dict(value: object) -> EventRecord:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted event is invalid")
    try:
        placement_value = value.get("visual_placement")
        placement = None
        if placement_value is not None:
            if not isinstance(placement_value, dict):
                raise ValueError("persisted Visual Placement is invalid")
            placement = _mint_visual_placement(
                placement_cue_ids=tuple(str(item) for item in placement_value["placement_cue_ids"]),
                t0=float(placement_value["t0"]),
                t1=float(placement_value["t1"]),
                section_id=cast(str | None, placement_value["section_id"]),
            )
        return EventRecord(
            event_id=str(value["event_id"]),
            master_cue_ids=tuple(str(item) for item in value["master_cue_ids"]),
            text_hash=str(value["text_hash"]),
            intent=str(value["intent"]),
            asset_ref=cast(str | None, value["asset_ref"]),
            visual_status=cast(str | None, value["visual_status"]),
            text=str(value["text"]),
            t0=float(value["t0"]),
            t1=float(value["t1"]),
            section_id=cast(str | None, value["section_id"]),
            display=str(value["display"]),
            semantic_kind=str(value["semantic_kind"]),
            intentional_aroll=bool(value["intentional_aroll"]),
            implementation_kind=str(value["implementation_kind"]),
            lane=cast(ComponentLane | None, value["lane"]),
            visual_placement=placement,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted event fields are invalid") from error


def _accepted_to_dict(stage: AcceptedStage) -> dict[str, object]:
    return {
        "acceptance_id": stage.acceptance_id,
        "run_id": stage.run_id,
        "request_id": stage.request_id,
        "stage": stage.stage,
        "attempt": stage.attempt,
        "scope": stage.scope,
        "event_id": stage.event_id,
        "parent_acceptance_id": stage.parent_acceptance_id,
        "events": [_event_to_dict(event) for event in stage.events],
        "components": [_component_proposal_to_dict(component) for component in stage.components],
        "built_components": [
            _built_component_to_dict(component) for component in stage.built_components
        ],
    }


def _accepted_from_dict(value: object) -> AcceptedStage:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted AcceptedStage is invalid")
    try:
        events_value = value["events"]
        if not isinstance(events_value, list):
            raise ProductionStoreError("persisted AcceptedStage events are invalid")
        return _mint_accepted_stage(
            acceptance_id=str(value["acceptance_id"]),
            run_id=str(value["run_id"]),
            request_id=str(value["request_id"]),
            stage=cast(StageName, value["stage"]),
            attempt=int(value["attempt"]),
            scope=cast(RequestScope, value["scope"]),
            event_id=cast(str | None, value["event_id"]),
            parent_acceptance_id=cast(str | None, value["parent_acceptance_id"]),
            events=tuple(_event_from_dict(event) for event in events_value),
            components=tuple(_component_proposal_from_dict(item) for item in value["components"]),
            built_components=tuple(
                _built_component_from_dict(item) for item in value.get("built_components", [])
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted AcceptedStage fields are invalid") from error


def _correction_to_dict(correction: _PreReleaseCorrection) -> dict[str, object]:
    return {
        "event_id": correction.event_id,
        "feedback": correction.feedback,
        "remaining_base_acceptance_ids": list(correction.remaining_base_acceptance_ids),
    }


def _correction_from_dict(value: object) -> _PreReleaseCorrection:
    if not isinstance(value, dict) or set(value) != {
        "event_id",
        "feedback",
        "remaining_base_acceptance_ids",
    }:
        raise ProductionStoreError("persisted pre-release correction is invalid")
    try:
        return _PreReleaseCorrection(
            event_id=str(value["event_id"]),
            feedback=str(value["feedback"]),
            remaining_base_acceptance_ids=tuple(
                str(item) for item in value["remaining_base_acceptance_ids"]
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted pre-release correction fields are invalid") from error


def _policy_diagnostic_to_dict(diagnostic: PolicyDiagnostic) -> dict[str, object]:
    return {
        "code": diagnostic.code,
        "message": diagnostic.message,
        "component_ids": list(diagnostic.component_ids),
        "section_ids": list(diagnostic.section_ids),
        "asset_refs": list(diagnostic.asset_refs),
    }


def _policy_diagnostic_from_dict(value: object) -> PolicyDiagnostic:
    if not isinstance(value, dict) or set(value) != {
        "code",
        "message",
        "component_ids",
        "section_ids",
        "asset_refs",
    }:
        raise ProductionStoreError("persisted policy diagnostic is invalid")
    try:
        return PolicyDiagnostic(
            code=cast(PolicyDiagnosticCode, value["code"]),
            message=str(value["message"]),
            component_ids=tuple(str(item) for item in value["component_ids"]),
            section_ids=tuple(str(item) for item in value["section_ids"]),
            asset_refs=tuple(str(item) for item in value["asset_refs"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted policy diagnostic fields are invalid") from error


def _plan_to_dict(plan: MaterializationPlan) -> dict[str, object]:
    return {
        "plan_id": plan.plan_id,
        "run_id": plan.run_id,
        "command_id": plan.command_id,
        "episode_id": plan.episode_id,
        "cut_id": plan.cut_id,
        "format": plan.format,
        "director_acceptance_id": plan.director_acceptance_id,
        "dp_acceptance_id": plan.dp_acceptance_id,
        "visual_acceptance_id": plan.visual_acceptance_id,
        "events": [_event_to_dict(event) for event in plan.events],
        "components": [_projected_to_dict(component) for component in plan.components],
    }


def _plan_from_dict(value: object) -> MaterializationPlan:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted MaterializationPlan is invalid")
    try:
        events_value = value["events"]
        if not isinstance(events_value, list):
            raise ProductionStoreError("persisted MaterializationPlan events are invalid")
        return _mint_materialization_plan(
            plan_id=str(value["plan_id"]),
            run_id=str(value["run_id"]),
            command_id=str(value["command_id"]),
            episode_id=str(value["episode_id"]),
            cut_id=str(value["cut_id"]),
            format=cast(Format, value["format"]),
            director_acceptance_id=str(value["director_acceptance_id"]),
            dp_acceptance_id=str(value["dp_acceptance_id"]),
            visual_acceptance_id=str(value["visual_acceptance_id"]),
            events=tuple(_event_from_dict(event) for event in events_value),
            components=tuple(_projected_from_dict(item) for item in value["components"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted MaterializationPlan fields are invalid") from error


def _component_proposal_to_dict(component: ComponentProposal) -> dict[str, object]:
    return {
        "component_id": component.component_id,
        "event_id": component.event_id,
        "semantic_kind": component.semantic_kind,
        "implementation_kind": component.implementation_kind,
        "lane": component.lane,
        "display": component.display,
        "t0": component.t0,
        "t1": component.t1,
    }


def _component_proposal_from_dict(value: object) -> ComponentProposal:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted component proposal is invalid")
    try:
        return ComponentProposal(
            component_id=str(value["component_id"]),
            event_id=str(value["event_id"]),
            semantic_kind=str(value["semantic_kind"]),
            implementation_kind=str(value["implementation_kind"]),
            lane=cast(ComponentLane, value["lane"]),
            display=str(value["display"]),
            t0=float(value["t0"]),
            t1=float(value["t1"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted component proposal fields are invalid") from error


def _projected_to_dict(component: ProjectedComponent) -> dict[str, object]:
    return {
        **_component_proposal_to_dict(
            ComponentProposal(
                component.component_id,
                component.event_id,
                component.semantic_kind,
                component.implementation_kind,
                component.lane,
                component.display,
                component.t0,
                component.t1,
            )
        ),
        "asset_ref": component.asset_ref,
    }


def _projected_from_dict(value: object) -> ProjectedComponent:
    proposal = _component_proposal_from_dict(value)
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted projected component is invalid")
    return _mint_projected_component(
        component_id=proposal.component_id,
        event_id=proposal.event_id,
        semantic_kind=proposal.semantic_kind,
        implementation_kind=proposal.implementation_kind,
        lane=proposal.lane,
        display=proposal.display,
        t0=proposal.t0,
        t1=proposal.t1,
        asset_ref=cast(str | None, value.get("asset_ref")),
    )


def _context_to_dict(context: EditorialCutContext) -> dict[str, object]:
    return {
        "episode_id": context.episode_id,
        "cut_id": context.cut_id,
        "format": context.format,
        "editorial_master_id": context.editorial_master_id,
        "tight_cut_id": context.tight_cut_id,
        "duration_sec": context.duration_sec,
        "source_ranges": [
            {"t0": source_range.t0, "t1": source_range.t1} for source_range in context.source_ranges
        ],
        "cues": [
            {
                "cue_id": cue.cue_id,
                "text": cue.text,
                "t0": cue.t0,
                "t1": cue.t1,
                "section_id": cue.section_id,
            }
            for cue in context.cues
        ],
        "sections": [
            {
                "section_id": section.section_id,
                "chapter_title": section.chapter_title,
                "t0": section.t0,
                "transition_before": section.transition_before,
                "transition_title": section.transition_title,
            }
            for section in context.sections
        ],
        "editorial_feedback": list(context.editorial_feedback),
    }


def _context_from_dict(value: object) -> EditorialCutContext:
    if not isinstance(value, dict):
        raise ProductionStoreError("persisted Editorial Cut Context is invalid")
    try:
        return EditorialCutContext(
            episode_id=str(value["episode_id"]),
            cut_id=str(value["cut_id"]),
            format=cast(Format, value["format"]),
            editorial_master_id=str(value["editorial_master_id"]),
            tight_cut_id=str(value["tight_cut_id"]),
            duration_sec=float(value["duration_sec"]),
            source_ranges=tuple(
                CutSourceRange(float(item["t0"]), float(item["t1"]))
                for item in value["source_ranges"]
            ),
            cues=tuple(
                CueAnchor(
                    cue_id=str(item["cue_id"]),
                    text=str(item["text"]),
                    t0=float(item["t0"]),
                    t1=float(item["t1"]),
                    section_id=cast(str | None, item["section_id"]),
                )
                for item in value["cues"]
            ),
            sections=tuple(
                CanonicalSection(
                    section_id=str(item["section_id"]),
                    chapter_title=str(item["chapter_title"]),
                    t0=float(item["t0"]),
                    transition_before=bool(item["transition_before"]),
                    transition_title=cast(str | None, item["transition_title"]),
                )
                for item in value["sections"]
            ),
            editorial_feedback=tuple(str(item) for item in value["editorial_feedback"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionStoreError("persisted Editorial Cut Context fields are invalid") from error


def _proposal_event_to_dict(
    event: DirectorEventProposal | DPEventProposal | VisualEventProposal | EventRecord,
) -> dict[str, object]:
    if isinstance(event, DirectorEventProposal):
        return {
            "kind": "director",
            "event_id": event.event_id,
            "master_cue_ids": list(event.master_cue_ids),
            "intent": event.intent,
            "display": event.display,
            "semantic_kind": event.semantic_kind,
            "intentional_aroll": event.intentional_aroll,
        }
    if isinstance(event, DPEventProposal):
        return {
            "kind": "dp",
            "event_id": event.event_id,
            "implementation_kind": event.implementation_kind,
            "lane": event.lane,
            "asset_ref": event.asset_ref,
            "placement_cue_ids": list(event.placement_cue_ids),
        }
    if isinstance(event, VisualEventProposal):
        return {
            "kind": "visual_review",
            "event_id": event.event_id,
            "status": event.status,
        }
    return {"kind": "event_record", "event": _event_to_dict(event)}


def _proposal_event_from_dict(
    value: object,
) -> DirectorEventProposal | DPEventProposal | VisualEventProposal | EventRecord:
    if not isinstance(value, dict):
        raise SemanticDispatchStoreError("semantic proposal event is invalid")
    try:
        kind = value["kind"]
        if kind == "director":
            return DirectorEventProposal(
                event_id=str(value["event_id"]),
                master_cue_ids=tuple(str(item) for item in value["master_cue_ids"]),
                intent=str(value["intent"]),
                display=str(value["display"]),
                semantic_kind=str(value["semantic_kind"]),
                intentional_aroll=bool(value["intentional_aroll"]),
            )
        if kind == "dp":
            return DPEventProposal(
                event_id=str(value["event_id"]),
                implementation_kind=str(value["implementation_kind"]),
                lane=cast(ComponentLane | None, value["lane"]),
                asset_ref=cast(str | None, value["asset_ref"]),
                placement_cue_ids=tuple(str(item) for item in value.get("placement_cue_ids", [])),
            )
        if kind == "visual_review":
            status = value["status"]
            if status not in {"approved", "failed"}:
                raise ValueError("visual status is invalid")
            return VisualEventProposal(
                event_id=str(value["event_id"]),
                status=cast(Literal["approved", "failed"], status),
            )
        if kind == "event_record":
            return _event_from_dict(value["event"])
    except (KeyError, TypeError, ValueError) as error:
        raise SemanticDispatchStoreError("semantic proposal event fields are invalid") from error
    raise SemanticDispatchStoreError("semantic proposal event kind is invalid")


def _proposal_to_dict(proposal: StageProposal) -> dict[str, object]:
    return {
        "run_id": proposal.run_id,
        "request_id": proposal.request_id,
        "episode_id": proposal.episode_id,
        "cut_id": proposal.cut_id,
        "format": proposal.format,
        "stage": proposal.stage,
        "attempt": proposal.attempt,
        "scope": proposal.scope,
        "event_id": proposal.event_id,
        "parent_acceptance_id": proposal.parent_acceptance_id,
        "events": [_proposal_event_to_dict(event) for event in proposal.events],
        "components": [_component_proposal_to_dict(component) for component in proposal.components],
        "schema": proposal.schema,
    }


def _proposal_from_dict(value: object) -> StageProposal:
    if not isinstance(value, dict):
        raise SemanticDispatchStoreError("semantic proposal is invalid")
    try:
        return StageProposal(
            run_id=str(value["run_id"]),
            request_id=str(value["request_id"]),
            episode_id=str(value["episode_id"]),
            cut_id=str(value["cut_id"]),
            format=cast(Format, value["format"]),
            stage=cast(StageName, value["stage"]),
            attempt=int(value["attempt"]),
            scope=cast(RequestScope, value["scope"]),
            event_id=cast(str | None, value["event_id"]),
            parent_acceptance_id=cast(str | None, value["parent_acceptance_id"]),
            events=tuple(_proposal_event_from_dict(event) for event in value["events"]),
            components=tuple(
                _component_proposal_from_dict(component) for component in value["components"]
            ),
            schema=str(value["schema"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SemanticDispatchStoreError("semantic proposal fields are invalid") from error


def _outcome_to_dict(outcome: SemanticDispatchOutcome) -> dict[str, object]:
    return {
        "request_id": outcome.request_id,
        "state": outcome.state,
        "proposal": _proposal_to_dict(outcome.proposal) if outcome.proposal is not None else None,
        "reason_code": outcome.reason_code,
        "diagnostic": outcome.diagnostic,
    }


def _outcome_from_dict(value: object) -> SemanticDispatchOutcome:
    if not isinstance(value, dict) or set(value) != {
        "request_id",
        "state",
        "proposal",
        "reason_code",
        "diagnostic",
    }:
        raise SemanticDispatchStoreError("semantic dispatch outcome is invalid")
    try:
        state = value["state"]
        if state not in {"ready", "failed", "indeterminate"}:
            raise ValueError("semantic dispatch outcome state is invalid")
        return SemanticDispatchOutcome(
            request_id=str(value["request_id"]),
            state=cast(Literal["ready", "failed", "indeterminate"], state),
            proposal=(
                _proposal_from_dict(value["proposal"]) if value["proposal"] is not None else None
            ),
            reason_code=cast(str | None, value["reason_code"]),
            diagnostic=cast(str | None, value["diagnostic"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SemanticDispatchStoreError("semantic dispatch outcome fields are invalid") from error
