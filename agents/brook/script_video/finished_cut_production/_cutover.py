"""Three-cut global commit, publication, and deployment coordination."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from ._records import FinishedCutRelease, StagedReleaseCandidate
from ._release import (
    FinishedCutReleaseLifecycle,
    _release_receipt_bytes,
)
from ._resolve import CommitReceipt

_DEPLOYMENT_POINTER_SCHEMA = "nakama.finished-cut-deployment-pointer.v1"
_POINTER_SNAPSHOT_RE = re.compile(r"pointer-[0-9a-f]{64}")

CutoverStatus = Literal[
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
]


class GlobalCutoverError(RuntimeError):
    """The coordinated cutover failed or violates its journal identity."""


@dataclass(frozen=True, slots=True)
class PointerSnapshot:
    index_id: str


@dataclass(frozen=True, slots=True)
class DeploymentSnapshot:
    deployment_id: str


@dataclass(frozen=True, slots=True)
class UnpublishedReleaseIndex:
    index_id: str
    episode_id: str
    release_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GlobalCutoverJournal:
    cutover_id: str
    episode_id: str
    fixed_cut_order: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    transaction_ids: tuple[str, ...]
    target_deployment_id: str
    old_pointer: PointerSnapshot
    old_deployment: DeploymentSnapshot
    status: CutoverStatus = "prepared"
    committed_transaction_ids: tuple[str, ...] = ()
    compensated_transaction_ids: tuple[str, ...] = ()
    releases: tuple[FinishedCutRelease, ...] = ()
    unpublished_index: UnpublishedReleaseIndex | None = None
    pointer_published: bool = False
    deployment_activated: bool = False
    failure: str | None = None


class ResolveTransactions(Protocol):
    def inspect_transaction(self, transaction_id: str) -> dict[str, object]: ...

    def commit(self, transaction_id: str, *, expected_cut_id: str) -> CommitReceipt: ...

    def compensating_rollback(
        self,
        transaction_id: str,
        *,
        expected_cut_id: str,
    ) -> object: ...


class ReleaseSealer(Protocol):
    def seal_candidate(
        self,
        candidate: StagedReleaseCandidate,
    ) -> FinishedCutRelease: ...

    def discard_unpublished(
        self,
        releases: tuple[FinishedCutRelease, ...],
    ) -> None: ...


class CurrentIndexAdapter(Protocol):
    def snapshot_pointer(self) -> PointerSnapshot: ...

    def inspect_pointer(self) -> PointerSnapshot: ...

    def build_index(
        self,
        releases: tuple[FinishedCutRelease, ...],
    ) -> UnpublishedReleaseIndex: ...

    def publish_pointer(self, index: UnpublishedReleaseIndex) -> None: ...

    def restore_pointer(self, snapshot: PointerSnapshot) -> None: ...

    def discard_index(self, index: UnpublishedReleaseIndex) -> None: ...


class DeploymentAdapter(Protocol):
    def snapshot_deployment(self) -> DeploymentSnapshot: ...

    def inspect_deployment(self) -> DeploymentSnapshot: ...

    def activate(self, deployment_id: str) -> None: ...

    def restore_deployment(self, snapshot: DeploymentSnapshot) -> None: ...


class CutoverJournalStore(Protocol):
    def load(self, cutover_id: str) -> GlobalCutoverJournal | None: ...

    def save(self, journal: GlobalCutoverJournal) -> None: ...


class FilesystemReleaseCutoverAdapter:
    """Adapt one Release lifecycle to staged index publication and rollback."""

    def __init__(
        self,
        *,
        lifecycle: FinishedCutReleaseLifecycle,
        episode_id: str,
        rollback_root: Path,
    ) -> None:
        if not episode_id or episode_id != episode_id.strip():
            raise GlobalCutoverError("cutover episode identity is invalid")
        self._lifecycle = lifecycle
        self._episode_id = episode_id
        self._rollback_root = Path(rollback_root).resolve()

    def seal_candidate(
        self,
        candidate: StagedReleaseCandidate,
    ) -> FinishedCutRelease:
        if candidate.episode_id != self._episode_id:
            raise GlobalCutoverError("Candidate belongs to another cutover episode")
        return self._lifecycle.seal_candidate(candidate)

    def discard_unpublished(
        self,
        releases: tuple[FinishedCutRelease, ...],
    ) -> None:
        current = self._read_current()
        for release in releases:
            if release.episode_id != self._episode_id:
                raise GlobalCutoverError("unpublished Release belongs to another episode")
            if release.release_id.encode("utf-8") in current:
                raise GlobalCutoverError("current pointer still references unpublished Release")
            path = self._lifecycle._release_receipt_path(release.release_id)
            if not path.exists():
                continue
            expected = _release_receipt_bytes(release)
            try:
                if path.read_bytes() != expected:
                    raise GlobalCutoverError("unpublished Release receipt differs")
                path.unlink()
            except OSError as exc:
                raise GlobalCutoverError("unpublished Release could not be discarded") from exc

    def snapshot_pointer(self) -> PointerSnapshot:
        payload = self._read_current()
        snapshot = PointerSnapshot(index_id=_pointer_snapshot_id(payload))
        self._lifecycle._write_once(self._snapshot_path(snapshot), payload)
        return snapshot

    def inspect_pointer(self) -> PointerSnapshot:
        payload = self._read_current()
        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return PointerSnapshot(index_id=_pointer_snapshot_id(payload))
        if not isinstance(document, dict) or document.get("schema") != (
            "nakama.finished_cut_review_manifest.v3"
        ):
            return PointerSnapshot(index_id=_pointer_snapshot_id(payload))
        releases = self._lifecycle.inspect_current(self._episode_id)
        manifest_id, expected = self._lifecycle._manifest_payload(releases)
        if payload != expected:
            raise GlobalCutoverError("current v3 pointer is not canonical")
        return PointerSnapshot(index_id=manifest_id)

    def build_index(
        self,
        releases: tuple[FinishedCutRelease, ...],
    ) -> UnpublishedReleaseIndex:
        if not releases or any(release.episode_id != self._episode_id for release in releases):
            raise GlobalCutoverError("Release index does not bind one exact episode")
        manifest_id, payload = self._lifecycle._manifest_payload(releases)
        version_path = self._version_path(manifest_id)
        self._lifecycle._write_once(version_path, payload)
        if version_path.read_bytes() != payload:
            raise GlobalCutoverError("unpublished manifest v3 differs after write")
        return UnpublishedReleaseIndex(
            index_id=manifest_id,
            episode_id=self._episode_id,
            release_ids=tuple(release.release_id for release in releases),
        )

    def publish_pointer(self, index: UnpublishedReleaseIndex) -> None:
        payload = self._verified_index_payload(index)
        current_path = self._lifecycle._current_path()
        staging_path = current_path.with_name(f".{current_path.name}.{index.index_id}.staging")
        self._lifecycle._write_once(staging_path, payload)
        try:
            self._lifecycle.pointer_writer.replace(staging_path, current_path)
        except OSError as exc:
            raise GlobalCutoverError("manifest v3 current pointer publication failed") from exc

    def restore_pointer(self, snapshot: PointerSnapshot) -> None:
        path = self._snapshot_path(snapshot)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise GlobalCutoverError("pre-cutover pointer snapshot is unavailable") from exc
        if _pointer_snapshot_id(payload) != snapshot.index_id:
            raise GlobalCutoverError("pre-cutover pointer snapshot digest differs")
        current_path = self._lifecycle._current_path()
        staging_path = current_path.with_name(
            f".{current_path.name}.{snapshot.index_id}.restore.staging"
        )
        self._lifecycle._write_once(staging_path, payload)
        try:
            self._lifecycle.pointer_writer.replace(staging_path, current_path)
        except OSError as exc:
            raise GlobalCutoverError("pre-cutover pointer restoration failed") from exc

    def discard_index(self, index: UnpublishedReleaseIndex) -> None:
        if self.inspect_pointer().index_id == index.index_id:
            raise GlobalCutoverError("current pointer still references unpublished index")
        path = self._version_path(index.index_id)
        if not path.exists():
            return
        self._verified_index_payload(index)
        try:
            path.unlink()
        except OSError as exc:
            raise GlobalCutoverError("unpublished manifest v3 could not be discarded") from exc

    def _verified_index_payload(self, index: UnpublishedReleaseIndex) -> bytes:
        if index.episode_id != self._episode_id:
            raise GlobalCutoverError("unpublished index belongs to another episode")
        try:
            payload = self._version_path(index.index_id).read_bytes()
            document = json.loads(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GlobalCutoverError("unpublished manifest v3 is unavailable") from exc
        if (
            not isinstance(document, dict)
            or document.get("schema") != "nakama.finished_cut_review_manifest.v3"
            or document.get("manifest_id") != index.index_id
            or document.get("episode_id") != self._episode_id
        ):
            raise GlobalCutoverError("unpublished manifest v3 identity differs")
        rows = document.get("releases")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise GlobalCutoverError("unpublished manifest v3 Release rows are invalid")
        release_ids = tuple(
            row.get("release_id") for row in rows if isinstance(row.get("release_id"), str)
        )
        if sorted(release_ids) != sorted(index.release_ids) or len(release_ids) != len(rows):
            raise GlobalCutoverError("unpublished manifest v3 Release identities differ")
        return payload

    def _read_current(self) -> bytes:
        try:
            payload = self._lifecycle._current_path().read_bytes()
        except OSError as exc:
            raise GlobalCutoverError("pre-cutover current pointer is unavailable") from exc
        if not payload:
            raise GlobalCutoverError("pre-cutover current pointer is empty")
        return payload

    def _snapshot_path(self, snapshot: PointerSnapshot) -> Path:
        if _POINTER_SNAPSHOT_RE.fullmatch(snapshot.index_id) is None:
            raise GlobalCutoverError("pre-cutover pointer snapshot identity is invalid")
        return self._rollback_root / f"{snapshot.index_id}.json"

    def _version_path(self, index_id: str) -> Path:
        if (
            not isinstance(index_id, str)
            or re.fullmatch(r"manifest-[0-9a-f]{24}", index_id) is None
        ):
            raise GlobalCutoverError("manifest v3 identity is invalid")
        return (
            self._lifecycle.episode_root
            / "highlights"
            / "releases"
            / "index"
            / "v3"
            / f"{index_id}.json"
        )


class AtomicDeploymentPointerAdapter:
    """Activate only one pinned deployment through one atomic pointer."""

    def __init__(self, path: Path, *, target_deployment_id: str) -> None:
        self._path = Path(path).resolve()
        if not _deployment_identity(target_deployment_id):
            raise GlobalCutoverError("target deployment identity is invalid")
        self._target_deployment_id = target_deployment_id
        self.inspect_deployment()

    def snapshot_deployment(self) -> DeploymentSnapshot:
        return self.inspect_deployment()

    def inspect_deployment(self) -> DeploymentSnapshot:
        try:
            payload = self._path.read_bytes()
            document = json.loads(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GlobalCutoverError("deployment pointer is unavailable") from exc
        if (
            not isinstance(document, dict)
            or set(document) != {"schema", "deployment_id"}
            or document.get("schema") != _DEPLOYMENT_POINTER_SCHEMA
            or not _deployment_identity(document.get("deployment_id"))
            or payload != _deployment_pointer_bytes(str(document["deployment_id"]))
        ):
            raise GlobalCutoverError("deployment pointer is not canonical")
        return DeploymentSnapshot(str(document["deployment_id"]))

    def activate(self, deployment_id: str) -> None:
        if deployment_id != self._target_deployment_id:
            raise GlobalCutoverError("deployment activation does not match pinned target")
        self._replace(deployment_id)

    def restore_deployment(self, snapshot: DeploymentSnapshot) -> None:
        if not _deployment_identity(snapshot.deployment_id):
            raise GlobalCutoverError("deployment rollback identity is invalid")
        self._replace(snapshot.deployment_id)

    def _replace(self, deployment_id: str) -> None:
        payload = _deployment_pointer_bytes(deployment_id)
        staging = self._staging_path(deployment_id)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if staging.exists():
                if staging.read_bytes() != payload:
                    raise GlobalCutoverError("staged deployment pointer differs")
            else:
                with staging.open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.replace(staging, self._path)
        except GlobalCutoverError:
            raise
        except OSError as exc:
            raise GlobalCutoverError("deployment pointer update failed") from exc

    def _staging_path(self, deployment_id: str) -> Path:
        digest = hashlib.sha256(_deployment_pointer_bytes(deployment_id)).hexdigest()[:24]
        return self._path.with_name(f".{self._path.name}.{digest}.staging")


def _pointer_snapshot_id(payload: bytes) -> str:
    return f"pointer-{hashlib.sha256(payload).hexdigest()}"


def _deployment_identity(value: object) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= 256
        and not any(character in value for character in "/\\{}[]\r\n\t")
    )


def _deployment_pointer_bytes(deployment_id: str) -> bytes:
    return (
        json.dumps(
            {
                "schema": _DEPLOYMENT_POINTER_SCHEMA,
                "deployment_id": deployment_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


class GlobalCutover:
    """Hide fixed ordering and cross-seam publication behind one journaled Interface."""

    def __init__(
        self,
        *,
        resolve: ResolveTransactions,
        sealer: ReleaseSealer,
        current_index: CurrentIndexAdapter,
        deployment: DeploymentAdapter,
        journals: CutoverJournalStore,
        fixed_cut_order: tuple[str, ...],
    ) -> None:
        self._resolve = resolve
        self._sealer = sealer
        self._current_index = current_index
        self._deployment = deployment
        self._journals = journals
        self._fixed_cut_order = fixed_cut_order

    def run(
        self,
        cutover_id: str,
        *,
        candidates: tuple[StagedReleaseCandidate, ...],
        target_deployment_id: str,
    ) -> GlobalCutoverJournal:
        ordered = self._ordered_candidates(candidates)
        existing = self._journals.load(cutover_id)
        if existing is not None:
            self._require_journal_identity(
                existing,
                ordered,
                target_deployment_id,
            )
            if existing.status == "completed":
                return existing
            if existing.status == "rolled_back":
                raise GlobalCutoverError("cutover journal is already rolled back")
            if existing.status in {"rolling_back", "rollback_failed"}:
                self._rollback(
                    existing.cutover_id,
                    existing.failure or "resuming rollback",
                )
                raise GlobalCutoverError("cutover restart completed rollback")
            self._require_resumable_transactions(existing, ordered)
            journal = existing
        else:
            self._require_preview_ready(ordered)
            journal = GlobalCutoverJournal(
                cutover_id=cutover_id,
                episode_id=ordered[0].episode_id,
                fixed_cut_order=self._fixed_cut_order,
                candidate_ids=tuple(candidate.candidate_id for candidate in ordered),
                transaction_ids=tuple(
                    candidate.preview_ready_transaction_id for candidate in ordered
                ),
                target_deployment_id=target_deployment_id,
                old_pointer=self._current_index.snapshot_pointer(),
                old_deployment=self._deployment.snapshot_deployment(),
            )
            self._journals.save(journal)
        try:
            return self._run_forward(journal, ordered)
        except Exception as exc:
            try:
                self._rollback(journal.cutover_id, str(exc))
            except Exception as rollback_exc:
                raise GlobalCutoverError(
                    f"global cutover failed and rollback failed: {rollback_exc}"
                ) from exc
            raise GlobalCutoverError(f"global cutover failed: {exc}") from exc

    def _run_forward(
        self,
        journal: GlobalCutoverJournal,
        ordered: tuple[StagedReleaseCandidate, ...],
    ) -> GlobalCutoverJournal:
        for candidate in ordered:
            transaction_id = candidate.preview_ready_transaction_id
            transaction = self._resolve.inspect_transaction(transaction_id)
            if transaction_id in journal.committed_transaction_ids:
                if transaction.get("status") != "committed":
                    raise GlobalCutoverError(
                        "journaled committed transaction no longer reports committed"
                    )
                continue
            if transaction.get("status") == "committed":
                journal = replace(
                    journal,
                    committed_transaction_ids=(
                        *journal.committed_transaction_ids,
                        transaction_id,
                    ),
                )
                self._journals.save(journal)
                continue
            if transaction.get("status") != "preview_ready":
                raise GlobalCutoverError(
                    "transaction cannot resume global commit from its current status"
                )
            journal = replace(journal, status="committing")
            self._journals.save(journal)
            self._resolve.commit(
                transaction_id,
                expected_cut_id=candidate.cut_id,
            )
            journal = replace(
                journal,
                committed_transaction_ids=(
                    *journal.committed_transaction_ids,
                    transaction_id,
                ),
            )
            self._journals.save(journal)
        journal = replace(journal, status="committed")
        self._journals.save(journal)

        if len(journal.releases) > len(ordered):
            raise GlobalCutoverError("cutover journal contains extra sealed Releases")
        for position, candidate in enumerate(ordered):
            if position < len(journal.releases):
                release = journal.releases[position]
                if (
                    release.cut_id != candidate.cut_id
                    or release.command_id != candidate.command_id
                    or release.run_id != candidate.run_id
                ):
                    raise GlobalCutoverError(
                        "cutover journal sealed Release does not match Candidate"
                    )
                continue
            journal = replace(journal, status="sealing")
            self._journals.save(journal)
            release = self._sealer.seal_candidate(candidate)
            journal = replace(journal, releases=(*journal.releases, release))
            self._journals.save(journal)

        index = journal.unpublished_index
        if index is None:
            index = self._current_index.build_index(journal.releases)
            journal = replace(
                journal,
                status="index_built",
                unpublished_index=index,
            )
            self._journals.save(journal)
        target_pointer = PointerSnapshot(index_id=index.index_id)
        pointer = self._current_index.inspect_pointer()
        if pointer == target_pointer:
            if not journal.pointer_published:
                journal = replace(
                    journal,
                    status="pointer_published",
                    pointer_published=True,
                )
                self._journals.save(journal)
        elif journal.pointer_published:
            raise GlobalCutoverError("published current pointer no longer matches journal")
        else:
            self._current_index.publish_pointer(index)
            journal = replace(
                journal,
                status="pointer_published",
                pointer_published=True,
            )
            self._journals.save(journal)
        target_deployment = DeploymentSnapshot(deployment_id=journal.target_deployment_id)
        deployment = self._deployment.inspect_deployment()
        if deployment == target_deployment:
            activated = True
        elif journal.deployment_activated:
            raise GlobalCutoverError("activated deployment no longer matches cutover journal")
        else:
            self._deployment.activate(journal.target_deployment_id)
            activated = True
        journal = replace(
            journal,
            status="completed",
            deployment_activated=activated,
        )
        self._journals.save(journal)
        return journal

    def _rollback(self, cutover_id: str, failure: str) -> GlobalCutoverJournal:
        journal = self._journals.load(cutover_id)
        if journal is None:
            raise GlobalCutoverError("global cutover journal disappeared during rollback")
        journal = replace(
            journal,
            status="rolling_back",
            failure=failure,
        )
        self._journals.save(journal)
        try:
            if self._deployment.inspect_deployment() != journal.old_deployment:
                self._deployment.restore_deployment(journal.old_deployment)
            if self._current_index.inspect_pointer() != journal.old_pointer:
                self._current_index.restore_pointer(journal.old_pointer)
            if journal.unpublished_index is not None:
                self._current_index.discard_index(journal.unpublished_index)
            if journal.releases:
                self._sealer.discard_unpublished(journal.releases)

            cut_by_transaction = dict(
                zip(
                    journal.transaction_ids,
                    journal.fixed_cut_order,
                    strict=True,
                )
            )
            for transaction_id in reversed(journal.committed_transaction_ids):
                if transaction_id in journal.compensated_transaction_ids:
                    continue
                self._resolve.compensating_rollback(
                    transaction_id,
                    expected_cut_id=cut_by_transaction[transaction_id],
                )
                journal = replace(
                    journal,
                    compensated_transaction_ids=(
                        *journal.compensated_transaction_ids,
                        transaction_id,
                    ),
                )
                self._journals.save(journal)
        except Exception as exc:
            journal = replace(
                journal,
                status="rollback_failed",
                failure=f"{failure}; rollback failed: {exc}",
            )
            self._journals.save(journal)
            raise
        journal = replace(journal, status="rolled_back")
        self._journals.save(journal)
        return journal

    def _ordered_candidates(
        self,
        candidates: tuple[StagedReleaseCandidate, ...],
    ) -> tuple[StagedReleaseCandidate, ...]:
        by_cut = {candidate.cut_id: candidate for candidate in candidates}
        if (
            not self._fixed_cut_order
            or len(candidates) != len(self._fixed_cut_order)
            or len(by_cut) != len(candidates)
            or set(by_cut) != set(self._fixed_cut_order)
        ):
            raise GlobalCutoverError("cutover Candidates do not match fixed cut identities")
        ordered = tuple(by_cut[cut_id] for cut_id in self._fixed_cut_order)
        episode_id = ordered[0].episode_id
        if any(candidate.episode_id != episode_id for candidate in ordered):
            raise GlobalCutoverError("cutover Candidates cannot mix episodes")
        return ordered

    def _require_preview_ready(
        self,
        candidates: tuple[StagedReleaseCandidate, ...],
    ) -> None:
        for candidate in candidates:
            transaction = self._resolve.inspect_transaction(candidate.preview_ready_transaction_id)
            if (
                transaction.get("transaction_id") != candidate.preview_ready_transaction_id
                or transaction.get("cut_id") != candidate.cut_id
                or transaction.get("status") != "preview_ready"
            ):
                raise GlobalCutoverError(
                    "cutover transaction is not exact preview_ready Candidate authority"
                )

    def _require_journal_identity(
        self,
        journal: GlobalCutoverJournal,
        candidates: tuple[StagedReleaseCandidate, ...],
        target_deployment_id: str,
    ) -> None:
        if (
            journal.fixed_cut_order != self._fixed_cut_order
            or journal.episode_id != candidates[0].episode_id
            or journal.candidate_ids != tuple(candidate.candidate_id for candidate in candidates)
            or journal.transaction_ids
            != tuple(candidate.preview_ready_transaction_id for candidate in candidates)
            or journal.target_deployment_id != target_deployment_id
        ):
            raise GlobalCutoverError(
                "restart Candidates or deployment do not match the cutover journal"
            )

    def _require_resumable_transactions(
        self,
        journal: GlobalCutoverJournal,
        candidates: tuple[StagedReleaseCandidate, ...],
    ) -> None:
        for candidate in candidates:
            transaction = self._resolve.inspect_transaction(candidate.preview_ready_transaction_id)
            if (
                transaction.get("transaction_id") != candidate.preview_ready_transaction_id
                or transaction.get("cut_id") != candidate.cut_id
                or transaction.get("status") not in {"preview_ready", "committed"}
            ):
                raise GlobalCutoverError("restart transaction no longer matches its Candidate")
            if (
                candidate.preview_ready_transaction_id in journal.committed_transaction_ids
                and transaction.get("status") != "committed"
            ):
                raise GlobalCutoverError("journaled committed transaction lost committed state")
