from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object
from agents.brook.podcast_subtitles.transcript_gold import (
    AdjudicationProvenance,
    AudioClipBinding,
    AudioOnlyTranscriptSubmission,
    CandidateClipOutput,
    CandidateCorrectionDecision,
    CandidateTimedCoverageV2,
    CandidateTimedTextSpanV2,
    CorrectionGoldLabel,
    GoldClipLabel,
    GoldMetricCoverageV1,
    GoldOmissionLabel,
    GoldSpanLabel,
    GoldToken,
    SpellingAuthoritySource,
    SpellingAuthorityUse,
    TranscriptAdjudicationRecord,
    TranscriptAnnotationPacket,
    TranscriptAnnotationProtocol,
    TranscriptCandidateArtifact,
    TranscriptCandidateArtifactV1,
    TranscriptEvaluationStatus,
    TranscriptGoldSuite,
    evaluate_transcript_candidate,
    load_annotation_packet,
    load_transcript_candidate,
    load_transcript_evaluation,
    load_transcript_gold_suite,
    measure_lexical_evaluator_identity,
    verify_annotation_packet_blinding,
)


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clip(
    clip_id: str,
    start_ms: int,
    end_ms: int,
    *,
    normalized_hash: str | None = None,
    normalized_size: int = 10_000,
) -> AudioClipBinding:
    return AudioClipBinding.build(
        clip_id=clip_id,
        start_ms=start_ms,
        end_ms=end_ms,
        clip_audio_hash=_h(f"audio:{clip_id}:{start_ms}:{end_ms}"),
        clip_audio_size_bytes=end_ms - start_ms,
        normalized_audio_hash=normalized_hash or _h("normalized"),
        normalized_audio_size_bytes=normalized_size,
    )


def _packet(
    *, clips: tuple[AudioClipBinding, ...] | None = None
) -> TranscriptAnnotationPacket:
    selected = clips or (_clip("clip-1", 1_000, 2_000), _clip("clip-2", 3_000, 4_000))
    return TranscriptAnnotationPacket.build(
        packet_id="angie-transcript-challenge-v1",
        episode_id="angie",
        normalized_audio_hash=selected[0].normalized_audio_hash,
        normalized_audio_size_bytes=selected[0].normalized_audio_size_bytes,
        sampling_declaration_hash=None,
        instruction_profile_id="audio-only-transcript-v1",
        protocol=TranscriptAnnotationProtocol(protocol_id="two-pass-third-adjudication-v1"),
        clips=selected,
    )


def _provenance(
    *,
    packet: TranscriptAnnotationPacket,
    clip: AudioClipBinding,
    final_outcome: str,
    final_text: str | None,
    final_tokens: tuple[GoldToken, ...],
    final_span_labels: tuple[GoldSpanLabel, ...] = (),
    final_omission_labels: tuple[GoldOmissionLabel, ...] = (),
    final_correction_labels: tuple[CorrectionGoldLabel, ...] = (),
    spelling_authority_uses: tuple[SpellingAuthorityUse, ...] = (),
    completed: bool = True,
    first_submission_id: str = "submission-a",
    adjudicator_id: str = "adjudicator-c",
    metric_coverage: GoldMetricCoverageV1 | None = None,
) -> AdjudicationProvenance:
    audio_tokens = tuple(token.text for token in final_tokens)
    submissions = tuple(
        AudioOnlyTranscriptSubmission.build(
            submission_id=first_submission_id if index == 0 else "submission-b",
            annotation_packet_hash=packet.packet_hash,
            clip=clip,
            annotator_id=annotator,
            outcome=final_outcome,  # type: ignore[arg-type]
            text=final_text,
            tokens=audio_tokens,
        )
        for index, annotator in enumerate(("annotator-a", "annotator-b"))
    )
    record = None
    if completed:
        record = TranscriptAdjudicationRecord.build(
            record_id="adjudication-c",
            annotation_packet_hash=packet.packet_hash,
            clip=clip,
            first_pass_submission_hashes=tuple(  # type: ignore[arg-type]
                item.submission_hash for item in submissions
            ),
            adjudicator_id=adjudicator_id,
            spelling_authority_uses=spelling_authority_uses,
            metric_coverage=metric_coverage or (
                GoldMetricCoverageV1(
                    entity_labels_exhaustive=True,
                    code_switch_labels_exhaustive=True,
                    numeric_labels_exhaustive=True,
                    critical_omission_labels_exhaustive=True,
                )
                if final_outcome == "accepted"
                else GoldMetricCoverageV1()
            ),
            final_expected_outcome=final_outcome,  # type: ignore[arg-type]
            final_text=final_text,
            final_tokens=final_tokens,
            final_span_labels=final_span_labels,
            final_omission_labels=final_omission_labels,
            final_correction_labels=final_correction_labels,
        )
    return AdjudicationProvenance(
        first_pass_submissions=submissions,  # type: ignore[arg-type]
        adjudication_record=record,
    )


def _accepted_gold(
    packet: TranscriptAnnotationPacket | None = None,
    *,
    provenance: AdjudicationProvenance | None = None,
    completed: bool = True,
    first_submission_id: str = "submission-a",
    adjudicator_id: str = "adjudicator-c",
    metric_coverage: GoldMetricCoverageV1 | None = None,
) -> GoldClipLabel:
    packet = packet or _packet()
    tokens = (
        GoldToken(token_id="t1", text="安吉"),
        GoldToken(token_id="t2", text="在"),
        GoldToken(token_id="t3", text=" Traveling Village"),
        GoldToken(token_id="t4", text=" 做了"),
        GoldToken(token_id="t5", text=" 3"),
        GoldToken(token_id="t6", text=" 次研究"),
    )
    span_labels = (
        GoldSpanLabel(
            label_id="entity-angie",
            kind="entity",
            token_ids=("t1",),
            expected_text="安吉",
        ),
        GoldSpanLabel(
            label_id="code-traveling-village",
            kind="code_switch",
            token_ids=("t3",),
            expected_text=" Traveling Village",
        ),
        GoldSpanLabel(
            label_id="numeric-three",
            kind="numeric",
            token_ids=("t5",),
            expected_text=" 3",
        ),
    )
    omission_labels = (
        GoldOmissionLabel(
            label_id="required-three-studies",
            token_ids=("t5", "t6"),
            expected_text=" 3 次研究",
            severity="critical",
        ),
    )
    correction_labels = (
        CorrectionGoldLabel(
            label_id="gold-name-angie",
            target_start_ms=1_000,
            target_end_ms=1_400,
            token_ids=("t1",),
            expected_outcome="accepted",
            expected_text="安吉",
            authorized_spelling_source_ids=("author-book",),
        ),
        CorrectionGoldLabel(
            label_id="gold-type-wording",
            target_start_ms=1_500,
            target_end_ms=1_900,
            token_ids=("t2",),
            expected_outcome="accepted",
            expected_text="在",
        ),
    )
    text = "".join(token.text for token in tokens)
    actual_provenance = provenance or _provenance(
        packet=packet,
        clip=packet.clips[0],
        final_outcome="accepted",
        final_text=text,
        final_tokens=tokens,
        final_span_labels=span_labels,
        final_omission_labels=omission_labels,
        final_correction_labels=correction_labels,
        spelling_authority_uses=(
            SpellingAuthorityUse(source_id="author-book", locator="chapter-1"),
        ),
        completed=completed,
        first_submission_id=first_submission_id,
        adjudicator_id=adjudicator_id,
        metric_coverage=metric_coverage,
    )
    return GoldClipLabel(
        clip_id="clip-1",
        expected_outcome="accepted",
        text=text,
        tokens=tokens,
        span_labels=span_labels,
        omission_labels=omission_labels,
        correction_labels=correction_labels,
        provenance=actual_provenance,
    )


def _review_gold(
    packet: TranscriptAnnotationPacket | None = None,
    *,
    provenance: AdjudicationProvenance | None = None,
    completed: bool = True,
) -> GoldClipLabel:
    packet = packet or _packet()
    corrections = (
        CorrectionGoldLabel(
            label_id="gold-unclear-name",
            target_start_ms=3_000,
            target_end_ms=4_000,
            token_ids=(),
            expected_outcome="needs_review",
            expected_text=None,
        ),
    )
    return GoldClipLabel(
        clip_id="clip-2",
        expected_outcome="needs_review",
        text=None,
        tokens=(),
        correction_labels=corrections,
        provenance=provenance
        or _provenance(
            packet=packet,
            clip=packet.clips[-1],
            final_outcome="needs_review",
            final_text=None,
            final_tokens=(),
            final_correction_labels=corrections,
            completed=completed,
        ),
    )


def _gold(
    packet: TranscriptAnnotationPacket,
    *,
    complete: bool = True,
    labels: tuple[GoldClipLabel, ...] | None = None,
) -> TranscriptGoldSuite:
    selected = (_accepted_gold(packet), _review_gold(packet)) if labels is None else labels
    return TranscriptGoldSuite.build(
        suite_id="angie-gold-v1",
        annotation_packet=packet,
        complete=complete,
        spelling_sources=(
            SpellingAuthoritySource(source_id="author-book", artifact_hash=_h("book")),
        ),
        labels=selected,
        correction_targets_revealed_at_utc="2026-08-13T01:00:00Z",
    )


def _candidate_clip_one(
    packet: TranscriptAnnotationPacket,
    *,
    text: str | None = None,
    tokens: tuple[str, ...] | None = None,
    corrections: tuple[CandidateCorrectionDecision, ...] | None = None,
) -> CandidateClipOutput:
    gold = _accepted_gold(packet)
    selected_text = gold.text if text is None else text
    selected_tokens = tuple(token.text for token in gold.tokens) if tokens is None else tokens
    selected_corrections = (
        corrections
        if corrections is not None
        else (
            CandidateCorrectionDecision(
                decision_id="candidate-decision-472",
                target_start_ms=1_000,
                target_end_ms=1_400,
                action="apply",
                recognition_text="安琪",
                final_text="安吉",
                source_artifact_ids=("author-book",),
            ),
            CandidateCorrectionDecision(
                decision_id="candidate-decision-901",
                target_start_ms=1_500,
                target_end_ms=1_900,
                action="keep_original",
                recognition_text="在",
                final_text="在",
            ),
        )
    )
    return CandidateClipOutput.build(
        clip=packet.clips[0],
        outcome="accepted",
        text=selected_text,
        tokens=selected_tokens,
        corrections=selected_corrections,
    )


def _timed_coverage(
    clip: AudioClipBinding,
    spans: tuple[tuple[str, int, int, str, str], ...],
) -> CandidateTimedCoverageV2:
    return CandidateTimedCoverageV2.build(
        clip=clip,
        spans=tuple(
            CandidateTimedTextSpanV2(
                span_id=span_id,
                start_ms=start_ms,
                end_ms=end_ms,
                recognition_text=recognition_text,
                final_text=final_text,
            )
            for span_id, start_ms, end_ms, recognition_text, final_text in spans
        ),
    )


def _default_timed_coverages(
    packet: TranscriptAnnotationPacket,
) -> tuple[CandidateTimedCoverageV2, ...]:
    return (
        _timed_coverage(
            packet.clips[0],
            (
                ("name", 1_000, 1_400, "安琪", "安吉"),
                ("pause-a", 1_400, 1_500, "", ""),
                ("wording", 1_500, 1_900, "在", "在"),
                (
                    "remainder",
                    1_900,
                    2_000,
                    " Traveling Village 做了 3 次研究",
                    " Traveling Village 做了 3 次研究",
                ),
            ),
        ),
        _timed_coverage(
            packet.clips[1],
            (("review", 3_000, 4_000, "安琪", "安琪"),),
        ),
    )


def _candidate(
    packet: TranscriptAnnotationPacket,
    *,
    complete: bool = True,
    clips: tuple[CandidateClipOutput, ...] | None = None,
    timed_coverages: tuple[CandidateTimedCoverageV2, ...] | None = None,
) -> TranscriptCandidateArtifact:
    selected = clips or (
        _candidate_clip_one(packet),
        CandidateClipOutput.build(
            clip=packet.clips[1],
            outcome="needs_review",
            text="安琪",
            tokens=("安琪",),
            corrections=(
                CandidateCorrectionDecision(
                    decision_id="candidate-decision-review",
                    target_start_ms=3_000,
                    target_end_ms=4_000,
                    action="needs_review",
                    recognition_text="安琪",
                    final_text="安琪",
                ),
            ),
        ),
    )
    return TranscriptCandidateArtifact.build(
        candidate_id="candidate-v2",
        system_id="podcast-subtitle-v2",
        generation_id="generation-1",
        annotation_packet=packet,
        generated_at_utc="2026-08-13T00:00:00Z",
        complete=complete,
        clips=selected,
        timed_coverages=(
            _default_timed_coverages(packet)
            if timed_coverages is None and clips is None
            else timed_coverages or ()
        ),
    )


def _single_clip_evaluation(*, gold_text: str, candidate_text: str):
    clip = _clip("clip-1", 0, 1_000)
    packet = _packet(clips=(clip,))
    gold_tokens = tuple(
        GoldToken(token_id=f"t{index}", text=character)
        for index, character in enumerate(gold_text)
    )
    gold = GoldClipLabel(
        clip_id=clip.clip_id,
        expected_outcome="accepted",
        text=gold_text,
        tokens=gold_tokens,
        provenance=_provenance(
            packet=packet,
            clip=clip,
            final_outcome="accepted",
            final_text=gold_text,
            final_tokens=gold_tokens,
        ),
    )
    suite = TranscriptGoldSuite.build(
        suite_id="single-clip-evaluation",
        annotation_packet=packet,
        complete=True,
        labels=(gold,),
        correction_targets_revealed_at_utc="2026-08-13T01:00:00Z",
    )
    candidate = TranscriptCandidateArtifact.build(
        candidate_id="single-clip-evaluation",
        system_id="recognizer",
        generation_id="g",
        annotation_packet=packet,
        generated_at_utc="2026-08-13T00:00:00Z",
        complete=True,
        clips=(
            CandidateClipOutput.build(
                clip=clip,
                outcome="accepted",
                text=candidate_text,
                tokens=(candidate_text,),
            ),
        ),
    )
    return evaluate_transcript_candidate(suite, candidate)


def test_exact_traditional_chinese_and_code_switch_candidate_scores_perfectly() -> None:
    packet = _packet()
    result = evaluate_transcript_candidate(_gold(packet), _candidate(packet))

    assert result.status is TranscriptEvaluationStatus.EVALUATED
    assert result.reason_codes == ()
    assert result.metrics is not None
    assert result.metrics.lexical_character_error_rate.value == 0.0
    assert result.metrics.lexical_character_accuracy.value == 1.0
    assert result.metrics.word_token_accuracy.status == "not_evaluated"
    assert result.metrics.entity_recall.value == 1.0
    assert result.metrics.code_switch_recall.value == 1.0
    assert result.metrics.numeric_recall.value == 1.0
    assert result.metrics.critical_omission_rate.value == 0.0
    assert result.metrics.needs_review_precision.value == 1.0
    assert result.metrics.needs_review_recall.value == 1.0
    assert result.metrics.correction_detection_recall.value == 1.0
    assert result.metrics.correction_apply_recall.value == 1.0
    assert result.metrics.false_keep_original_rate.value == 0.0
    assert result.metrics.harmful_apply_rate.value == 0.0
    assert result.metrics.source_precision.value == 1.0


def test_empty_and_incomplete_human_gold_is_typed_not_evaluated() -> None:
    packet = _packet()
    suite = _gold(packet, complete=False, labels=())

    result = evaluate_transcript_candidate(suite, _candidate(packet))

    assert result.status is TranscriptEvaluationStatus.NOT_EVALUATED
    assert result.reason_codes == ("gold_labels_missing", "gold_suite_incomplete")
    assert result.metrics is None
    assert result.clip_results == ()


def test_incomplete_candidate_is_typed_not_evaluated() -> None:
    packet = _packet()
    candidate = _candidate(
        packet,
        complete=False,
        clips=(_candidate_clip_one(packet),),
    )

    result = evaluate_transcript_candidate(_gold(packet), candidate)

    assert result.status is TranscriptEvaluationStatus.NOT_EVALUATED
    assert result.reason_codes == (
        "candidate_artifact_incomplete",
        "candidate_outputs_missing",
    )


def test_normalized_audio_lineage_mismatch_fails_closed() -> None:
    packet = _packet()
    other_clips = (
        _clip("clip-1", 1_000, 2_000, normalized_hash=_h("other")),
        _clip("clip-2", 3_000, 4_000, normalized_hash=_h("other")),
    )
    other_packet = _packet(clips=other_clips)
    candidate = _candidate(other_packet)

    with pytest.raises(ValueError, match="normalized-audio lineage"):
        evaluate_transcript_candidate(_gold(packet), candidate)


def test_candidate_interval_or_audio_binding_drift_fails_closed() -> None:
    packet = _packet()
    drifted = _clip("clip-1", 1_001, 2_000)
    output = CandidateClipOutput.build(
        clip=drifted,
        outcome="accepted",
        text="安吉",
        tokens=("安吉",),
    )

    with pytest.raises(ValueError, match="clip binding drift"):
        TranscriptCandidateArtifact.build(
            candidate_id="drift",
            system_id="v2",
            generation_id="g",
            annotation_packet=packet,
            complete=False,
            clips=(output,),
        )


def test_candidate_text_hash_or_token_text_drift_fails_closed() -> None:
    packet = _packet()
    with pytest.raises(ValidationError, match="token concatenation"):
        CandidateClipOutput(
            clip=packet.clips[0],
            outcome="accepted",
            text="安吉",
            tokens=("安琪",),
            transcript_text_hash=hash_object({"text": "安吉"}),
        )
    with pytest.raises(ValidationError, match="transcript_text_hash mismatch"):
        CandidateClipOutput(
            clip=packet.clips[0],
            outcome="accepted",
            text="安吉",
            tokens=("安吉",),
            transcript_text_hash=_h("different"),
        )


@pytest.mark.parametrize(
    ("spans", "message"),
    (
        (
            (
                ("first", 1_000, 1_400, "甲", "甲"),
                ("second", 1_500, 2_000, "乙", "乙"),
            ),
            "must not contain gaps",
        ),
        (
            (
                ("first", 1_000, 1_600, "甲", "甲"),
                ("second", 1_500, 2_000, "乙", "乙"),
            ),
            "must not overlap",
        ),
        (
            (("outside", 1_000, 2_001, "甲", "甲"),),
            "escapes its audio clip",
        ),
    ),
)
def test_timed_coverage_gap_overlap_and_out_of_bounds_fail_closed(
    spans: tuple[tuple[str, int, int, str, str], ...],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _timed_coverage(_packet().clips[0], spans)


def test_timed_coverage_aggregate_and_candidate_text_tampering_fail_closed() -> None:
    packet = _packet()
    coverage = _timed_coverage(
        packet.clips[0],
        (("all", 1_000, 2_000, "安吉", "安吉"),),
    )
    payload = coverage.model_dump(mode="python")
    payload["final_text"] = "竄改"
    with pytest.raises(ValidationError, match="final_text drifts from its spans"):
        CandidateTimedCoverageV2.model_validate(payload, strict=True)

    output = CandidateClipOutput.build(
        clip=packet.clips[0],
        outcome="accepted",
        text="不同",
        tokens=("不同",),
    )
    with pytest.raises(ValidationError, match="differs from candidate output text"):
        TranscriptCandidateArtifact.build(
            candidate_id="coverage-text-drift",
            system_id="v2",
            generation_id="g",
            annotation_packet=packet,
            complete=False,
            clips=(output,),
            timed_coverages=(coverage,),
        )


@pytest.mark.parametrize(
    ("recognition_text", "final_text"),
    (("錯字", ""), ("", "補字")),
)
def test_timed_trace_supports_deletion_and_insertion_states(
    recognition_text: str,
    final_text: str,
) -> None:
    clip = _clip("clip-1", 0, 1_000)
    packet = _packet(clips=(clip,))
    decision = CandidateCorrectionDecision(
        decision_id="edit",
        target_start_ms=0,
        target_end_ms=1_000,
        action="apply",
        recognition_text=recognition_text,
        final_text=final_text,
    )
    output = CandidateClipOutput.build(
        clip=clip,
        outcome="accepted",
        text=final_text,
        tokens=() if not final_text else (final_text,),
        corrections=(decision,),
    )
    coverage = _timed_coverage(
        clip,
        (("edit", 0, 1_000, recognition_text, final_text),),
    )

    candidate = TranscriptCandidateArtifact.build(
        candidate_id="empty-side-edit",
        system_id="v2",
        generation_id="g",
        annotation_packet=packet,
        complete=True,
        clips=(output,),
        timed_coverages=(coverage,),
    )

    assert candidate.timed_coverages[0].recognition_text == recognition_text
    assert candidate.timed_coverages[0].final_text == final_text


def test_canonical_loader_rejects_tampered_timed_coverage_bytes() -> None:
    candidate = _candidate(_packet())
    payload = candidate.model_dump(mode="json")
    payload["timed_coverages"][0]["spans"][0]["recognition_text"] = "竄改"

    with pytest.raises(ValueError, match="invalid TranscriptCandidateArtifact JSON"):
        load_transcript_candidate(canonical_json_bytes(payload))


def test_duplicate_reordered_and_overlapping_clips_fail_closed() -> None:
    first = _clip("first", 1_000, 2_000)
    second = _clip("second", 3_000, 4_000)
    with pytest.raises(ValidationError, match="chronological canonical order"):
        _packet(clips=(second, first))
    with pytest.raises(ValidationError, match="duplicate clip_id"):
        _packet(clips=(first, _clip("first", 3_000, 4_000)))
    with pytest.raises(ValidationError, match="must not overlap"):
        _packet(clips=(first, _clip("second", 1_999, 3_000)))


def test_annotation_packet_candidate_hash_leakage_fails_closed() -> None:
    leaked = _h("candidate-v1")
    clip = _clip("clip", 0, 1_000)
    with pytest.raises(ValueError, match="forbidden candidate artifact hash"):
        TranscriptAnnotationPacket.build(
            packet_id="packet",
            episode_id="episode",
            normalized_audio_hash=clip.normalized_audio_hash,
            normalized_audio_size_bytes=clip.normalized_audio_size_bytes,
            instruction_profile_id=f"neutral-{leaked}",
            protocol=TranscriptAnnotationProtocol(protocol_id="protocol"),
            clips=(clip,),
            forbidden_candidate_hashes=(leaked,),
        )

    packet = TranscriptAnnotationPacket.build(
        packet_id=leaked,
        episode_id="episode",
        normalized_audio_hash=clip.normalized_audio_hash,
        normalized_audio_size_bytes=clip.normalized_audio_size_bytes,
        instruction_profile_id="neutral",
        protocol=TranscriptAnnotationProtocol(protocol_id="protocol"),
        clips=(clip,),
    )
    with pytest.raises(ValueError, match="forbidden candidate artifact hash"):
        verify_annotation_packet_blinding(packet, (leaked,))


def test_gold_candidate_hash_leakage_fails_during_evaluation() -> None:
    packet = _packet()
    leaked_v1_hash = _h("candidate-v1")
    labels = (
        _accepted_gold(packet, first_submission_id=leaked_v1_hash),
        _review_gold(packet),
    )
    suite = _gold(packet, labels=labels)

    with pytest.raises(ValueError, match="gold suite contains a forbidden"):
        evaluate_transcript_candidate(
            suite,
            _candidate(packet),
            all_candidate_artifact_hashes=(leaked_v1_hash,),
        )


def test_nfc_equivalence_is_not_a_character_error() -> None:
    clip = _clip("clip-1", 0, 1_000)
    packet = _packet(clips=(clip,))
    tokens = (GoldToken(token_id="t", text="臺灣 café"),)
    gold = GoldClipLabel(
        clip_id="clip-1",
        expected_outcome="accepted",
        text="臺灣 café",
        tokens=tokens,
        provenance=_provenance(
            packet=packet,
            clip=clip,
            final_outcome="accepted",
            final_text="臺灣 café",
            final_tokens=tokens,
        ),
    )
    suite = TranscriptGoldSuite.build(
        suite_id="unicode",
        annotation_packet=packet,
        complete=True,
        labels=(gold,),
        correction_targets_revealed_at_utc="2026-08-13T01:00:00Z",
    )
    decomposed = "臺灣 cafe\u0301"
    candidate = TranscriptCandidateArtifact.build(
        candidate_id="unicode",
        system_id="v2",
        generation_id="g",
        annotation_packet=packet,
        generated_at_utc="2026-08-13T00:00:00Z",
        complete=True,
        clips=(
            CandidateClipOutput.build(
                clip=clip,
                outcome="accepted",
                text=decomposed,
                tokens=(decomposed,),
            ),
        ),
    )

    result = evaluate_transcript_candidate(suite, candidate)

    assert result.metrics is not None
    assert result.metrics.lexical_character_error_rate.value == 0.0
    assert result.metrics.lexical_character_accuracy.value == 1.0
    assert result.metrics.word_token_accuracy.status == "not_evaluated"
    assert result.metrics.entity_recall.value is None
    assert result.metrics.code_switch_recall.value is None
    assert result.metrics.numeric_recall.value is None
    assert result.metrics.critical_omission_rate.value is None


def test_lexical_accuracy_ignores_presentation_style_and_provider_token_boundaries() -> None:
    clip = _clip("clip-1", 0, 1_000)
    packet = _packet(clips=(clip,))
    gold_text = "臺灣ＡＢＣ， Café！"
    gold_tokens = tuple(
        GoldToken(token_id=f"t{index}", text=character)
        for index, character in enumerate(gold_text)
    )
    gold = GoldClipLabel(
        clip_id=clip.clip_id,
        expected_outcome="accepted",
        text=gold_text,
        tokens=gold_tokens,
        provenance=_provenance(
            packet=packet,
            clip=clip,
            final_outcome="accepted",
            final_text=gold_text,
            final_tokens=gold_tokens,
        ),
    )
    suite = TranscriptGoldSuite.build(
        suite_id="lexical-style",
        annotation_packet=packet,
        complete=True,
        labels=(gold,),
        correction_targets_revealed_at_utc="2026-08-13T01:00:00Z",
    )
    candidate_text = "台湾abc café"
    candidate = TranscriptCandidateArtifact.build(
        candidate_id="lexical-style",
        system_id="recognizer",
        generation_id="g",
        annotation_packet=packet,
        generated_at_utc="2026-08-13T00:00:00Z",
        complete=True,
        clips=(
            CandidateClipOutput.build(
                clip=clip,
                outcome="accepted",
                text=candidate_text,
                tokens=("台湾", "abc", " ", "café"),
            ),
        ),
    )

    result = evaluate_transcript_candidate(suite, candidate)

    assert result.metrics is not None
    assert result.metrics.lexical_normalization_profile == (
        "unicode-nfkc-casefold-opencc-s2tw-ignore-punctuation-separators-controls-v1"
    )
    assert result.metrics.lexical_character_error_rate.value == 0.0
    assert result.metrics.lexical_character_accuracy.value == 1.0
    assert result.metrics.word_token_accuracy.status == "not_evaluated"
    assert result.metrics.word_token_accuracy.reason_codes == (
        "provider_token_boundaries_not_comparable",
    )


def test_lexical_accuracy_still_counts_real_substitution_deletion_and_insertion() -> None:
    clip = _clip("clip-1", 0, 1_000)
    packet = _packet(clips=(clip,))
    gold_text = "甲乙丙"
    gold_tokens = tuple(
        GoldToken(token_id=f"t{index}", text=character)
        for index, character in enumerate(gold_text)
    )
    gold = GoldClipLabel(
        clip_id=clip.clip_id,
        expected_outcome="accepted",
        text=gold_text,
        tokens=gold_tokens,
        provenance=_provenance(
            packet=packet,
            clip=clip,
            final_outcome="accepted",
            final_text=gold_text,
            final_tokens=gold_tokens,
        ),
    )
    suite = TranscriptGoldSuite.build(
        suite_id="lexical-errors",
        annotation_packet=packet,
        complete=True,
        labels=(gold,),
        correction_targets_revealed_at_utc="2026-08-13T01:00:00Z",
    )
    candidate = TranscriptCandidateArtifact.build(
        candidate_id="lexical-errors",
        system_id="recognizer",
        generation_id="g",
        annotation_packet=packet,
        generated_at_utc="2026-08-13T00:00:00Z",
        complete=True,
        clips=(
            CandidateClipOutput.build(
                clip=clip,
                outcome="accepted",
                text="甲丁戊丙",
                tokens=("甲丁", "戊丙"),
            ),
        ),
    )

    result = evaluate_transcript_candidate(suite, candidate)

    assert result.metrics is not None
    assert result.metrics.lexical_character_substitutions == 1
    assert result.metrics.lexical_character_deletions == 0
    assert result.metrics.lexical_character_insertions == 1
    assert result.metrics.lexical_character_error_rate == (
        result.metrics.lexical_character_error_rate.of(2, 3)
    )


def test_unattested_gold_label_categories_are_typed_not_evaluated() -> None:
    packet = _packet()
    gold = _accepted_gold(packet, metric_coverage=GoldMetricCoverageV1())
    suite = _gold(packet, labels=(gold, _review_gold(packet)))

    result = evaluate_transcript_candidate(suite, _candidate(packet))

    assert result.metrics is not None
    assert result.metrics.entity_recall.reason_codes == ("entity_labels_not_exhaustive",)
    assert result.metrics.code_switch_recall.reason_codes == (
        "code_switch_labels_not_exhaustive",
    )
    assert result.metrics.numeric_recall.reason_codes == ("numeric_labels_not_exhaustive",)
    assert result.metrics.critical_omission_rate.reason_codes == (
        "critical_omission_labels_not_exhaustive",
    )


def test_lexical_evaluator_identity_binds_installed_opencc_and_evaluator_code() -> None:
    identity = measure_lexical_evaluator_identity()

    assert identity.normalization_profile.endswith("-v1")
    assert identity.implementation == "opencc-python-reimplemented"
    assert identity.conversion_config == "s2tw"
    assert tuple(item.path for item in identity.inventory) == (
        "config/s2tw.json",
        "dictionary/STPhrases.txt",
        "dictionary/STCharacters.txt",
        "dictionary/TWVariants.txt",
        "__init__.py",
        "opencc.py",
    )
    assert identity.python_implementation
    assert identity.python_version
    assert identity.python_cache_tag
    assert identity.unicode_database_version


def test_lexical_evaluator_identity_changes_when_measured_bytes_drift() -> None:
    original = measure_lexical_evaluator_identity()

    with patch(
        "agents.brook.podcast_subtitles.transcript_gold.measure_regular_file",
        side_effect=lambda path: (_h(f"drift:{path}"), 1),
    ):
        drifted = measure_lexical_evaluator_identity()

    assert drifted.inventory_hash != original.inventory_hash
    assert drifted.evaluator_code_hash != original.evaluator_code_hash
    assert drifted.identity_hash != original.identity_hash


def test_evaluation_fails_closed_if_evaluator_changes_during_scoring() -> None:
    packet = _packet()
    exact = measure_lexical_evaluator_identity()
    drifted_payload = exact.model_dump(exclude={"identity_hash"})
    drifted_payload["python_version"] = "drifted-runtime"
    changed = type(exact)(
        **drifted_payload,
        identity_hash=hash_object(
            {"artifact_kind": "lexical_evaluator_identity", **drifted_payload}
        ),
    )

    with patch(
        "agents.brook.podcast_subtitles.transcript_gold.measure_lexical_evaluator_identity",
        side_effect=(exact, changed),
    ):
        with pytest.raises(ValueError, match="changed while recognition metrics were scored"):
            evaluate_transcript_candidate(_gold(packet), _candidate(packet))


def test_common_script_normalization_does_not_collapse_distinct_traditional_words() -> None:
    correct = _single_clip_evaluation(gold_text="發展", candidate_text="发展")
    wrong = _single_clip_evaluation(gold_text="發展", candidate_text="髮展")

    assert correct.metrics is not None
    assert correct.metrics.lexical_character_error_rate.value == 0.0
    assert wrong.metrics is not None
    assert wrong.metrics.lexical_character_substitutions == 1


def test_numeric_and_code_switch_recall_preserve_semantic_punctuation() -> None:
    clip = _clip("clip-1", 0, 1_000)
    packet = _packet(clips=(clip,))
    gold_text = "比例3.5 can't"
    gold_tokens = tuple(
        GoldToken(token_id=f"t{index}", text=character)
        for index, character in enumerate(gold_text)
    )
    numeric_ids = tuple(token.token_id for token in gold_tokens[2:5])
    code_switch_ids = tuple(token.token_id for token in gold_tokens[6:11])
    span_labels = (
        GoldSpanLabel(
            label_id="numeric-decimal",
            kind="numeric",
            token_ids=numeric_ids,
            expected_text="3.5",
        ),
        GoldSpanLabel(
            label_id="code-contraction",
            kind="code_switch",
            token_ids=code_switch_ids,
            expected_text="can't",
        ),
    )
    omission_labels = (
        GoldOmissionLabel(
            label_id="critical-decimal",
            token_ids=numeric_ids,
            expected_text="3.5",
            severity="critical",
        ),
    )
    gold = GoldClipLabel(
        clip_id=clip.clip_id,
        expected_outcome="accepted",
        text=gold_text,
        tokens=gold_tokens,
        span_labels=span_labels,
        omission_labels=omission_labels,
        provenance=_provenance(
            packet=packet,
            clip=clip,
            final_outcome="accepted",
            final_text=gold_text,
            final_tokens=gold_tokens,
            final_span_labels=span_labels,
            final_omission_labels=omission_labels,
        ),
    )
    suite = TranscriptGoldSuite.build(
        suite_id="semantic-punctuation",
        annotation_packet=packet,
        complete=True,
        labels=(gold,),
        correction_targets_revealed_at_utc="2026-08-13T01:00:00Z",
    )
    candidate_text = "比例35 cant"
    candidate = TranscriptCandidateArtifact.build(
        candidate_id="semantic-punctuation",
        system_id="recognizer",
        generation_id="g",
        annotation_packet=packet,
        generated_at_utc="2026-08-13T00:00:00Z",
        complete=True,
        clips=(
            CandidateClipOutput.build(
                clip=clip,
                outcome="accepted",
                text=candidate_text,
                tokens=(candidate_text,),
            ),
        ),
    )

    result = evaluate_transcript_candidate(suite, candidate)

    assert result.metrics is not None
    assert result.metrics.numeric_recall == result.metrics.numeric_recall.of(0, 1)
    assert result.metrics.code_switch_recall == result.metrics.code_switch_recall.of(0, 1)
    assert result.metrics.critical_omission_rate == result.metrics.critical_omission_rate.of(
        1, 1
    )


@pytest.mark.parametrize(
    ("kind", "gold_literal", "candidate_text"),
    (
        ("entity", "Paul", "Pauline"),
        ("code_switch", "AI", "said"),
        ("numeric", "3", "13"),
        ("entity", "安吉", "平安，吉祥"),
    ),
)
def test_label_recall_does_not_credit_substrings_or_cross_punctuation_joining(
    kind: str,
    gold_literal: str,
    candidate_text: str,
) -> None:
    clip = _clip("clip-1", 0, 1_000)
    packet = _packet(clips=(clip,))
    gold_tokens = tuple(
        GoldToken(token_id=f"t{index}", text=character)
        for index, character in enumerate(gold_literal)
    )
    label = GoldSpanLabel(
        label_id="target",
        kind=kind,  # type: ignore[arg-type]
        token_ids=tuple(token.token_id for token in gold_tokens),
        expected_text=gold_literal,
    )
    gold = GoldClipLabel(
        clip_id=clip.clip_id,
        expected_outcome="accepted",
        text=gold_literal,
        tokens=gold_tokens,
        span_labels=(label,),
        provenance=_provenance(
            packet=packet,
            clip=clip,
            final_outcome="accepted",
            final_text=gold_literal,
            final_tokens=gold_tokens,
            final_span_labels=(label,),
        ),
    )
    suite = TranscriptGoldSuite.build(
        suite_id="substring-label",
        annotation_packet=packet,
        complete=True,
        labels=(gold,),
        correction_targets_revealed_at_utc="2026-08-13T01:00:00Z",
    )
    candidate = TranscriptCandidateArtifact.build(
        candidate_id="substring-label",
        system_id="recognizer",
        generation_id="g",
        annotation_packet=packet,
        generated_at_utc="2026-08-13T00:00:00Z",
        complete=True,
        clips=(
            CandidateClipOutput.build(
                clip=clip,
                outcome="accepted",
                text=candidate_text,
                tokens=(candidate_text,),
            ),
        ),
    )

    result = evaluate_transcript_candidate(suite, candidate)

    assert result.metrics is not None
    metric = {
        "entity": result.metrics.entity_recall,
        "code_switch": result.metrics.code_switch_recall,
        "numeric": result.metrics.numeric_recall,
    }[kind]
    assert metric == metric.of(0, 1)


def test_omission_and_hallucination_lexical_character_errors_are_counted_separately() -> None:
    clip = _clip("clip-1", 0, 1_000)
    packet = _packet(clips=(clip,))
    tokens = (
        GoldToken(token_id="a", text="甲"),
        GoldToken(token_id="b", text="乙"),
        GoldToken(token_id="c", text="丙"),
        GoldToken(token_id="d", text="丁"),
    )
    omissions = (
        GoldOmissionLabel(
            label_id="critical-b",
            token_ids=("b",),
            expected_text="乙",
            severity="critical",
        ),
    )
    gold = GoldClipLabel(
        clip_id="clip-1",
        expected_outcome="accepted",
        text="甲乙丙丁",
        tokens=tokens,
        omission_labels=omissions,
        provenance=_provenance(
            packet=packet,
            clip=clip,
            final_outcome="accepted",
            final_text="甲乙丙丁",
            final_tokens=tokens,
            final_omission_labels=omissions,
        ),
    )
    suite = TranscriptGoldSuite.build(
        suite_id="edits",
        annotation_packet=packet,
        complete=True,
        labels=(gold,),
        correction_targets_revealed_at_utc="2026-08-13T01:00:00Z",
    )
    candidate = TranscriptCandidateArtifact.build(
        candidate_id="edits",
        system_id="v2",
        generation_id="g",
        annotation_packet=packet,
        generated_at_utc="2026-08-13T00:00:00Z",
        complete=True,
        clips=(
            CandidateClipOutput.build(
                clip=clip,
                outcome="accepted",
                text="甲丙丁戊",
                tokens=("甲", "丙", "丁", "戊"),
            ),
        ),
    )

    result = evaluate_transcript_candidate(suite, candidate)

    assert result.metrics is not None
    assert result.metrics.lexical_character_deletions == 1
    assert result.metrics.lexical_character_insertions == 1
    assert result.metrics.lexical_character_substitutions == 0
    assert (
        result.metrics.lexical_deletion_edit_rate
        == result.metrics.lexical_deletion_edit_rate.of(1, 4)
    )
    assert result.metrics.lexical_insertion_edit_rate.value == pytest.approx(1 / 4)
    assert result.metrics.critical_omission_rate.value == 1.0
    assert result.metrics.word_token_accuracy.status == "not_evaluated"


def test_correct_to_wrong_apply_is_harmful_while_wrong_keep_is_a_miss() -> None:
    packet = _packet()
    corrections = (
        CandidateCorrectionDecision(
            decision_id="own-id-name",
            target_start_ms=1_000,
            target_end_ms=1_400,
            action="keep_original",
            recognition_text="安琪",
            final_text="安琪",
            source_artifact_ids=("author-book",),
        ),
        CandidateCorrectionDecision(
            decision_id="own-id-type",
            target_start_ms=1_500,
            target_end_ms=1_900,
            action="apply",
            recognition_text="在",
            final_text="錯",
            source_artifact_ids=("author-book",),
        ),
    )
    final_text = "安琪錯 Traveling Village 做了 3 次研究"
    coverages = (
        _timed_coverage(
            packet.clips[0],
            (
                ("name", 1_000, 1_400, "安琪", "安琪"),
                ("pause-a", 1_400, 1_500, "", ""),
                ("wording", 1_500, 1_900, "在", "錯"),
                (
                    "remainder",
                    1_900,
                    2_000,
                    " Traveling Village 做了 3 次研究",
                    " Traveling Village 做了 3 次研究",
                ),
            ),
        ),
        _default_timed_coverages(packet)[1],
    )
    candidate = _candidate(
        packet,
        clips=(
            _candidate_clip_one(
                packet,
                text=final_text,
                tokens=(final_text,),
                corrections=corrections,
            ),
            CandidateClipOutput.build(
                clip=packet.clips[1],
                outcome="needs_review",
                text="安琪",
                tokens=("安琪",),
                corrections=(
                    CandidateCorrectionDecision(
                        decision_id="candidate-decision-review",
                        target_start_ms=3_000,
                        target_end_ms=4_000,
                        action="needs_review",
                        recognition_text="安琪",
                        final_text="安琪",
                    ),
                ),
            ),
        ),
        timed_coverages=coverages,
    )

    result = evaluate_transcript_candidate(_gold(packet), candidate)

    assert result.metrics is not None
    assert result.metrics.correction_detection_recall.value == 1.0
    assert result.metrics.correction_apply_recall.value == 0.0
    assert result.metrics.false_keep_original_rate.value == 1.0
    assert result.metrics.harmful_apply_rate.numerator == 1
    assert result.metrics.harmful_apply_rate.denominator == 1
    assert result.metrics.source_precision.value == pytest.approx(1 / 2)
    assert result.metrics.needs_review_recall.value == 1.0


def test_wrong_to_different_wrong_apply_is_harmful() -> None:
    packet = _packet()
    final_text = "安淇在 Traveling Village 做了 3 次研究"
    name_apply = CandidateCorrectionDecision(
        decision_id="wrong-to-wrong",
        target_start_ms=1_000,
        target_end_ms=1_400,
        action="apply",
        recognition_text="安琪",
        final_text="安淇",
        source_artifact_ids=("author-book",),
    )
    coverages = (
        _timed_coverage(
            packet.clips[0],
            (
                ("name", 1_000, 1_400, "安琪", "安淇"),
                ("pause-a", 1_400, 1_500, "", ""),
                ("wording", 1_500, 1_900, "在", "在"),
                (
                    "remainder",
                    1_900,
                    2_000,
                    " Traveling Village 做了 3 次研究",
                    " Traveling Village 做了 3 次研究",
                ),
            ),
        ),
        _default_timed_coverages(packet)[1],
    )
    default = _candidate(packet)
    candidate = _candidate(
        packet,
        clips=(
            _candidate_clip_one(
                packet,
                text=final_text,
                tokens=(final_text,),
                corrections=(name_apply,),
            ),
            default.clips[1],
        ),
        timed_coverages=coverages,
    )

    result = evaluate_transcript_candidate(_gold(packet), candidate)

    assert result.correction_metrics_status is TranscriptEvaluationStatus.EVALUATED
    assert result.metrics is not None
    assert result.metrics.correction_detection_recall.value == 1.0
    assert result.metrics.correction_apply_recall.value == 0.0
    assert result.metrics.harmful_apply_rate.numerator == 1
    assert result.metrics.harmful_apply_rate.denominator == 1


def test_zero_sparse_decisions_cannot_evade_wrong_unchanged_recall_denominator() -> None:
    packet = _packet()
    final_text = "安琪在 Traveling Village 做了 3 次研究"
    coverages = (
        _timed_coverage(
            packet.clips[0],
            (
                ("name", 1_000, 1_400, "安琪", "安琪"),
                ("pause-a", 1_400, 1_500, "", ""),
                ("wording", 1_500, 1_900, "在", "在"),
                (
                    "remainder",
                    1_900,
                    2_000,
                    " Traveling Village 做了 3 次研究",
                    " Traveling Village 做了 3 次研究",
                ),
            ),
        ),
        _default_timed_coverages(packet)[1],
    )
    default = _candidate(packet)
    candidate = _candidate(
        packet,
        clips=(
            _candidate_clip_one(
                packet,
                text=final_text,
                tokens=(final_text,),
                corrections=(),
            ),
            default.clips[1],
        ),
        timed_coverages=coverages,
    )

    result = evaluate_transcript_candidate(_gold(packet), candidate)

    assert result.correction_metrics_status is TranscriptEvaluationStatus.EVALUATED
    assert result.metrics is not None
    assert result.metrics.correction_detection_recall.numerator == 0
    assert result.metrics.correction_detection_recall.denominator == 1
    assert result.metrics.correction_apply_recall.numerator == 0
    assert result.metrics.correction_apply_recall.denominator == 1
    assert result.metrics.false_keep_original_rate.numerator == 1
    assert result.metrics.false_keep_original_rate.denominator == 1


def test_missing_timed_coverage_is_typed_not_evaluated_for_corrections() -> None:
    packet = _packet()
    candidate = _candidate(packet, timed_coverages=())

    result = evaluate_transcript_candidate(_gold(packet), candidate)

    assert result.status is TranscriptEvaluationStatus.EVALUATED
    assert result.metrics is not None
    assert result.correction_metrics_status is TranscriptEvaluationStatus.NOT_EVALUATED
    assert result.correction_metrics_reason_codes == (
        "candidate_timed_coverage_incomplete",
    )
    assert result.metrics.correction_detection_recall.value is None


def test_legacy_v1_candidate_replays_but_correction_metrics_fail_closed() -> None:
    packet = _packet()
    current = _candidate(packet)
    legacy = TranscriptCandidateArtifactV1.build(
        candidate_id="legacy-v1",
        system_id="podcast-subtitle-v1",
        generation_id="legacy-generation",
        annotation_packet=packet,
        generated_at_utc="2026-08-13T00:00:00Z",
        complete=True,
        clips=current.clips,
    )

    loaded = load_transcript_candidate(legacy.canonical_bytes())
    result = evaluate_transcript_candidate(_gold(packet), loaded)

    assert isinstance(loaded, TranscriptCandidateArtifactV1)
    assert loaded.canonical_bytes() == legacy.canonical_bytes()
    assert result.status is TranscriptEvaluationStatus.EVALUATED
    assert result.correction_metrics_status is TranscriptEvaluationStatus.NOT_EVALUATED
    assert result.correction_metrics_reason_codes == (
        "candidate_timed_coverage_unavailable",
    )


def test_correction_matching_uses_exact_span_not_shared_hidden_id_or_text_guessing() -> None:
    packet = _packet()
    candidate = _candidate(packet)

    result = evaluate_transcript_candidate(_gold(packet), candidate)

    assert result.metrics is not None
    assert result.metrics.correction_apply_recall.value == 1.0
    gold_ids = {
        correction.label_id
        for label in _gold(packet).labels
        for correction in label.correction_labels
    }
    candidate_ids = {
        decision.decision_id
        for clip in candidate.clips
        for decision in clip.corrections
    }
    assert gold_ids.isdisjoint(candidate_ids)


def test_wrong_correction_span_is_not_matched_even_when_text_is_identical() -> None:
    packet = _packet()
    default = _candidate(packet)
    wrong_span = CandidateCorrectionDecision(
        decision_id="candidate-own-id",
        target_start_ms=1_001,
        target_end_ms=1_400,
        action="apply",
        recognition_text="安琪",
        final_text="安吉",
        source_artifact_ids=("author-book",),
    )
    with pytest.raises(ValidationError, match="align to timed span boundaries"):
        _candidate(
            packet,
            clips=(
                _candidate_clip_one(packet, corrections=(wrong_span,)),
                default.clips[1],
            ),
            timed_coverages=default.timed_coverages,
        )


def test_apply_outside_non_exhaustive_gold_authority_is_not_evaluated() -> None:
    packet = _packet()
    unmatched = CandidateCorrectionDecision(
        decision_id="unmatched-apply",
        target_start_ms=1_900,
        target_end_ms=2_000,
        action="apply",
        recognition_text="原文",
        final_text="杜撰",
    )
    final_text = "安吉在杜撰"
    coverages = (
        _timed_coverage(
            packet.clips[0],
            (
                ("name", 1_000, 1_400, "安吉", "安吉"),
                ("pause-a", 1_400, 1_500, "", ""),
                ("wording", 1_500, 1_900, "在", "在"),
                ("outside-authority", 1_900, 2_000, "原文", "杜撰"),
            ),
        ),
        _default_timed_coverages(packet)[1],
    )
    default = _candidate(packet)
    candidate = _candidate(
        packet,
        clips=(
            _candidate_clip_one(
                packet,
                text=final_text,
                tokens=(final_text,),
                corrections=(unmatched,),
            ),
            default.clips[1],
        ),
        timed_coverages=coverages,
    )

    result = evaluate_transcript_candidate(_gold(packet), candidate)

    assert result.correction_metrics_status is TranscriptEvaluationStatus.NOT_EVALUATED
    assert result.correction_metrics_reason_codes == (
        "candidate_apply_outside_gold_authority",
    )
    assert result.metrics is not None
    assert result.metrics.harmful_apply_rate.value is None


def test_correction_metrics_need_mechanical_target_reveal_phase_order() -> None:
    packet = _packet()
    suite = TranscriptGoldSuite.build(
        suite_id="no-custody-order",
        annotation_packet=packet,
        complete=True,
        spelling_sources=(
            SpellingAuthoritySource(source_id="author-book", artifact_hash=_h("book")),
        ),
        labels=(_accepted_gold(packet), _review_gold(packet)),
        correction_targets_revealed_at_utc=None,
    )

    result = evaluate_transcript_candidate(suite, _candidate(packet))

    assert result.status is TranscriptEvaluationStatus.EVALUATED
    assert result.correction_metrics_status is TranscriptEvaluationStatus.NOT_EVALUATED
    assert result.correction_metrics_reason_codes == (
        "candidate_generated_before_target_reveal_unverified",
    )
    assert result.metrics is not None
    assert result.metrics.correction_detection_recall.value is None


def test_duplicate_entity_occurrences_use_multiset_recall() -> None:
    clip = _clip("clip-1", 0, 1_000)
    packet = _packet(clips=(clip,))
    tokens = (
        GoldToken(token_id="a", text="安吉"),
        GoldToken(token_id="b", text="和"),
        GoldToken(token_id="c", text="安吉"),
    )
    spans = (
        GoldSpanLabel(label_id="first", kind="entity", token_ids=("a",), expected_text="安吉"),
        GoldSpanLabel(label_id="second", kind="entity", token_ids=("c",), expected_text="安吉"),
    )
    label = GoldClipLabel(
        clip_id="clip-1",
        expected_outcome="accepted",
        text="安吉和安吉",
        tokens=tokens,
        span_labels=spans,
        provenance=_provenance(
            packet=packet,
            clip=clip,
            final_outcome="accepted",
            final_text="安吉和安吉",
            final_tokens=tokens,
            final_span_labels=spans,
        ),
    )
    suite = TranscriptGoldSuite.build(
        suite_id="multiset",
        annotation_packet=packet,
        complete=True,
        labels=(label,),
        correction_targets_revealed_at_utc="2026-08-13T01:00:00Z",
    )
    candidate = TranscriptCandidateArtifact.build(
        candidate_id="multiset",
        system_id="v2",
        generation_id="g",
        annotation_packet=packet,
        generated_at_utc="2026-08-13T00:00:00Z",
        complete=True,
        clips=(
            CandidateClipOutput.build(
                clip=clip,
                outcome="accepted",
                text="安吉和安琪",
                tokens=("安吉", "和", "安琪"),
            ),
        ),
    )

    result = evaluate_transcript_candidate(suite, candidate)

    assert result.metrics is not None
    assert result.metrics.entity_recall.numerator == 1
    assert result.metrics.entity_recall.denominator == 2
    assert result.metrics.entity_recall.value == 0.5


def test_all_needs_review_gold_has_typed_zero_denominator_text_metrics() -> None:
    clip = _clip("clip-2", 3_000, 4_000)
    packet = _packet(clips=(clip,))
    suite = TranscriptGoldSuite.build(
        suite_id="all-review",
        annotation_packet=packet,
        complete=True,
        labels=(_review_gold(packet),),
        correction_targets_revealed_at_utc="2026-08-13T01:00:00Z",
    )
    candidate = TranscriptCandidateArtifact.build(
        candidate_id="all-review",
        system_id="v2",
        generation_id="g",
        annotation_packet=packet,
        generated_at_utc="2026-08-13T00:00:00Z",
        complete=True,
        clips=(
            CandidateClipOutput.build(
                clip=clip,
                outcome="needs_review",
                text=None,
                tokens=(),
            ),
        ),
    )

    result = evaluate_transcript_candidate(suite, candidate)

    assert result.metrics is not None
    assert result.metrics.lexical_character_error_rate.denominator == 0
    assert result.metrics.lexical_character_error_rate.value is None
    assert result.metrics.lexical_character_accuracy.value is None
    assert result.metrics.word_token_accuracy.status == "not_evaluated"
    assert result.metrics.entity_recall.status == "not_evaluated"
    assert result.metrics.code_switch_recall.status == "not_evaluated"
    assert result.metrics.numeric_recall.status == "not_evaluated"
    assert result.metrics.critical_omission_rate.status == "not_evaluated"
    assert result.metrics.correction_detection_recall.value is None
    assert result.metrics.correction_apply_recall.value is None
    assert result.metrics.false_keep_original_rate.value is None
    assert result.metrics.harmful_apply_rate.value is None
    assert result.metrics.source_precision.value is None


def test_reference_schema_cannot_carry_literal_as_spoken_truth() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SpellingAuthoritySource.model_validate(
            {
                "source_id": "book",
                "artifact_hash": _h("book"),
                "authority_scope": "spelling_only",
                "spoken_truth": "安吉",
            },
            strict=True,
        )


def test_complete_gold_requires_two_audio_only_passes_and_third_adjudication() -> None:
    with pytest.raises(ValidationError, match="third adjudicator"):
        _accepted_gold(_packet(), adjudicator_id="annotator-a")
    packet = _packet()
    labels = (
        _accepted_gold(packet, completed=False),
        _review_gold(packet),
    )
    with pytest.raises(ValidationError, match="completed third-person adjudication"):
        _gold(packet, complete=True, labels=labels)


def test_arbitrary_submission_hashes_cannot_self_attest_completed_gold() -> None:
    with pytest.raises(ValidationError, match="first_pass_submissions"):
        AdjudicationProvenance(
            first_pass_annotator_ids=("annotator-a", "annotator-b"),
            first_pass_submission_hashes=(_h("random-a"), _h("random-b")),
            adjudicator_id="adjudicator-c",
            adjudication_record_hash=_h("random-adjudication"),
            completed=True,
        )


def test_canonical_bytes_replay_and_tamper_detection() -> None:
    packet = _packet()
    suite = _gold(packet)
    candidate = _candidate(packet)
    evaluation = evaluate_transcript_candidate(suite, candidate)

    assert load_annotation_packet(packet.canonical_bytes()) == packet
    assert load_transcript_gold_suite(suite.canonical_bytes()) == suite
    assert load_transcript_candidate(candidate.canonical_bytes()) == candidate
    assert load_transcript_evaluation(evaluation.canonical_bytes()) == evaluation

    tampered = suite.canonical_bytes().replace(b"angie-gold-v1", b"angie-gold-v2")
    with pytest.raises(ValueError, match="invalid TranscriptGoldSuite JSON"):
        load_transcript_gold_suite(tampered)
    with pytest.raises(ValueError, match="exact canonical JSON"):
        load_transcript_gold_suite(suite.canonical_bytes() + b"\n")


def test_canonical_loader_rejects_unknown_fields_even_with_valid_json() -> None:
    packet = _packet()
    payload = packet.model_dump(mode="json")
    payload["candidate_id"] = "v2"
    raw = canonical_json_bytes(payload)

    with pytest.raises(ValueError, match="invalid TranscriptAnnotationPacket JSON"):
        load_annotation_packet(raw)


def test_canonical_loader_rejects_missing_defaulted_fields_and_scalar_coercion() -> None:
    packet = _packet()
    missing = packet.model_dump(mode="json")
    missing.pop("schema_version")
    with pytest.raises(ValueError, match="invalid TranscriptAnnotationPacket JSON"):
        load_annotation_packet(canonical_json_bytes(missing))

    coerced = packet.model_dump(mode="json")
    coerced["normalized_audio_size_bytes"] = str(packet.normalized_audio_size_bytes)
    coerced["packet_hash"] = _h("irrelevant")
    with pytest.raises(ValueError, match="invalid TranscriptAnnotationPacket JSON"):
        load_annotation_packet(canonical_json_bytes(coerced))


def test_audio_only_submission_schema_cannot_carry_reference_or_system_output() -> None:
    packet = _packet()
    valid = AudioOnlyTranscriptSubmission.build(
        submission_id="blind",
        annotation_packet_hash=packet.packet_hash,
        clip=packet.clips[0],
        annotator_id="annotator",
        outcome="accepted",
        text="安吉",
        tokens=("安吉",),
    )
    leaked = valid.model_dump(mode="json")
    leaked["reference_literal"] = "安吉"
    leaked["candidate_artifact_hash"] = _h("v2")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AudioOnlyTranscriptSubmission.model_validate(leaked, strict=True)


def test_annotation_packet_cannot_disclose_hidden_correction_target_spans() -> None:
    packet = _packet()
    leaked = packet.model_dump(mode="json")
    leaked["correction_targets"] = [
        {"start_ms": 1_000, "end_ms": 1_400, "expected_text": "安吉"}
    ]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TranscriptAnnotationPacket.model_validate(leaked, strict=True)
