"""Canonical risk projection for independent speech-activity coverage.

The Adapter receipt is immutable audio Evidence.  This module performs the
separate domain decision: map uncovered activity to stable Canonical Span IDs
without inventing text or changing timing.
"""

from __future__ import annotations

from collections.abc import Sequence

from shared.schemas.podcast_subtitles_v2 import (
    CanonicalSpan,
    CanonicalTranscript,
    ReviewIssue,
    SpeechActivityInterval,
    SpeechCoverageReceipt,
    speech_coverage_receipt_content_hash,
)

from .hashing import hash_object
from .risk import RiskRecord


def verified_speech_coverage_hash(receipt: SpeechCoverageReceipt | None) -> str | None:
    """Return an attestation only for a completed coverage pass."""

    if receipt is None or receipt.status != "completed" or not receipt.passed:
        return None
    return speech_coverage_receipt_content_hash(receipt)


def _global_span_anchors(spans: Sequence[CanonicalSpan]) -> tuple[str, ...]:
    if not spans:
        raise ValueError("speech coverage risks require Canonical spans")
    if len(spans) == 1:
        return (spans[0].id,)
    return (spans[0].id, spans[-1].id)


def _interval_span_anchors(
    spans: Sequence[CanonicalSpan],
    interval: SpeechActivityInterval,
) -> tuple[str, ...]:
    """Attach a textless audio gap to its nearest stable transcript boundaries."""

    overlapping = tuple(
        span.id
        for span in spans
        if span.start_ms < interval.end_ms and interval.start_ms < span.end_ms
    )
    if overlapping:
        return overlapping
    left = next(
        (
            span
            for span in reversed(spans)
            if span.end_ms <= interval.start_ms
        ),
        None,
    )
    right = next(
        (span for span in spans if span.start_ms >= interval.end_ms),
        None,
    )
    anchors = tuple(
        dict.fromkeys(
            span.id for span in (left, right) if span is not None
        )
    )
    return anchors or _global_span_anchors(spans)


def _issue(
    *,
    code: str,
    risk: str,
    severity: str,
    span_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...],
    candidates: tuple[str, ...] = (),
    identity: object,
) -> RiskRecord:
    issue = ReviewIssue(
        id=f"issue-{hash_object({'code': code, 'identity': identity})}",
        risk=risk,
        severity=severity,
        code=code,
        span_ids=span_ids,
        audio_evidence_ids=evidence_ids,
        candidates=candidates,
        status="unresolved",
    )
    return RiskRecord(
        issue=issue,
        audio_span_ids=span_ids,
        evidence_ids=evidence_ids,
    )


def speech_coverage_risks(
    transcript: CanonicalTranscript,
    receipt: SpeechCoverageReceipt | None,
) -> tuple[RiskRecord, ...]:
    """Map coverage state to stable fail-closed review issues.

    Missing/failed coverage and uncovered activity are blocking.  Recognition
    wholly outside observed activity is retained as a low-severity warning:
    energy detection can be wrong, so it never removes ASR text on its own.
    """

    spans = transcript.spans
    global_anchors = _global_span_anchors(spans)
    if receipt is None:
        return (
            _issue(
                code="speech_coverage_receipt_missing",
                risk="provenance",
                severity="blocking",
                span_ids=global_anchors,
                evidence_ids=(),
                identity={
                    "audio": transcript.normalized_audio_hash,
                    "recognition_evidence": transcript.evidence_hash,
                },
            ),
        )
    if (
        receipt.episode_id != transcript.episode_id
        or receipt.normalized_audio_hash != transcript.normalized_audio_hash
    ):
        raise ValueError("Speech Coverage Receipt crossed Canonical audio lineage")

    receipt_hash = speech_coverage_receipt_content_hash(receipt)
    receipt_evidence_id = f"speech-coverage-receipt:{receipt_hash}"
    if receipt.status == "failed":
        return (
            _issue(
                code="speech_coverage_analyzer_failed",
                risk="provenance",
                severity="blocking",
                span_ids=global_anchors,
                evidence_ids=(receipt_evidence_id,),
                identity={
                    "receipt": receipt_hash,
                    "failure_reason": receipt.failure_reason,
                },
            ),
        )

    risks: list[RiskRecord] = []
    for interval in receipt.uncovered_intervals:
        anchors = _interval_span_anchors(spans, interval)
        evidence_ids = (receipt_evidence_id, interval.id)
        risks.append(
            _issue(
                code="uncovered_speech_activity",
                risk="text",
                severity="blocking",
                span_ids=anchors,
                evidence_ids=evidence_ids,
                identity={
                    "receipt": receipt_hash,
                    "interval": interval.id,
                    "start_ms": interval.start_ms,
                    "end_ms": interval.end_ms,
                    "anchors": anchors,
                },
            )
        )

    token_by_id = {token.id: token for token in transcript.tokens}
    for span in spans:
        if any(
            interval.start_ms < span.end_ms and span.start_ms < interval.end_ms
            for interval in receipt.activity_intervals
        ):
            continue
        text = "".join(token_by_id[token_id].text for token_id in span.token_ids)
        risks.append(
            _issue(
                code="recognition_outside_audio_activity",
                risk="text",
                severity="low",
                span_ids=(span.id,),
                evidence_ids=(receipt_evidence_id,),
                candidates=(text,),
                identity={"receipt": receipt_hash, "span": span.id},
            )
        )
    return tuple(risks)


__all__ = ["speech_coverage_risks", "verified_speech_coverage_hash"]
