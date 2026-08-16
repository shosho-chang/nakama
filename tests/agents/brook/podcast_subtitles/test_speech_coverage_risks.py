from __future__ import annotations

from agents.brook.podcast_subtitles.hashing import hash_object
from agents.brook.podcast_subtitles.speech_coverage import (
    speech_coverage_risks,
    verified_speech_coverage_hash,
)
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    CanonicalSpan,
    CanonicalToken,
    CanonicalTranscript,
    SpeechActivityInterval,
    SpeechCoverageReceipt,
    canonical_content_hash,
    speech_coverage_receipt_content_hash,
)

H0 = "0" * 64
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64


def _transcript() -> CanonicalTranscript:
    tokens = tuple(
        CanonicalToken(
            id=f"token-{label}",
            text=text,
            start_ms=start,
            end_ms=end,
            timing_basis="recognition",
            alignment_evidence_ids=(f"evidence-{label}",),
            evidence_ids=(f"evidence-{label}",),
            speaker="guest",
            confidence=0.99,
        )
        for label, text, start, end in (
            ("a", "甲", 500, 1_000),
            ("b", "乙", 2_000, 2_500),
            ("c", "丙", 3_500, 4_000),
        )
    )
    spans = tuple(
        CanonicalSpan(
            id=f"span-{label}",
            token_ids=(f"token-{label}",),
            start_ms=start,
            end_ms=end,
            alignment_evidence_ids=(f"evidence-{label}",),
        )
        for label, start, end in (
            ("a", 500, 1_000),
            ("b", 2_000, 2_500),
            ("c", 3_500, 4_000),
        )
    )
    return CanonicalTranscript(
        episode_id="episode-coverage",
        generation_id="generation-coverage",
        revision=1,
        status="draft",
        source_audio_hash=H0,
        normalized_audio_hash=H1,
        normalization_receipt_hash=H2,
        evidence_hash=H3,
        reference_evidence_hash=H4,
        ledger_hash=H0,
        policy_hash=H2,
        acceptance_policy={"permit_unresolved_low_risk": True},
        tokens=tokens,
        spans=spans,
        content_hash=canonical_content_hash(tokens),
    )


def _interval(
    label: str,
    start_ms: int,
    end_ms: int,
) -> SpeechActivityInterval:
    return SpeechActivityInterval(
        id=f"activity-{label}",
        start_ms=start_ms,
        end_ms=end_ms,
    )


def _receipt(
    *,
    activity: tuple[SpeechActivityInterval, ...],
    uncovered: tuple[SpeechActivityInterval, ...] = (),
    failed: bool = False,
) -> SpeechCoverageReceipt:
    digest = hash_object(
        {
            "activity": activity,
            "uncovered": uncovered,
            "failed": failed,
        }
    )
    return SpeechCoverageReceipt(
        status="failed" if failed else "completed",
        episode_id="episode-coverage",
        invocation_id="recognition-coverage",
        analyzer="fixture-speech-coverage",
        analyzer_version="1",
        analyzer_config_hash=H0,
        analyzer_code_hash=H1,
        analyzer_runtime_hash=H2,
        method="fixture_intervals_v1",
        normalized_audio_hash=H1,
        normalized_audio_size_bytes=123,
        normalized_audio_duration_ms=5_000,
        recognition_evidence_hash=H3,
        recognition_interval_hash=H4,
        coverage_tolerance_ms=0,
        minimum_uncovered_ms=100,
        raw_output=ArtifactDigest(
            uri=f"generation-artifact://speech_coverage/raw/{digest}.json",
            sha256=digest,
            size_bytes=123,
        ),
        raw_output_hash=digest,
        activity_intervals=() if failed else activity,
        uncovered_intervals=() if failed else uncovered,
        passed=not failed and not uncovered,
        failure_reason="analyzer failed" if failed else None,
    )


def test_missing_or_failed_receipt_creates_stable_blocking_global_issue() -> None:
    transcript = _transcript()

    missing = speech_coverage_risks(transcript, None)
    failed = speech_coverage_risks(transcript, _receipt(activity=(), failed=True))

    assert missing[0].issue.code == "speech_coverage_receipt_missing"
    assert failed[0].issue.code == "speech_coverage_analyzer_failed"
    assert missing[0].issue.severity == failed[0].issue.severity == "blocking"
    assert missing[0].issue.span_ids == failed[0].issue.span_ids == (
        "span-a",
        "span-c",
    )
    assert verified_speech_coverage_hash(None) is None
    assert verified_speech_coverage_hash(_receipt(activity=(), failed=True)) is None


def test_uncovered_activity_anchors_leading_middle_and_trailing_audio() -> None:
    uncovered = (
        _interval("leading", 0, 400),
        _interval("middle", 1_100, 1_900),
        _interval("trailing", 4_100, 4_500),
    )
    activity = (
        uncovered[0],
        _interval("a", 500, 1_000),
        uncovered[1],
        _interval("b", 2_000, 2_500),
        _interval("c", 3_500, 4_000),
        uncovered[2],
    )
    receipt = _receipt(activity=activity, uncovered=uncovered)

    risks = speech_coverage_risks(_transcript(), receipt)
    uncovered_risks = tuple(
        risk for risk in risks if risk.issue.code == "uncovered_speech_activity"
    )

    assert tuple(risk.issue.span_ids for risk in uncovered_risks) == (
        ("span-a",),
        ("span-a", "span-b"),
        ("span-c",),
    )
    assert all(risk.issue.severity == "blocking" for risk in uncovered_risks)
    assert all(not risk.issue.candidates for risk in uncovered_risks)
    assert tuple(risk.issue.id for risk in uncovered_risks) == tuple(
        risk.issue.id
        for risk in speech_coverage_risks(_transcript(), receipt)
        if risk.issue.code == "uncovered_speech_activity"
    )
    assert verified_speech_coverage_hash(receipt) is None


def test_recognition_outside_energy_activity_is_low_warning_not_text_deletion() -> None:
    receipt = _receipt(activity=(_interval("a", 500, 1_000),))

    risks = speech_coverage_risks(_transcript(), receipt)
    warnings = tuple(
        risk for risk in risks if risk.issue.code == "recognition_outside_audio_activity"
    )

    assert tuple(risk.issue.span_ids for risk in warnings) == (("span-b",), ("span-c",))
    assert tuple(risk.issue.candidates for risk in warnings) == (("乙",), ("丙",))
    assert all(risk.issue.severity == "low" for risk in warnings)
    assert verified_speech_coverage_hash(receipt) == speech_coverage_receipt_content_hash(
        receipt
    )


def test_receipt_from_another_audio_is_rejected() -> None:
    receipt = _receipt(
        activity=(
            _interval("a", 500, 1_000),
            _interval("b", 2_000, 2_500),
            _interval("c", 3_500, 4_000),
        )
    ).model_copy(update={"normalized_audio_hash": H2})

    try:
        speech_coverage_risks(_transcript(), receipt)
    except ValueError as exc:
        assert "crossed Canonical audio lineage" in str(exc)
    else:  # pragma: no cover - assertion style keeps exact message visible.
        raise AssertionError("cross-audio coverage receipt was accepted")
