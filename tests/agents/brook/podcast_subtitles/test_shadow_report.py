from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip(
    "scripts.subtitle_v2_shadow_report",
    reason="legacy shadow-report CLI is outside the Memo-first Subtitle V2 implementation scope",
)

from scripts.subtitle_v2_shadow_report import (
    build_shadow_report,
    extract_legacy_candidate,
    parse_srt_bytes,
)


def _corpora() -> tuple[dict, dict, dict]:
    common = {
        "schema_version": 1,
        "episode_id": "episode",
        "lineage_id": "lineage",
        "normalized_audio_hash": "0" * 64,
    }
    correction = {
        **common,
        "corpus_id": "correction",
        "sources": [
            {"source_id": "s", "path": "candidate.srt", "sha256": "1" * 64}
        ],
        "cases": [
            {
                "case_id": "term",
                "category": "post_seal_freeze",
                "start_ms": 100,
                "end_ms": 500,
                "expected_text": "類型",
                "forbidden_texts": ["型別"],
                "lineage_id": "lineage",
                "normalized_audio_hash": "0" * 64,
                "verification": {"level": "x", "locator": "x", "source_ids": ["s"]},
            }
        ],
    }
    boundary = {
        **common,
        "cases": [
            {
                "case_id": "phrase",
                "start_ms": 500,
                "end_ms": 1500,
                "canonical_text": "人生真實樣貌的一個展現",
                "lexemes": list("人生真實樣貌的一個展現"),
                "forbidden_breaks": [1, 2, 3, 4, 5, 7, 8, 9, 10],
                "lineage_id": "lineage",
                "normalized_audio_hash": "0" * 64,
            }
        ],
    }
    review = {
        **common,
        "cases": [
            {
                "case_id": "ambiguity",
                "start_ms": 1500,
                "end_ms": 2000,
                "expected_outcome": "needs_review",
                "lineage_id": "lineage",
                "normalized_audio_hash": "0" * 64,
            }
        ],
    }
    return correction, boundary, review


def test_legacy_extraction_uses_candidate_bytes_and_exposes_unsafe_promotion() -> None:
    cues = parse_srt_bytes(
        (
            "1\n00:00:00,100 --> 00:00:00,500\n這是型別\n\n"
            "2\n00:00:00,500 --> 00:00:01,000\n人生真實樣貌的一個\n\n"
            "3\n00:00:01,000 --> 00:00:01,500\n展現\n\n"
            "4\n00:00:01,500 --> 00:00:02,000\n我心裡開始健康有問題\n"
        ).encode()
    )
    correction, boundary, review = _corpora()
    candidate = extract_legacy_candidate(
        candidate_id="legacy",
        cues=cues,
        boundary_cues=cues,
        correction_gold=correction,
        boundary_gold=boundary,
        review_gold=review,
    )

    assert candidate.text_observations[0].text == "型別"
    assert candidate.boundary_observations[0].break_positions == (9,)
    assert candidate.review_observations[0].outcome.value == "accepted"


def test_parse_srt_rejects_noncontiguous_sequences() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        parse_srt_bytes(
            (
                "1\n00:00:00,000 --> 00:00:00,500\n甲\n\n"
                "3\n00:00:00,500 --> 00:00:01,000\n乙\n"
            ).encode()
        )


def test_parse_srt_preserves_empty_legacy_cue_as_quality_evidence() -> None:
    cues = parse_srt_bytes(b"1\n00:00:00,000 --> 00:00:00,020\n")

    assert cues[0].lines == ()
    assert cues[0].text == ""


def test_shadow_report_rejects_wrong_audio_lineage(tmp_path: Path) -> None:
    correction, boundary, review = _corpora()
    paths = []
    for name, corpus in zip(("c", "b", "r"), (correction, boundary, review)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(corpus), encoding="utf-8")
        paths.append(path)
    srt = tmp_path / "candidate.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n文字\n", encoding="utf-8")
    audio = tmp_path / "normalized.wav"
    audio.write_bytes(b"not-the-declared-audio")

    with pytest.raises(ValueError, match="lineage"):
        build_shadow_report(
            candidate_srt=srt,
            boundary_srt=None,
            correction_gold=paths[0],
            boundary_gold=paths[1],
            review_gold=paths[2],
            normalized_audio=audio,
        )
