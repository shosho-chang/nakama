"""Private persistent authority store for Finished Cut Production."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar
from uuid import uuid4

from ._assets import WorkerCatalogItem, WorkerSelectionCatalog
from ._codec import RecordCodec, RecordCodecError
from ._commands import ApprovedCutCommand, TargetedRevisionCommand
from ._correction import _PreReleaseCorrection
from ._records import (
    AcceptedStage,
    DirectorEventProposal,
    DPEventProposal,
    EventRecord,
    FinishedCutRelease,
    MaterializationPlan,
    ProjectedComponent,
    StageProposal,
    StageRequest,
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


#: 落盤格式的定義就是各個 record 的欄位宣告，這裡不再抄第二份。例外只有四種，
#: 全部掛在下面的 `register`：退役投影要走 mint、AcceptedStage 要走 mint、
#: 衍生欄位不落盤、tagged union 結構描述不了。
_CODEC = RecordCodec(localns={"_PreReleaseCorrection": _PreReleaseCorrection})

#: badge 是規則推導出來的，不是輸入。落盤會讓它變成第二個真相來源，之後就分不出
#: 「當初算出來的」跟「有人手改過的」。
_PLAN_DERIVED_FIELDS = frozenset({"brand_badge_overlays"})

_T = TypeVar("_T")


def _load(
    cls: type[_T],
    value: object,
    *,
    label: str,
    error: type[Exception] = ProductionStoreError,
) -> _T:
    try:
        return _CODEC.load(cls, value)
    except RecordCodecError as failure:
        raise error(f"persisted {label} is invalid: {failure}") from failure


def _view_to_dict(view: _ProductionRun) -> dict[str, object]:
    return _CODEC.dump_record(view)


def _view_from_dict(value: object) -> _ProductionRun:
    if isinstance(value, dict) and "accepted_stage_history" not in value:
        # 分開記歷史之前存的 run：當時「歷史」就等於「目前」。
        value = {**value, "accepted_stage_history": value.get("accepted_stages", [])}
    return _load(_ProductionRun, value, label="ProductionRun view")


def _request_to_dict(request: StageRequest) -> dict[str, object]:
    return _CODEC.dump_record(request)


def _event_to_dict(event: EventRecord) -> dict[str, object]:
    return _CODEC.dump_record(event)


def _event_from_dict(value: object) -> EventRecord:
    return _load(EventRecord, value, label="event")


def _accepted_from_dict(value: object) -> AcceptedStage:
    try:
        return _CODEC.load_record(AcceptedStage, value, factory=_mint_accepted_stage)
    except RecordCodecError as failure:
        raise ProductionStoreError(f"persisted AcceptedStage is invalid: {failure}") from failure


def _plan_to_dict(plan: MaterializationPlan) -> dict[str, object]:
    return _CODEC.dump_record(plan, exclude=_PLAN_DERIVED_FIELDS)


def _plan_from_dict(value: object) -> MaterializationPlan:
    try:
        return _CODEC.load_record(
            MaterializationPlan,
            value,
            exclude=_PLAN_DERIVED_FIELDS,
            factory=_mint_materialization_plan,
        )
    except RecordCodecError as failure:
        raise ProductionStoreError(
            f"persisted MaterializationPlan is invalid: {failure}"
        ) from failure


def _projected_from_dict(value: object) -> ProjectedComponent:
    try:
        return _CODEC.load_record(ProjectedComponent, value, factory=_mint_projected_component)
    except RecordCodecError as failure:
        raise ProductionStoreError(
            f"persisted projected component is invalid: {failure}"
        ) from failure


def _catalog_to_list(catalog: WorkerSelectionCatalog) -> list[dict[str, object]]:
    return [_CODEC.dump_record(item) for item in catalog.items()]


def _catalog_from_list(value: object) -> WorkerSelectionCatalog:
    if not isinstance(value, list):
        raise ProductionStoreError("persisted Worker Selection Catalog is invalid")
    return WorkerSelectionCatalog(
        _load(WorkerCatalogItem, item, label="Worker Selection Catalog entry") for item in value
    )


def _command_to_dict(
    command: ApprovedCutCommand | TargetedRevisionCommand,
) -> dict[str, object]:
    return _CODEC.dump_record(command)


def _command_kind(command: ApprovedCutCommand | TargetedRevisionCommand) -> str:
    return "approved_cut" if isinstance(command, ApprovedCutCommand) else "targeted_revision"


def _targeted_revision_to_dict(command: TargetedRevisionCommand) -> dict[str, object]:
    return _CODEC.dump_record(command)


def _targeted_revision_from_dict(value: object) -> TargetedRevisionCommand:
    return _load(TargetedRevisionCommand, value, label="TargetedRevision command")


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
        command: ApprovedCutCommand | TargetedRevisionCommand = _load(
            ApprovedCutCommand, value["command"], label="ApprovedCut command"
        )
    elif command_kind == "targeted_revision":
        command = _targeted_revision_from_dict(value["command"])
    else:
        raise ProductionStoreError("persisted ProductionRun command kind is invalid")
    base_release_id = value["base_release_id"]
    if base_release_id is not None and not isinstance(base_release_id, str):
        raise ProductionStoreError("persisted ProductionRun base release is invalid")
    return _StoredRun(
        command=command,
        view=_view_from_dict(value["view"]),
        worker_catalog=_catalog_from_list(value["worker_catalog"]),
        base_release_id=base_release_id,
    )


#: Stage proposal 的 events 是 tagged union——四種 record 共用一個陣列，靠 `kind`
#: 分辨。這是唯一一個「欄位型別描述不了」的形狀，所以自己寫。
_PROPOSAL_EVENT_KINDS: dict[
    str, type[DirectorEventProposal] | type[DPEventProposal] | type[VisualEventProposal]
] = {
    "director": DirectorEventProposal,
    "dp": DPEventProposal,
    "visual_review": VisualEventProposal,
}


def _proposal_event_to_dict(
    event: DirectorEventProposal | DPEventProposal | VisualEventProposal | EventRecord,
) -> dict[str, object]:
    for kind, cls in _PROPOSAL_EVENT_KINDS.items():
        if type(event) is cls:
            return {"kind": kind, **_CODEC.dump_record(event)}
    return {"kind": "event_record", "event": _event_to_dict(event)}


def _proposal_event_from_dict(
    value: object,
) -> DirectorEventProposal | DPEventProposal | VisualEventProposal | EventRecord:
    if not isinstance(value, dict):
        raise SemanticDispatchStoreError("semantic proposal event is invalid")
    kind = value.get("kind")
    if kind == "event_record":
        return _event_from_dict(value.get("event"))
    cls = _PROPOSAL_EVENT_KINDS.get(kind) if isinstance(kind, str) else None
    if cls is None:
        raise SemanticDispatchStoreError("semantic proposal event kind is invalid")
    return _load(
        cls,
        value,
        label="semantic proposal event",
        error=SemanticDispatchStoreError,
    )


def _proposal_to_dict(proposal: StageProposal) -> dict[str, object]:
    return _CODEC.dump_record(
        proposal,
        overrides={"events": [_proposal_event_to_dict(event) for event in proposal.events]},
    )


def _proposal_from_dict(value: object) -> StageProposal:
    if not isinstance(value, dict):
        raise SemanticDispatchStoreError("semantic proposal is invalid")
    events = value.get("events")
    if not isinstance(events, list):
        raise SemanticDispatchStoreError("semantic proposal events are invalid")
    try:
        return _CODEC.load_record(
            StageProposal,
            value,
            presets={"events": tuple(_proposal_event_from_dict(item) for item in events)},
        )
    except RecordCodecError as failure:
        raise SemanticDispatchStoreError(f"semantic proposal is invalid: {failure}") from failure


def _outcome_to_dict(outcome: SemanticDispatchOutcome) -> dict[str, object]:
    return _CODEC.dump_record(outcome)


def _outcome_from_dict(value: object) -> SemanticDispatchOutcome:
    return _load(
        SemanticDispatchOutcome,
        value,
        label="semantic dispatch outcome",
        error=SemanticDispatchStoreError,
    )


_CODEC.register(AcceptedStage, load=_accepted_from_dict)
_CODEC.register(MaterializationPlan, dump=_plan_to_dict, load=_plan_from_dict)
_CODEC.register(ProjectedComponent, load=_projected_from_dict)
_CODEC.register(StageProposal, dump=_proposal_to_dict, load=_proposal_from_dict)
