"""Resolve-backed, read-only authority for Finished Cut materialization."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agents.brook.script_video.editorial_master import (
    EditorialMasterSelection,
    verify_editorial_master,
)

from ._materialization import CanonicalTimelineInspection
from ._resolve import TimelineIdentity, TimelineSnapshot
from ._resolve_davinci import (
    ResolveFacade,
    ResolveProjectBinding,
    ResolveProjectIdentity,
    ResolveTimelineState,
)

_CACHE_SCHEMA = "nakama.finished-cut-verified-editorial-master.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CanonicalAuthorityError(ValueError):
    """A live Resolve or verified Master fact cannot prove canonical authority."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class VerifiedEditorialMasterContract:
    """Path-private immutable facts extracted from one verified ADR-064 receipt."""

    episode_id: str
    editorial_master_content_hash: str
    master_media_path: Path
    master_media_sha256: str
    master_media_bytes: int
    resolve_project_name: str
    editorial_master_timeline_name: str
    editorial_master_timeline_uid: str
    frame_rate: float
    duration_sec: float


class _EditorialMasterVerifier(Protocol):
    def __call__(
        self,
        episode_root: str | Path,
        *,
        expected_episode_id: str | None = None,
        expected_content_hash: str | None = None,
    ) -> EditorialMasterSelection: ...


class _EditorialMasterContractSource(Protocol):
    def load(
        self,
        *,
        episode_id: str,
        editorial_master_content_hash: str,
    ) -> VerifiedEditorialMasterContract: ...


class _TimelineSnapshotReader(Protocol):
    def snapshot(self, timeline: TimelineIdentity) -> TimelineSnapshot: ...


class VerifiedEditorialMasterContractCache:
    """Verify a large immutable Master once, then reopen checksum-bound facts."""

    def __init__(
        self,
        *,
        episode_root: str | Path,
        cache_path: str | Path,
        verifier: _EditorialMasterVerifier = verify_editorial_master,
    ) -> None:
        self._episode_root = Path(episode_root).resolve()
        self._cache_path = Path(cache_path)
        self._verifier = verifier

    def load(
        self,
        *,
        episode_id: str,
        editorial_master_content_hash: str,
    ) -> VerifiedEditorialMasterContract:
        _require_identity(episode_id, "episode ID")
        _require_sha256(editorial_master_content_hash, "Editorial Master content hash")
        cached = self._read()
        if cached is not None:
            _assert_requested_contract(
                cached,
                episode_id=episode_id,
                editorial_master_content_hash=editorial_master_content_hash,
            )
            return cached

        try:
            selection = self._verifier(
                self._episode_root,
                expected_episode_id=episode_id,
                expected_content_hash=editorial_master_content_hash,
            )
        except Exception as exc:
            raise CanonicalAuthorityError(
                "ADR-064 Editorial Master verification failed",
                reason_code="editorial_master_verification_failed",
            ) from exc
        contract = _contract_from_selection(selection, episode_root=self._episode_root)
        _assert_requested_contract(
            contract,
            episode_id=episode_id,
            editorial_master_content_hash=editorial_master_content_hash,
        )
        self._write(contract)
        return contract

    def _read(self) -> VerifiedEditorialMasterContract | None:
        if not self._cache_path.exists():
            return None
        try:
            document = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CanonicalAuthorityError(
                "verified Editorial Master cache is unreadable",
                reason_code="editorial_master_cache_invalid",
            ) from exc
        if not isinstance(document, dict) or set(document) != {
            "schema",
            "payload_sha256",
            "payload",
        }:
            raise CanonicalAuthorityError(
                "verified Editorial Master cache envelope is invalid",
                reason_code="editorial_master_cache_invalid",
            )
        payload = document.get("payload")
        if document.get("schema") != _CACHE_SCHEMA or not isinstance(payload, dict):
            raise CanonicalAuthorityError(
                "verified Editorial Master cache schema is invalid",
                reason_code="editorial_master_cache_invalid",
            )
        if document.get("payload_sha256") != _sha256_json(payload):
            raise CanonicalAuthorityError(
                "verified Editorial Master cache digest is invalid",
                reason_code="editorial_master_cache_invalid",
            )
        return _contract_from_payload(payload, episode_root=self._episode_root)

    def _write(self, contract: VerifiedEditorialMasterContract) -> None:
        payload = _contract_payload(contract, episode_root=self._episode_root)
        document = {
            "schema": _CACHE_SCHEMA,
            "payload_sha256": _sha256_json(payload),
            "payload": payload,
        }
        encoded = (json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        staging = self._cache_path.with_name(f".{self._cache_path.name}.{uuid.uuid4().hex}.staging")
        try:
            with staging.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, self._cache_path)
        finally:
            if staging.exists():
                staging.unlink()


class ResolveCanonicalTimelineAuthority:
    """Inspect one exact live cut without exposing any Resolve mutation capability."""

    def __init__(
        self,
        *,
        binding: ResolveProjectBinding,
        facade: ResolveFacade,
        timeline_adapter: _TimelineSnapshotReader,
        editorial_master: _EditorialMasterContractSource,
    ) -> None:
        self._binding = binding
        self._facade = facade
        self._timeline_adapter = timeline_adapter
        self._editorial_master = editorial_master

    def inspect(
        self,
        *,
        episode_id: str,
        cut_id: str,
        editorial_master_content_hash: str,
    ) -> tuple[CanonicalTimelineInspection, ...]:
        _require_identity(episode_id, "episode ID")
        _require_identity(cut_id, "cut ID")
        _require_sha256(editorial_master_content_hash, "Editorial Master content hash")
        if self._binding.episode_id != episode_id:
            raise CanonicalAuthorityError(
                "Resolve binding belongs to another episode",
                reason_code="resolve_project_identity_mismatch",
            )
        expected_project = ResolveProjectIdentity(
            episode_id=self._binding.episode_id,
            name=self._binding.project_name,
            uid=self._binding.project_uid,
        )
        if self._facade.project_identity() != expected_project:
            raise CanonicalAuthorityError(
                "live Resolve project differs from its exact binding",
                reason_code="resolve_project_identity_mismatch",
            )
        cut_matches = tuple(cut for cut in self._binding.cuts if cut.cut_id == cut_id)
        if not cut_matches:
            raise CanonicalAuthorityError(
                "cut has no canonical Resolve binding",
                reason_code="canonical_binding_unknown",
            )
        if len(cut_matches) != 1:
            raise CanonicalAuthorityError(
                "cut has ambiguous canonical Resolve bindings",
                reason_code="canonical_binding_ambiguous",
            )
        canonical = cut_matches[0].canonical
        inventory = self._facade.timeline_identities()
        uid_matches = tuple(row for row in inventory if row.uid == canonical.uid)
        exact_matches = tuple(row for row in uid_matches if row == canonical)
        if not uid_matches:
            raise CanonicalAuthorityError(
                "canonical Timeline UID is absent from live Resolve",
                reason_code="canonical_timeline_unknown",
            )
        if len(uid_matches) != 1 or len(exact_matches) != 1:
            raise CanonicalAuthorityError(
                "canonical Timeline UID is not one exact live identity",
                reason_code="canonical_timeline_ambiguous",
            )

        contract = self._editorial_master.load(
            episode_id=episode_id,
            editorial_master_content_hash=editorial_master_content_hash,
        )
        if contract.resolve_project_name != self._binding.project_name:
            raise CanonicalAuthorityError(
                "verified Editorial Master belongs to another Resolve project",
                reason_code="editorial_master_project_mismatch",
            )
        first_state = self._facade.timeline_state(canonical.uid)
        first_baseline = self._timeline_adapter.snapshot(canonical)
        second_state = self._facade.timeline_state(canonical.uid)
        second_baseline = self._timeline_adapter.snapshot(canonical)
        if first_state != second_state or first_baseline != second_baseline:
            raise CanonicalAuthorityError(
                "canonical Timeline changed during its read-only inspection",
                reason_code="canonical_timeline_live_drift",
            )
        frame_rate = _state_frame_rate(first_state)
        return (
            CanonicalTimelineInspection(
                episode_id=episode_id,
                cut_id=cut_id,
                canonical=canonical,
                editorial_master_content_hash=contract.editorial_master_content_hash,
                editorial_master_media_sha256=contract.master_media_sha256,
                timeline_frame_rate=frame_rate,
                editorial_master_frame_rate=contract.frame_rate,
                editorial_master_duration_sec=contract.duration_sec,
                state=first_state,
                baseline=first_baseline,
            ),
        )


def _contract_from_selection(
    selection: EditorialMasterSelection,
    *,
    episode_root: Path,
) -> VerifiedEditorialMasterContract:
    receipt = selection.receipt
    if not isinstance(receipt, dict):
        raise CanonicalAuthorityError(
            "verified Editorial Master receipt is invalid",
            reason_code="editorial_master_contract_invalid",
        )
    artifacts = _mapping(receipt, "artifacts")
    media = _mapping(artifacts, "media")
    project = _mapping(receipt, "project")
    timeline = _mapping(receipt, "timeline")
    try:
        media_path = selection.video_path.resolve(strict=True)
        media_path.relative_to(episode_root)
        media_bytes = _positive_int(media.get("bytes"), "Master media bytes")
        if not media_path.is_file() or media_path.stat().st_size != media_bytes:
            raise ValueError("Master media size differs")
        contract = VerifiedEditorialMasterContract(
            episode_id=_string(receipt.get("episode_id"), "episode ID"),
            editorial_master_content_hash=_sha256(
                receipt.get("content_hash"), "Editorial Master content hash"
            ),
            master_media_path=media_path,
            master_media_sha256=_sha256(media.get("sha256"), "Master media SHA-256"),
            master_media_bytes=media_bytes,
            resolve_project_name=_string(project.get("name"), "Resolve project name"),
            editorial_master_timeline_name=_string(
                timeline.get("name"), "Editorial Master Timeline name"
            ),
            editorial_master_timeline_uid=_string(
                timeline.get("uid"), "Editorial Master Timeline UID"
            ),
            frame_rate=_positive_number(timeline.get("fps"), "Editorial Master frame rate"),
            duration_sec=_positive_number(
                timeline.get("duration_sec"), "Editorial Master duration"
            ),
        )
    except (OSError, ValueError) as exc:
        raise CanonicalAuthorityError(
            "verified Editorial Master receipt facts are invalid",
            reason_code="editorial_master_contract_invalid",
        ) from exc
    return contract


def _contract_payload(
    contract: VerifiedEditorialMasterContract,
    *,
    episode_root: Path,
) -> dict[str, object]:
    try:
        media_relative_path = contract.master_media_path.relative_to(episode_root).as_posix()
    except ValueError as exc:
        raise CanonicalAuthorityError(
            "verified Master media path escapes its episode",
            reason_code="editorial_master_contract_invalid",
        ) from exc
    return {
        "episode_id": contract.episode_id,
        "editorial_master_content_hash": contract.editorial_master_content_hash,
        "master_media_relative_path": media_relative_path,
        "master_media_sha256": contract.master_media_sha256,
        "master_media_bytes": contract.master_media_bytes,
        "resolve_project_name": contract.resolve_project_name,
        "editorial_master_timeline_name": contract.editorial_master_timeline_name,
        "editorial_master_timeline_uid": contract.editorial_master_timeline_uid,
        "frame_rate": contract.frame_rate,
        "duration_sec": contract.duration_sec,
    }


def _contract_from_payload(
    payload: Mapping[str, object],
    *,
    episode_root: Path,
) -> VerifiedEditorialMasterContract:
    expected = {
        "episode_id",
        "editorial_master_content_hash",
        "master_media_relative_path",
        "master_media_sha256",
        "master_media_bytes",
        "resolve_project_name",
        "editorial_master_timeline_name",
        "editorial_master_timeline_uid",
        "frame_rate",
        "duration_sec",
    }
    if set(payload) != expected:
        raise CanonicalAuthorityError(
            "verified Editorial Master cache payload is invalid",
            reason_code="editorial_master_cache_invalid",
        )
    relative = Path(_string(payload.get("master_media_relative_path"), "Master media path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise CanonicalAuthorityError(
            "verified Editorial Master cache path escapes its episode",
            reason_code="editorial_master_cache_invalid",
        )
    try:
        media_path = (episode_root / relative).resolve(strict=True)
        media_path.relative_to(episode_root)
        media_bytes = _positive_int(payload.get("master_media_bytes"), "Master media bytes")
        if not media_path.is_file() or media_path.stat().st_size != media_bytes:
            raise ValueError("Master media size differs")
        return VerifiedEditorialMasterContract(
            episode_id=_string(payload.get("episode_id"), "episode ID"),
            editorial_master_content_hash=_sha256(
                payload.get("editorial_master_content_hash"),
                "Editorial Master content hash",
            ),
            master_media_path=media_path,
            master_media_sha256=_sha256(payload.get("master_media_sha256"), "Master media SHA-256"),
            master_media_bytes=media_bytes,
            resolve_project_name=_string(
                payload.get("resolve_project_name"), "Resolve project name"
            ),
            editorial_master_timeline_name=_string(
                payload.get("editorial_master_timeline_name"),
                "Editorial Master Timeline name",
            ),
            editorial_master_timeline_uid=_string(
                payload.get("editorial_master_timeline_uid"),
                "Editorial Master Timeline UID",
            ),
            frame_rate=_positive_number(payload.get("frame_rate"), "Editorial Master frame rate"),
            duration_sec=_positive_number(payload.get("duration_sec"), "Editorial Master duration"),
        )
    except (OSError, ValueError) as exc:
        raise CanonicalAuthorityError(
            "verified Editorial Master cache facts are invalid",
            reason_code="editorial_master_cache_invalid",
        ) from exc


def _assert_requested_contract(
    contract: VerifiedEditorialMasterContract,
    *,
    episode_id: str,
    editorial_master_content_hash: str,
) -> None:
    if (
        contract.episode_id != episode_id
        or contract.editorial_master_content_hash != editorial_master_content_hash
    ):
        raise CanonicalAuthorityError(
            "verified Editorial Master cache belongs to another authority",
            reason_code="editorial_master_identity_mismatch",
        )


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    nested = value.get(key)
    if not isinstance(nested, Mapping):
        raise CanonicalAuthorityError(
            f"verified Editorial Master {key} is invalid",
            reason_code="editorial_master_contract_invalid",
        )
    return nested


def _require_identity(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CanonicalAuthorityError(
            f"{label} is invalid",
            reason_code="editorial_master_identity_mismatch",
        )


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise CanonicalAuthorityError(
            f"{label} is invalid",
            reason_code="editorial_master_identity_mismatch",
        )


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is invalid")
    return value.strip()


def _sha256(value: object, label: str) -> str:
    result = _string(value, label)
    if _SHA256_RE.fullmatch(result) is None:
        raise ValueError(f"{label} is invalid")
    return result


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} is invalid")
    return value


def _positive_number(value: object, label: str) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} is invalid")
    return result


def _state_frame_rate(state: ResolveTimelineState) -> float:
    try:
        return _positive_number(state.frame_rate, "live Timeline frame rate")
    except ValueError as exc:
        raise CanonicalAuthorityError(
            "live Resolve Timeline frame rate is unavailable",
            reason_code="timeline_frame_rate_unavailable",
        ) from exc


def _sha256_json(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
