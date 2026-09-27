"""Deterministic insertion of targeted Memo bundled-runner cues into a VAD gap."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .adapters.normalized_handoff import wav_duration_ms
from .hashing import canonical_json_bytes, measure_regular_file, sha256_bytes
from .memo_bundled_runner import (
    MemoBundledRunnerRequest,
    load_memo_bundled_runner_execution_receipt,
    load_verified_memo_bundled_runner_execution,
)
from .memo_projection import MemoCue, parse_srt
from .memo_srt_repair import load_verified_memo_srt_repair
from .ports import AdapterInputError, AdapterIntegrityError


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MemoVadGapRepairInputs(_StrictModel):
    """All immutable artifacts required to recompute a targeted gap insertion."""

    normalized_audio: Path
    normalized_handoff: Path
    raw_source: Path
    parent_repaired_source: Path
    parent_repair_receipt: Path
    target_wav: Path
    target_srt: Path
    target_stdout: Path
    target_stderr: Path
    target_execution_receipt: Path
    runner: Path
    model: Path
    global_offset_ms: int = Field(ge=0)
    declared_gap_start_ms: int = Field(ge=0)
    declared_gap_end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def _valid_gap(self) -> "MemoVadGapRepairInputs":
        if self.declared_gap_end_ms <= self.declared_gap_start_ms:
            raise ValueError("Memo VAD repair declared gap must have positive duration")
        if self.global_offset_ms != self.declared_gap_start_ms:
            raise ValueError("Memo VAD repair global offset must equal declared gap start")
        return self


class MemoVadGapInsertedCueV1(_StrictModel):
    target_index: int = Field(gt=0)
    local_start_ms: int = Field(ge=0)
    local_end_ms: int = Field(gt=0)
    global_start_ms: int = Field(ge=0)
    global_end_ms: int = Field(gt=0)
    output_index: int = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def _valid_cue(self) -> "MemoVadGapInsertedCueV1":
        if (
            not self.text.strip()
            or self.local_end_ms <= self.local_start_ms
            or self.global_end_ms <= self.global_start_ms
            or self.global_end_ms - self.global_start_ms
            != self.local_end_ms - self.local_start_ms
        ):
            raise ValueError("Memo VAD repair inserted cue is invalid")
        return self


class MemoVadGapRepairReceiptV1(_StrictModel):
    schema_version: Literal[1] = 1
    contract: Literal["memo-bundled-runner-vad-gap-repair-v1"] = (
        "memo-bundled-runner-vad-gap-repair-v1"
    )
    normalized_audio_sha256: str
    normalized_audio_size_bytes: int = Field(gt=0)
    normalized_audio_duration_ms: int = Field(gt=0)
    normalized_handoff_sha256: str
    normalized_handoff_size_bytes: int = Field(gt=0)
    raw_source_sha256: str
    raw_source_size_bytes: int = Field(gt=0)
    parent_repaired_source_sha256: str
    parent_repaired_source_size_bytes: int = Field(gt=0)
    parent_repair_receipt_sha256: str
    parent_repair_receipt_size_bytes: int = Field(gt=0)
    target_wav_sha256: str
    target_wav_size_bytes: int = Field(gt=0)
    target_wav_duration_ms: int = Field(gt=0)
    target_srt_sha256: str
    target_srt_size_bytes: int = Field(gt=0)
    target_execution_receipt_sha256: str
    target_execution_receipt_size_bytes: int = Field(gt=0)
    runner_sha256: str
    runner_size_bytes: int = Field(gt=0)
    runner_filename: str
    model_sha256: str
    model_size_bytes: int = Field(gt=0)
    model_filename: Literal["ggml-large-v2.bin"] = "ggml-large-v2.bin"
    global_offset_ms: int = Field(ge=0)
    declared_gap_start_ms: int = Field(ge=0)
    declared_gap_end_ms: int = Field(gt=0)
    inserted_cues: tuple[MemoVadGapInsertedCueV1, ...]
    output_sha256: str
    output_size_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def _valid_receipt(self) -> "MemoVadGapRepairReceiptV1":
        digest_fields = (
            self.normalized_audio_sha256,
            self.normalized_handoff_sha256,
            self.raw_source_sha256,
            self.parent_repaired_source_sha256,
            self.parent_repair_receipt_sha256,
            self.target_wav_sha256,
            self.target_srt_sha256,
            self.target_execution_receipt_sha256,
            self.runner_sha256,
            self.model_sha256,
            self.output_sha256,
        )
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in digest_fields
        ):
            raise ValueError("Memo VAD repair digest must be lowercase SHA-256")
        if not self.runner_filename or not self.inserted_cues:
            raise ValueError("Memo VAD repair requires runner identity and inserted cues")
        if (
            self.declared_gap_end_ms <= self.declared_gap_start_ms
            or self.global_offset_ms != self.declared_gap_start_ms
            or self.target_wav_duration_ms
            != self.declared_gap_end_ms - self.declared_gap_start_ms
        ):
            raise ValueError("Memo VAD repair receipt has inconsistent gap timing")
        return self


def _render_srt(cues: tuple[MemoCue, ...]) -> bytes:
    def timestamp(value: int) -> str:
        hours, remainder = divmod(value, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    payload = "\n\n".join(
        f"{index}\n{timestamp(cue.start_ms)} --> {timestamp(cue.end_ms)}\n{cue.text}"
        for index, cue in enumerate(cues, start=1)
    )
    return f"{payload}\n".encode("utf-8")


def _artifact(path: Path) -> tuple[str, int]:
    return measure_regular_file(path)


def repair_memo_vad_gap(
    inputs: MemoVadGapRepairInputs,
) -> tuple[bytes, MemoVadGapRepairReceiptV1]:
    """Recompute a composite SRT from a verified parent and exact runner export."""

    parent_receipt = load_verified_memo_srt_repair(
        raw_source=inputs.raw_source,
        repaired_source=inputs.parent_repaired_source,
        receipt_path=inputs.parent_repair_receipt,
    )
    if inputs.model.name != "ggml-large-v2.bin":
        raise ValueError("Memo VAD repair requires ggml-large-v2.bin")
    execution_declaration = load_memo_bundled_runner_execution_receipt(
        inputs.target_execution_receipt
    )
    execution = load_verified_memo_bundled_runner_execution(
        request=MemoBundledRunnerRequest(
            runner=inputs.runner,
            model=inputs.model,
            input_wav=inputs.target_wav,
            output_srt=inputs.target_srt,
            stdout_output=inputs.target_stdout,
            stderr_output=inputs.target_stderr,
            receipt_output=inputs.target_execution_receipt,
            gpu=execution_declaration.gpu,
            language=execution_declaration.language,
            prompt=execution_declaration.prompt,
            max_context=execution_declaration.max_context,
            max_len=execution_declaration.max_len,
        )
    )
    target_duration_ms = wav_duration_ms(inputs.target_wav)
    if target_duration_ms != inputs.declared_gap_end_ms - inputs.declared_gap_start_ms:
        raise ValueError("Memo VAD repair target WAV duration differs from declared gap")

    try:
        parent_cues = parse_srt(inputs.parent_repaired_source.read_text(encoding="utf-8-sig"))
        target_cues = parse_srt(inputs.target_srt.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid Memo VAD repair SRT: {exc}") from exc
    if target_cues[-1].end_ms > target_duration_ms:
        raise ValueError("Memo VAD repair target cue is outside declared gap")

    shifted = tuple(
        MemoCue(
            index=cue.index,
            start_ms=inputs.global_offset_ms + cue.start_ms,
            end_ms=inputs.global_offset_ms + cue.end_ms,
            text=cue.text,
            source_indexes=(cue.index,),
        )
        for cue in target_cues
    )
    if any(
        cue.start_ms < inputs.declared_gap_start_ms
        or cue.end_ms > inputs.declared_gap_end_ms
        for cue in shifted
    ):
        raise ValueError("Memo VAD repair target cue is outside declared gap")
    if any(
        cue.start_ms < inputs.declared_gap_end_ms
        and cue.end_ms > inputs.declared_gap_start_ms
        for cue in parent_cues
    ):
        raise ValueError("Memo VAD repair declared gap overlaps parent cues")

    before = tuple(cue for cue in parent_cues if cue.end_ms <= inputs.declared_gap_start_ms)
    after = tuple(cue for cue in parent_cues if cue.start_ms >= inputs.declared_gap_end_ms)
    if len(before) + len(after) != len(parent_cues):
        raise ValueError("Memo VAD repair declared gap overlaps parent cues")
    composite_cues = (*before, *shifted, *after)
    composite = _render_srt(composite_cues)
    parse_srt(composite.decode("utf-8"))

    normalized_hash, normalized_size = _artifact(inputs.normalized_audio)
    handoff_hash, handoff_size = _artifact(inputs.normalized_handoff)
    raw_hash, raw_size = _artifact(inputs.raw_source)
    parent_hash, parent_size = _artifact(inputs.parent_repaired_source)
    parent_receipt_hash, parent_receipt_size = _artifact(inputs.parent_repair_receipt)
    target_wav_hash, target_wav_size = _artifact(inputs.target_wav)
    target_srt_hash, target_srt_size = _artifact(inputs.target_srt)
    execution_hash, execution_size = _artifact(inputs.target_execution_receipt)
    runner_hash, runner_size = _artifact(inputs.runner)
    model_hash, model_size = _artifact(inputs.model)
    if (raw_hash, raw_size, parent_hash, parent_size) != (
        parent_receipt.raw_source_sha256,
        parent_receipt.raw_source_size_bytes,
        parent_receipt.repaired_source_sha256,
        parent_receipt.repaired_source_size_bytes,
    ):
        raise ValueError("Memo VAD repair parent lineage differs from receipt")
    output_hash = sha256_bytes(composite)
    receipt = MemoVadGapRepairReceiptV1(
        normalized_audio_sha256=normalized_hash,
        normalized_audio_size_bytes=normalized_size,
        normalized_audio_duration_ms=wav_duration_ms(inputs.normalized_audio),
        normalized_handoff_sha256=handoff_hash,
        normalized_handoff_size_bytes=handoff_size,
        raw_source_sha256=raw_hash,
        raw_source_size_bytes=raw_size,
        parent_repaired_source_sha256=parent_hash,
        parent_repaired_source_size_bytes=parent_size,
        parent_repair_receipt_sha256=parent_receipt_hash,
        parent_repair_receipt_size_bytes=parent_receipt_size,
        target_wav_sha256=target_wav_hash,
        target_wav_size_bytes=target_wav_size,
        target_wav_duration_ms=target_duration_ms,
        target_srt_sha256=target_srt_hash,
        target_srt_size_bytes=target_srt_size,
        target_execution_receipt_sha256=execution_hash,
        target_execution_receipt_size_bytes=execution_size,
        runner_sha256=runner_hash,
        runner_size_bytes=runner_size,
        runner_filename=inputs.runner.name,
        model_sha256=model_hash,
        model_size_bytes=model_size,
        global_offset_ms=inputs.global_offset_ms,
        declared_gap_start_ms=inputs.declared_gap_start_ms,
        declared_gap_end_ms=inputs.declared_gap_end_ms,
        inserted_cues=tuple(
            MemoVadGapInsertedCueV1(
                target_index=target.index,
                local_start_ms=target.start_ms,
                local_end_ms=target.end_ms,
                global_start_ms=shifted_cue.start_ms,
                global_end_ms=shifted_cue.end_ms,
                output_index=len(before) + index,
                text=target.text,
            )
            for index, (target, shifted_cue) in enumerate(
                zip(target_cues, shifted, strict=True), start=1
            )
        ),
        output_sha256=output_hash,
        output_size_bytes=len(composite),
    )
    if (
        execution.output_srt_sha256 != receipt.target_srt_sha256
        or execution.runner_sha256 != receipt.runner_sha256
        or execution.model_sha256 != receipt.model_sha256
        or execution.input_wav_sha256 != receipt.target_wav_sha256
    ):
        raise ValueError("Memo VAD repair execution lineage differs from target artifacts")
    return composite, receipt


def load_verified_memo_vad_gap_repair(
    *,
    inputs: MemoVadGapRepairInputs,
    composite_source: str | Path,
    receipt_path: str | Path,
) -> MemoVadGapRepairReceiptV1:
    """Load a canonical receipt and replay every bound artifact fail-closed."""

    try:
        receipt_bytes = Path(receipt_path).read_bytes()
        json.loads(receipt_bytes, object_pairs_hook=_reject_duplicate_keys)
        receipt = MemoVadGapRepairReceiptV1.model_validate_json(receipt_bytes, strict=True)
        composite_bytes = Path(composite_source).read_bytes()
        expected_bytes, expected_receipt = repair_memo_vad_gap(inputs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise AdapterInputError(f"invalid Memo VAD gap repair lineage: {exc}") from exc
    if canonical_json_bytes(receipt) != receipt_bytes:
        raise AdapterIntegrityError("Memo VAD gap repair receipt must be canonical JSON")
    if composite_bytes != expected_bytes or receipt != expected_receipt:
        raise AdapterIntegrityError("Memo VAD gap repair lineage is stale or tampered")
    return receipt


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


__all__ = [
    "MemoVadGapInsertedCueV1",
    "MemoVadGapRepairInputs",
    "MemoVadGapRepairReceiptV1",
    "load_verified_memo_vad_gap_repair",
    "repair_memo_vad_gap",
]
