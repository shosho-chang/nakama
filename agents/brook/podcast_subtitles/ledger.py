"""Append-only, hash-chained correction decisions keyed by stable span identity."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import LedgerConflictError, LedgerIntegrityError, StaleFingerprintError
from .hashing import canonical_json_bytes, hash_object, sha256_bytes

GENESIS_HASH = sha256_bytes(b"nakama:podcast-subtitle-v2:correction-ledger:genesis")

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock


@contextmanager
def _exclusive_process_lock(path: Path):
    """Hold an OS-level lock across ledger read/compare/append/fsync.

    The sidecar is intentionally separate from the append-only data file so
    replacing or initially creating the ledger cannot invalidate the locked
    handle.  ``filelock`` uses native platform locking and is already a core
    Nakama dependency.
    """

    try:
        from filelock import FileLock, Timeout
    except ImportError as exc:  # pragma: no cover - declared core dependency
        raise LedgerIntegrityError(
            "cross-process correction ledger locking requires filelock"
        ) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    lock = FileLock(str(lock_path))
    try:
        with lock.acquire(timeout=30):
            yield
    except Timeout as exc:
        raise LedgerIntegrityError(
            f"timed out acquiring correction ledger lock: {lock_path}"
        ) from exc


def _decision_payload(decision: Any) -> dict[str, Any]:
    if hasattr(decision, "model_dump"):
        payload = decision.model_dump(mode="json", exclude_none=False)
    elif isinstance(decision, Mapping):
        payload = dict(decision)
    else:
        raise TypeError(f"unsupported correction decision: {type(decision).__name__}")
    event_id = payload.get("event_id", payload.get("decision_id"))
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("CorrectionDecision.event_id must be a non-empty string")
    return payload


def _reviewed_fingerprint(payload: Mapping[str, Any]) -> str:
    for field in (
        "evidence_fingerprint",
        "target_fingerprint",
        "expected_fingerprint",
        "source_fingerprint",
        "reviewed_fingerprint",
    ):
        value = payload.get(field)
        if isinstance(value, str) and value:
            return value
    raise ValueError("CorrectionDecision must carry a reviewed target fingerprint")


@dataclass(frozen=True)
class LedgerEntry:
    sequence: int
    previous_hash: str
    decision: dict[str, Any]
    decision_hash: str
    entry_hash: str


class CorrectionLedger:
    """A JSONL ledger whose entries are immutable and independently verifiable."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = _lock_for(self.path)

    def entries(self) -> tuple[LedgerEntry, ...]:
        if not self.path.exists():
            return ()
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise LedgerIntegrityError(f"cannot read correction ledger: {self.path}") from exc
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("correction ledger has a truncated final record")

        result: list[LedgerEntry] = []
        previous_hash = GENESIS_HASH
        for line_number, raw_line in enumerate(raw.splitlines(), start=1):
            if not raw_line:
                raise LedgerIntegrityError(f"empty ledger record at line {line_number}")
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise LedgerIntegrityError(
                    f"invalid correction ledger JSON at line {line_number}"
                ) from exc
            required = {
                "sequence",
                "previous_hash",
                "decision",
                "decision_hash",
                "entry_hash",
            }
            if not isinstance(record, dict) or set(record) != required:
                raise LedgerIntegrityError(f"invalid ledger record shape at line {line_number}")
            if record["sequence"] != line_number:
                raise LedgerIntegrityError(f"ledger sequence mismatch at line {line_number}")
            if record["previous_hash"] != previous_hash:
                raise LedgerIntegrityError(f"ledger chain mismatch at line {line_number}")
            decision_hash = hash_object(record["decision"])
            if record["decision_hash"] != decision_hash:
                raise LedgerIntegrityError(f"decision hash mismatch at line {line_number}")
            identity = {key: record[key] for key in required if key != "entry_hash"}
            entry_hash = hash_object(identity)
            if record["entry_hash"] != entry_hash:
                raise LedgerIntegrityError(f"entry hash mismatch at line {line_number}")
            result.append(
                LedgerEntry(
                    sequence=line_number,
                    previous_hash=previous_hash,
                    decision=dict(record["decision"]),
                    decision_hash=decision_hash,
                    entry_hash=entry_hash,
                )
            )
            previous_hash = entry_hash
        return tuple(result)

    @property
    def head_hash(self) -> str:
        entries = self.entries()
        return entries[-1].entry_hash if entries else GENESIS_HASH

    @staticmethod
    def _entry_for_payload(
        payload: dict[str, Any],
        *,
        previous_hash: str,
        sequence: int,
    ) -> LedgerEntry:
        decision_hash = hash_object(payload)
        identity = {
            "sequence": sequence,
            "previous_hash": previous_hash,
            "decision": payload,
            "decision_hash": decision_hash,
        }
        return LedgerEntry(
            sequence=sequence,
            previous_hash=previous_hash,
            decision=payload,
            decision_hash=decision_hash,
            entry_hash=hash_object(identity),
        )

    @staticmethod
    def _matching_existing(
        existing: tuple[LedgerEntry, ...],
        *,
        event_id: str,
        decision_hash: str,
    ) -> LedgerEntry | None:
        for entry in existing:
            existing_id = entry.decision.get("event_id", entry.decision.get("decision_id"))
            if existing_id != event_id:
                continue
            if entry.decision_hash == decision_hash:
                return entry
            raise LedgerConflictError(
                f"event_id {event_id!r} already exists with different content"
            )
        return None

    def _prepare_locked(
        self,
        payload: dict[str, Any],
        *,
        current_fingerprint: str,
        expected_head: str | None,
    ) -> LedgerEntry:
        existing = self.entries()
        event_id = payload.get("event_id", payload.get("decision_id"))
        decision_hash = hash_object(payload)
        duplicate = self._matching_existing(
            existing,
            event_id=event_id,
            decision_hash=decision_hash,
        )
        if duplicate is not None:
            return duplicate

        reviewed_fingerprint = _reviewed_fingerprint(payload)
        if reviewed_fingerprint != current_fingerprint:
            raise StaleFingerprintError(
                f"decision {event_id!r} reviewed {reviewed_fingerprint}, "
                f"current target is {current_fingerprint}"
            )
        actual_head = existing[-1].entry_hash if existing else GENESIS_HASH
        if expected_head is not None and actual_head != expected_head:
            raise LedgerConflictError(
                "correction ledger head changed before transaction preparation: "
                f"expected={expected_head}, actual={actual_head}"
            )
        return self._entry_for_payload(
            payload,
            previous_hash=actual_head,
            sequence=len(existing) + 1,
        )

    def prepare(
        self,
        decision: Any,
        *,
        current_fingerprint: str,
        expected_head: str,
    ) -> LedgerEntry:
        """Calculate the exact next entry without mutating the ledger.

        The caller can bind immutable child artifacts to ``entry_hash`` before
        publishing the entry.  ``append_prepared`` performs the same head CAS
        again, so work done between these calls cannot silently fork the chain.
        """

        payload = _decision_payload(decision)
        with self._lock, _exclusive_process_lock(self.path):
            return self._prepare_locked(
                payload,
                current_fingerprint=current_fingerprint,
                expected_head=expected_head,
            )

    def _append_prepared_locked(self, entry: LedgerEntry) -> LedgerEntry:
        existing = self.entries()
        event_id = entry.decision.get("event_id", entry.decision.get("decision_id"))
        duplicate = self._matching_existing(
            existing,
            event_id=event_id,
            decision_hash=entry.decision_hash,
        )
        if duplicate is not None:
            if duplicate != entry:
                raise LedgerIntegrityError(
                    "prepared correction entry differs from its existing record"
                )
            return duplicate

        actual_head = existing[-1].entry_hash if existing else GENESIS_HASH
        expected = self._entry_for_payload(
            dict(entry.decision),
            previous_hash=actual_head,
            sequence=len(existing) + 1,
        )
        if entry != expected:
            raise LedgerConflictError(
                "prepared correction entry no longer follows the ledger head: "
                f"expected={entry.previous_hash}, actual={actual_head}"
            )

        identity = {
            "sequence": entry.sequence,
            "previous_hash": entry.previous_hash,
            "decision": entry.decision,
            "decision_hash": entry.decision_hash,
        }
        record = {**identity, "entry_hash": entry.entry_hash}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = canonical_json_bytes(record) + b"\n"
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            written = os.write(descriptor, encoded)
            if written != len(encoded):
                raise LedgerIntegrityError("short write while appending correction ledger")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return entry

    def append_prepared(self, entry: LedgerEntry) -> LedgerEntry:
        """Append one precomputed entry iff its previous hash is still current."""

        with self._lock, _exclusive_process_lock(self.path):
            return self._append_prepared_locked(entry)

    def append(self, decision: Any, *, current_fingerprint: str) -> LedgerEntry:
        """Append one decision or return its identical existing entry.

        Idempotency is checked before stale-text detection.  Retrying an already
        accepted decision therefore succeeds even after the canonical text has
        advanced, while a new decision against stale reviewed text is rejected.
        """

        payload = _decision_payload(decision)
        with self._lock, _exclusive_process_lock(self.path):
            entry = self._prepare_locked(
                payload,
                current_fingerprint=current_fingerprint,
                expected_head=None,
            )
            return self._append_prepared_locked(entry)


__all__ = ["CorrectionLedger", "GENESIS_HASH", "LedgerEntry"]
