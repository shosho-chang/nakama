from __future__ import annotations

from dataclasses import replace

import pytest

from agents.brook.podcast_subtitles.accurate_correction import (
    AccurateCorrectionResult,
    CorrectedTimedToken,
    CorrectionDecision,
    RecognitionLineage,
)
from agents.brook.podcast_subtitles.episode_edits import (
    EpisodeTranscriptEdit,
    apply_episode_transcript_edits,
)


def _correction(text: str) -> AccurateCorrectionResult:
    tokens = tuple(
        CorrectedTimedToken(
            id=f"t-{index}",
            text=character,
            start_ms=index * 100,
            end_ms=(index + 1) * 100,
            confidence=0.8,
            speaker=None,
            source_primary_token_ids=(f"p-{index}",),
            recognition_refs=(f"evidence:x:p-{index}",),
        )
        for index, character in enumerate(text)
    )
    return AccurateCorrectionResult(
        schema_version=1,
        episode_id="episode",
        normalized_audio_hash="a" * 64,
        status="completed",
        tokens=tokens,
        applied=(),
        unresolved=(),
    )


def test_exact_bounded_episode_edit_changes_only_target_span() -> None:
    source = _correction("甲錯字乙")
    edited = apply_episode_transcript_edits(
        source,
        (
            EpisodeTranscriptEdit(
                id="edit-1",
                start_ms=50,
                end_ms=450,
                current="錯字",
                replacement="正確文字",
                evidence="bounded audio relisten",
            ),
        ),
    )

    assert edited.text == "甲正確文字乙"
    assert edited.tokens[0].text == source.tokens[0].text
    assert edited.tokens[0].start_ms == source.tokens[0].start_ms
    assert edited.tokens[0].end_ms == source.tokens[0].end_ms
    assert edited.tokens[-1].text == "乙"
    assert edited.tokens[-1].start_ms == source.tokens[-1].start_ms
    assert [token.id for token in edited.tokens] == [
        f"corrected-{index:08d}" for index in range(len(edited.tokens))
    ]


def test_stale_or_ambiguous_episode_edit_fails_closed() -> None:
    source = _correction("甲錯字乙錯字丙")

    with pytest.raises(ValueError, match="exact target count"):
        apply_episode_transcript_edits(
            source,
            (
                EpisodeTranscriptEdit(
                    id="stale",
                    start_ms=0,
                    end_ms=1_000,
                    current="不存在",
                    replacement="正確",
                    evidence="bounded audio relisten",
                ),
            ),
        )

    with pytest.raises(ValueError, match="exact target count"):
        apply_episode_transcript_edits(
            source,
            (
                EpisodeTranscriptEdit(
                    id="ambiguous",
                    start_ms=0,
                    end_ms=1_100,
                    current="錯字",
                    replacement="正確",
                    evidence="bounded audio relisten",
                ),
            ),
        )


def test_episode_edit_attaches_interword_spaces_to_nonblank_tokens() -> None:
    source = _correction("甲錯字乙")

    edited = apply_episode_transcript_edits(
        source,
        (
            EpisodeTranscriptEdit(
                id="english-title",
                start_ms=50,
                end_ms=350,
                current="錯字",
                replacement="Pathless Path",
                evidence="bounded audio relisten",
            ),
        ),
    )

    assert edited.text == "甲Pathless Path乙"
    assert all(token.text.strip() for token in edited.tokens)
    assert any(token.text.endswith(" ") for token in edited.tokens)


def test_episode_edit_drops_overlapping_machine_review_and_shifts_later_target() -> None:
    source = _correction("甲錯字乙待決丙")
    lineage = (
        RecognitionLineage("b" * 64, "faster", "model", ("p",)),
        RecognitionLineage("c" * 64, "qwen", "model", ("q",)),
    )
    source = replace(
        source,
        status="completed_with_review",
        unresolved=(
            CorrectionDecision(
                id="overlap",
                status="unresolved",
                category="recognition_disagreement",
                start_ms=100,
                end_ms=300,
                current="錯字",
                candidates=("誤字",),
                selected=None,
                reason="machine disagreement",
                recognition_lineage=lineage,
                target_start_char=1,
                target_end_char=3,
            ),
            CorrectionDecision(
                id="later",
                status="unresolved",
                category="recognition_disagreement",
                start_ms=400,
                end_ms=600,
                current="待決",
                candidates=("候選",),
                selected=None,
                reason="machine disagreement",
                recognition_lineage=lineage,
                target_start_char=4,
                target_end_char=6,
            ),
        ),
    )

    edited = apply_episode_transcript_edits(
        source,
        (
            EpisodeTranscriptEdit(
                id="edit",
                start_ms=50,
                end_ms=350,
                current="錯字",
                replacement="正確文字",
                evidence="bounded audio relisten",
            ),
        ),
    )

    assert [decision.id for decision in edited.unresolved] == ["later"]
    assert edited.unresolved[0].target_start_char == 6
    assert edited.text[6:8] == "待決"
