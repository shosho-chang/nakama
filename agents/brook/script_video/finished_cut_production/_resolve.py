"""Typed Resolve duplicate-work transactions for Finished Cut Production."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from ._records import MaterializationPlan

ResolveTransactionStatus = Literal[
    "preview_ready",
    "committed",
    "compensated",
    "rolled_back",
    "rollback_failed",
]


class ResolveTransactionError(ValueError):
    """A typed transaction cannot safely advance against its exact cut."""


@dataclass(frozen=True, slots=True)
class TimelineIdentity:
    name: str
    uid: str


@dataclass(frozen=True, slots=True)
class TimelineSnapshot:
    protected_fingerprint: str
    full_fingerprint: str


@dataclass(frozen=True, slots=True)
class TimelineWorkspace:
    canonical: TimelineIdentity
    work: TimelineIdentity
    backup: TimelineIdentity


@dataclass(frozen=True, slots=True)
class PreviewRender:
    path: Path
    duration_sec: float
    video_codec: str
    audio_codec: str | None


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    transaction_id: str
    cut_id: str
    work_uid: str
    transaction_receipt_id: str
    rollback_ref: str
    backup_retained: bool


@dataclass(frozen=True, slots=True)
class ResolveTransaction:
    transaction_id: str
    episode_id: str
    cut_id: str
    plan_id: str
    plan_fingerprint: str
    status: ResolveTransactionStatus
    canonical: TimelineIdentity
    workspace: TimelineWorkspace
    baseline: TimelineSnapshot
    preview: PreviewRender
    subtitle_path: Path
    transaction_receipt_id: str | None = None
    rollback_ref: str | None = None
    backup_retained: bool = False


class TimelineAdapter(Protocol):
    """Resolve host seam; production and in-memory Adapters satisfy this port."""

    def preflight_plan(self, plan: MaterializationPlan) -> None: ...

    def snapshot(self, timeline: TimelineIdentity) -> TimelineSnapshot: ...

    def duplicate(
        self,
        canonical: TimelineIdentity,
        *,
        transaction_id: str,
    ) -> TimelineWorkspace: ...

    def apply_plan(self, work: TimelineIdentity, plan: MaterializationPlan) -> None: ...

    def render_preview(self, work: TimelineIdentity, output: Path) -> PreviewRender: ...

    def rollback(self, workspace: TimelineWorkspace) -> None: ...

    def commit(
        self,
        workspace: TimelineWorkspace,
        *,
        transaction_id: str,
        cut_id: str,
        retain_backup: bool,
    ) -> CommitReceipt: ...

    def compensate(
        self,
        workspace: TimelineWorkspace,
        receipt: CommitReceipt,
    ) -> None: ...


class ResolveTransactionStore(Protocol):
    """Durable seam used by the transaction Module across process restarts."""

    def load(self, transaction_id: str) -> ResolveTransaction | None: ...

    def save(self, transaction: ResolveTransaction) -> None: ...


class _InMemoryResolveTransactionStore:
    def __init__(self) -> None:
        self._transactions: dict[str, ResolveTransaction] = {}

    def load(self, transaction_id: str) -> ResolveTransaction | None:
        return self._transactions.get(transaction_id)

    def save(self, transaction: ResolveTransaction) -> None:
        self._transactions[transaction.transaction_id] = transaction


class ResolveTransactionManager:
    """Hide duplicate, protected-baseline and preview gates behind one Interface."""

    def __init__(
        self,
        adapter: TimelineAdapter,
        *,
        store: ResolveTransactionStore | None = None,
    ) -> None:
        self._adapter = adapter
        self._store = store or _InMemoryResolveTransactionStore()

    def prepare(
        self,
        plan: MaterializationPlan,
        *,
        canonical: TimelineIdentity,
        preview_path: Path,
        subtitle_path: Path,
        expected_baseline: TimelineSnapshot | None = None,
    ) -> ResolveTransaction:
        requested_preview = Path(preview_path)
        requested_subtitle = Path(subtitle_path)
        plan_fingerprint = _plan_fingerprint(plan)
        transaction_id = _transaction_id(
            plan,
            canonical,
            plan_fingerprint=plan_fingerprint,
            preview_path=requested_preview,
            subtitle_path=requested_subtitle,
        )
        existing = self._store.load(transaction_id)
        if existing is not None:
            if existing.status in {"preview_ready", "committed"}:
                return existing
            raise ResolveTransactionError(
                f"transaction cannot prepare from status: {existing.status}"
            )
        try:
            self._adapter.preflight_plan(plan)
        except Exception as exc:
            raise ResolveTransactionError(f"Resolve preflight failed: {exc}") from exc
        baseline = self._adapter.snapshot(canonical)
        if expected_baseline is not None and baseline != expected_baseline:
            raise ResolveTransactionError(
                "canonical Timeline changed after materialization preflight"
            )
        workspace = self._adapter.duplicate(
            canonical,
            transaction_id=transaction_id,
        )
        try:
            if (
                workspace.canonical != canonical
                or workspace.work.uid == canonical.uid
                or workspace.backup.uid != canonical.uid
            ):
                raise ResolveTransactionError(
                    "Resolve duplicate workspace does not preserve canonical identity"
                )
            self._adapter.apply_plan(workspace.work, plan)
            after = self._adapter.snapshot(workspace.work)
            if after.protected_fingerprint != baseline.protected_fingerprint:
                raise ResolveTransactionError(
                    "typed plan changed protected Editorial Master or audio baseline"
                )
            preview = self._adapter.render_preview(workspace.work, requested_preview)
            _validate_preview(preview)
        except Exception as exc:
            try:
                self._adapter.rollback(workspace)
                restored = self._adapter.snapshot(canonical)
                if restored != baseline:
                    raise ResolveTransactionError(
                        "rollback did not restore the original Timeline snapshot"
                    )
            except Exception as rollback_exc:
                raise ResolveTransactionError(
                    f"Resolve prepare failed and rollback failed: {rollback_exc}"
                ) from exc
            if isinstance(exc, ResolveTransactionError):
                raise
            raise ResolveTransactionError(f"Resolve prepare failed: {exc}") from exc
        transaction = ResolveTransaction(
            transaction_id=transaction_id,
            episode_id=plan.episode_id,
            cut_id=plan.cut_id,
            plan_id=plan.plan_id,
            plan_fingerprint=plan_fingerprint,
            status="preview_ready",
            canonical=canonical,
            workspace=workspace,
            baseline=baseline,
            preview=preview,
            subtitle_path=requested_subtitle,
        )
        try:
            self._store.save(transaction)
        except Exception as exc:
            try:
                self._adapter.rollback(workspace)
                restored = self._adapter.snapshot(canonical)
                if restored != baseline:
                    raise ResolveTransactionError(
                        "rollback after persistence failure did not restore Timeline"
                    )
            except Exception as rollback_exc:
                raise ResolveTransactionError(
                    f"transaction persistence failed and Resolve rollback failed: {rollback_exc}"
                ) from exc
            raise ResolveTransactionError(f"transaction persistence failed: {exc}") from exc
        return transaction

    def commit(self, transaction_id: str, *, expected_cut_id: str) -> CommitReceipt:
        transaction = self._exact_transaction(transaction_id, expected_cut_id)
        if transaction.status == "committed":
            return _commit_receipt(transaction)
        if transaction.status != "preview_ready":
            raise ResolveTransactionError(
                f"transaction cannot commit from status: {transaction.status}"
            )
        receipt = self._adapter.commit(
            transaction.workspace,
            transaction_id=transaction.transaction_id,
            cut_id=transaction.cut_id,
            retain_backup=True,
        )
        if (
            receipt.transaction_id != transaction.transaction_id
            or receipt.cut_id != transaction.cut_id
            or receipt.work_uid != transaction.workspace.work.uid
            or not receipt.transaction_receipt_id
            or not receipt.rollback_ref
            or receipt.backup_retained is not True
        ):
            raise ResolveTransactionError(
                "Resolve commit receipt does not bind the exact transaction and retained backup"
            )
        committed = replace(
            transaction,
            status="committed",
            transaction_receipt_id=receipt.transaction_receipt_id,
            rollback_ref=receipt.rollback_ref,
            backup_retained=True,
        )
        self._store.save(committed)
        return receipt

    def inspect_transaction(self, transaction_id: str) -> dict[str, object]:
        transaction = self._store.load(transaction_id)
        if transaction is None:
            raise ResolveTransactionError(f"transaction is unknown: {transaction_id}")
        return {
            "transaction_id": transaction.transaction_id,
            "cut_id": transaction.cut_id,
            "status": transaction.status,
            "transaction_receipt_id": transaction.transaction_receipt_id,
            "rollback_ref": transaction.rollback_ref,
            "backup_retained": transaction.backup_retained,
        }

    def compensating_rollback(
        self,
        transaction_id: str,
        *,
        expected_cut_id: str,
    ) -> ResolveTransaction:
        transaction = self._exact_transaction(transaction_id, expected_cut_id)
        if transaction.status == "compensated":
            return transaction
        if transaction.status not in {"committed", "rollback_failed"}:
            raise ResolveTransactionError(
                f"transaction cannot compensate from status: {transaction.status}"
            )
        receipt = _commit_receipt(transaction)
        try:
            self._adapter.compensate(transaction.workspace, receipt)
            restored = self._adapter.snapshot(transaction.canonical)
            if restored != transaction.baseline:
                raise ResolveTransactionError(
                    "compensation did not restore the original Timeline snapshot"
                )
        except Exception as exc:
            self._store.save(replace(transaction, status="rollback_failed"))
            raise ResolveTransactionError(
                f"committed transaction compensation failed: {exc}"
            ) from exc
        compensated = replace(transaction, status="compensated")
        self._store.save(compensated)
        return compensated

    def _exact_transaction(
        self,
        transaction_id: str,
        expected_cut_id: str,
    ) -> ResolveTransaction:
        transaction = self._store.load(transaction_id)
        if transaction is None or transaction.cut_id != expected_cut_id:
            raise ResolveTransactionError("transaction identity does not match the requested cut")
        return transaction


def _plan_fingerprint(plan: MaterializationPlan) -> str:
    encoded = json.dumps(
        asdict(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _transaction_id(
    plan: MaterializationPlan,
    canonical: TimelineIdentity,
    *,
    plan_fingerprint: str,
    preview_path: Path,
    subtitle_path: Path,
) -> str:
    encoded = json.dumps(
        {
            "canonical_name": canonical.name,
            "canonical_uid": canonical.uid,
            "episode_id": plan.episode_id,
            "cut_id": plan.cut_id,
            "plan_id": plan.plan_id,
            "plan_fingerprint": plan_fingerprint,
            "preview_path": str(preview_path),
            "subtitle_path": str(subtitle_path),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"resolve-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _validate_preview(preview: PreviewRender) -> None:
    if preview.video_codec.lower() not in {"h264", "avc1"}:
        raise ResolveTransactionError("preview video codec is not H.264")
    if preview.audio_codec is not None and preview.audio_codec.lower() != "aac":
        raise ResolveTransactionError("preview audio codec is not AAC or absent")
    if not math.isfinite(preview.duration_sec) or preview.duration_sec <= 0:
        raise ResolveTransactionError("preview duration is not positive and finite")


def _commit_receipt(transaction: ResolveTransaction) -> CommitReceipt:
    if (
        transaction.transaction_receipt_id is None
        or transaction.rollback_ref is None
        or not transaction.backup_retained
    ):
        raise ResolveTransactionError("committed transaction has no retained-backup receipt")
    return CommitReceipt(
        transaction_id=transaction.transaction_id,
        cut_id=transaction.cut_id,
        work_uid=transaction.workspace.work.uid,
        transaction_receipt_id=transaction.transaction_receipt_id,
        rollback_ref=transaction.rollback_ref,
        backup_retained=True,
    )
