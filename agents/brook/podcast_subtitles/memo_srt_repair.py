"""Deterministic, fail-closed repair for Memo zero-duration SRT cues."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .hashing import canonical_json_bytes, sha256_bytes
from .memo_projection import MemoCue, parse_srt
from .ports import AdapterInputError, AdapterIntegrityError


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MemoSrtRepairSourceCueV1(_StrictModel):
    index: int = Field(gt=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str

    @model_validator(mode="after")
    def _valid(self) -> "MemoSrtRepairSourceCueV1":
        if not self.text.strip() or self.end_ms < self.start_ms:
            raise ValueError("Memo SRT repair source cue is invalid")
        return self


class MemoSrtZeroDurationMergeV1(_StrictModel):
    source_cues: tuple[MemoSrtRepairSourceCueV1, ...]
    output_index: int = Field(gt=0)
    output_start_ms: int = Field(ge=0)
    output_end_ms: int = Field(gt=0)
    output_text: str

    @model_validator(mode="after")
    def _valid(self) -> "MemoSrtZeroDurationMergeV1":
        if len(self.source_cues) < 2:
            raise ValueError("Memo SRT zero-duration repair requires adjacent source cues")
        zero = tuple(cue for cue in self.source_cues if cue.start_ms == cue.end_ms)
        positive = tuple(cue for cue in self.source_cues if cue.end_ms > cue.start_ms)
        if not zero or len(positive) != 1:
            raise ValueError("Memo SRT repair must merge zero cues with one positive cue")
        anchor = positive[0]
        if any(
            cue.start_ms != anchor.start_ms and cue.end_ms != anchor.end_ms for cue in zero
        ):
            raise ValueError("Memo SRT zero cue lacks an exact shared boundary")
        if (
            self.output_start_ms != self.source_cues[0].start_ms
            or self.output_end_ms != self.source_cues[-1].end_ms
            or self.output_end_ms <= self.output_start_ms
            or self.output_text != "".join(cue.text for cue in self.source_cues)
        ):
            raise ValueError("Memo SRT repair changed text or outer timing")
        return self


class MemoSrtRepairReceiptV1(_StrictModel):
    schema_version: Literal[1] = 1
    contract: Literal["memo-srt-zero-duration-repair-v1"] = (
        "memo-srt-zero-duration-repair-v1"
    )
    raw_source_sha256: str
    raw_source_size_bytes: int = Field(gt=0)
    repaired_source_sha256: str
    repaired_source_size_bytes: int = Field(gt=0)
    merges: tuple[MemoSrtZeroDurationMergeV1, ...]

    @model_validator(mode="after")
    def _valid(self) -> "MemoSrtRepairReceiptV1":
        for label, value in (
            ("raw source", self.raw_source_sha256),
            ("repaired source", self.repaired_source_sha256),
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"Memo SRT repair {label} digest must be lowercase SHA-256")
        if not self.merges:
            raise ValueError("Memo SRT repair receipt requires at least one merge")
        return self


def _render_srt(cues: tuple[MemoCue, ...]) -> bytes:
    def timestamp(value: int) -> str:
        hours, remainder = divmod(value, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    text = "\n\n".join(
        f"{index}\n{timestamp(cue.start_ms)} --> {timestamp(cue.end_ms)}\n{cue.text}"
        for index, cue in enumerate(cues, start=1)
    )
    return f"{text}\n".encode("utf-8")


def _receipt_cue(cue: MemoCue) -> MemoSrtRepairSourceCueV1:
    return MemoSrtRepairSourceCueV1(
        index=cue.index,
        start_ms=cue.start_ms,
        end_ms=cue.end_ms,
        text=cue.text,
    )


def repair_memo_srt_bytes(raw_bytes: bytes) -> tuple[bytes, MemoSrtRepairReceiptV1]:
    """Merge only zero-duration cues into an exact-boundary adjacent cue.

    A following positive cue sharing the zero cue's start is authoritative when
    available (the shape emitted by Memo 1.7.5).  A preceding positive cue is
    used only when no exact forward anchor exists.  No timestamp is invented.
    """

    try:
        cues = parse_srt(raw_bytes.decode("utf-8-sig"), allow_zero_duration=True)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid raw Memo SRT: {exc}") from exc
    indexes = tuple(cue.index for cue in cues)
    if len(set(indexes)) != len(indexes):
        raise ValueError("raw Memo SRT cue indexes must be unique")

    output: list[MemoCue] = []
    merges: list[MemoSrtZeroDurationMergeV1] = []
    position = 0
    while position < len(cues):
        cue = cues[position]
        if cue.end_ms > cue.start_ms:
            output.append(cue)
            position += 1
            continue

        zero_end = position
        while (
            zero_end + 1 < len(cues)
            and cues[zero_end + 1].start_ms == cues[zero_end + 1].end_ms == cue.start_ms
        ):
            zero_end += 1
        zero_cues = cues[position : zero_end + 1]
        next_position = zero_end + 1
        if (
            next_position < len(cues)
            and cues[next_position].end_ms > cues[next_position].start_ms
            and cues[next_position].start_ms == cue.start_ms
        ):
            selected = (*zero_cues, cues[next_position])
            position = next_position + 1
        elif output and output[-1].end_ms == cue.start_ms:
            selected = (output.pop(), *zero_cues)
            position = zero_end + 1
        else:
            raise ValueError(
                f"Memo SRT zero-duration cue {cue.index} has no exact adjacent positive anchor"
            )

        merged = MemoCue(
            index=len(output) + 1,
            start_ms=selected[0].start_ms,
            end_ms=selected[-1].end_ms,
            text="".join(item.text for item in selected),
            source_indexes=tuple(item.index for item in selected),
        )
        if merged.end_ms <= merged.start_ms:
            raise ValueError("Memo SRT zero-duration merge lacks a positive outer span")
        output.append(merged)
        merges.append(
            MemoSrtZeroDurationMergeV1(
                source_cues=tuple(_receipt_cue(item) for item in selected),
                output_index=len(output),
                output_start_ms=merged.start_ms,
                output_end_ms=merged.end_ms,
                output_text=merged.text,
            )
        )

    if not merges:
        raise ValueError("Memo SRT contains no zero-duration cues to repair")
    repaired = _render_srt(tuple(output))
    parse_srt(repaired.decode("utf-8"))
    receipt = MemoSrtRepairReceiptV1(
        raw_source_sha256=sha256_bytes(raw_bytes),
        raw_source_size_bytes=len(raw_bytes),
        repaired_source_sha256=sha256_bytes(repaired),
        repaired_source_size_bytes=len(repaired),
        merges=tuple(merges),
    )
    return repaired, receipt


def load_verified_memo_srt_repair(
    *, raw_source: str | Path, repaired_source: str | Path, receipt_path: str | Path
) -> MemoSrtRepairReceiptV1:
    try:
        raw_bytes = Path(raw_source).read_bytes()
        repaired_bytes = Path(repaired_source).read_bytes()
        receipt_bytes = Path(receipt_path).read_bytes()
        json.loads(receipt_bytes, object_pairs_hook=_reject_duplicate_keys)
        receipt = MemoSrtRepairReceiptV1.model_validate_json(receipt_bytes, strict=True)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise AdapterInputError(f"invalid Memo SRT repair lineage: {exc}") from exc
    if canonical_json_bytes(receipt) != receipt_bytes:
        raise AdapterIntegrityError("Memo SRT repair receipt must be canonical JSON")
    expected_repaired, expected_receipt = repair_memo_srt_bytes(raw_bytes)
    if repaired_bytes != expected_repaired or receipt != expected_receipt:
        raise AdapterIntegrityError("Memo SRT repair lineage is stale or tampered")
    return receipt


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


__all__ = [
    "MemoSrtRepairReceiptV1",
    "MemoSrtRepairSourceCueV1",
    "MemoSrtZeroDurationMergeV1",
    "load_verified_memo_srt_repair",
    "repair_memo_srt_bytes",
]
