#!/usr/bin/env python3
"""Create immutable operator evidence for Memo-first Podcast Subtitle V2."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.brook.podcast_subtitles.adapters.memo_recognition import (  # noqa: E402
    MemoAgentAuditRefV1,
    MemoExecutionReceiptRefV1,
    MemoRecognitionAcceptanceReceiptV1,
    MemoRecognitionManifestV1,
    MemoRecognitionTokenV1,
    MemoRecognizerAdapter,
    load_memo_recognition_manifest,
)
from agents.brook.podcast_subtitles.adapters.normalized_handoff import (  # noqa: E402
    NormalizedAudioHandoffManifestV1,
    VerifiedNormalizedAudioHandoffAdapter,
    wav_duration_ms,
)
from agents.brook.podcast_subtitles.hashing import (  # noqa: E402
    canonical_json_bytes,
    hash_file,
    measure_regular_file,
    sha256_bytes,
)
from agents.brook.podcast_subtitles.memo_boundary import (  # noqa: E402
    MemoSrtAcceptanceReceiptV1,
    MemoSrtBoundaryAuthorityV1,
    MemoSrtReviewCueV1,
    MemoSrtReviewManifestV1,
)
from agents.brook.podcast_subtitles.memo_bundled_runner import (  # noqa: E402
    MemoBundledRunnerRequest,
    execute_memo_bundled_runner,
    load_memo_bundled_runner_execution_receipt,
    load_verified_memo_bundled_runner_execution,
)
from agents.brook.podcast_subtitles.memo_projection import parse_srt  # noqa: E402
from agents.brook.podcast_subtitles.memo_srt_repair import (  # noqa: E402
    load_verified_memo_srt_repair,
    repair_memo_srt_bytes,
)
from agents.brook.podcast_subtitles.memo_vad_gap_repair import (  # noqa: E402
    MemoVadGapRepairInputs,
    MemoVadGapRepairReceiptV1,
    load_verified_memo_vad_gap_repair,
    repair_memo_vad_gap,
)
from agents.brook.podcast_subtitles.ports import (  # noqa: E402
    NormalizeRequest,
    RecognitionRequest,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _MemoTokenExportV1(_StrictModel):
    tokens: tuple[MemoRecognitionTokenV1, ...]

    @model_validator(mode="after")
    def _canonical_ids(self) -> "_MemoTokenExportV1":
        if not self.tokens or any(
            token.id != f"memo-token-{index:06d}"
            for index, token in enumerate(self.tokens, start=1)
        ):
            raise ValueError("Memo token IDs must be canonical and sequential")
        return self


class _MemoRecognitionReviewV1(_StrictModel):
    schema_version: Literal[1] = 1
    contract: Literal["memo-recognition-review-v1"] = "memo-recognition-review-v1"
    episode_id: str
    memo_version: str
    model: Literal["ggml-large-v2.bin"] = "ggml-large-v2.bin"
    language: str
    prompt: str
    normalized_audio_sha256: str
    normalized_audio_size_bytes: int
    normalized_audio_duration_ms: int
    normalized_handoff_manifest_sha256: str
    source_export_sha256: str
    source_export_size_bytes: int
    source_export_kind: Literal["memo_stdout", "memo_srt", "memo_json"]
    raw_source_export_sha256: str | None = None
    raw_source_export_size_bytes: int | None = None
    source_repair_receipt_sha256: str | None = None
    memo_execution_receipt: MemoExecutionReceiptRefV1
    token_source: Literal["canonical_token_export", "memo_srt"]
    token_export_sha256: str
    unresolved_findings: tuple[str, ...] = ()
    tokens: tuple[MemoRecognitionTokenV1, ...]

    @model_validator(mode="after")
    def _valid_tokens(self) -> "_MemoRecognitionReviewV1":
        validated = _MemoTokenExportV1(tokens=self.tokens)
        if validated.tokens[-1].end_ms > self.normalized_audio_duration_ms:
            raise ValueError("Memo recognition token exceeds normalized audio duration")
        previous_end = -1
        for token in validated.tokens:
            if token.start_ms < previous_end:
                raise ValueError("Memo recognition tokens must be ordered and non-overlapping")
            previous_end = token.end_ms
        if self.source_export_kind == "memo_srt" and self.token_source != "memo_srt":
            raise ValueError("Memo SRT recognition must derive tokens from the exact SRT bytes")
        if self.source_export_kind != "memo_srt" and self.token_source != "canonical_token_export":
            raise ValueError("Memo JSON/stdout recognition requires a canonical token export")
        repair_lineage = (
            self.raw_source_export_sha256,
            self.raw_source_export_size_bytes,
            self.source_repair_receipt_sha256,
        )
        if any(value is not None for value in repair_lineage) != all(
            value is not None for value in repair_lineage
        ):
            raise ValueError("Memo recognition review repair lineage must be complete")
        for value in (self.raw_source_export_sha256, self.source_repair_receipt_sha256):
            if value is not None and (
                len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError("Memo recognition review repair digest must be lowercase SHA-256")
        return self


class _MemoRecognitionWorkerAuditV1(_StrictModel):
    schema_version: Literal[1] = 1
    contract: Literal["memo-recognition-worker-audit-v1"] = (
        "memo-recognition-worker-audit-v1"
    )
    episode_id: str
    worker_id: str
    normalized_audio_sha256: str
    normalized_audio_size_bytes: int
    source_export_sha256: str
    source_export_size_bytes: int
    review_manifest_sha256: str
    token_export_sha256: str
    memo_execution_receipt_sha256: str
    reviewed_item_count: int
    qc_passed: bool
    accepted: bool
    unresolved_findings: tuple[str, ...] = ()


class _MemoCueWorkerAuditV1(_StrictModel):
    schema_version: Literal[1] = 1
    contract: Literal["memo-cue-worker-audit-v1"] = "memo-cue-worker-audit-v1"
    episode_id: str
    worker_id: str
    normalized_audio_sha256: str
    normalized_audio_size_bytes: int
    source_export_sha256: str
    source_export_size_bytes: int
    review_manifest_sha256: str
    recognition_manifest_sha256: str
    reviewed_item_count: int
    qc_passed: bool
    accepted: bool
    unresolved_findings: tuple[str, ...] = ()


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def _seal_normalized(args: argparse.Namespace) -> int:
    audio = Path(args.audio)
    digest, size = measure_regular_file(audio)
    manifest = NormalizedAudioHandoffManifestV1(
        normalized_audio_sha256=digest,
        normalized_audio_size_bytes=size,
        normalized_audio_duration_ms=wav_duration_ms(audio),
        accepted_at=datetime.fromisoformat(args.accepted_at),
    )
    _write_new(Path(args.output), canonical_json_bytes(manifest))
    return 0


def _load_canonical_model(path: Path, model: type[BaseModel]) -> BaseModel:
    payload = path.read_bytes()
    json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    value = model.model_validate_json(payload, strict=True)
    if canonical_json_bytes(value) != payload:
        raise ValueError(f"{path} must use canonical JSON bytes")
    return value


def _load_worker_audits(
    args: argparse.Namespace,
    *,
    model: type[_MemoRecognitionWorkerAuditV1] | type[_MemoCueWorkerAuditV1],
    expected: dict[str, object],
) -> tuple[str, tuple[MemoAgentAuditRefV1, MemoAgentAuditRefV1]]:
    root = Path(args.episode_root).resolve()
    if not root.is_dir():
        raise ValueError("--episode-root must be an existing episode directory")
    episode_id = root.name
    loaded: list[_MemoRecognitionWorkerAuditV1 | _MemoCueWorkerAuditV1] = []
    refs: list[MemoAgentAuditRefV1] = []
    for label, raw_path in (("audit A", args.audit_a), ("audit B", args.audit_b)):
        path = Path(raw_path).resolve()
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"{label} must stay inside the episode root") from exc
        audit = _load_canonical_model(path, model)
        assert isinstance(audit, (_MemoRecognitionWorkerAuditV1, _MemoCueWorkerAuditV1))
        actual = audit.model_dump(mode="python")
        mismatched = [key for key, value in expected.items() if actual.get(key) != value]
        if audit.episode_id != episode_id or mismatched:
            raise ValueError(f"{label} source/review binding mismatch: {mismatched}")
        if (
            not audit.worker_id.strip()
            or audit.worker_id != audit.worker_id.strip()
            or audit.accepted is not True
            or audit.qc_passed is not True
            or audit.unresolved_findings
        ):
            raise ValueError(f"{label} did not produce a resolved acceptance")
        digest, size = measure_regular_file(path)
        loaded.append(audit)
        refs.append(
            MemoAgentAuditRefV1(
                contract=audit.contract,
                worker_id=audit.worker_id,
                path=relative,
                sha256=digest,
                size_bytes=size,
            )
        )
    if loaded[0].worker_id == loaded[1].worker_id:
        raise ValueError("worker audits must come from two independent workers")
    return episode_id, (refs[0], refs[1])


def _episode_relative(root: Path, path: Path, *, label: str) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the episode root") from exc


def _verified_execution_ref(
    args: argparse.Namespace,
    *,
    source_kind: str,
    language: str,
    prompt: str,
) -> MemoExecutionReceiptRefV1:
    root = Path(args.episode_root).resolve()
    if not root.is_dir():
        raise ValueError("--episode-root must be an existing episode directory")
    audio = Path(args.normalized_audio)
    source = Path(args.source_export)
    execution_source = (
        Path(args.raw_source_export) if args.raw_source_export else source
    )
    receipt_path = Path(args.memo_execution_receipt)
    output_srt = Path(args.memo_output_srt)
    stdout = Path(args.memo_stdout)
    stderr = Path(args.memo_stderr)
    relative = _episode_relative(root, receipt_path, label="Memo execution receipt")
    for label, path in (
        ("normalized audio", audio),
        ("Memo source export", source),
        ("Memo output SRT", output_srt),
        ("Memo stdout", stdout),
        ("Memo stderr", stderr),
        ("Memo execution source", execution_source),
    ):
        _episode_relative(root, path, label=label)
    declaration = load_memo_bundled_runner_execution_receipt(receipt_path)
    verified = load_verified_memo_bundled_runner_execution(
        request=MemoBundledRunnerRequest(
            runner=Path(args.memo_runner),
            model=Path(args.memo_model),
            input_wav=audio,
            output_srt=output_srt,
            stdout_output=stdout,
            stderr_output=stderr,
            receipt_output=receipt_path,
            gpu=declaration.gpu,
            language=declaration.language,
            prompt=declaration.prompt,
            max_context=declaration.max_context,
            max_len=declaration.max_len,
        )
    )
    if (verified.language, verified.prompt) != (language, prompt):
        raise ValueError("Memo execution language/prompt differs from recognition review")
    source_identity = measure_regular_file(execution_source)
    expected_source = (
        (verified.output_srt_sha256, verified.output_srt_size_bytes)
        if source_kind == "memo_srt"
        else (verified.stdout_sha256, verified.stdout_size_bytes)
    )
    if source_identity != expected_source:
        raise ValueError("Memo source export is not an output of this execution receipt")
    receipt_hash, receipt_size = measure_regular_file(receipt_path)
    return MemoExecutionReceiptRefV1(
        path=relative,
        runner_path=str(Path(args.memo_runner).resolve()),
        model_path=str(Path(args.memo_model).resolve()),
        input_wav_path=_episode_relative(root, audio, label="normalized audio"),
        output_srt_path=_episode_relative(root, output_srt, label="Memo output SRT"),
        stdout_path=_episode_relative(root, stdout, label="Memo stdout"),
        stderr_path=_episode_relative(root, stderr, label="Memo stderr"),
        sha256=receipt_hash,
        size_bytes=receipt_size,
        runner_sha256=verified.runner_sha256,
        model_sha256=verified.model_sha256,
        input_wav_sha256=verified.input_wav_sha256,
        output_srt_sha256=verified.output_srt_sha256,
        stdout_sha256=verified.stdout_sha256,
        stderr_sha256=verified.stderr_sha256,
    )


def _fresh_verify_execution_ref(
    root: Path, expected: MemoExecutionReceiptRefV1
) -> MemoExecutionReceiptRefV1:
    receipt_path = root / expected.path
    receipt = load_memo_bundled_runner_execution_receipt(receipt_path)
    namespace = argparse.Namespace(
        episode_root=str(root),
        normalized_audio=str(root / expected.input_wav_path),
        source_export=str(root / expected.output_srt_path),
        raw_source_export=None,
        memo_execution_receipt=str(receipt_path),
        memo_runner=expected.runner_path,
        memo_model=expected.model_path,
        memo_output_srt=str(root / expected.output_srt_path),
        memo_stdout=str(root / expected.stdout_path),
        memo_stderr=str(root / expected.stderr_path),
    )
    actual = _verified_execution_ref(
        namespace,
        source_kind="memo_srt",
        language=receipt.language,
        prompt=receipt.prompt,
    )
    if actual != expected:
        raise ValueError("Memo execution receipt lineage is stale or tampered")
    return actual


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _source_repair_lineage(
    args: argparse.Namespace,
    *,
    source_export: Path,
    expected_normalized_audio_hash: str | None = None,
) -> dict[str, str | int | None]:
    raw_value = getattr(args, "raw_source_export", None)
    receipt_value = getattr(args, "repair_receipt", None)
    if bool(raw_value) != bool(receipt_value):
        raise ValueError("--raw-source-export and --repair-receipt must be supplied together")
    if not raw_value:
        return {
            "raw_source_export_sha256": None,
            "raw_source_export_size_bytes": None,
            "source_repair_receipt_sha256": None,
        }
    raw_source = Path(raw_value)
    receipt_path = Path(receipt_value)
    receipt_payload = receipt_path.read_bytes()
    receipt_object = json.loads(receipt_payload, object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(receipt_object, dict):
        raise ValueError("Memo repair receipt must be a JSON object")
    contract = receipt_object.get("contract")
    if contract == "memo-srt-zero-duration-repair-v1":
        receipt = load_verified_memo_srt_repair(
            raw_source=raw_source,
            repaired_source=source_export,
            receipt_path=receipt_path,
        )
        raw_hash = receipt.raw_source_sha256
        raw_size = receipt.raw_source_size_bytes
    elif contract == "memo-bundled-runner-vad-gap-repair-v1":
        loaded_receipt = _load_canonical_model(receipt_path, MemoVadGapRepairReceiptV1)
        assert isinstance(loaded_receipt, MemoVadGapRepairReceiptV1)
        inputs = _vad_gap_inputs_from_args(args, receipt=loaded_receipt)
        verified = VerifiedNormalizedAudioHandoffAdapter(inputs.normalized_handoff).normalize(
            NormalizeRequest(
                source_audio=inputs.normalized_audio,
                expected_source_hash=(
                    expected_normalized_audio_hash
                    or loaded_receipt.normalized_audio_sha256
                ),
            )
        )
        if (
            verified.receipt.normalized.sha256
            != loaded_receipt.normalized_audio_sha256
            or hash_file(inputs.normalized_handoff)
            != loaded_receipt.normalized_handoff_sha256
        ):
            raise ValueError("Memo VAD repair belongs to another normalized handoff")
        receipt = load_verified_memo_vad_gap_repair(
            inputs=inputs,
            composite_source=source_export,
            receipt_path=receipt_path,
        )
        raw_hash = receipt.raw_source_sha256
        raw_size = receipt.raw_source_size_bytes
    else:
        raise ValueError(f"unsupported Memo repair receipt contract: {contract!r}")
    return {
        "raw_source_export_sha256": raw_hash,
        "raw_source_export_size_bytes": raw_size,
        "source_repair_receipt_sha256": hash_file(receipt_path),
    }


def _assert_repair_lineage_matches(
    loaded: BaseModel, lineage: dict[str, str | int | None]
) -> None:
    for field, actual in lineage.items():
        if getattr(loaded, field) != actual:
            raise ValueError("Memo SRT repair lineage differs from prepared review")


def _repair_memo_srt(args: argparse.Namespace) -> int:
    source = Path(args.source_export)
    output = Path(args.output)
    receipt_output = Path(args.receipt_output)
    if output.exists() or receipt_output.exists():
        raise FileExistsError("repair-memo-srt outputs must not already exist")
    repaired, receipt = repair_memo_srt_bytes(source.read_bytes())
    _write_new(output, repaired)
    _write_new(receipt_output, canonical_json_bytes(receipt))
    return 0


def _run_memo_bundled(args: argparse.Namespace) -> int:
    request = MemoBundledRunnerRequest(
        runner=Path(args.memo_runner),
        model=Path(args.memo_model),
        input_wav=Path(args.input_wav),
        output_srt=Path(args.output),
        stdout_output=Path(args.stdout_output),
        stderr_output=Path(args.stderr_output),
        receipt_output=Path(args.receipt_output),
        gpu=args.gpu,
        language=args.language,
        prompt=args.prompt,
        max_context=args.max_context,
        max_len=args.max_len,
    )
    execute_memo_bundled_runner(request)
    return 0


def _vad_gap_inputs_from_args(
    args: argparse.Namespace, *, receipt: MemoVadGapRepairReceiptV1 | None = None
) -> MemoVadGapRepairInputs:
    required = {
        "normalized_audio": getattr(args, "normalized_audio", None),
        "normalized_handoff": getattr(args, "normalized_manifest", None),
        "raw_source": getattr(args, "raw_source_export", None),
        "parent_repaired_source": getattr(args, "parent_repaired_source", None),
        "parent_repair_receipt": getattr(args, "parent_repair_receipt", None),
        "target_wav": getattr(args, "vad_gap_target_wav", None),
        "target_srt": getattr(args, "vad_gap_target_srt", None),
        "target_stdout": getattr(args, "vad_gap_target_stdout", None),
        "target_stderr": getattr(args, "vad_gap_target_stderr", None),
        "target_execution_receipt": getattr(
            args, "vad_gap_execution_receipt", None
        ),
        "runner": getattr(args, "memo_runner", None),
        "model": getattr(args, "memo_model", None),
    }
    missing = tuple(f"--{name.replace('_', '-')}" for name, value in required.items() if not value)
    if missing:
        raise ValueError(
            "Memo VAD gap repair verification requires " + ", ".join(missing)
        )
    if receipt is None:
        offset = args.global_offset_ms
        gap_start = args.declared_gap_start_ms
        gap_end = args.declared_gap_end_ms
    else:
        offset = receipt.global_offset_ms
        gap_start = receipt.declared_gap_start_ms
        gap_end = receipt.declared_gap_end_ms
    return MemoVadGapRepairInputs(
        **{key: Path(value) for key, value in required.items()},
        global_offset_ms=offset,
        declared_gap_start_ms=gap_start,
        declared_gap_end_ms=gap_end,
    )


def _repair_memo_vad_gap(args: argparse.Namespace) -> int:
    output = Path(args.output)
    receipt_output = Path(args.receipt_output)
    if output.exists() or receipt_output.exists():
        raise FileExistsError("repair-memo-vad-gap outputs must not already exist")
    inputs = _vad_gap_inputs_from_args(args)
    VerifiedNormalizedAudioHandoffAdapter(inputs.normalized_handoff).normalize(
        NormalizeRequest(
            source_audio=inputs.normalized_audio,
            expected_source_hash=hash_file(inputs.normalized_audio),
        )
    )
    composite, receipt = repair_memo_vad_gap(inputs)
    _write_new(output, composite)
    _write_new(receipt_output, canonical_json_bytes(receipt))
    return 0


def _prepare_recognition(args: argparse.Namespace) -> int:
    audio = Path(args.normalized_audio)
    handoff = Path(args.normalized_manifest)
    verified = VerifiedNormalizedAudioHandoffAdapter(handoff).normalize(
        NormalizeRequest(source_audio=audio, expected_source_hash=hash_file(audio))
    )
    source_export = Path(args.source_export)
    source_hash, source_size = measure_regular_file(source_export)
    execution_ref = _verified_execution_ref(
        args,
        source_kind=args.source_export_kind,
        language=args.language,
        prompt=args.prompt,
    )
    if args.source_export_kind != "memo_srt" and (
        args.raw_source_export or args.repair_receipt
    ):
        raise ValueError("Memo source repair lineage is only valid for memo_srt")
    repair_lineage = _source_repair_lineage(
        args,
        source_export=source_export,
        expected_normalized_audio_hash=verified.receipt.normalized.sha256,
    )
    tokens, token_hash, token_source = _recognition_tokens(
        source_export=source_export,
        source_export_kind=args.source_export_kind,
        tokens_json=args.tokens_json,
    )
    review = _MemoRecognitionReviewV1(
        episode_id=Path(args.episode_root).resolve().name,
        memo_version=args.memo_version,
        language=args.language,
        prompt=args.prompt,
        normalized_audio_sha256=verified.receipt.normalized.sha256,
        normalized_audio_size_bytes=verified.receipt.normalized.size_bytes,
        normalized_audio_duration_ms=verified.receipt.normalized_duration_ms,
        normalized_handoff_manifest_sha256=hash_file(handoff),
        source_export_sha256=source_hash,
        source_export_size_bytes=source_size,
        source_export_kind=args.source_export_kind,
        **repair_lineage,
        memo_execution_receipt=execution_ref,
        token_source=token_source,
        token_export_sha256=token_hash,
        unresolved_findings=tuple(args.unresolved_finding),
        tokens=tokens,
    )
    _write_new(Path(args.output), canonical_json_bytes(review))
    return 0


def _accept_recognition(args: argparse.Namespace) -> int:
    review_path = Path(args.review)
    loaded = _load_canonical_model(review_path, _MemoRecognitionReviewV1)
    assert isinstance(loaded, _MemoRecognitionReviewV1)
    if loaded.unresolved_findings:
        raise ValueError("recognition review has unresolved findings")
    root = Path(args.episode_root).resolve()
    if loaded.episode_id != root.name:
        raise ValueError("recognition review belongs to another episode root")
    _fresh_verify_execution_ref(root, loaded.memo_execution_receipt)
    audio = Path(args.normalized_audio)
    handoff = Path(args.normalized_manifest)
    verified = VerifiedNormalizedAudioHandoffAdapter(handoff).normalize(
        NormalizeRequest(source_audio=audio, expected_source_hash=loaded.normalized_audio_sha256)
    )
    if (
        verified.receipt.normalized.sha256 != loaded.normalized_audio_sha256
        or verified.receipt.normalized.size_bytes != loaded.normalized_audio_size_bytes
        or verified.receipt.normalized_duration_ms != loaded.normalized_audio_duration_ms
        or hash_file(handoff) != loaded.normalized_handoff_manifest_sha256
    ):
        raise ValueError("recognition review belongs to another normalized handoff")
    source = Path(args.source_export)
    source_hash, source_size = measure_regular_file(source)
    if (source_hash, source_size) != (
        loaded.source_export_sha256,
        loaded.source_export_size_bytes,
    ):
        raise ValueError("Memo source export differs from prepared recognition review")
    repair_lineage = _source_repair_lineage(
        args,
        source_export=source,
        expected_normalized_audio_hash=loaded.normalized_audio_sha256,
    )
    _assert_repair_lineage_matches(loaded, repair_lineage)
    tokens, token_hash, token_source = _recognition_tokens(
        source_export=source,
        source_export_kind=loaded.source_export_kind,
        tokens_json=args.tokens_json,
    )
    if (
        token_hash != loaded.token_export_sha256
        or token_source != loaded.token_source
        or tokens != loaded.tokens
    ):
        raise ValueError("Memo token export differs from prepared recognition review")
    episode_id, agent_audits = _load_worker_audits(
        args,
        model=_MemoRecognitionWorkerAuditV1,
        expected={
            "normalized_audio_sha256": loaded.normalized_audio_sha256,
            "normalized_audio_size_bytes": loaded.normalized_audio_size_bytes,
            "source_export_sha256": loaded.source_export_sha256,
            "source_export_size_bytes": loaded.source_export_size_bytes,
            "review_manifest_sha256": hash_file(review_path),
            "token_export_sha256": loaded.token_export_sha256,
            "memo_execution_receipt_sha256": loaded.memo_execution_receipt.sha256,
            "reviewed_item_count": len(loaded.tokens),
        },
    )
    accepted_at = datetime.fromisoformat(args.accepted_at)
    receipt = MemoRecognitionAcceptanceReceiptV1(
        normalized_audio_sha256=loaded.normalized_audio_sha256,
        normalized_audio_size_bytes=loaded.normalized_audio_size_bytes,
        normalized_audio_duration_ms=loaded.normalized_audio_duration_ms,
        normalized_handoff_manifest_sha256=loaded.normalized_handoff_manifest_sha256,
        source_export_sha256=loaded.source_export_sha256,
        source_export_size_bytes=loaded.source_export_size_bytes,
        raw_source_export_sha256=loaded.raw_source_export_sha256,
        raw_source_export_size_bytes=loaded.raw_source_export_size_bytes,
        source_repair_receipt_sha256=loaded.source_repair_receipt_sha256,
        memo_execution_receipt=loaded.memo_execution_receipt,
        review_manifest_sha256=hash_file(review_path),
        token_export_sha256=loaded.token_export_sha256,
        reviewer="agent-quorum",
        accepted_at=accepted_at,
        episode_id=episode_id,
        agent_audits=agent_audits,
    )
    receipt_bytes = canonical_json_bytes(receipt)
    manifest = MemoRecognitionManifestV1(
        memo_version=loaded.memo_version,
        language=loaded.language,
        prompt=loaded.prompt,
        normalized_audio_sha256=loaded.normalized_audio_sha256,
        normalized_audio_size_bytes=loaded.normalized_audio_size_bytes,
        normalized_audio_duration_ms=loaded.normalized_audio_duration_ms,
        source_export_sha256=loaded.source_export_sha256,
        source_export_size_bytes=loaded.source_export_size_bytes,
        source_export_kind=loaded.source_export_kind,
        raw_source_export_sha256=loaded.raw_source_export_sha256,
        raw_source_export_size_bytes=loaded.raw_source_export_size_bytes,
        source_repair_receipt_sha256=loaded.source_repair_receipt_sha256,
        memo_execution_receipt=loaded.memo_execution_receipt,
        accepted_by_receipt_sha256=sha256_bytes(receipt_bytes),
        tokens=loaded.tokens,
    )
    receipt_output = Path(args.receipt_output)
    manifest_output = Path(args.manifest_output)
    if receipt_output.exists() or manifest_output.exists():
        raise FileExistsError("accept-recognition outputs must not already exist")
    _write_new(receipt_output, receipt_bytes)
    _write_new(manifest_output, canonical_json_bytes(manifest))
    return 0


def _recognition_tokens(
    *,
    source_export: Path,
    source_export_kind: str,
    tokens_json: str | None,
) -> tuple[tuple[MemoRecognitionTokenV1, ...], str, str]:
    if source_export_kind == "memo_srt":
        if tokens_json is not None:
            raise ValueError("Memo SRT recognition forbids a separate --tokens-json")
        payload = source_export.read_bytes()
        try:
            cues = parse_srt(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"invalid Memo recognition SRT: {exc}") from exc
        tokens = tuple(
            MemoRecognitionTokenV1(
                id=f"memo-token-{index:06d}",
                text=cue.text,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
            )
            for index, cue in enumerate(cues, start=1)
        )
        return tokens, sha256_bytes(payload), "memo_srt"
    if tokens_json is None:
        raise ValueError("Memo JSON/stdout recognition requires --tokens-json")
    token_path = Path(tokens_json)
    token_bytes = token_path.read_bytes()
    token_export = _load_canonical_model(token_path, _MemoTokenExportV1)
    assert isinstance(token_export, _MemoTokenExportV1)
    return token_export.tokens, sha256_bytes(token_bytes), "canonical_token_export"


def _prepare_cues(args: argparse.Namespace) -> int:
    recognition_path = Path(args.recognition_manifest)
    recognition, _ = load_memo_recognition_manifest(recognition_path)
    source = Path(args.source_export)
    source_bytes = source.read_bytes()
    repair_lineage = _source_repair_lineage(
        args,
        source_export=source,
        expected_normalized_audio_hash=recognition.normalized_audio_sha256,
    )
    try:
        parsed = parse_srt(source_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid Memo SRT: {exc}") from exc
    if parsed[-1].end_ms > recognition.normalized_audio_duration_ms:
        raise ValueError("Memo SRT exceeds normalized audio duration")
    review = MemoSrtReviewManifestV1(
        recognition_manifest_sha256=hash_file(recognition_path),
        source_export_sha256=sha256_bytes(source_bytes),
        source_export_size_bytes=len(source_bytes),
        **repair_lineage,
        unresolved_findings=tuple(args.unresolved_finding),
        cues=tuple(
            MemoSrtReviewCueV1(
                id=f"memo-cue-{index:06d}",
                source_index=cue.index,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=cue.text,
            )
            for index, cue in enumerate(parsed, start=1)
        ),
    )
    _write_new(Path(args.output), canonical_json_bytes(review))
    return 0


def _accept_cues(args: argparse.Namespace) -> int:
    review_path = Path(args.review)
    loaded = _load_canonical_model(review_path, MemoSrtReviewManifestV1)
    assert isinstance(loaded, MemoSrtReviewManifestV1)
    if loaded.unresolved_findings:
        raise ValueError("Memo cue review has unresolved findings")
    recognition_path = Path(args.recognition_manifest)
    recognition, _ = load_memo_recognition_manifest(recognition_path)
    if hash_file(recognition_path) != loaded.recognition_manifest_sha256:
        raise ValueError("Memo cue review belongs to another recognition manifest")
    source = Path(args.source_export)
    source_hash, source_size = measure_regular_file(source)
    if (source_hash, source_size) != (
        loaded.source_export_sha256,
        loaded.source_export_size_bytes,
    ):
        raise ValueError("Memo SRT differs from prepared cue review")
    repair_lineage = _source_repair_lineage(
        args,
        source_export=source,
        expected_normalized_audio_hash=recognition.normalized_audio_sha256,
    )
    _assert_repair_lineage_matches(loaded, repair_lineage)
    episode_id, agent_audits = _load_worker_audits(
        args,
        model=_MemoCueWorkerAuditV1,
        expected={
            "normalized_audio_sha256": recognition.normalized_audio_sha256,
            "normalized_audio_size_bytes": recognition.normalized_audio_size_bytes,
            "source_export_sha256": loaded.source_export_sha256,
            "source_export_size_bytes": loaded.source_export_size_bytes,
            "review_manifest_sha256": hash_file(review_path),
            "recognition_manifest_sha256": loaded.recognition_manifest_sha256,
            "reviewed_item_count": len(loaded.cues),
        },
    )
    receipt = MemoSrtAcceptanceReceiptV1(
        source_export_sha256=source_hash,
        source_export_size_bytes=source_size,
        raw_source_export_sha256=loaded.raw_source_export_sha256,
        raw_source_export_size_bytes=loaded.raw_source_export_size_bytes,
        source_repair_receipt_sha256=loaded.source_repair_receipt_sha256,
        recognition_manifest_sha256=loaded.recognition_manifest_sha256,
        review_manifest_sha256=hash_file(review_path),
        reviewer="agent-quorum",
        accepted_at=datetime.fromisoformat(args.accepted_at),
        episode_id=episode_id,
        agent_audits=agent_audits,
    )
    _write_new(Path(args.receipt_output), canonical_json_bytes(receipt))
    return 0


def _status(args: argparse.Namespace) -> int:
    audio = Path(args.normalized_audio)
    handoff = Path(args.normalized_manifest)
    normalized = VerifiedNormalizedAudioHandoffAdapter(handoff).normalize(
        NormalizeRequest(source_audio=audio, expected_source_hash=hash_file(audio))
    )
    recognition_manifest_path = Path(args.recognition_manifest)
    recognition_manifest, _ = load_memo_recognition_manifest(recognition_manifest_path)
    recognition_receipt_path = Path(args.recognition_acceptance_receipt)
    recognition_receipt = _load_canonical_model(
        recognition_receipt_path, MemoRecognitionAcceptanceReceiptV1
    )
    assert isinstance(recognition_receipt, MemoRecognitionAcceptanceReceiptV1)
    if (
        hash_file(recognition_receipt_path)
        != recognition_manifest.accepted_by_receipt_sha256
        or recognition_receipt.normalized_audio_sha256
        != normalized.receipt.normalized.sha256
        or recognition_receipt.normalized_audio_size_bytes
        != normalized.receipt.normalized.size_bytes
        or recognition_receipt.normalized_audio_duration_ms
        != normalized.receipt.normalized_duration_ms
        or recognition_receipt.normalized_handoff_manifest_sha256 != hash_file(handoff)
        or recognition_receipt.source_export_sha256
        != recognition_manifest.source_export_sha256
        or recognition_receipt.source_export_size_bytes
        != recognition_manifest.source_export_size_bytes
    ):
        raise ValueError("Memo recognition acceptance lineage does not match production inputs")
    recognition_source = Path(args.recognition_source_export)
    adapter = MemoRecognizerAdapter(
        recognition_manifest_path,
        source_export=recognition_source,
        acceptance_receipt=recognition_receipt_path,
    )
    evidence = adapter.recognize(
        RecognitionRequest(
            episode_id="evidence-status",
            invocation_id="evidence-status-recognition",
            normalized_audio=audio,
            expected_normalized_audio_hash=normalized.receipt.normalized.sha256,
        )
    )
    cue_source = Path(args.cue_source_export)
    cue_receipt_path = Path(args.cue_acceptance_receipt)
    cue_receipt = _load_canonical_model(cue_receipt_path, MemoSrtAcceptanceReceiptV1)
    assert isinstance(cue_receipt, MemoSrtAcceptanceReceiptV1)
    if (
        cue_receipt.recognition_manifest_sha256 is not None
        and cue_receipt.recognition_manifest_sha256 != hash_file(recognition_manifest_path)
    ):
        raise ValueError("Memo cue acceptance crossed recognition manifests")
    MemoSrtBoundaryAuthorityV1.load_verified(
        cue_source,
        acceptance_receipt=cue_receipt_path,
        recognition_evidence=evidence,
    )
    environment = {
        "PODCAST_SUBTITLE_V2_NORMALIZED_HANDOFF_MANIFEST": str(handoff.resolve()),
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_MANIFEST": str(
            recognition_manifest_path.resolve()
        ),
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_SOURCE_EXPORT": str(
            recognition_source.resolve()
        ),
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_ACCEPTANCE_RECEIPT": str(
            recognition_receipt_path.resolve()
        ),
        "PODCAST_SUBTITLE_V2_MEMO_CUE_SOURCE_EXPORT": str(cue_source.resolve()),
        "PODCAST_SUBTITLE_V2_MEMO_CUE_ACCEPTANCE_RECEIPT": str(
            cue_receipt_path.resolve()
        ),
    }
    print(canonical_json_bytes({"environment": environment, "ready": True}).decode("utf-8"))
    return 0


def _add_vad_gap_verification_arguments(
    command: argparse.ArgumentParser, *, include_normalized: bool = False
) -> None:
    if include_normalized:
        command.add_argument("--normalized-audio")
        command.add_argument("--normalized-manifest")
    command.add_argument("--parent-repaired-source")
    command.add_argument("--parent-repair-receipt")
    command.add_argument("--vad-gap-target-wav")
    command.add_argument("--vad-gap-target-srt")
    command.add_argument("--vad-gap-target-stdout")
    command.add_argument("--vad-gap-target-stderr")
    command.add_argument("--vad-gap-execution-receipt")
    command.add_argument("--memo-runner")
    command.add_argument("--memo-model")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seal = commands.add_parser("seal-normalized")
    seal.add_argument("--audio", required=True)
    seal.add_argument("--output", required=True)
    seal.add_argument("--accepted-at", required=True)
    seal.set_defaults(handler=_seal_normalized)
    repair = commands.add_parser("repair-memo-srt")
    repair.add_argument("--source-export", required=True)
    repair.add_argument("--output", required=True)
    repair.add_argument("--receipt-output", required=True)
    repair.set_defaults(handler=_repair_memo_srt)
    bundled = commands.add_parser("run-memo-bundled")
    bundled.add_argument("--memo-runner", required=True)
    bundled.add_argument("--memo-model", required=True)
    bundled.add_argument("--input-wav", required=True)
    bundled.add_argument("--gpu", required=True)
    bundled.add_argument("--language", default="zh")
    bundled.add_argument("--prompt", default="")
    bundled.add_argument("--max-context", type=int, default=-1)
    bundled.add_argument("--max-len", type=int, default=0)
    bundled.add_argument("--output", required=True)
    bundled.add_argument("--stdout-output", required=True)
    bundled.add_argument("--stderr-output", required=True)
    bundled.add_argument("--receipt-output", required=True)
    bundled.set_defaults(handler=_run_memo_bundled)
    vad_gap = commands.add_parser("repair-memo-vad-gap")
    vad_gap.add_argument("--normalized-audio", required=True)
    vad_gap.add_argument("--normalized-manifest", required=True)
    vad_gap.add_argument("--raw-source-export", required=True)
    vad_gap.add_argument("--parent-repaired-source", required=True)
    vad_gap.add_argument("--parent-repair-receipt", required=True)
    vad_gap.add_argument("--vad-gap-target-wav", required=True)
    vad_gap.add_argument("--vad-gap-target-srt", required=True)
    vad_gap.add_argument("--vad-gap-target-stdout", required=True)
    vad_gap.add_argument("--vad-gap-target-stderr", required=True)
    vad_gap.add_argument("--vad-gap-execution-receipt", required=True)
    vad_gap.add_argument("--memo-runner", required=True)
    vad_gap.add_argument("--memo-model", required=True)
    vad_gap.add_argument("--global-offset-ms", required=True, type=int)
    vad_gap.add_argument("--declared-gap-start-ms", required=True, type=int)
    vad_gap.add_argument("--declared-gap-end-ms", required=True, type=int)
    vad_gap.add_argument("--output", required=True)
    vad_gap.add_argument("--receipt-output", required=True)
    vad_gap.set_defaults(handler=_repair_memo_vad_gap)
    recognition = commands.add_parser("prepare-recognition")
    recognition.add_argument("--episode-root", required=True)
    recognition.add_argument("--normalized-audio", required=True)
    recognition.add_argument("--normalized-manifest", required=True)
    recognition.add_argument("--source-export", required=True)
    recognition.add_argument(
        "--source-export-kind",
        required=True,
        choices=("memo_stdout", "memo_srt", "memo_json"),
    )
    recognition.add_argument("--tokens-json")
    recognition.add_argument("--memo-execution-receipt", required=True)
    recognition.add_argument("--memo-output-srt", required=True)
    recognition.add_argument("--memo-stdout", required=True)
    recognition.add_argument("--memo-stderr", required=True)
    recognition.add_argument("--raw-source-export")
    recognition.add_argument("--repair-receipt")
    _add_vad_gap_verification_arguments(recognition)
    recognition._option_string_actions["--memo-runner"].required = True
    recognition._option_string_actions["--memo-model"].required = True
    recognition.add_argument("--memo-version", required=True)
    recognition.add_argument("--language", required=True)
    recognition.add_argument("--prompt", required=True)
    recognition.add_argument("--unresolved-finding", action="append", default=[])
    recognition.add_argument("--output", required=True)
    recognition.set_defaults(handler=_prepare_recognition)
    accept_recognition = commands.add_parser("accept-recognition")
    accept_recognition.add_argument("--review", required=True)
    accept_recognition.add_argument("--normalized-audio", required=True)
    accept_recognition.add_argument("--normalized-manifest", required=True)
    accept_recognition.add_argument("--source-export", required=True)
    accept_recognition.add_argument("--tokens-json")
    accept_recognition.add_argument("--raw-source-export")
    accept_recognition.add_argument("--repair-receipt")
    _add_vad_gap_verification_arguments(accept_recognition)
    accept_recognition.add_argument("--episode-root", required=True)
    accept_recognition.add_argument("--audit-a", required=True)
    accept_recognition.add_argument("--audit-b", required=True)
    accept_recognition.add_argument("--reviewer", help=argparse.SUPPRESS)
    accept_recognition.add_argument("--accepted-at", required=True)
    accept_recognition.add_argument(
        "--confirm-reviewed", action="store_true", help=argparse.SUPPRESS
    )
    accept_recognition.add_argument("--receipt-output", required=True)
    accept_recognition.add_argument("--manifest-output", required=True)
    accept_recognition.set_defaults(handler=_accept_recognition)
    cues = commands.add_parser("prepare-cues")
    cues.add_argument("--recognition-manifest", required=True)
    cues.add_argument("--source-export", required=True)
    cues.add_argument("--raw-source-export")
    cues.add_argument("--repair-receipt")
    _add_vad_gap_verification_arguments(cues, include_normalized=True)
    cues.add_argument("--unresolved-finding", action="append", default=[])
    cues.add_argument("--output", required=True)
    cues.set_defaults(handler=_prepare_cues)
    accept_cues = commands.add_parser("accept-cues")
    accept_cues.add_argument("--review", required=True)
    accept_cues.add_argument("--recognition-manifest", required=True)
    accept_cues.add_argument("--source-export", required=True)
    accept_cues.add_argument("--raw-source-export")
    accept_cues.add_argument("--repair-receipt")
    _add_vad_gap_verification_arguments(accept_cues, include_normalized=True)
    accept_cues.add_argument("--episode-root", required=True)
    accept_cues.add_argument("--audit-a", required=True)
    accept_cues.add_argument("--audit-b", required=True)
    accept_cues.add_argument("--reviewer", help=argparse.SUPPRESS)
    accept_cues.add_argument("--accepted-at", required=True)
    accept_cues.add_argument(
        "--confirm-reviewed", action="store_true", help=argparse.SUPPRESS
    )
    accept_cues.add_argument("--receipt-output", required=True)
    accept_cues.set_defaults(handler=_accept_cues)
    status = commands.add_parser("status")
    status.add_argument("--normalized-audio", required=True)
    status.add_argument("--normalized-manifest", required=True)
    status.add_argument("--recognition-manifest", required=True)
    status.add_argument("--recognition-source-export", required=True)
    status.add_argument("--recognition-acceptance-receipt", required=True)
    status.add_argument("--cue-source-export", required=True)
    status.add_argument("--cue-acceptance-receipt", required=True)
    status.set_defaults(handler=_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
