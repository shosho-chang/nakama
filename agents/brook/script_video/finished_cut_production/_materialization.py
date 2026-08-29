"""Private, fail-closed coordination from an exact plan to a staged Candidate."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from ._assets import AssetContractError, AssetKind, AssetResolver, ResolvedAsset
from ._commands import ApprovedCutCommand, _is_authoritative_approved_cut
from ._context import EditorialCutContext
from ._records import (
    MaterializationPlan,
    ReleaseArtifact,
    StagedReleaseCandidate,
    _mint_staged_release_candidate,
)
from ._release import (
    FinishedCutReleaseLifecycle,
    ReleaseLifecycleError,
    _artifact_from_receipt,
    _measure,
)
from ._resolve import (
    ResolveTransaction,
    ResolveTransactionError,
    ResolveTransactionManager,
    TimelineIdentity,
    TimelineSnapshot,
)
from ._resolve_davinci import ResolveTimelineState


class MaterializationError(ValueError):
    """An exact Finished Cut plan cannot safely reach Resolve preview_ready."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class _ProductionRunReader(Protocol):
    def load_run(self, command_id: str) -> object | None: ...


@dataclass(frozen=True, slots=True)
class CanonicalTimelineInspection:
    """UID-bound, read-only base facts captured before the first mutation."""

    episode_id: str
    cut_id: str
    canonical: TimelineIdentity
    editorial_master_content_hash: str
    editorial_master_media_sha256: str
    timeline_frame_rate: float
    editorial_master_frame_rate: float
    editorial_master_duration_sec: float
    state: ResolveTimelineState
    baseline: TimelineSnapshot


@dataclass(frozen=True, slots=True)
class MaterializationPreparation:
    """Private, uncommitted result of an exact preview-ready preparation."""

    command_id: str
    run_id: str
    plan_id: str
    status: Literal["preview_ready"]
    transaction_id: str
    subtitle_sha256: str
    candidate: StagedReleaseCandidate


class _CanonicalTimelineAuthority(Protocol):
    def inspect(
        self,
        *,
        episode_id: str,
        cut_id: str,
        editorial_master_content_hash: str,
    ) -> tuple[CanonicalTimelineInspection, ...]: ...


class MaterializationCoordinator:
    """Keep all read-only preflight ahead of the first Timeline mutation."""

    def __init__(
        self,
        *,
        run_store: _ProductionRunReader,
        canonical_authority: _CanonicalTimelineAuthority,
        assets: AssetResolver,
        transactions: ResolveTransactionManager,
        releases: FinishedCutReleaseLifecycle,
        episode_root: Path,
    ) -> None:
        self._run_store = run_store
        self._canonical_authority = canonical_authority
        self._assets = assets
        self._transactions = transactions
        self._releases = releases
        self._episode_root = Path(episode_root)

    def prepare(self, command_id: str) -> MaterializationPreparation:
        stored = self._run_store.load_run(command_id)
        if stored is None:
            raise MaterializationError(
                "ProductionRun is missing",
                reason_code="production_run_missing",
            )
        view = stored.view
        if view.status != "review_ready":
            raise MaterializationError(
                "ProductionRun is not review_ready",
                reason_code="production_run_not_review_ready",
            )
        if view.materialization_plan is None:
            raise MaterializationError(
                "MaterializationPlan is missing",
                reason_code="materialization_plan_missing",
            )
        plan = view.materialization_plan
        context = getattr(view, "editorial_context", None)
        command = stored.command
        if not isinstance(plan, MaterializationPlan) or not isinstance(
            context, EditorialCutContext
        ):
            raise MaterializationError(
                "Materialization authority is not a typed current plan and context",
                reason_code="authority_chain_mismatch",
            )
        if (
            not isinstance(command, ApprovedCutCommand)
            or not _is_authoritative_approved_cut(command, command_id)
            or re.fullmatch(r"[0-9a-f]{64}", command.editorial_master_id) is None
            or (
                command.command_id,
                command.episode_id,
                command.cut_id,
                command.format,
                command.editorial_master_id,
                command.tight_cut_id,
            )
            != (
                command_id,
                plan.episode_id,
                plan.cut_id,
                plan.format,
                context.editorial_master_id,
                context.tight_cut_id,
            )
        ):
            raise MaterializationError(
                "command, plan, and Editorial Cut Context do not share one authority chain",
                reason_code="authority_chain_mismatch",
            )
        _validate_context_contract(context)
        if (
            view.command_id != command_id
            or plan.command_id != command_id
            or plan.run_id != view.run_id
            or (
                context.episode_id,
                context.cut_id,
                context.format,
            )
            != (plan.episode_id, plan.cut_id, plan.format)
        ):
            raise MaterializationError(
                "command, run, plan, and context identities differ",
                reason_code="authority_chain_mismatch",
            )
        _validate_final_assets(plan, self._assets)
        subtitle_path, preview_path = _materialization_paths(
            self._episode_root,
            plan,
            context,
        )
        journal_path = subtitle_path.parent / "materialization.json"
        prior_journal = _read_materialization_journal(journal_path)
        if prior_journal is not None:
            return self._reopen_prior_preparation(
                prior_journal,
                command=command,
                plan=plan,
                context=context,
                subtitle_path=subtitle_path,
                preview_path=preview_path,
            )
        try:
            inspections = self._canonical_authority.inspect(
                episode_id=plan.episode_id,
                cut_id=plan.cut_id,
                editorial_master_content_hash=context.editorial_master_id,
            )
        except ValueError as error:
            reason_code = getattr(error, "reason_code", None)
            raise MaterializationError(
                "canonical Timeline authority rejected materialization preflight",
                reason_code=(
                    reason_code
                    if isinstance(reason_code, str) and reason_code
                    else "canonical_authority_failed"
                ),
            ) from error
        if not inspections:
            raise MaterializationError(
                "canonical Timeline UID is unknown",
                reason_code="canonical_timeline_unknown",
            )
        if len(inspections) != 1:
            raise MaterializationError(
                "canonical Timeline UID is ambiguous",
                reason_code="canonical_timeline_ambiguous",
            )
        inspection = inspections[0]
        _validate_editorial_base(inspection, context)
        subtitle_sha256 = _stage_review_subtitle(subtitle_path, context)
        try:
            transaction = self._transactions.prepare(
                plan,
                canonical=inspection.canonical,
                preview_path=preview_path,
                subtitle_path=subtitle_path,
                expected_baseline=inspection.baseline,
            )
        except ResolveTransactionError as error:
            raise MaterializationError(
                "Resolve transaction could not prepare the exact plan",
                reason_code="resolve_prepare_failed",
            ) from error
        _validate_prepared_transaction(
            transaction,
            plan=plan,
            inspection=inspection,
            preview_path=preview_path,
            subtitle_path=subtitle_path,
            context=context,
        )
        try:
            candidate = self._releases.stage_candidate(
                plan,
                editorial_master_id=context.editorial_master_id,
                winner_id=command.winner_id,
                tight_cut_id=context.tight_cut_id,
                transaction_id=transaction.transaction_id,
                preview_path=preview_path,
                subtitle_path=subtitle_path,
            )
        except ReleaseLifecycleError as error:
            raise MaterializationError(
                "preview_ready transaction cannot stage its exact Candidate",
                reason_code="candidate_staging_failed",
            ) from error
        preparation = MaterializationPreparation(
            command_id=command_id,
            run_id=plan.run_id,
            plan_id=plan.plan_id,
            status="preview_ready",
            transaction_id=transaction.transaction_id,
            subtitle_sha256=subtitle_sha256,
            candidate=candidate,
        )
        payload = _preparation_payload(preparation)
        _write_materialization_journal(journal_path, payload)
        return preparation

    def _reopen_prior_preparation(
        self,
        payload: dict[str, object],
        *,
        command: ApprovedCutCommand,
        plan: MaterializationPlan,
        context: EditorialCutContext,
        subtitle_path: Path,
        preview_path: Path,
    ) -> MaterializationPreparation:
        transaction_id = payload.get("transaction_id")
        if (
            not isinstance(transaction_id, str)
            or re.fullmatch(r"resolve-[0-9a-f]{24}", transaction_id) is None
        ):
            raise MaterializationError(
                "persisted materialization transaction identity is invalid",
                reason_code="materialization_journal_conflict",
            )
        try:
            subtitle_payload = subtitle_path.read_bytes()
        except OSError as error:
            raise MaterializationError(
                "persisted materialization subtitle is unavailable",
                reason_code="materialization_journal_conflict",
            ) from error
        subtitle_sha256 = hashlib.sha256(subtitle_payload).hexdigest()
        _verify_srt_bytes(
            subtitle_payload,
            context,
            expected_digest=subtitle_sha256,
        )
        try:
            transaction = self._transactions.inspect_transaction(transaction_id)
        except ResolveTransactionError as error:
            raise MaterializationError(
                "persisted materialization transaction is unavailable",
                reason_code="materialization_journal_conflict",
            ) from error
        status = transaction.get("status")
        if (
            transaction.get("transaction_id") != transaction_id
            or transaction.get("cut_id") != plan.cut_id
            or status not in {"preview_ready", "committed"}
            or (
                status == "committed"
                and (
                    not isinstance(transaction.get("transaction_receipt_id"), str)
                    or not transaction.get("transaction_receipt_id")
                    or not isinstance(transaction.get("rollback_ref"), str)
                    or not transaction.get("rollback_ref")
                    or transaction.get("backup_retained") is not True
                )
            )
        ):
            raise MaterializationError(
                "persisted materialization transaction is not exact",
                reason_code="materialization_journal_conflict",
            )
        if status == "preview_ready":
            try:
                candidate = self._releases.stage_candidate(
                    plan,
                    editorial_master_id=context.editorial_master_id,
                    winner_id=command.winner_id,
                    tight_cut_id=context.tight_cut_id,
                    transaction_id=transaction_id,
                    preview_path=preview_path,
                    subtitle_path=subtitle_path,
                )
            except ReleaseLifecycleError as error:
                raise MaterializationError(
                    "persisted materialization Candidate is not exact",
                    reason_code="materialization_journal_conflict",
                ) from error
        else:
            candidate = _candidate_from_prior_payload(
                payload.get("candidate"),
                command=command,
                plan=plan,
                context=context,
                transaction_id=transaction_id,
                episode_root=self._episode_root,
                subtitle_path=subtitle_path,
                preview_path=preview_path,
            )
        preparation = MaterializationPreparation(
            command_id=command.command_id,
            run_id=plan.run_id,
            plan_id=plan.plan_id,
            status="preview_ready",
            transaction_id=transaction_id,
            subtitle_sha256=subtitle_sha256,
            candidate=candidate,
        )
        if _canonical_json(payload) != _canonical_json(_preparation_payload(preparation)):
            raise MaterializationError(
                "persisted materialization Candidate differs from exact current preparation",
                reason_code="materialization_journal_conflict",
            )
        return preparation


def _candidate_from_prior_payload(
    value: object,
    *,
    command: ApprovedCutCommand,
    plan: MaterializationPlan,
    context: EditorialCutContext,
    transaction_id: str,
    episode_root: Path,
    subtitle_path: Path,
    preview_path: Path,
) -> StagedReleaseCandidate:
    if not isinstance(value, dict):
        raise MaterializationError(
            "persisted materialization Candidate is invalid",
            reason_code="materialization_journal_conflict",
        )
    try:
        preview = _artifact_from_receipt(value.get("preview"))
        subtitle = _artifact_from_receipt(value.get("subtitle"))
    except ReleaseLifecycleError as error:
        raise MaterializationError(
            "persisted materialization Candidate artifacts are invalid",
            reason_code="materialization_journal_conflict",
        ) from error
    _verify_prior_artifact(
        preview,
        expected_path=preview_path,
        episode_root=episode_root,
    )
    _verify_prior_artifact(
        subtitle,
        expected_path=subtitle_path,
        episode_root=episode_root,
    )
    candidate_core = {
        "episode_id": plan.episode_id,
        "cut_id": plan.cut_id,
        "format": plan.format,
        "command_id": plan.command_id,
        "run_id": plan.run_id,
        "editorial_master_id": context.editorial_master_id,
        "winner_id": command.winner_id,
        "tight_cut_id": context.tight_cut_id,
        "director_acceptance_id": plan.director_acceptance_id,
        "dp_acceptance_id": plan.dp_acceptance_id,
        "visual_acceptance_id": plan.visual_acceptance_id,
        "materialization_plan": asdict(plan),
        "preview": asdict(preview),
        "subtitle": asdict(subtitle),
        "preview_ready_transaction_id": transaction_id,
    }
    candidate_id = f"candidate-{hashlib.sha256(_canonical_json(candidate_core)).hexdigest()[:24]}"
    candidate = _mint_staged_release_candidate(
        candidate_id=candidate_id,
        episode_id=plan.episode_id,
        cut_id=plan.cut_id,
        format=plan.format,
        command_id=plan.command_id,
        run_id=plan.run_id,
        editorial_master_id=context.editorial_master_id,
        winner_id=command.winner_id,
        tight_cut_id=context.tight_cut_id,
        director_acceptance_id=plan.director_acceptance_id,
        dp_acceptance_id=plan.dp_acceptance_id,
        visual_acceptance_id=plan.visual_acceptance_id,
        materialization_plan=plan,
        preview=preview,
        subtitle=subtitle,
        preview_ready_transaction_id=transaction_id,
    )
    if _canonical_json(value) != _canonical_json(asdict(candidate)):
        raise MaterializationError(
            "persisted materialization Candidate identity differs",
            reason_code="materialization_journal_conflict",
        )
    if (
        preview.duration_sec is None
        or abs(preview.duration_sec - context.duration_sec) > 1e-6
        or subtitle.sha256 != hashlib.sha256(_render_srt(context)).hexdigest()
    ):
        raise MaterializationError(
            "persisted materialization Candidate artifacts differ",
            reason_code="materialization_journal_conflict",
        )
    return candidate


def _verify_prior_artifact(
    artifact: ReleaseArtifact,
    *,
    expected_path: Path,
    episode_root: Path,
) -> None:
    root = Path(episode_root).resolve()
    expected = Path(expected_path).resolve()
    try:
        relative = expected.relative_to(root).as_posix()
        # Streamed: this runs on resume against the same ~1 GB preview.
        size, digest = _measure(expected)
    except (OSError, ValueError) as error:
        raise MaterializationError(
            "persisted materialization artifact is unavailable",
            reason_code="materialization_journal_conflict",
        ) from error
    if (
        artifact.path != relative
        or not size
        or artifact.bytes != size
        or artifact.sha256 != digest
    ):
        raise MaterializationError(
            "persisted materialization artifact bytes differ",
            reason_code="materialization_journal_conflict",
        )


def _validate_prepared_transaction(
    transaction: ResolveTransaction,
    *,
    plan: MaterializationPlan,
    inspection: CanonicalTimelineInspection,
    preview_path: Path,
    subtitle_path: Path,
    context: EditorialCutContext,
) -> None:
    preview = transaction.preview
    if (
        transaction.status != "preview_ready"
        or transaction.episode_id != plan.episode_id
        or transaction.cut_id != plan.cut_id
        or transaction.plan_id != plan.plan_id
        or transaction.canonical != inspection.canonical
        or transaction.baseline != inspection.baseline
        or transaction.subtitle_path != subtitle_path
        or preview.path != preview_path
    ):
        raise MaterializationError(
            "Resolve transaction does not bind the exact plan, base, and artifacts",
            reason_code="preview_transaction_mismatch",
        )
    if (
        preview.video_codec.lower() not in {"h264", "avc1"}
        or preview.audio_codec is None
        or preview.audio_codec.lower() != "aac"
        or not math.isfinite(preview.duration_sec)
        or abs(preview.duration_sec - context.duration_sec) > 1.0 / inspection.timeline_frame_rate
        or not preview.path.is_file()
    ):
        raise MaterializationError(
            "Resolve preview codec, duration, or object contract differs",
            reason_code="preview_probe_failed",
        )


def _preparation_payload(preparation: MaterializationPreparation) -> dict[str, object]:
    return {
        "command_id": preparation.command_id,
        "run_id": preparation.run_id,
        "plan_id": preparation.plan_id,
        "status": preparation.status,
        "transaction_id": preparation.transaction_id,
        "subtitle_sha256": preparation.subtitle_sha256,
        "candidate": asdict(preparation.candidate),
    }


def _read_materialization_journal(path: Path) -> dict[str, object] | None:
    staging = path.with_name(f".{path.name}.staging")
    if staging.exists():
        raise MaterializationError(
            "incomplete materialization journal exists",
            reason_code="materialization_journal_incomplete",
        )
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MaterializationError(
            "materialization journal is unreadable",
            reason_code="materialization_journal_invalid",
        ) from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "payload_sha256", "payload"}
        or document.get("schema") != "nakama.finished-cut-materialization.v1"
        or not isinstance(document.get("payload"), dict)
    ):
        raise MaterializationError(
            "materialization journal schema is invalid",
            reason_code="materialization_journal_invalid",
        )
    payload = cast(dict[str, object], document["payload"])
    if document.get("payload_sha256") != hashlib.sha256(_canonical_json(payload)).hexdigest():
        raise MaterializationError(
            "materialization journal checksum differs",
            reason_code="materialization_journal_invalid",
        )
    return payload


def _write_materialization_journal(path: Path, payload: dict[str, object]) -> None:
    envelope = {
        "schema": "nakama.finished-cut-materialization.v1",
        "payload_sha256": hashlib.sha256(_canonical_json(payload)).hexdigest(),
        "payload": payload,
    }
    encoded = _canonical_json(envelope) + b"\n"
    staging = path.with_name(f".{path.name}.staging")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with staging.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
        if path.read_bytes() != encoded:
            raise MaterializationError(
                "materialization journal bytes differ after atomic replace",
                reason_code="materialization_journal_invalid",
            )
    except OSError as error:
        raise MaterializationError(
            "materialization journal could not persist atomically",
            reason_code="materialization_journal_write_failed",
        ) from error
    finally:
        staging.unlink(missing_ok=True)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise MaterializationError(
            "materialization identity is not canonical JSON",
            reason_code="materialization_journal_invalid",
        ) from error


def _materialization_paths(
    episode_root: Path,
    plan: MaterializationPlan,
    context: EditorialCutContext,
) -> tuple[Path, Path]:
    identity = hashlib.sha256(
        json.dumps(
            {
                "plan": asdict(plan),
                "editorial_master_id": context.editorial_master_id,
                "tight_cut_id": context.tight_cut_id,
                "cues": [asdict(cue) for cue in context.cues],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()[:24]
    workspace = Path(episode_root).resolve() / "highlights" / "staging" / "finished-cut" / identity
    return workspace / "review.srt", workspace / "preview.mp4"


def _stage_review_subtitle(path: Path, context: EditorialCutContext) -> str:
    payload = _render_srt(context)
    expected_digest = hashlib.sha256(payload).hexdigest()
    if path.exists():
        try:
            current = path.read_bytes()
        except OSError as error:
            raise MaterializationError(
                "staged review subtitle is unreadable",
                reason_code="subtitle_staging_conflict",
            ) from error
        if current != payload:
            raise MaterializationError(
                "staged review subtitle differs from current cue authority",
                reason_code="subtitle_staging_conflict",
            )
        _verify_srt_bytes(current, context, expected_digest=expected_digest)
        return expected_digest
    staging = path.with_name(f".{path.name}.staging")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with staging.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
        written = path.read_bytes()
    except OSError as error:
        raise MaterializationError(
            "review subtitle could not be staged atomically",
            reason_code="subtitle_staging_failed",
        ) from error
    finally:
        staging.unlink(missing_ok=True)
    _verify_srt_bytes(written, context, expected_digest=expected_digest)
    return expected_digest


def _render_srt(context: EditorialCutContext) -> bytes:
    blocks: list[str] = []
    previous_end_ms = -1
    for index, cue in enumerate(context.cues, start=1):
        start_ms = _cue_milliseconds(cue.t0)
        end_ms = _cue_milliseconds(cue.t1)
        if (
            not cue.text
            or cue.text.startswith("\ufeff")
            or "\x00" in cue.text
            or start_ms < previous_end_ms
            or start_ms >= end_ms
            or end_ms > _cue_milliseconds(context.duration_sec)
        ):
            raise MaterializationError(
                "current cue timing or text cannot produce a review subtitle",
                reason_code="subtitle_contract_drift",
            )
        blocks.append(
            f"{index}\n{_srt_timestamp(start_ms)} --> {_srt_timestamp(end_ms)}\n{cue.text}"
        )
        previous_end_ms = end_ms
    if not blocks:
        raise MaterializationError(
            "current cue authority is empty",
            reason_code="subtitle_contract_drift",
        )
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


def _cue_milliseconds(value: float) -> int:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise MaterializationError(
            "current cue time is invalid",
            reason_code="subtitle_contract_drift",
        )
    return round(value * 1000)


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def _verify_srt_bytes(
    payload: bytes,
    context: EditorialCutContext,
    *,
    expected_digest: str,
) -> None:
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MaterializationError(
            "staged review subtitle is not UTF-8",
            reason_code="subtitle_staging_conflict",
        ) from error
    if decoded.startswith("\ufeff") or payload != _render_srt(context):
        raise MaterializationError(
            "staged review subtitle cue contract differs",
            reason_code="subtitle_staging_conflict",
        )
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise MaterializationError(
            "staged review subtitle digest differs",
            reason_code="subtitle_staging_conflict",
        )


def _validate_final_assets(plan: MaterializationPlan, assets: AssetResolver) -> None:
    verified: dict[str, ResolvedAsset] = {}
    for component in plan.components:
        reference = component.asset_ref
        if not isinstance(reference, str) or not reference:
            raise MaterializationError(
                "materialized component has no final asset reference",
                reason_code="final_asset_unavailable",
            )
        resolved = verified.get(reference)
        if resolved is None:
            try:
                resolved = assets.resolve_active_asset(reference)
            except (AssetContractError, OSError) as error:
                raise MaterializationError(
                    "component final asset is unavailable in the Active Store",
                    reason_code="final_asset_unavailable",
                ) from error
            if resolved.record.reference != reference or resolved.record.compact_receipt is None:
                raise MaterializationError(
                    "Active Store result does not bind the exact component reference",
                    reason_code="final_asset_identity_mismatch",
                )
            path = resolved.path
            if path is None:
                raise MaterializationError(
                    "component final asset has no materializable object",
                    reason_code="final_asset_unavailable",
                )
            try:
                if not path.is_file() or _file_sha256(path) != resolved.record.digest:
                    raise MaterializationError(
                        "component object bytes differ from its Active Store digest",
                        reason_code="final_asset_identity_mismatch",
                    )
            except OSError as error:
                raise MaterializationError(
                    "component final asset object is unreadable",
                    reason_code="final_asset_unavailable",
                ) from error
            verified[reference] = resolved
        if component.implementation_kind == "stock_video":
            width = resolved.record.width
            height = resolved.record.height
            if (
                resolved.record.kind is not AssetKind.STOCK
                or type(width) is not int
                or type(height) is not int
                or width <= height
                or width * 9 != height * 16
            ):
                raise MaterializationError(
                    "Stock component is not native 16:9 landscape",
                    reason_code="stock_not_landscape_16_9",
                )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_context_contract(context: EditorialCutContext) -> None:
    if (
        not math.isfinite(context.duration_sec)
        or context.duration_sec <= 0
        or not context.source_ranges
        or not context.cues
    ):
        raise MaterializationError(
            "Editorial Cut Context duration, ranges, or cues are invalid",
            reason_code="authority_chain_mismatch",
        )
    source_total = 0.0
    previous_source_end = -1.0
    for source in context.source_ranges:
        if (
            not math.isfinite(source.t0)
            or not math.isfinite(source.t1)
            or source.t0 < 0
            or source.t0 >= source.t1
            or source.t0 < previous_source_end
        ):
            raise MaterializationError(
                "Editorial Cut Context source ranges are invalid",
                reason_code="authority_chain_mismatch",
            )
        source_total += source.t1 - source.t0
        previous_source_end = source.t1
    if not math.isclose(source_total, context.duration_sec, rel_tol=0.0, abs_tol=1e-6):
        raise MaterializationError(
            "Editorial Cut Context source ranges do not equal its duration",
            reason_code="authority_chain_mismatch",
        )
    cue_ids: set[str] = set()
    previous_cue_end = -1.0
    for cue in context.cues:
        if (
            not cue.cue_id
            or cue.cue_id in cue_ids
            or not cue.text
            or not math.isfinite(cue.t0)
            or not math.isfinite(cue.t1)
            or cue.t0 < previous_cue_end
            or cue.t0 < 0
            or cue.t0 >= cue.t1
            or cue.t1 > context.duration_sec
        ):
            raise MaterializationError(
                "Editorial Cut Context cue contract is invalid",
                reason_code="authority_chain_mismatch",
            )
        cue_ids.add(cue.cue_id)
        previous_cue_end = cue.t1


def _validate_editorial_base(
    inspection: CanonicalTimelineInspection,
    context: EditorialCutContext,
) -> None:
    if (
        inspection.episode_id,
        inspection.cut_id,
    ) != (context.episode_id, context.cut_id) or not (
        inspection.canonical.name and inspection.canonical.uid
    ):
        raise MaterializationError(
            "canonical Timeline inspection belongs to another cut",
            reason_code="canonical_identity_mismatch",
        )
    if (
        re.fullmatch(r"[0-9a-f]{64}", inspection.editorial_master_content_hash) is None
        or context.editorial_master_id != inspection.editorial_master_content_hash
    ):
        raise MaterializationError(
            "Editorial Cut Context does not bind the verified ADR-064 receipt",
            reason_code="editorial_master_content_identity_mismatch",
        )
    if re.fullmatch(r"[0-9a-f]{64}", inspection.editorial_master_media_sha256) is None:
        raise MaterializationError(
            "verified ADR-064 Master media identity is invalid",
            reason_code="editorial_master_media_drift",
        )
    master_duration = inspection.editorial_master_duration_sec
    if (
        isinstance(master_duration, bool)
        or not math.isfinite(master_duration)
        or master_duration <= 0
    ):
        raise MaterializationError(
            "verified ADR-064 Master duration is invalid",
            reason_code="editorial_master_duration_invalid",
        )
    if any(source.t1 > master_duration + 1e-6 for source in context.source_ranges):
        raise MaterializationError(
            "ApprovedCut source range exceeds the verified ADR-064 Master",
            reason_code="source_range_outside_editorial_master",
        )
    timeline_fps = inspection.timeline_frame_rate
    master_fps = inspection.editorial_master_frame_rate
    if (
        isinstance(timeline_fps, bool)
        or isinstance(master_fps, bool)
        or not math.isfinite(timeline_fps)
        or not math.isfinite(master_fps)
        or timeline_fps <= 0
        or master_fps <= 0
        or not math.isclose(timeline_fps, master_fps, rel_tol=0.0, abs_tol=1e-6)
    ):
        raise MaterializationError(
            "canonical Timeline frame rate differs from the Editorial Master",
            reason_code="frame_rate_drift",
        )
    state = inspection.state
    if state.end_frame <= state.start_frame or (
        abs((state.end_frame - state.start_frame) / timeline_fps - context.duration_sec)
        > 1.0 / timeline_fps
    ):
        raise MaterializationError(
            "canonical Timeline duration differs by more than one frame",
            reason_code="timeline_duration_drift",
        )

    protected_tracks = tuple(
        track
        for track in state.tracks
        if track.track_type in {"audio", "subtitle"}
        or (track.track_type == "video" and track.track_index == 1)
    )
    kinds = {track.track_type for track in protected_tracks if track.enabled}
    if kinds != {"video", "audio", "subtitle"} or any(
        not track.enabled for track in protected_tracks
    ):
        raise MaterializationError(
            "protected video, audio, or subtitle track contract differs",
            reason_code="protected_track_drift",
        )
    items_by_track = {
        (track.track_type, track.track_index): tuple(
            sorted(
                (
                    item
                    for item in state.items
                    if (item.track_type, item.track_index) == (track.track_type, track.track_index)
                ),
                key=lambda item: (item.start_frame, item.item_id),
            )
        )
        for track in protected_tracks
    }
    if any(
        track.item_ids
        != tuple(item.item_id for item in items_by_track[(track.track_type, track.track_index)])
        for track in protected_tracks
    ):
        raise MaterializationError(
            "protected track inventory differs from its item contract",
            reason_code="protected_track_drift",
        )

    base_tracks = tuple(
        items
        for (track_type, track_index), items in items_by_track.items()
        if (track_type == "video" and track_index == 1)
        or (track_type == "audio" and items)
    )
    if not base_tracks or any(len(items) != len(context.source_ranges) for items in base_tracks):
        raise MaterializationError(
            "protected V1 or audio source inventory differs",
            reason_code="protected_track_drift",
        )
    for items in base_tracks:
        record_cursor = state.start_frame
        for item, source in zip(items, context.source_ranges, strict=True):
            duration = source.t1 - source.t0
            expected_record_end = record_cursor + round(duration * timeline_fps)
            expected_source_in = round(source.t0 * master_fps)
            expected_source_out = round(source.t1 * master_fps)
            if item.media_digest != inspection.editorial_master_media_sha256:
                raise MaterializationError(
                    "protected V1 or audio media is not the ADR-064 Master",
                    reason_code="editorial_master_media_drift",
                )
            if (
                item.start_frame != record_cursor
                or item.end_frame != expected_record_end
                or item.source_in_frame != expected_source_in
                or item.source_out_frame != expected_source_out
            ):
                raise MaterializationError(
                    "protected V1 or audio source range differs from ApprovedCut",
                    reason_code="source_range_drift",
                )
            record_cursor = expected_record_end
        if record_cursor != state.end_frame:
            raise MaterializationError(
                "protected V1 or audio record spans do not cover the exact cut",
                reason_code="source_range_drift",
            )

    subtitle_items = tuple(
        item
        for (track_type, _), items in items_by_track.items()
        if track_type == "subtitle"
        for item in items
    )
    if len(subtitle_items) != len(context.cues):
        raise MaterializationError(
            "protected subtitle cue count differs from tight context",
            reason_code="subtitle_contract_drift",
        )
    for item, cue in zip(subtitle_items, context.cues, strict=True):
        if (
            item.start_frame != state.start_frame + round(cue.t0 * timeline_fps)
            or item.end_frame != state.start_frame + round(cue.t1 * timeline_fps)
            or _subtitle_text(item.properties) != cue.text
        ):
            raise MaterializationError(
                "protected subtitle timing or text differs from tight context",
                reason_code="subtitle_contract_drift",
            )


def _subtitle_text(properties: tuple[tuple[str, object], ...]) -> str | None:
    values = dict(properties)
    direct = values.get("Text")
    if isinstance(direct, str):
        return direct
    encoded = values.get("timeline_properties")
    if not isinstance(encoded, str):
        return None
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError:
        return None
    text = decoded.get("Text") if isinstance(decoded, dict) else None
    return text if isinstance(text, str) else None
