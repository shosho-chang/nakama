"""零長度 cue 與結尾超出同時出現時的兩段修補（20261009 周慕姿 cue 3514 + 結尾超出 50 ms）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, sha256_bytes
from agents.brook.podcast_subtitles.memo_projection import parse_srt
from agents.brook.podcast_subtitles.memo_srt_chained_repair import (
    MemoSrtChainedRepairReceiptV1,
    load_verified_memo_srt_chained_repair,
    repair_memo_srt_zero_duration_then_tail_bytes,
)
from agents.brook.podcast_subtitles.memo_srt_repair import repair_memo_srt_bytes
from agents.brook.podcast_subtitles.memo_srt_tail_repair import (
    repair_memo_srt_tail_overrun_bytes,
)
from agents.brook.podcast_subtitles.ports import AdapterIntegrityError

AUDIO_SHA = "a" * 64
OTHER_SHA = "b" * 64
DURATION_MS = 5_000

# 周慕姿集的形狀縮小版：中段一句零長度（重複輸出區），最後一句超出音檔 50 ms
RAW = (
    "1\n00:00:01,000 --> 00:00:01,800\n這個人跟他關係最好 接納他\n\n"
    "2\n00:00:01,800 --> 00:00:01,800\n接納他\n\n"
    "3\n00:00:01,800 --> 00:00:02,100\n接納他 對\n\n"
    "4\n00:00:02,100 --> 00:00:05,050\n好 謝謝\n"
).encode("utf-8")


def test_chain_equals_the_two_existing_repairs_applied_in_order() -> None:
    intermediate, repaired, receipt = repair_memo_srt_zero_duration_then_tail_bytes(
        RAW, normalized_audio_sha256=AUDIO_SHA, normalized_audio_duration_ms=DURATION_MS
    )

    expected_intermediate, zero_receipt = repair_memo_srt_bytes(RAW)
    expected_repaired, tail_receipt = repair_memo_srt_tail_overrun_bytes(
        expected_intermediate,
        normalized_audio_sha256=AUDIO_SHA,
        normalized_audio_duration_ms=DURATION_MS,
    )
    assert intermediate == expected_intermediate
    assert repaired == expected_repaired
    assert receipt.zero_duration == zero_receipt
    assert receipt.tail == tail_receipt
    assert receipt.contract == "memo-srt-zero-duration-then-tail-repair-v1"
    assert receipt.raw_source_sha256 == sha256_bytes(RAW)
    assert receipt.repaired_source_sha256 == sha256_bytes(repaired)

    cues = parse_srt(repaired.decode("utf-8"))
    assert all(cue.end_ms > cue.start_ms for cue in cues)
    assert cues[-1].end_ms == DURATION_MS
    assert len(cues) == 3


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        # 只有結尾超出：走 repair-memo-srt-tail，不准用串接收據
        ("1\n00:00:00,100 --> 00:00:05,050\nA\n", "zero"),
        # 只有零長度：走 repair-memo-srt，不准用串接收據
        (
            "1\n00:00:00,100 --> 00:00:00,500\nA\n\n2\n00:00:00,500 --> 00:00:00,500\nB\n",
            "no final cue beyond",
        ),
    ],
)
def test_chain_refuses_exports_that_need_only_one_repair(raw: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        repair_memo_srt_zero_duration_then_tail_bytes(
            raw.encode("utf-8"),
            normalized_audio_sha256=AUDIO_SHA,
            normalized_audio_duration_ms=DURATION_MS,
        )


def test_receipt_rejects_steps_that_do_not_link() -> None:
    _, _, receipt = repair_memo_srt_zero_duration_then_tail_bytes(
        RAW, normalized_audio_sha256=AUDIO_SHA, normalized_audio_duration_ms=DURATION_MS
    )
    payload = receipt.model_dump()
    payload["tail"] = {**payload["tail"], "raw_source_sha256": OTHER_SHA}
    with pytest.raises(ValueError, match="step 2 must start from the step 1 output"):
        MemoSrtChainedRepairReceiptV1.model_validate(payload)


def _write_lineage(tmp_path: Path) -> tuple[Path, Path, Path]:
    raw = tmp_path / "memo-raw.srt"
    repaired = tmp_path / "memo-chain-repaired.srt"
    receipt_path = tmp_path / "memo-chain-repair.json"
    raw.write_bytes(RAW)
    _, repaired_bytes, receipt = repair_memo_srt_zero_duration_then_tail_bytes(
        RAW, normalized_audio_sha256=AUDIO_SHA, normalized_audio_duration_ms=DURATION_MS
    )
    repaired.write_bytes(repaired_bytes)
    receipt_path.write_bytes(canonical_json_bytes(receipt))
    return raw, repaired, receipt_path


def test_verified_lineage_round_trips(tmp_path: Path) -> None:
    raw, repaired, receipt_path = _write_lineage(tmp_path)
    receipt = load_verified_memo_srt_chained_repair(
        raw_source=raw,
        repaired_source=repaired,
        receipt_path=receipt_path,
        expected_normalized_audio_sha256=AUDIO_SHA,
        expected_normalized_audio_duration_ms=DURATION_MS,
    )
    assert receipt.tail.clamp.output_end_ms == DURATION_MS


@pytest.mark.parametrize(
    ("sha", "duration_ms"), [(OTHER_SHA, DURATION_MS), (AUDIO_SHA, DURATION_MS + 1)]
)
def test_verified_lineage_rejects_another_normalized_audio(
    tmp_path: Path, sha: str, duration_ms: int
) -> None:
    raw, repaired, receipt_path = _write_lineage(tmp_path)
    with pytest.raises(AdapterIntegrityError, match="another normalized audio"):
        load_verified_memo_srt_chained_repair(
            raw_source=raw,
            repaired_source=repaired,
            receipt_path=receipt_path,
            expected_normalized_audio_sha256=sha,
            expected_normalized_audio_duration_ms=duration_ms,
        )


@pytest.mark.parametrize("tampered", ["raw", "repaired", "receipt"])
def test_verified_lineage_rejects_stale_or_tampered_artifacts(
    tmp_path: Path, tampered: str
) -> None:
    raw, repaired, receipt_path = _write_lineage(tmp_path)
    target = {"raw": raw, "repaired": repaired, "receipt": receipt_path}[tampered]
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(AdapterIntegrityError):
        load_verified_memo_srt_chained_repair(
            raw_source=raw,
            repaired_source=repaired,
            receipt_path=receipt_path,
            expected_normalized_audio_sha256=AUDIO_SHA,
            expected_normalized_audio_duration_ms=DURATION_MS,
        )
