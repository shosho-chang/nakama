"""Accepted Memo cue authority and closed, local boundary repair policy."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from shared.schemas.podcast_subtitles_v2 import CanonicalTranscript, RecognitionEvidence

from .display_metrics import display_columns, reading_units
from .hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from .memo_projection import (
    MemoCue,
    SourceToken,
    parse_srt,
    project_tokens_to_memo_cues,
)
from .memo_projection import (
    ProjectionResult as MemoProjectionResult,
)
from .ports import AdapterInputError, AdapterIntegrityError

MemoBoundaryRepairReason = Literal[
    "hard_length",
    "cps_duration",
    "invalid_empty",
    "invalid_overlap",
    "explicit_semantic",
    "explicit_punctuation",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MemoSourceCueV1(_StrictModel):
    id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str
    source_token_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _valid(self) -> "MemoSourceCueV1":
        if not self.id.strip() or self.end_ms <= self.start_ms:
            raise ValueError("Memo source cue requires an id and positive timing")
        if len(set(self.source_token_ids)) != len(self.source_token_ids):
            raise ValueError("Memo source cue token IDs must be unique")
        if bool(self.text) != bool(self.source_token_ids):
            raise ValueError("only an empty source cue may have no recognition tokens")
        return self


class MemoAcceptedCueV1(_StrictModel):
    id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str
    source_token_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _valid(self) -> "MemoAcceptedCueV1":
        if not self.id.strip() or not self.text.strip() or self.end_ms <= self.start_ms:
            raise ValueError("accepted Memo cue requires non-blank text and positive timing")
        if not self.source_token_ids or len(set(self.source_token_ids)) != len(
            self.source_token_ids
        ):
            raise ValueError("accepted Memo cue requires unique source token IDs")
        return self


class MemoBoundaryEvidenceV1(_StrictModel):
    id: str
    kind: Literal["semantic_receipt"]
    payload_utf8: str
    sha256: str
    size_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def _sealed(self) -> "MemoBoundaryEvidenceV1":
        payload = self.payload_utf8.encode("utf-8")
        if (
            not self.id.strip()
            or sha256_bytes(payload) != self.sha256
            or len(payload) != self.size_bytes
        ):
            raise ValueError("Memo boundary evidence bytes/digest do not match")
        return self


class MemoBoundaryRepairProofV1(_StrictModel):
    measured_columns: float | None = Field(default=None, ge=0)
    hard_limit_columns: float | None = Field(default=None, gt=0)
    measured_cps: float | None = Field(default=None, ge=0)
    hard_cps: float | None = Field(default=None, gt=0)
    semantic_evidence_id: str | None = None
    punctuation: str | None = None
    punctuation_scalar_offset: int | None = Field(default=None, ge=0)


class MemoBoundaryRepairV1(_StrictModel):
    id: str
    reason_code: MemoBoundaryRepairReason
    source_cue_ids: tuple[str, ...]
    output_cue_ids: tuple[str, ...]
    proof: MemoBoundaryRepairProofV1

    @model_validator(mode="after")
    def _bounded(self) -> "MemoBoundaryRepairV1":
        if not self.id.strip() or not self.source_cue_ids or not self.output_cue_ids:
            raise ValueError("boundary repair requires source and output cue IDs")
        if len(self.source_cue_ids) > 8 or len(self.output_cue_ids) > 8:
            raise ValueError("boundary repair exceeds the bounded local window")
        if len(set(self.source_cue_ids)) != len(self.source_cue_ids) or len(
            set(self.output_cue_ids)
        ) != len(self.output_cue_ids):
            raise ValueError("boundary repair cue IDs must be unique")
        return self


class MemoCueBoundaryManifestV1(_StrictModel):
    schema_version: Literal[1] = 1
    contract: Literal["memo-accepted-cue-boundaries-v1"] = "memo-accepted-cue-boundaries-v1"
    boundary_authority: Literal["accepted_memo_export"] = "accepted_memo_export"
    recognition_manifest_sha256: str
    source_export_sha256: str
    source_export_size_bytes: int = Field(gt=0)
    source_export_kind: Literal["memo_gui_srt", "reviewed_memo_cue_projection"]
    acceptance_receipt_sha256: str
    unresolved_findings: tuple[str, ...] = ()
    base_cues: tuple[MemoSourceCueV1, ...]
    cues: tuple[MemoAcceptedCueV1, ...]
    boundary_evidence: tuple[MemoBoundaryEvidenceV1, ...] = ()
    repairs: tuple[MemoBoundaryRepairV1, ...] = ()

    @model_validator(mode="after")
    def _valid(self) -> "MemoCueBoundaryManifestV1":
        for label, value in (
            ("recognition", self.recognition_manifest_sha256),
            ("export", self.source_export_sha256),
            ("acceptance", self.acceptance_receipt_sha256),
        ):
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"{label} digest must be lowercase SHA-256")
        if self.unresolved_findings or not self.base_cues or not self.cues:
            raise ValueError("accepted Memo boundary manifest must be complete and resolved")
        base_by_id = {cue.id: cue for cue in self.base_cues}
        out_by_id = {cue.id: cue for cue in self.cues}
        evidence_by_id = {item.id: item for item in self.boundary_evidence}
        if (
            len(base_by_id) != len(self.base_cues)
            or len(out_by_id) != len(self.cues)
            or len(evidence_by_id) != len(self.boundary_evidence)
        ):
            raise ValueError("Memo boundary manifest IDs must be unique")
        output_token_ids = tuple(token for cue in self.cues for token in cue.source_token_ids)
        if len(set(output_token_ids)) != len(output_token_ids):
            raise ValueError("accepted output cues must uniquely partition recognition tokens")
        previous_end = -1
        for cue in self.cues:
            if cue.start_ms < previous_end:
                raise ValueError("accepted Memo output cues must be ordered and non-overlapping")
            previous_end = cue.end_ms

        repaired_sources: set[str] = set()
        repaired_outputs: set[str] = set()
        for repair in self.repairs:
            if repaired_sources.intersection(
                repair.source_cue_ids
            ) or repaired_outputs.intersection(repair.output_cue_ids):
                raise ValueError("boundary repair groups overlap")
            repaired_sources.update(repair.source_cue_ids)
            repaired_outputs.update(repair.output_cue_ids)
            try:
                sources = tuple(base_by_id[item] for item in repair.source_cue_ids)
                outputs = tuple(out_by_id[item] for item in repair.output_cue_ids)
            except KeyError as exc:
                raise ValueError(f"boundary repair references unknown cue {exc.args[0]}") from exc
            source_text = "".join(item.text for item in sources)
            if source_text != "".join(item.text for item in outputs):
                raise ValueError("boundary repair must preserve exact ordered text")
            if (
                sources[0].start_ms != outputs[0].start_ms
                or sources[-1].end_ms != outputs[-1].end_ms
            ):
                raise ValueError("boundary repair must preserve its exact outer span")
            proof = repair.proof
            if repair.reason_code == "hard_length":
                measured = display_columns(source_text)
                valid = (
                    proof.measured_columns is not None
                    and math.isclose(proof.measured_columns, measured, rel_tol=0, abs_tol=1e-9)
                    and proof.hard_limit_columns is not None
                    and measured > proof.hard_limit_columns
                )
            elif repair.reason_code == "cps_duration":
                duration_ms = sources[-1].end_ms - sources[0].start_ms
                measured = reading_units(source_text) * 1_000 / duration_ms
                valid = (
                    proof.measured_cps is not None
                    and math.isclose(proof.measured_cps, measured, rel_tol=0, abs_tol=1e-9)
                    and proof.hard_cps is not None
                    and measured > proof.hard_cps
                )
            elif repair.reason_code == "invalid_empty":
                valid = any(not item.text for item in sources)
            elif repair.reason_code == "invalid_overlap":
                valid = any(
                    right.start_ms < left.end_ms for left, right in zip(sources, sources[1:])
                )
            elif repair.reason_code == "explicit_semantic":
                valid = proof.semantic_evidence_id in evidence_by_id
            else:
                offset = proof.punctuation_scalar_offset
                valid = (
                    proof.punctuation
                    in {"，", "。", "？", "！", "；", "：", ",", ".", "?", "!", ";", ":"}
                    and offset is not None
                    and 0 < offset < len(source_text)
                    and source_text[offset - 1] == proof.punctuation
                    and any(
                        len("".join(item.text for item in outputs[:index])) == offset
                        for index in range(1, len(outputs))
                    )
                )
            if not valid:
                raise ValueError(f"boundary repair lacks verifiable {repair.reason_code} proof")

        # Any cue not covered by a repair must be an exact one-to-one carryover.
        for source_id, source in base_by_id.items():
            if source_id in repaired_sources:
                continue
            output = out_by_id.get(source_id)
            if output is None or (
                output.start_ms,
                output.end_ms,
                output.text,
                output.source_token_ids,
            ) != (source.start_ms, source.end_ms, source.text, source.source_token_ids):
                raise ValueError("unlogged Memo boundary change is forbidden")
        if set(out_by_id) != repaired_outputs.union(set(base_by_id) - repaired_sources):
            raise ValueError("Memo boundary outputs are incomplete or unlogged")
        return self


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_memo_boundary_manifest(path: str | Path) -> tuple[MemoCueBoundaryManifestV1, bytes]:
    try:
        payload = Path(path).read_bytes()
        json.loads(payload, object_pairs_hook=_pairs)
        manifest = MemoCueBoundaryManifestV1.model_validate_json(payload, strict=True)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise AdapterInputError(f"invalid Memo cue boundary manifest: {exc}") from exc
    if canonical_json_bytes(manifest) != payload:
        raise AdapterIntegrityError("Memo cue boundary manifest must use canonical JSON bytes")
    return manifest, payload


class MemoBoundaryAuthorityV1:
    def __init__(
        self,
        manifest: MemoCueBoundaryManifestV1,
        *,
        manifest_bytes: bytes,
        recognition_evidence: RecognitionEvidence,
    ) -> None:
        self.manifest = manifest
        self.manifest_bytes = manifest_bytes
        self.manifest_sha256 = sha256_bytes(manifest_bytes)
        raw_ids = tuple(token.id for token in recognition_evidence.tokens)
        base_ids = tuple(token for cue in manifest.base_cues for token in cue.source_token_ids)
        output_ids = tuple(token for cue in manifest.cues for token in cue.source_token_ids)
        if base_ids != raw_ids or output_ids != raw_ids:
            raise AdapterIntegrityError("Memo base/output cues must exactly partition raw tokens")
        text = {token.id: token.text for token in recognition_evidence.tokens}
        for cue in (*manifest.base_cues, *manifest.cues):
            if "".join(text[item] for item in cue.source_token_ids) != cue.text:
                raise AdapterIntegrityError("Memo cue text differs from raw recognition text")
        self._cue_by_raw = {
            token: cue.id for cue in manifest.cues for token in cue.source_token_ids
        }
        self._raw_ids = frozenset(raw_ids)
        self.content_hash = hash_object(
            {
                "manifest_sha256": self.manifest_sha256,
                "recognition_evidence": recognition_evidence.model_dump(mode="json"),
            }
        )

    @classmethod
    def load_verified(
        cls,
        manifest_path: str | Path,
        *,
        recognition_manifest_sha256: str,
        recognition_evidence: RecognitionEvidence,
        source_export: str | Path,
        acceptance_receipt: str | Path,
    ) -> "MemoBoundaryAuthorityV1":
        manifest, payload = load_memo_boundary_manifest(manifest_path)
        if manifest.recognition_manifest_sha256 != recognition_manifest_sha256:
            raise AdapterIntegrityError("Memo cue authority crossed recognition manifest")
        export_path = Path(source_export)
        receipt_path = Path(acceptance_receipt)
        if not export_path.is_file() or not receipt_path.is_file():
            raise AdapterInputError("Memo cue export and acceptance receipt must exist")
        if (
            hash_file(export_path) != manifest.source_export_sha256
            or export_path.stat().st_size != manifest.source_export_size_bytes
        ):
            raise AdapterIntegrityError("Memo cue source export differs from manifest")
        if hash_file(receipt_path) != manifest.acceptance_receipt_sha256:
            raise AdapterIntegrityError("Memo cue acceptance receipt differs from manifest")
        return cls(manifest, manifest_bytes=payload, recognition_evidence=recognition_evidence)

    def _raw_for_token(self, evidence_ids: Sequence[str]) -> tuple[str, ...]:
        found = tuple(
            dict.fromkeys(
                item.rsplit(":", 1)[-1]
                for item in evidence_ids
                if item.rsplit(":", 1)[-1] in self._raw_ids
            )
        )
        if not found:
            raise AdapterIntegrityError("Canonical token lost Memo lineage")
        return found

    def _layout(
        self, transcript: CanonicalTranscript
    ) -> tuple[tuple[str, ...], dict[str, tuple[int, int]]]:
        token_to_span = {
            token_id: (span.start_ms, span.end_ms)
            for span in transcript.spans
            for token_id in span.token_ids
        }
        sequence: list[str] = []
        covered: set[str] = set()
        ranges: dict[str, list[tuple[int, int]]] = {}
        for token in transcript.tokens:
            raw = self._raw_for_token(token.evidence_ids)
            cue_ids = {self._cue_by_raw[item] for item in raw}
            if len(cue_ids) != 1:
                raise AdapterIntegrityError("text correction crossed an accepted Memo cue boundary")
            cue_id = next(iter(cue_ids))
            covered.update(raw)
            sequence.append(cue_id)
            ranges.setdefault(cue_id, []).append(token_to_span[token.id])
        if covered != self._raw_ids:
            raise AdapterIntegrityError("Canonical transcript lost Memo source coverage")
        compressed = tuple(dict.fromkeys(sequence))
        if compressed != tuple(cue.id for cue in self.manifest.cues):
            raise AdapterIntegrityError("Canonical transcript drifted from Memo cue order")
        outer = {
            cue_id: (min(item[0] for item in items), max(item[1] for item in items))
            for cue_id, items in ranges.items()
        }
        expected = {cue.id: (cue.start_ms, cue.end_ms) for cue in self.manifest.cues}
        if outer != expected:
            raise AdapterIntegrityError("Canonical transcript changed accepted Memo cue timing")
        return tuple(sequence), outer

    def mandatory_cue_boundaries(self, transcript: CanonicalTranscript) -> tuple[int, ...]:
        sequence, _outer = self._layout(transcript)
        return tuple(
            index for index in range(1, len(sequence)) if sequence[index - 1] != sequence[index]
        )

    def projection_contract(
        self,
        transcript: CanonicalTranscript,
    ) -> tuple[tuple[int, ...], tuple[tuple[int, int, int, int], ...]]:
        """Return the only legal cue edges and exact accepted Memo cue times."""

        sequence, _outer = self._layout(transcript)
        boundaries = tuple(
            index for index in range(1, len(sequence)) if sequence[index - 1] != sequence[index]
        )
        starts = (0, *boundaries)
        ends = (*boundaries, len(sequence))
        authoritative = tuple(
            (start, end, cue.start_ms, cue.end_ms)
            for start, end, cue in zip(starts, ends, self.manifest.cues, strict=True)
        )
        return boundaries, authoritative

    def assert_text_correction_preserved_boundaries(
        self, before: CanonicalTranscript, after: CanonicalTranscript
    ) -> None:
        before_sequence, before_outer = self._layout(before)
        after_sequence, after_outer = self._layout(after)
        if (
            tuple(dict.fromkeys(before_sequence)) != tuple(dict.fromkeys(after_sequence))
            or before_outer != after_outer
        ):
            raise AdapterIntegrityError("ordinary text correction changed Memo cue boundaries")


class MemoSrtAcceptanceReceiptV1(_StrictModel):
    """Small operator receipt binding one accepted Memo GUI SRT export."""

    schema_version: Literal[1] = 1
    contract: Literal["accepted-memo-gui-srt-v1"] = "accepted-memo-gui-srt-v1"
    accepted: Literal[True] = True
    source_export_sha256: str
    source_export_size_bytes: int = Field(gt=0)
    reviewer: str
    accepted_at: datetime

    @model_validator(mode="after")
    def _valid(self) -> "MemoSrtAcceptanceReceiptV1":
        if (
            len(self.source_export_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.source_export_sha256)
            or not self.reviewer.strip()
            or self.accepted_at.tzinfo is None
        ):
            raise ValueError("Memo SRT acceptance receipt is incomplete")
        return self


class MemoSrtBoundaryAuthorityV1:
    """Project corrected Canonical text onto an accepted Memo GUI SRT.

    This is the production form of the Zheng Guowei projection contract:
    global text alignment, token-edge snap, adjacent Memo cue merge, exact
    Canonical text copy, one-line output, and fail-closed retention QC.
    """

    def __init__(
        self,
        *,
        memo_srt_bytes: bytes,
        acceptance_receipt: MemoSrtAcceptanceReceiptV1,
        acceptance_receipt_bytes: bytes,
        recognition_evidence: RecognitionEvidence,
        min_boundary_retention: float = 0.95,
        min_alignment_ratio: float = 0.90,
    ) -> None:
        try:
            self.memo_cues: tuple[MemoCue, ...] = parse_srt(memo_srt_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AdapterInputError(f"invalid accepted Memo GUI SRT: {exc}") from exc
        self.memo_srt_bytes = memo_srt_bytes
        self.acceptance_receipt = acceptance_receipt
        self.acceptance_receipt_bytes = acceptance_receipt_bytes
        self.recognition_evidence = recognition_evidence
        self.min_boundary_retention = min_boundary_retention
        self.min_alignment_ratio = min_alignment_ratio
        self.content_hash = hash_object(
            {
                "contract": "memo-srt-corrected-projection-v1",
                "memo_srt_sha256": sha256_bytes(memo_srt_bytes),
                "acceptance_receipt_sha256": sha256_bytes(acceptance_receipt_bytes),
                "recognition_evidence": recognition_evidence.model_dump(mode="json"),
                "min_boundary_retention": min_boundary_retention,
                "min_alignment_ratio": min_alignment_ratio,
            }
        )

    @classmethod
    def load_verified(
        cls,
        source_export: str | Path,
        *,
        acceptance_receipt: str | Path,
        recognition_evidence: RecognitionEvidence,
        min_boundary_retention: float = 0.95,
        min_alignment_ratio: float = 0.90,
    ) -> "MemoSrtBoundaryAuthorityV1":
        export_path = Path(source_export)
        receipt_path = Path(acceptance_receipt)
        try:
            memo_srt_bytes = export_path.read_bytes()
            receipt_bytes = receipt_path.read_bytes()
            json.loads(receipt_bytes, object_pairs_hook=_pairs)
            receipt = MemoSrtAcceptanceReceiptV1.model_validate_json(receipt_bytes, strict=True)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise AdapterInputError(f"invalid Memo SRT acceptance boundary: {exc}") from exc
        if canonical_json_bytes(receipt) != receipt_bytes:
            raise AdapterIntegrityError("Memo SRT acceptance receipt must be canonical JSON")
        if (
            sha256_bytes(memo_srt_bytes) != receipt.source_export_sha256
            or len(memo_srt_bytes) != receipt.source_export_size_bytes
        ):
            raise AdapterIntegrityError("Memo GUI SRT differs from its acceptance receipt")
        return cls(
            memo_srt_bytes=memo_srt_bytes,
            acceptance_receipt=receipt,
            acceptance_receipt_bytes=receipt_bytes,
            recognition_evidence=recognition_evidence,
            min_boundary_retention=min_boundary_retention,
            min_alignment_ratio=min_alignment_ratio,
        )

    @staticmethod
    def _source_tokens(transcript: CanonicalTranscript) -> tuple[SourceToken, ...]:
        span_by_token = {token_id: span for span in transcript.spans for token_id in span.token_ids}
        return tuple(
            SourceToken(
                id=token.id,
                text=token.text,
                start_ms=(
                    token.start_ms
                    if token.start_ms is not None
                    else span_by_token[token.id].start_ms
                ),
                end_ms=(
                    token.end_ms if token.end_ms is not None else span_by_token[token.id].end_ms
                ),
            )
            for token in transcript.tokens
        )

    def project_result(self, transcript: CanonicalTranscript) -> MemoProjectionResult:
        try:
            return project_tokens_to_memo_cues(
                self._source_tokens(transcript),
                self.memo_cues,
                min_boundary_retention=self.min_boundary_retention,
                min_alignment_ratio=self.min_alignment_ratio,
            )
        except ValueError as exc:
            raise AdapterIntegrityError(
                f"Memo SRT corrected-text projection failed QC: {exc}"
            ) from exc

    def projection_contract(
        self,
        transcript: CanonicalTranscript,
    ) -> tuple[tuple[int, ...], tuple[tuple[int, int, int, int], ...]]:
        result = self.project_result(transcript)
        lengths = tuple(len(cue.token_ids) for cue in result.cues)
        boundaries: list[int] = []
        cursor = 0
        for length in lengths[:-1]:
            cursor += length
            boundaries.append(cursor)
        starts = (0, *boundaries)
        ends = (*boundaries, len(transcript.tokens))
        authoritative = tuple(
            (start, end, cue.start_ms, cue.end_ms)
            for start, end, cue in zip(starts, ends, result.cues, strict=True)
        )
        return tuple(boundaries), authoritative


__all__ = [
    "MemoAcceptedCueV1",
    "MemoBoundaryAuthorityV1",
    "MemoBoundaryEvidenceV1",
    "MemoBoundaryRepairProofV1",
    "MemoBoundaryRepairReason",
    "MemoBoundaryRepairV1",
    "MemoCueBoundaryManifestV1",
    "MemoSourceCueV1",
    "MemoSrtAcceptanceReceiptV1",
    "MemoSrtBoundaryAuthorityV1",
    "load_memo_boundary_manifest",
]
