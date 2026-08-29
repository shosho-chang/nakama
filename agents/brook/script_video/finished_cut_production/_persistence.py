"""Atomic durable stores for Finished Cut Production transactions and cutovers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from ._cutover import (
    DeploymentSnapshot,
    GlobalCutoverJournal,
    PointerSnapshot,
    UnpublishedReleaseIndex,
)
from ._records import (
    EventRecord,
    FinishedCutRelease,
    ProjectedComponent,
    ReleaseArtifact,
    _mint_projected_component,
    _seal_finished_cut_release,
)
from ._resolve import (
    PreviewRender,
    ResolveTransaction,
    TimelineIdentity,
    TimelineSnapshot,
    TimelineWorkspace,
)

_TRANSACTION_SCHEMA = "nakama.finished-cut-resolve-transaction.v1"
_CUTOVER_SCHEMA = "nakama.finished-cut-global-cutover-journal.v1"
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TRANSACTION_STATUSES = frozenset(
    {"preview_ready", "committed", "compensated", "rolled_back", "rollback_failed"}
)
_CUTOVER_STATUSES = frozenset(
    {
        "prepared",
        "committing",
        "committed",
        "sealing",
        "index_built",
        "pointer_published",
        "completed",
        "rolling_back",
        "rolled_back",
        "rollback_failed",
    }
)


class PersistenceError(ValueError):
    """Durable state is incomplete, corrupt, or does not bind its requested identity."""


class AtomicResolveTransactionStore:
    """Persist each Resolve transaction as one checksum-bound atomic record."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def load(self, transaction_id: str) -> ResolveTransaction | None:
        path = self._record_path(transaction_id)
        document = _read_envelope(path, schema=_TRANSACTION_SCHEMA)
        if document is None:
            return None
        transaction = _transaction_from_payload(document)
        if transaction.transaction_id != transaction_id:
            raise PersistenceError("transaction record does not match requested identity")
        return transaction

    def save(self, transaction: ResolveTransaction) -> None:
        path = self._record_path(transaction.transaction_id)
        _atomic_write_envelope(
            path,
            schema=_TRANSACTION_SCHEMA,
            payload=_transaction_payload(transaction),
        )

    def _record_path(self, transaction_id: str) -> Path:
        _require_identity(transaction_id, field="transaction_id")
        return self._root / f"{transaction_id}.json"


class AtomicCutoverJournalStore:
    """Persist the global cutover journal independently of process lifetime."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def load(self, cutover_id: str) -> GlobalCutoverJournal | None:
        path = self._record_path(cutover_id)
        document = _read_envelope(path, schema=_CUTOVER_SCHEMA)
        if document is None:
            return None
        journal = _journal_from_payload(document)
        if journal.cutover_id != cutover_id:
            raise PersistenceError("cutover journal does not match requested identity")
        return journal

    def save(self, journal: GlobalCutoverJournal) -> None:
        path = self._record_path(journal.cutover_id)
        _atomic_write_envelope(
            path,
            schema=_CUTOVER_SCHEMA,
            payload=_journal_payload(journal),
        )

    def _record_path(self, cutover_id: str) -> Path:
        _require_identity(cutover_id, field="cutover_id")
        return self._root / f"{cutover_id}.json"


def _journal_payload(journal: GlobalCutoverJournal) -> dict[str, Any]:
    return {
        "cutover_id": journal.cutover_id,
        "episode_id": journal.episode_id,
        "fixed_cut_order": list(journal.fixed_cut_order),
        "candidate_ids": list(journal.candidate_ids),
        "transaction_ids": list(journal.transaction_ids),
        "target_deployment_id": journal.target_deployment_id,
        "old_pointer": {"index_id": journal.old_pointer.index_id},
        "old_deployment": {"deployment_id": journal.old_deployment.deployment_id},
        "status": journal.status,
        "committed_transaction_ids": list(journal.committed_transaction_ids),
        "compensated_transaction_ids": list(journal.compensated_transaction_ids),
        "releases": [_release_payload(release) for release in journal.releases],
        "unpublished_index": (
            {
                "index_id": journal.unpublished_index.index_id,
                "episode_id": journal.unpublished_index.episode_id,
                "release_ids": list(journal.unpublished_index.release_ids),
            }
            if journal.unpublished_index is not None
            else None
        ),
        "pointer_published": journal.pointer_published,
        "deployment_activated": journal.deployment_activated,
        "failure": journal.failure,
    }


def _journal_from_payload(payload: Mapping[str, Any]) -> GlobalCutoverJournal:
    status = _required_string(payload, "status")
    if status not in _CUTOVER_STATUSES:
        raise PersistenceError("cutover journal status is invalid")
    release_rows = payload.get("releases")
    if not isinstance(release_rows, list) or any(not isinstance(row, dict) for row in release_rows):
        raise PersistenceError("cutover journal Releases are invalid")
    releases = tuple(_release_from_payload(row) for row in release_rows)
    index_row = payload.get("unpublished_index")
    if index_row is None:
        unpublished_index = None
    elif isinstance(index_row, dict):
        unpublished_index = UnpublishedReleaseIndex(
            index_id=_required_string(index_row, "index_id"),
            episode_id=_required_string(index_row, "episode_id"),
            release_ids=_string_tuple(index_row, "release_ids"),
        )
    else:
        raise PersistenceError("cutover journal unpublished index is invalid")
    pointer_published = payload.get("pointer_published")
    deployment_activated = payload.get("deployment_activated")
    if not isinstance(pointer_published, bool) or not isinstance(deployment_activated, bool):
        raise PersistenceError("cutover journal publication flags are invalid")
    return GlobalCutoverJournal(
        cutover_id=_required_string(payload, "cutover_id"),
        episode_id=_required_string(payload, "episode_id"),
        fixed_cut_order=_string_tuple(payload, "fixed_cut_order"),
        candidate_ids=_string_tuple(payload, "candidate_ids"),
        transaction_ids=_string_tuple(payload, "transaction_ids"),
        target_deployment_id=_required_string(payload, "target_deployment_id"),
        old_pointer=PointerSnapshot(
            index_id=_required_string(_required_mapping(payload, "old_pointer"), "index_id")
        ),
        old_deployment=DeploymentSnapshot(
            deployment_id=_required_string(
                _required_mapping(payload, "old_deployment"), "deployment_id"
            )
        ),
        status=status,  # type: ignore[arg-type]
        committed_transaction_ids=_string_tuple(payload, "committed_transaction_ids"),
        compensated_transaction_ids=_string_tuple(payload, "compensated_transaction_ids"),
        releases=releases,
        unpublished_index=unpublished_index,
        pointer_published=pointer_published,
        deployment_activated=deployment_activated,
        failure=_optional_string(payload, "failure"),
    )


def _release_payload(release: FinishedCutRelease) -> dict[str, Any]:
    return {
        "release_id": release.release_id,
        "episode_id": release.episode_id,
        "cut_id": release.cut_id,
        "format": release.format,
        "command_id": release.command_id,
        "run_id": release.run_id,
        "editorial_master_id": release.editorial_master_id,
        "winner_id": release.winner_id,
        "tight_cut_id": release.tight_cut_id,
        "director_acceptance_id": release.director_acceptance_id,
        "dp_acceptance_id": release.dp_acceptance_id,
        "visual_acceptance_id": release.visual_acceptance_id,
        "materialization_plan_id": release.materialization_plan_id,
        "events": [_event_payload(event) for event in release.events],
        "preview": _artifact_payload(release.preview),
        "subtitle": _artifact_payload(release.subtitle),
        "transaction_receipt_id": release.transaction_receipt_id,
        "rollback_ref": release.rollback_ref,
        "components": [_component_payload(component) for component in release.components],
    }


def _release_from_payload(payload: Mapping[str, Any]) -> FinishedCutRelease:
    format_name = _required_string(payload, "format")
    if format_name not in {"long", "short"}:
        raise PersistenceError("Finished Cut Release format is invalid")
    event_rows = _mapping_list(payload, "events")
    component_rows = _mapping_list(payload, "components")
    return _seal_finished_cut_release(
        release_id=_required_string(payload, "release_id"),
        episode_id=_required_string(payload, "episode_id"),
        cut_id=_required_string(payload, "cut_id"),
        format=format_name,  # type: ignore[arg-type]
        command_id=_required_string(payload, "command_id"),
        run_id=_required_string(payload, "run_id"),
        editorial_master_id=_required_string(payload, "editorial_master_id"),
        winner_id=_required_string(payload, "winner_id"),
        tight_cut_id=_required_string(payload, "tight_cut_id"),
        director_acceptance_id=_required_string(payload, "director_acceptance_id"),
        dp_acceptance_id=_required_string(payload, "dp_acceptance_id"),
        visual_acceptance_id=_required_string(payload, "visual_acceptance_id"),
        materialization_plan_id=_required_string(payload, "materialization_plan_id"),
        events=tuple(_event_from_payload(row) for row in event_rows),
        preview=_artifact_from_payload(_required_mapping(payload, "preview")),
        subtitle=_artifact_from_payload(_required_mapping(payload, "subtitle")),
        transaction_receipt_id=_required_string(payload, "transaction_receipt_id"),
        rollback_ref=_required_string(payload, "rollback_ref"),
        components=tuple(_component_from_payload(row) for row in component_rows),
    )


def _event_payload(event: EventRecord) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "master_cue_ids": list(event.master_cue_ids),
        "text_hash": event.text_hash,
        "intent": event.intent,
        "asset_ref": event.asset_ref,
        "visual_status": event.visual_status,
    }


def _event_from_payload(payload: Mapping[str, Any]) -> EventRecord:
    return EventRecord(
        event_id=_required_string(payload, "event_id"),
        master_cue_ids=_string_tuple(payload, "master_cue_ids"),
        text_hash=_required_string(payload, "text_hash"),
        intent=_required_string(payload, "intent"),
        asset_ref=_optional_string(payload, "asset_ref"),
        visual_status=_optional_string(payload, "visual_status"),
    )


def _component_payload(component: ProjectedComponent) -> dict[str, Any]:
    return {
        "component_id": component.component_id,
        "event_id": component.event_id,
        "semantic_kind": component.semantic_kind,
        "implementation_kind": component.implementation_kind,
        "lane": component.lane,
        "display": component.display,
        "t0": component.t0,
        "t1": component.t1,
        "asset_ref": component.asset_ref,
    }


def _component_from_payload(payload: Mapping[str, Any]) -> ProjectedComponent:
    lane = _required_string(payload, "lane")
    if lane not in {
        "b_roll",
        "identity_card",
        "hero_title",
        "fullscreen_transition",
        "visual_effect",
    }:
        raise PersistenceError("Finished Cut component lane is invalid")
    return _mint_projected_component(
        component_id=_required_string(payload, "component_id"),
        event_id=_required_string(payload, "event_id"),
        semantic_kind=_required_string(payload, "semantic_kind"),
        implementation_kind=_required_string(payload, "implementation_kind"),
        lane=lane,  # type: ignore[arg-type]
        display=_required_string(payload, "display"),
        t0=_required_number(payload, "t0"),
        t1=_required_number(payload, "t1"),
        asset_ref=_optional_string(payload, "asset_ref"),
    )


def _artifact_payload(artifact: ReleaseArtifact) -> dict[str, Any]:
    return {
        "path": artifact.path,
        "bytes": artifact.bytes,
        "sha256": artifact.sha256,
        "duration_sec": artifact.duration_sec,
        "probe": [[key, value] for key, value in artifact.probe],
    }


def _artifact_from_payload(payload: Mapping[str, Any]) -> ReleaseArtifact:
    byte_count = payload.get("bytes")
    if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
        raise PersistenceError("Release artifact byte count is invalid")
    duration_value = payload.get("duration_sec")
    if duration_value is None:
        duration = None
    elif isinstance(duration_value, bool) or not isinstance(duration_value, (int, float)):
        raise PersistenceError("Release artifact duration is invalid")
    else:
        duration = float(duration_value)
    probe_rows = payload.get("probe")
    if not isinstance(probe_rows, list):
        raise PersistenceError("Release artifact probe is invalid")
    probe: list[tuple[str, str | int | float | bool | None]] = []
    for row in probe_rows:
        if (
            not isinstance(row, list)
            or len(row) != 2
            or not isinstance(row[0], str)
            or not isinstance(row[1], (str, int, float, bool, type(None)))
        ):
            raise PersistenceError("Release artifact probe entry is invalid")
        probe.append((row[0], row[1]))
    return ReleaseArtifact(
        path=_required_string(payload, "path"),
        bytes=byte_count,
        sha256=_required_string(payload, "sha256"),
        duration_sec=duration,
        probe=tuple(probe),
    )


def _transaction_payload(transaction: ResolveTransaction) -> dict[str, Any]:
    return {
        "transaction_id": transaction.transaction_id,
        "episode_id": transaction.episode_id,
        "cut_id": transaction.cut_id,
        "plan_id": transaction.plan_id,
        "plan_fingerprint": transaction.plan_fingerprint,
        "status": transaction.status,
        "canonical": _timeline_identity_payload(transaction.canonical),
        "workspace": {
            "canonical": _timeline_identity_payload(transaction.workspace.canonical),
            "work": _timeline_identity_payload(transaction.workspace.work),
            "backup": _timeline_identity_payload(transaction.workspace.backup),
        },
        "baseline": {
            "protected_fingerprint": transaction.baseline.protected_fingerprint,
            "full_fingerprint": transaction.baseline.full_fingerprint,
        },
        "preview": {
            "path": str(transaction.preview.path),
            "duration_sec": transaction.preview.duration_sec,
            "video_codec": transaction.preview.video_codec,
            "audio_codec": transaction.preview.audio_codec,
        },
        "subtitle_path": str(transaction.subtitle_path),
        "transaction_receipt_id": transaction.transaction_receipt_id,
        "rollback_ref": transaction.rollback_ref,
        "backup_retained": transaction.backup_retained,
    }


def _transaction_from_payload(payload: Mapping[str, Any]) -> ResolveTransaction:
    status = _required_string(payload, "status")
    if status not in _TRANSACTION_STATUSES:
        raise PersistenceError("transaction status is invalid")
    canonical = _timeline_identity(_required_mapping(payload, "canonical"))
    workspace_payload = _required_mapping(payload, "workspace")
    baseline_payload = _required_mapping(payload, "baseline")
    preview_payload = _required_mapping(payload, "preview")
    duration = preview_payload.get("duration_sec")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise PersistenceError("transaction preview duration is invalid")
    audio_codec = preview_payload.get("audio_codec")
    if audio_codec is not None and not isinstance(audio_codec, str):
        raise PersistenceError("transaction preview audio codec is invalid")
    receipt_id = _optional_string(payload, "transaction_receipt_id")
    rollback_ref = _optional_string(payload, "rollback_ref")
    backup_retained = payload.get("backup_retained")
    if not isinstance(backup_retained, bool):
        raise PersistenceError("transaction backup-retained flag is invalid")
    return ResolveTransaction(
        transaction_id=_required_string(payload, "transaction_id"),
        episode_id=_required_string(payload, "episode_id"),
        cut_id=_required_string(payload, "cut_id"),
        plan_id=_required_string(payload, "plan_id"),
        plan_fingerprint=_required_string(payload, "plan_fingerprint"),
        status=status,  # type: ignore[arg-type]
        canonical=canonical,
        workspace=TimelineWorkspace(
            canonical=_timeline_identity(_required_mapping(workspace_payload, "canonical")),
            work=_timeline_identity(_required_mapping(workspace_payload, "work")),
            backup=_timeline_identity(_required_mapping(workspace_payload, "backup")),
        ),
        baseline=TimelineSnapshot(
            protected_fingerprint=_required_string(baseline_payload, "protected_fingerprint"),
            full_fingerprint=_required_string(baseline_payload, "full_fingerprint"),
        ),
        preview=PreviewRender(
            path=Path(_required_string(preview_payload, "path")),
            duration_sec=float(duration),
            video_codec=_required_string(preview_payload, "video_codec"),
            audio_codec=audio_codec,
        ),
        subtitle_path=Path(_required_string(payload, "subtitle_path")),
        transaction_receipt_id=receipt_id,
        rollback_ref=rollback_ref,
        backup_retained=backup_retained,
    )


def _timeline_identity_payload(identity: TimelineIdentity) -> dict[str, str]:
    return {"name": identity.name, "uid": identity.uid}


def _timeline_identity(payload: Mapping[str, Any]) -> TimelineIdentity:
    return TimelineIdentity(
        name=_required_string(payload, "name"),
        uid=_required_string(payload, "uid"),
    )


def _atomic_write_envelope(
    path: Path,
    *,
    schema: str,
    payload: Mapping[str, Any],
) -> None:
    payload_bytes = _canonical_json(payload)
    envelope = {
        "schema": schema,
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "payload": payload,
    }
    encoded = _canonical_json(envelope) + b"\n"
    staging = _staging_path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with staging.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    except OSError as exc:
        raise PersistenceError(f"atomic record write failed: {path.name}") from exc


def _read_envelope(path: Path, *, schema: str) -> Mapping[str, Any] | None:
    staging = _staging_path(path)
    if not path.exists():
        if staging.exists():
            raise PersistenceError(f"incomplete atomic record exists: {path.name}")
        return None
    try:
        document = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PersistenceError(f"durable record is unreadable: {path.name}") from exc
    if not isinstance(document, dict) or document.get("schema") != schema:
        raise PersistenceError(f"durable record schema is invalid: {path.name}")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise PersistenceError(f"durable record payload is invalid: {path.name}")
    expected_digest = document.get("payload_sha256")
    actual_digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if expected_digest != actual_digest:
        raise PersistenceError(f"durable record checksum differs: {path.name}")
    return payload


def _staging_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.staging")


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PersistenceError("durable record is not canonical JSON") from exc


def _require_identity(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise PersistenceError(f"{field} is not a safe durable identity")
    return value


def _required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise PersistenceError(f"durable record field is not an object: {key}")
    return value


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PersistenceError(f"durable record field is not a string: {key}")
    return value


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise PersistenceError(f"durable record field is not an optional string: {key}")
    return value


def _string_tuple(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise PersistenceError(f"durable record field is not a string list: {key}")
    return tuple(value)


def _mapping_list(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise PersistenceError(f"durable record field is not an object list: {key}")
    return value


def _required_number(payload: Mapping[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PersistenceError(f"durable record field is not numeric: {key}")
    return float(value)
