"""Memo 最後一句超出音檔長度的截齊修補（20260903 程世嘉 cue 2544 超出 902 ms）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, sha256_bytes
from agents.brook.podcast_subtitles.memo_projection import parse_srt
from agents.brook.podcast_subtitles.memo_srt_tail_repair import (
    MAX_TAIL_OVERRUN_MS,
    load_verified_memo_srt_tail_repair,
    repair_memo_srt_tail_overrun_bytes,
)
from agents.brook.podcast_subtitles.ports import AdapterIntegrityError

AUDIO_SHA = "a" * 64
OTHER_SHA = "b" * 64
DURATION_MS = 4_452_158

# 程世嘉集實際的結尾形狀：最後幾句落在 Memo 的 2 秒格上，最後一句超出音檔 902 ms
RAW = (
    "1\n01:14:07,060 --> 01:14:09,060\n哦\n\n"
    "2\n01:14:09,060 --> 01:14:11,060\n哎呀谢谢\n\n"
    "3\n01:14:11,060 --> 01:14:13,060\n謝謝\n"
).encode("utf-8")


def test_clamps_only_the_final_cue_end_to_the_audio_duration() -> None:
    repaired, receipt = repair_memo_srt_tail_overrun_bytes(
        RAW, normalized_audio_sha256=AUDIO_SHA, normalized_audio_duration_ms=DURATION_MS
    )

    before, after = parse_srt(RAW.decode("utf-8")), parse_srt(repaired.decode("utf-8"))
    assert [(c.start_ms, c.end_ms, c.text) for c in after[:-1]] == [
        (c.start_ms, c.end_ms, c.text) for c in before[:-1]
    ]
    assert (after[-1].start_ms, after[-1].end_ms, after[-1].text) == (
        4_451_060,
        DURATION_MS,
        "謝謝",
    )
    assert receipt.contract == "memo-srt-tail-overrun-repair-v1"
    assert receipt.raw_source_sha256 == sha256_bytes(RAW)
    assert receipt.repaired_source_sha256 == sha256_bytes(repaired)
    assert receipt.normalized_audio_sha256 == AUDIO_SHA
    assert receipt.normalized_audio_duration_ms == DURATION_MS
    assert receipt.clamp.index == 3
    assert receipt.clamp.original_end_ms == 4_453_060
    assert receipt.clamp.output_end_ms == DURATION_MS


@pytest.mark.parametrize(
    ("raw", "duration_ms", "error"),
    [
        # 沒有超出：不是這條修補該處理的
        ("1\n00:00:00,100 --> 00:00:00,900\nA\n", 1_000, "no final cue beyond"),
        # 最後一句整句都在音檔之外：時間軸錯了，不是收尾誤差
        ("1\n00:00:01,200 --> 00:00:02,000\nA\n", 1_000, "starts beyond"),
        # 超出比 Memo 的 2 秒格還多：時間軸錯了
        (
            "1\n00:00:00,100 --> 00:00:03,100\nA\n",
            1_000,
            "overruns the audio by 2100 ms",
        ),
        # 零長度、重疊照舊由 parse_srt 擋下（零長度走 repair-memo-srt）
        ("1\n00:00:00,500 --> 00:00:00,500\nA\n", 400, "invalid raw Memo SRT"),
        (
            "1\n00:00:00,100 --> 00:00:00,600\nA\n\n2\n00:00:00,500 --> 00:00:01,200\nB\n",
            1_000,
            "invalid raw Memo SRT",
        ),
    ],
)
def test_fails_closed_outside_the_tail_overrun_shape(
    raw: str, duration_ms: int, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        repair_memo_srt_tail_overrun_bytes(
            raw.encode("utf-8"),
            normalized_audio_sha256=AUDIO_SHA,
            normalized_audio_duration_ms=duration_ms,
        )


def test_overrun_exactly_at_the_grid_limit_is_accepted() -> None:
    raw = f"1\n00:00:00,100 --> 00:00:0{(1_000 + MAX_TAIL_OVERRUN_MS) // 1000},000\nA\n"
    _, receipt = repair_memo_srt_tail_overrun_bytes(
        raw.encode("utf-8"), normalized_audio_sha256=AUDIO_SHA, normalized_audio_duration_ms=1_000
    )
    assert receipt.clamp.original_end_ms - receipt.clamp.output_end_ms == MAX_TAIL_OVERRUN_MS


def _write_lineage(tmp_path: Path) -> tuple[Path, Path, Path]:
    raw = tmp_path / "memo-raw.srt"
    repaired = tmp_path / "memo-tail-repaired.srt"
    receipt_path = tmp_path / "memo-tail-repair.json"
    raw.write_bytes(RAW)
    repaired_bytes, receipt = repair_memo_srt_tail_overrun_bytes(
        RAW, normalized_audio_sha256=AUDIO_SHA, normalized_audio_duration_ms=DURATION_MS
    )
    repaired.write_bytes(repaired_bytes)
    receipt_path.write_bytes(canonical_json_bytes(receipt))
    return raw, repaired, receipt_path


def test_verified_lineage_round_trips(tmp_path: Path) -> None:
    raw, repaired, receipt_path = _write_lineage(tmp_path)
    receipt = load_verified_memo_srt_tail_repair(
        raw_source=raw,
        repaired_source=repaired,
        receipt_path=receipt_path,
        expected_normalized_audio_sha256=AUDIO_SHA,
        expected_normalized_audio_duration_ms=DURATION_MS,
    )
    assert receipt.clamp.output_end_ms == DURATION_MS


@pytest.mark.parametrize(
    ("sha", "duration_ms"), [(OTHER_SHA, DURATION_MS), (AUDIO_SHA, DURATION_MS + 1)]
)
def test_verified_lineage_rejects_another_normalized_audio(
    tmp_path: Path, sha: str, duration_ms: int
) -> None:
    raw, repaired, receipt_path = _write_lineage(tmp_path)
    with pytest.raises(AdapterIntegrityError, match="another normalized audio"):
        load_verified_memo_srt_tail_repair(
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
        load_verified_memo_srt_tail_repair(
            raw_source=raw,
            repaired_source=repaired,
            receipt_path=receipt_path,
            expected_normalized_audio_sha256=AUDIO_SHA,
            expected_normalized_audio_duration_ms=DURATION_MS,
        )
