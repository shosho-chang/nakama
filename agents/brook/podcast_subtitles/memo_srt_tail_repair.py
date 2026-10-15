"""Deterministic, fail-closed repair for a Memo final cue that overruns the audio.

Memo 1.7.5 (whisper.cpp) closes the last few segments of a file on a fixed
2-second grid, so the final cue can end a fraction of a second *after* the
normalized audio ends.  20260903 程世嘉: cue 2544 ``01:14:11,060 --> 01:14:13,060``
against a 4,452,158 ms file — 902 ms past the end.  Every downstream validator
correctly refuses a token beyond the audio, and the zero-duration repair does not
cover this shape.

This repair changes exactly one number: the final cue's end is clamped to the
normalized audio duration.  Text, every other cue and every start time stay
byte-identical in meaning.  It fails closed unless:

- the overrun is on the final cue only (earlier cues already end inside the audio);
- that cue *starts* inside the audio (there is speech left to keep);
- the overrun is at most ``MAX_TAIL_OVERRUN_MS`` (Memo's 2-second grid) — anything
  larger is a timebase fault, not a rounding artefact, and must stop the line.

The receipt binds the raw/repaired bytes and the exact normalized audio identity,
so the lineage cannot be replayed against another file or duration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .hashing import canonical_json_bytes, sha256_bytes
from .memo_projection import MemoCue, parse_srt
from .memo_srt_repair import _render_srt
from .ports import AdapterInputError, AdapterIntegrityError

#: Memo closes trailing segments on a 2 s grid; a larger overrun is not that artefact.
MAX_TAIL_OVERRUN_MS = 2_000


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


class MemoSrtTailClampV1(_StrictModel):
    index: int = Field(gt=0)
    start_ms: int = Field(ge=0)
    original_end_ms: int = Field(gt=0)
    output_end_ms: int = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def _valid(self) -> "MemoSrtTailClampV1":
        if not self.text.strip():
            raise ValueError("Memo SRT tail clamp cue text must be non-empty")
        if not self.start_ms < self.output_end_ms < self.original_end_ms:
            raise ValueError("Memo SRT tail clamp must shorten a cue that starts inside the audio")
        if self.original_end_ms - self.output_end_ms > MAX_TAIL_OVERRUN_MS:
            raise ValueError("Memo SRT tail overrun exceeds the 2 s Memo segment grid")
        return self


class MemoSrtTailRepairReceiptV1(_StrictModel):
    schema_version: Literal[1] = 1
    contract: Literal["memo-srt-tail-overrun-repair-v1"] = "memo-srt-tail-overrun-repair-v1"
    raw_source_sha256: str
    raw_source_size_bytes: int = Field(gt=0)
    repaired_source_sha256: str
    repaired_source_size_bytes: int = Field(gt=0)
    normalized_audio_sha256: str
    normalized_audio_duration_ms: int = Field(gt=0)
    max_tail_overrun_ms: Literal[2_000] = MAX_TAIL_OVERRUN_MS
    clamp: MemoSrtTailClampV1

    @model_validator(mode="after")
    def _valid(self) -> "MemoSrtTailRepairReceiptV1":
        for label, value in (
            ("raw source", self.raw_source_sha256),
            ("repaired source", self.repaired_source_sha256),
            ("normalized audio", self.normalized_audio_sha256),
        ):
            if not _is_sha256(value):
                raise ValueError(f"Memo SRT tail repair {label} digest must be lowercase SHA-256")
        if self.clamp.output_end_ms != self.normalized_audio_duration_ms:
            raise ValueError("Memo SRT tail clamp must end exactly at the normalized audio end")
        return self


def repair_memo_srt_tail_overrun_bytes(
    raw_bytes: bytes,
    *,
    normalized_audio_sha256: str,
    normalized_audio_duration_ms: int,
) -> tuple[bytes, MemoSrtTailRepairReceiptV1]:
    """Clamp only the final cue's end to the normalized audio duration."""

    try:
        cues = parse_srt(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid raw Memo SRT: {exc}") from exc
    last = cues[-1]
    if last.end_ms <= normalized_audio_duration_ms:
        raise ValueError("Memo SRT has no final cue beyond the normalized audio to repair")
    if last.start_ms >= normalized_audio_duration_ms:
        raise ValueError(
            f"Memo SRT final cue {last.index} starts beyond the normalized audio; "
            "this is a timebase fault, not a tail overrun"
        )
    overrun = last.end_ms - normalized_audio_duration_ms
    if overrun > MAX_TAIL_OVERRUN_MS:
        raise ValueError(
            f"Memo SRT final cue overruns the audio by {overrun} ms "
            f"(> {MAX_TAIL_OVERRUN_MS} ms); this is a timebase fault, not a tail overrun"
        )

    clamped = MemoCue(
        index=last.index,
        start_ms=last.start_ms,
        end_ms=normalized_audio_duration_ms,
        text=last.text,
        source_indexes=last.source_indexes,
    )
    repaired = _render_srt((*cues[:-1], clamped))
    reparsed = parse_srt(repaired.decode("utf-8"))
    if [(c.start_ms, c.end_ms, c.text) for c in reparsed[:-1]] != [
        (c.start_ms, c.end_ms, c.text) for c in cues[:-1]
    ] or reparsed[-1].text != last.text:
        raise ValueError("Memo SRT tail repair changed text or timing beyond the final end")
    receipt = MemoSrtTailRepairReceiptV1(
        raw_source_sha256=sha256_bytes(raw_bytes),
        raw_source_size_bytes=len(raw_bytes),
        repaired_source_sha256=sha256_bytes(repaired),
        repaired_source_size_bytes=len(repaired),
        normalized_audio_sha256=normalized_audio_sha256,
        normalized_audio_duration_ms=normalized_audio_duration_ms,
        clamp=MemoSrtTailClampV1(
            index=last.index,
            start_ms=last.start_ms,
            original_end_ms=last.end_ms,
            output_end_ms=normalized_audio_duration_ms,
            text=last.text,
        ),
    )
    return repaired, receipt


def load_verified_memo_srt_tail_repair(
    *,
    raw_source: str | Path,
    repaired_source: str | Path,
    receipt_path: str | Path,
    expected_normalized_audio_sha256: str,
    expected_normalized_audio_duration_ms: int,
) -> MemoSrtTailRepairReceiptV1:
    try:
        raw_bytes = Path(raw_source).read_bytes()
        repaired_bytes = Path(repaired_source).read_bytes()
        receipt_bytes = Path(receipt_path).read_bytes()
        json.loads(receipt_bytes, object_pairs_hook=_reject_duplicate_keys)
        receipt = MemoSrtTailRepairReceiptV1.model_validate_json(receipt_bytes, strict=True)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise AdapterInputError(f"invalid Memo SRT tail repair lineage: {exc}") from exc
    if canonical_json_bytes(receipt) != receipt_bytes:
        raise AdapterIntegrityError("Memo SRT tail repair receipt must be canonical JSON")
    if (
        receipt.normalized_audio_sha256 != expected_normalized_audio_sha256
        or receipt.normalized_audio_duration_ms != expected_normalized_audio_duration_ms
    ):
        raise AdapterIntegrityError("Memo SRT tail repair belongs to another normalized audio")
    expected_repaired, expected_receipt = repair_memo_srt_tail_overrun_bytes(
        raw_bytes,
        normalized_audio_sha256=expected_normalized_audio_sha256,
        normalized_audio_duration_ms=expected_normalized_audio_duration_ms,
    )
    if repaired_bytes != expected_repaired or receipt != expected_receipt:
        raise AdapterIntegrityError("Memo SRT tail repair lineage is stale or tampered")
    return receipt


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


__all__ = [
    "MAX_TAIL_OVERRUN_MS",
    "MemoSrtTailClampV1",
    "MemoSrtTailRepairReceiptV1",
    "load_verified_memo_srt_tail_repair",
    "repair_memo_srt_tail_overrun_bytes",
]
