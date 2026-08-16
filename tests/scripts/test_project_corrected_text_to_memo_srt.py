from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.project_corrected_text_to_memo_srt import (
    BoundaryRetentionError,
    MemoCue,
    SourceToken,
    apply_boundary_merge_proposals,
    main,
    project_tokens_to_memo_cues,
)


def _token(token_id: str, text: str, start_ms: int, end_ms: int) -> SourceToken:
    return SourceToken(token_id, text, start_ms, end_ms)


def _cue(index: int, start_ms: int, end_ms: int, text: str) -> MemoCue:
    return MemoCue(index, start_ms, end_ms, text)


def test_global_alignment_projects_insert_delete_and_substitution_exactly() -> None:
    tokens = (
        _token("t0", "甲", 0, 100),
        _token("t1", "乙", 100, 200),
        _token("t2", "安吉", 200, 400),
        _token("t3", "報", 400, 500),
        _token("t4", "告", 500, 600),
    )
    memo_cues = (
        _cue(1, 0, 100, "甲"),
        _cue(2, 100, 220, "多餘乙"),  # Memo insertion.
        _cue(3, 220, 400, "安琪"),  # Substitution.
        _cue(4, 400, 600, "報"),  # Memo deletion of 告.
    )

    result = project_tokens_to_memo_cues(
        tokens,
        memo_cues,
        min_boundary_retention=0.0,
        min_alignment_ratio=0.0,
    )

    assert "".join(cue.text for cue in result.cues) == "甲乙安吉報告"
    assert result.alignment.source_text != result.alignment.target_text
    assert result.alignment.matching_characters > 0
    assert all(cue.text for cue in result.cues)
    assert all(cue.end_ms > cue.start_ms for cue in result.cues)


def test_english_multichar_token_is_never_split_at_memo_boundary() -> None:
    tokens = (
        _token("t0", "甲", 0, 100),
        _token("t1", "OpenAI", 100, 600),
        _token("t2", "乙", 600, 700),
    )
    memo_cues = (
        _cue(1, 0, 300, "甲Open"),
        _cue(2, 300, 700, "AI乙"),
    )

    result = project_tokens_to_memo_cues(
        tokens,
        memo_cues,
        min_boundary_retention=0.0,
        min_alignment_ratio=0.0,
    )

    assert [cue.text for cue in result.cues] in (
        ["甲", "OpenAI乙"],
        ["甲OpenAI", "乙"],
    )
    assert [token_id for cue in result.cues for token_id in cue.token_ids] == [
        "t0",
        "t1",
        "t2",
    ]


def test_duplicate_mapped_edges_merge_adjacent_memo_cues() -> None:
    tokens = (
        _token("t0", "甲", 0, 100),
        _token("t1", "乙", 300, 400),
    )
    memo_cues = (
        _cue(1, 0, 100, "甲"),
        _cue(2, 100, 200, "幻"),
        _cue(3, 200, 300, "覺"),
        _cue(4, 300, 400, "乙"),
    )

    result = project_tokens_to_memo_cues(
        tokens,
        memo_cues,
        min_boundary_retention=0.0,
        min_alignment_ratio=0.0,
    )

    assert "".join(cue.text for cue in result.cues) == "甲乙"
    assert result.merged_boundary_count >= 1
    assert len(result.cues) < len(memo_cues)


def test_projection_fails_closed_when_boundary_retention_is_below_threshold() -> None:
    tokens = (_token("english", "OpenAI", 0, 1000),)
    memo_cues = tuple(
        _cue(index + 1, index * 100, (index + 1) * 100, character)
        for index, character in enumerate("OpenAI")
    )

    with pytest.raises(BoundaryRetentionError):
        project_tokens_to_memo_cues(tokens, memo_cues, min_boundary_retention=0.95)


def test_cli_writes_single_line_srt_and_provenance_sidecar(tmp_path: Path) -> None:
    correction_path = tmp_path / "correction.json"
    memo_path = tmp_path / "memo.srt"
    output_srt = tmp_path / "candidate.srt"
    output_json = tmp_path / "candidate.json"
    correction_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "episode_id": "episode-1",
                "normalized_audio_hash": "sha256:audio",
                "status": "completed_with_review",
                "applied": [],
                "unresolved": [],
                "tokens": [
                    {
                        "id": "t0",
                        "text": "甲",
                        "start_ms": 0,
                        "end_ms": 100,
                        "confidence": 1.0,
                        "speaker": None,
                        "source_primary_token_ids": ["s0"],
                        "recognition_refs": ["r0"],
                    },
                    {
                        "id": "t1",
                        "text": "OpenAI",
                        "start_ms": 100,
                        "end_ms": 600,
                        "confidence": 1.0,
                        "speaker": None,
                        "source_primary_token_ids": ["s1"],
                        "recognition_refs": ["r1"],
                    },
                    {
                        "id": "t2",
                        "text": "乙",
                        "start_ms": 600,
                        "end_ms": 700,
                        "confidence": 1.0,
                        "speaker": None,
                        "source_primary_token_ids": ["s2"],
                        "recognition_refs": ["r2"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    memo_path.write_text(
        "1\n00:00:00,000 --> 00:00:00,300\n甲Open\n\n2\n00:00:00,300 --> 00:00:00,700\nAI乙\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--correction",
                str(correction_path),
                "--memo-srt",
                str(memo_path),
                "--output-srt",
                str(output_srt),
                "--output-json",
                str(output_json),
                "--min-boundary-retention",
                "0",
            ]
        )
        == 0
    )

    blocks = output_srt.read_text(encoding="utf-8").strip().split("\n\n")
    assert all(len(block.splitlines()) == 3 for block in blocks)
    assert "".join(block.splitlines()[2] for block in blocks) == "甲OpenAI乙"
    sidecar = json.loads(output_json.read_text(encoding="utf-8"))
    assert sidecar["schema_version"] == "memo-corrected-projection.v1"
    assert sidecar["qc"]["exact_canonical_copy"] is True
    assert sidecar["qc"]["single_line_cues"] is True
    assert sidecar["inputs"]["correction"]["sha256"]
    assert sidecar["outputs"]["candidate_srt"]["sha256"]


def test_boundary_proposals_only_merge_adjacent_raw_memo_cues() -> None:
    cues = (
        _cue(1, 0, 100, "甲"),
        _cue(2, 100, 200, "乙"),
        _cue(3, 200, 300, "丙"),
    )
    merged = apply_boundary_merge_proposals(
        cues,
        (
            {
                "id": "merge-1",
                "action": "merge",
                "cue_ids": [1, 2],
                "current_lines": ["甲", "乙"],
                "result_line": "甲乙",
            },
        ),
    )

    assert [(cue.start_ms, cue.end_ms, cue.text) for cue in merged] == [
        (0, 200, "甲乙"),
        (200, 300, "丙"),
    ]
    assert merged[0].source_indexes == (1, 2)
    with pytest.raises(ValueError, match="not adjacent"):
        apply_boundary_merge_proposals(
            cues,
            ({"action": "merge", "cue_ids": [1, 3]},),
        )
    with pytest.raises(ValueError, match="changes Memo text"):
        apply_boundary_merge_proposals(
            cues,
            ({"action": "merge", "cue_ids": [1, 2], "result_line": "甲"},),
        )


def test_episode_edits_use_exact_target_and_fail_closed(tmp_path: Path) -> None:
    correction_path = tmp_path / "correction.json"
    memo_path = tmp_path / "memo.srt"
    edits_path = tmp_path / "edits.json"
    correction_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "episode_id": "episode-1",
                "normalized_audio_hash": "sha256:audio",
                "status": "completed",
                "applied": [],
                "unresolved": [],
                "tokens": [
                    {
                        "id": "t0",
                        "text": "甲",
                        "start_ms": 0,
                        "end_ms": 100,
                        "confidence": 1.0,
                        "speaker": None,
                        "source_primary_token_ids": ["s0"],
                        "recognition_refs": ["r0"],
                    },
                    {
                        "id": "t1",
                        "text": "乙",
                        "start_ms": 100,
                        "end_ms": 200,
                        "confidence": 1.0,
                        "speaker": None,
                        "source_primary_token_ids": ["s1"],
                        "recognition_refs": ["r1"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    memo_path.write_text(
        "1\n00:00:00,000 --> 00:00:00,100\n甲\n\n2\n00:00:00,100 --> 00:00:00,200\n乙\n",
        encoding="utf-8",
    )
    edits_path.write_text(
        json.dumps(
            [
                {
                    "id": "stale-edit",
                    "start_ms": 0,
                    "end_ms": 100,
                    "current": "不存在",
                    "replacement": "丙",
                    "evidence": "test evidence",
                    "confidence": "confirmed",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exact target count is 0"):
        main(
            [
                "--correction",
                str(correction_path),
                "--memo-srt",
                str(memo_path),
                "--episode-edits-json",
                str(edits_path),
                "--output-srt",
                str(tmp_path / "candidate.srt"),
                "--output-json",
                str(tmp_path / "candidate.json"),
                "--min-boundary-retention",
                "0",
            ]
        )
