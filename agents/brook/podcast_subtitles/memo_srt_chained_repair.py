"""Deterministic, fail-closed chain: zero-duration repair, then final-cue tail clamp.

The two Memo repairs are each single-receipt lineages, and every downstream
prepare/accept takes exactly one ``--repair-receipt``.  20261009 周慕姿 needed both
on the same export: cue 3514 ``01:22:22,160 --> 01:22:22,160`` (zero duration, in a
repetition burst) *and* the final cue ending 50 ms past the 5,542,330 ms audio.
``repair-memo-srt-tail`` correctly refuses the raw export (``parse_srt`` rejects the
zero cue), and the zero-duration repair does not know the audio length.

This receipt embeds both existing receipts unchanged and adds no new repair rule:

- step 1 is exactly ``repair_memo_srt_bytes(raw)``;
- step 2 is exactly ``repair_memo_srt_tail_overrun_bytes(step1)`` against the bound
  normalized audio hash and duration.

Each step keeps its own fail-closed conditions, so an export that needs only one
repair cannot use this chain (step 1 refuses a clean export, step 2 refuses an
export with no overrun).  Verification replays both steps from the raw bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .hashing import canonical_json_bytes, sha256_bytes
from .memo_srt_repair import MemoSrtRepairReceiptV1, repair_memo_srt_bytes
from .memo_srt_tail_repair import (
    MemoSrtTailRepairReceiptV1,
    repair_memo_srt_tail_overrun_bytes,
)
from .ports import AdapterInputError, AdapterIntegrityError


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MemoSrtChainedRepairReceiptV1(_StrictModel):
    schema_version: Literal[1] = 1
    contract: Literal["memo-srt-zero-duration-then-tail-repair-v1"] = (
        "memo-srt-zero-duration-then-tail-repair-v1"
    )
    raw_source_sha256: str
    raw_source_size_bytes: int = Field(gt=0)
    repaired_source_sha256: str
    repaired_source_size_bytes: int = Field(gt=0)
    normalized_audio_sha256: str
    normalized_audio_duration_ms: int = Field(gt=0)
    zero_duration: MemoSrtRepairReceiptV1
    tail: MemoSrtTailRepairReceiptV1

    @model_validator(mode="after")
    def _valid(self) -> "MemoSrtChainedRepairReceiptV1":
        if (
            self.zero_duration.raw_source_sha256 != self.raw_source_sha256
            or self.zero_duration.raw_source_size_bytes != self.raw_source_size_bytes
        ):
            raise ValueError("Memo SRT chained repair step 1 must start from the raw source")
        if (
            self.tail.raw_source_sha256 != self.zero_duration.repaired_source_sha256
            or self.tail.raw_source_size_bytes != self.zero_duration.repaired_source_size_bytes
        ):
            raise ValueError("Memo SRT chained repair step 2 must start from the step 1 output")
        if (
            self.tail.repaired_source_sha256 != self.repaired_source_sha256
            or self.tail.repaired_source_size_bytes != self.repaired_source_size_bytes
        ):
            raise ValueError("Memo SRT chained repair output must be the step 2 output")
        if (
            self.tail.normalized_audio_sha256 != self.normalized_audio_sha256
            or self.tail.normalized_audio_duration_ms != self.normalized_audio_duration_ms
        ):
            raise ValueError("Memo SRT chained repair tail step belongs to another audio")
        return self


def repair_memo_srt_zero_duration_then_tail_bytes(
    raw_bytes: bytes,
    *,
    normalized_audio_sha256: str,
    normalized_audio_duration_ms: int,
) -> tuple[bytes, bytes, MemoSrtChainedRepairReceiptV1]:
    """Return ``(intermediate, repaired, receipt)`` for the two-step chain."""

    intermediate, zero_receipt = repair_memo_srt_bytes(raw_bytes)
    repaired, tail_receipt = repair_memo_srt_tail_overrun_bytes(
        intermediate,
        normalized_audio_sha256=normalized_audio_sha256,
        normalized_audio_duration_ms=normalized_audio_duration_ms,
    )
    receipt = MemoSrtChainedRepairReceiptV1(
        raw_source_sha256=sha256_bytes(raw_bytes),
        raw_source_size_bytes=len(raw_bytes),
        repaired_source_sha256=sha256_bytes(repaired),
        repaired_source_size_bytes=len(repaired),
        normalized_audio_sha256=normalized_audio_sha256,
        normalized_audio_duration_ms=normalized_audio_duration_ms,
        zero_duration=zero_receipt,
        tail=tail_receipt,
    )
    return intermediate, repaired, receipt


def load_verified_memo_srt_chained_repair(
    *,
    raw_source: str | Path,
    repaired_source: str | Path,
    receipt_path: str | Path,
    expected_normalized_audio_sha256: str,
    expected_normalized_audio_duration_ms: int,
) -> MemoSrtChainedRepairReceiptV1:
    try:
        raw_bytes = Path(raw_source).read_bytes()
        repaired_bytes = Path(repaired_source).read_bytes()
        receipt_bytes = Path(receipt_path).read_bytes()
        json.loads(receipt_bytes, object_pairs_hook=_reject_duplicate_keys)
        receipt = MemoSrtChainedRepairReceiptV1.model_validate_json(receipt_bytes, strict=True)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise AdapterInputError(f"invalid Memo SRT chained repair lineage: {exc}") from exc
    if canonical_json_bytes(receipt) != receipt_bytes:
        raise AdapterIntegrityError("Memo SRT chained repair receipt must be canonical JSON")
    if (
        receipt.normalized_audio_sha256 != expected_normalized_audio_sha256
        or receipt.normalized_audio_duration_ms != expected_normalized_audio_duration_ms
    ):
        raise AdapterIntegrityError("Memo SRT chained repair belongs to another normalized audio")
    try:
        _, expected_repaired, expected_receipt = repair_memo_srt_zero_duration_then_tail_bytes(
            raw_bytes,
            normalized_audio_sha256=expected_normalized_audio_sha256,
            normalized_audio_duration_ms=expected_normalized_audio_duration_ms,
        )
    except ValueError as exc:
        raise AdapterIntegrityError(f"Memo SRT chained repair does not replay: {exc}") from exc
    if repaired_bytes != expected_repaired or receipt != expected_receipt:
        raise AdapterIntegrityError("Memo SRT chained repair lineage is stale or tampered")
    return receipt


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


__all__ = [
    "MemoSrtChainedRepairReceiptV1",
    "load_verified_memo_srt_chained_repair",
    "repair_memo_srt_zero_duration_then_tail_bytes",
]
