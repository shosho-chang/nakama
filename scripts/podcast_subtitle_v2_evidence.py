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
from agents.brook.podcast_subtitles.memo_projection import parse_srt  # noqa: E402
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
    token_export_sha256: str
    unresolved_findings: tuple[str, ...] = ()
    tokens: tuple[MemoRecognitionTokenV1, ...]


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


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _prepare_recognition(args: argparse.Namespace) -> int:
    audio = Path(args.normalized_audio)
    handoff = Path(args.normalized_manifest)
    verified = VerifiedNormalizedAudioHandoffAdapter(handoff).normalize(
        NormalizeRequest(source_audio=audio, expected_source_hash=hash_file(audio))
    )
    token_path = Path(args.tokens_json)
    token_export = _load_canonical_model(token_path, _MemoTokenExportV1)
    assert isinstance(token_export, _MemoTokenExportV1)
    source_export = Path(args.source_export)
    source_hash, source_size = measure_regular_file(source_export)
    review = _MemoRecognitionReviewV1(
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
        token_export_sha256=sha256_bytes(token_path.read_bytes()),
        unresolved_findings=tuple(args.unresolved_finding),
        tokens=token_export.tokens,
    )
    _write_new(Path(args.output), canonical_json_bytes(review))
    return 0


def _accept_recognition(args: argparse.Namespace) -> int:
    review_path = Path(args.review)
    loaded = _load_canonical_model(review_path, _MemoRecognitionReviewV1)
    assert isinstance(loaded, _MemoRecognitionReviewV1)
    if loaded.unresolved_findings:
        raise ValueError("recognition review has unresolved findings")
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
    token_path = Path(args.tokens_json)
    token_bytes = token_path.read_bytes()
    token_export = _load_canonical_model(token_path, _MemoTokenExportV1)
    assert isinstance(token_export, _MemoTokenExportV1)
    if (
        sha256_bytes(token_bytes) != loaded.token_export_sha256
        or token_export.tokens != loaded.tokens
    ):
        raise ValueError("Memo token export differs from prepared recognition review")
    accepted_at = datetime.fromisoformat(args.accepted_at)
    receipt = MemoRecognitionAcceptanceReceiptV1(
        normalized_audio_sha256=loaded.normalized_audio_sha256,
        normalized_audio_size_bytes=loaded.normalized_audio_size_bytes,
        normalized_audio_duration_ms=loaded.normalized_audio_duration_ms,
        normalized_handoff_manifest_sha256=loaded.normalized_handoff_manifest_sha256,
        source_export_sha256=loaded.source_export_sha256,
        source_export_size_bytes=loaded.source_export_size_bytes,
        review_manifest_sha256=hash_file(review_path),
        token_export_sha256=loaded.token_export_sha256,
        reviewer=args.reviewer,
        accepted_at=accepted_at,
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


def _prepare_cues(args: argparse.Namespace) -> int:
    recognition_path = Path(args.recognition_manifest)
    recognition, _ = load_memo_recognition_manifest(recognition_path)
    source = Path(args.source_export)
    source_bytes = source.read_bytes()
    try:
        parsed = parse_srt(source_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid Memo GUI SRT: {exc}") from exc
    if parsed[-1].end_ms > recognition.normalized_audio_duration_ms:
        raise ValueError("Memo GUI SRT exceeds normalized audio duration")
    review = MemoSrtReviewManifestV1(
        recognition_manifest_sha256=hash_file(recognition_path),
        source_export_sha256=sha256_bytes(source_bytes),
        source_export_size_bytes=len(source_bytes),
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
    load_memo_recognition_manifest(recognition_path)
    if hash_file(recognition_path) != loaded.recognition_manifest_sha256:
        raise ValueError("Memo cue review belongs to another recognition manifest")
    source = Path(args.source_export)
    source_hash, source_size = measure_regular_file(source)
    if (source_hash, source_size) != (
        loaded.source_export_sha256,
        loaded.source_export_size_bytes,
    ):
        raise ValueError("Memo GUI SRT differs from prepared cue review")
    receipt = MemoSrtAcceptanceReceiptV1(
        source_export_sha256=source_hash,
        source_export_size_bytes=source_size,
        recognition_manifest_sha256=loaded.recognition_manifest_sha256,
        review_manifest_sha256=hash_file(review_path),
        reviewer=args.reviewer,
        accepted_at=datetime.fromisoformat(args.accepted_at),
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seal = commands.add_parser("seal-normalized")
    seal.add_argument("--audio", required=True)
    seal.add_argument("--output", required=True)
    seal.add_argument("--accepted-at", required=True)
    seal.set_defaults(handler=_seal_normalized)
    recognition = commands.add_parser("prepare-recognition")
    recognition.add_argument("--normalized-audio", required=True)
    recognition.add_argument("--normalized-manifest", required=True)
    recognition.add_argument("--source-export", required=True)
    recognition.add_argument(
        "--source-export-kind",
        required=True,
        choices=("memo_stdout", "memo_srt", "memo_json"),
    )
    recognition.add_argument("--tokens-json", required=True)
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
    accept_recognition.add_argument("--tokens-json", required=True)
    accept_recognition.add_argument("--reviewer", required=True)
    accept_recognition.add_argument("--accepted-at", required=True)
    accept_recognition.add_argument("--confirm-reviewed", action="store_true", required=True)
    accept_recognition.add_argument("--receipt-output", required=True)
    accept_recognition.add_argument("--manifest-output", required=True)
    accept_recognition.set_defaults(handler=_accept_recognition)
    cues = commands.add_parser("prepare-cues")
    cues.add_argument("--recognition-manifest", required=True)
    cues.add_argument("--source-export", required=True)
    cues.add_argument("--unresolved-finding", action="append", default=[])
    cues.add_argument("--output", required=True)
    cues.set_defaults(handler=_prepare_cues)
    accept_cues = commands.add_parser("accept-cues")
    accept_cues.add_argument("--review", required=True)
    accept_cues.add_argument("--recognition-manifest", required=True)
    accept_cues.add_argument("--source-export", required=True)
    accept_cues.add_argument("--reviewer", required=True)
    accept_cues.add_argument("--accepted-at", required=True)
    accept_cues.add_argument("--confirm-reviewed", action="store_true", required=True)
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
