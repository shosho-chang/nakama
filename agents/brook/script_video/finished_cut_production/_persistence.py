"""The atomic durable store for Finished Cut Production Resolve transactions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from ._resolve import (
    PreviewRender,
    ResolveTransaction,
    TimelineIdentity,
    TimelineSnapshot,
    TimelineWorkspace,
)

_TRANSACTION_SCHEMA = "nakama.finished-cut-resolve-transaction.v1"
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: 只有一個狀態到得了——見 `_resolve.ResolveTransactionStatus`。
_TRANSACTION_STATUSES = frozenset({"preview_ready"})


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

    def find_for_plan(
        self,
        *,
        episode_id: str,
        cut_id: str,
        plan_id: str,
        plan_fingerprint: str,
    ) -> ResolveTransaction | None:
        """掃這一集的交易紀錄，找出屬於這個 plan 的那一筆。

        交易 id 把 canonical 的身分也算進去，成功之後 canonical 就換人了——所以
        用 id 找不回自己。這裡改用 plan 的身分找。找到兩筆以上就不猜：回 None，
        讓上層照原本的路走並自己撞上該撞的錯。
        """
        if not self._root.is_dir():
            return None
        matches: list[ResolveTransaction] = []
        for path in sorted(self._root.glob("resolve-*.json")):
            document = _read_envelope(path, schema=_TRANSACTION_SCHEMA)
            if document is None:
                continue
            transaction = _transaction_from_payload(document)
            if (
                transaction.episode_id == episode_id
                and transaction.cut_id == cut_id
                and transaction.plan_id == plan_id
                and transaction.plan_fingerprint == plan_fingerprint
            ):
                matches.append(transaction)
        return matches[0] if len(matches) == 1 else None

    def _record_path(self, transaction_id: str) -> Path:
        _require_identity(transaction_id, field="transaction_id")
        return self._root / f"{transaction_id}.json"


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
    # ADR-069 之前的紀錄還帶著 transaction_receipt_id／rollback_ref／
    # backup_retained 三格（封存鏈用的）。那三格現在沒有人填也沒有人讀，
    # 讀回來時直接略過——既有檔案照樣載得進來。
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
