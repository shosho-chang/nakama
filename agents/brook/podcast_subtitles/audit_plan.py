"""Pure, replayable correction-audit planning for Podcast Subtitle V2.

This slice deliberately stops before any Corrector, Audio Auditor, Proposal,
Decision, Module, or store integration.  It proves structural coverage of a
closed audit matrix; it does not prove that a detector or model can recall
every semantic transcription error.
"""

from __future__ import annotations

import re
from collections import Counter
from heapq import heappop, heappush
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping, Sequence

from shared.schemas.podcast_subtitles_v2 import (
    CORRECTION_AUDIT_BOUNDARY_CATEGORIES,
    CORRECTION_AUDIT_SPAN_CATEGORIES,
    EMPTY_REFERENCE_EVIDENCE_HASH,
    AuditCell,
    AuditInputBindings,
    AuditPlan,
    BoundaryAuditCategory,
    BoundaryAuditTarget,
    BoundaryConstraintReceipt,
    CanonicalSpan,
    CanonicalTranscript,
    CorrectionAuditPolicySnapshot,
    RecognitionEvidence,
    RecognitionSeamEvidence,
    ReferenceRetrievalReceipt,
    SpanAuditCategory,
    SpanAuditTarget,
    SpeechCoverageReceipt,
    boundary_constraint_receipt_hash,
    recognition_evidence_content_hash,
    recognition_evidence_set_hash,
    reference_evidence_set_hash,
    speech_coverage_receipt_content_hash,
)

from .hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes

AUDIT_PLAN_BUILDER_ID = "nakama-correction-audit-plan"
AUDIT_PLAN_BUILDER_VERSION = 1
AUDIT_SIGNAL_RULE_SET_ID = "nakama-correction-audit-signals"
AUDIT_SIGNAL_RULE_SET_VERSION = 1

_NUMERIC_ARABIC_RE = re.compile(r"\d")
_NUMERIC_HAN_RE = re.compile(r"[零〇一二三四五六七八九十百千萬億兩]")
_NUMERIC_COMPOSITE_RE = re.compile(
    r"(?:\d+[.．]\d+|\d+\s*[%％]|\d+\s*[-–—~～至]\s*\d+|"
    r"\d{1,4}[年/月日:：]|(?:v|V)?\d+(?:\.\d+){1,}|"
    r"\d+\s*(?:kg|g|mg|μg|ug|km|cm|mm|mL|ml|L|%|％|個|位|名|本|篇|份|項|次|年|月|天))"
)
_NEGATION_RE = re.compile(r"(?:沒有|不是|不能|不會|不|沒|無|非|未|別)")
_LATIN_RE = re.compile(r"[A-Za-z]")
_ACRONYM_RE = re.compile(r"(?<![A-Za-z])[A-Z]{2,}(?![A-Za-z])")
_FILLERS = frozenset({"嗯", "呃", "額", "欸", "誒", "啊", "那個", "就是"})


class AuditPlanError(ValueError):
    """An audit input or stored plan cannot be reproduced exactly."""


def correction_audit_rule_set_hash(
    *, low_confidence_threshold: float, seam_match_tolerance_ms: int
) -> str:
    """Identity of every closed rule/policy value that changes applicability."""

    payload = {
        "rule_set_id": AUDIT_SIGNAL_RULE_SET_ID,
        "rule_set_version": AUDIT_SIGNAL_RULE_SET_VERSION,
        "language": "zh-Hant-TW",
        "span_categories": CORRECTION_AUDIT_SPAN_CATEGORIES,
        "boundary_categories": CORRECTION_AUDIT_BOUNDARY_CATEGORIES,
        "low_confidence_threshold": low_confidence_threshold,
        "seam_match_tolerance_ms": seam_match_tolerance_ms,
        "reference_requires_audio_confirmation": True,
        "missing_confidence_mode": "required_review_trigger",
        "missing_reference_mode": "unavailable_fail_closed",
        "detector_rule_set": "nakama-correction-audit-signals-v1",
        "candidate_grouping": "overlap-connected-components-v1",
    }
    return hash_object(payload)


def default_correction_audit_policy(
    *, low_confidence_threshold: float = 0.75, seam_match_tolerance_ms: int = 50
) -> CorrectionAuditPolicySnapshot:
    """Build the complete immutable policy rather than accepting a bare hash."""

    return CorrectionAuditPolicySnapshot(
        rule_set_hash=correction_audit_rule_set_hash(
            low_confidence_threshold=low_confidence_threshold,
            seam_match_tolerance_ms=seam_match_tolerance_ms,
        ),
        low_confidence_threshold=low_confidence_threshold,
        seam_match_tolerance_ms=seam_match_tolerance_ms,
    )


def audit_plan_bytes(plan: AuditPlan) -> bytes:
    return canonical_json_bytes(plan)


def audit_plan_digest(plan: AuditPlan) -> str:
    return sha256_bytes(audit_plan_bytes(plan))


def audit_plan_builder_code_hash() -> str:
    return hash_file(Path(__file__))


def span_text_flags(text: str) -> frozenset[str]:
    """Return conservative deterministic detectors used by plan and signals."""

    flags: set[str] = set()
    if _NUMERIC_ARABIC_RE.search(text) or _NUMERIC_HAN_RE.search(text):
        flags.add("numeric")
    if _NEGATION_RE.search(text):
        flags.add("negation")
    if _LATIN_RE.search(text):
        flags.add("latin")
    if _ACRONYM_RE.search(text):
        flags.add("acronym")
    if any(filler in text for filler in _FILLERS):
        flags.add("disfluency")
    if any(character * 2 in text for character in text if character.strip()):
        flags.add("disfluency")
    return frozenset(flags)


def _observed_speakers_by_span(
    spans: Sequence[CanonicalSpan | SpanAuditTarget],
    recognitions: Sequence[RecognitionEvidence],
) -> Mapping[str, frozenset[str]]:
    """Resolve strict-overlap speaker sets with one deterministic interval sweep."""

    ordered_tokens: list[tuple[int, int, int, int, str]] = []
    for evidence_index, evidence in enumerate(recognitions):
        for token_index, token in enumerate(evidence.tokens):
            speaker = token.speaker
            if speaker is None:
                continue
            ordered_tokens.append(
                (
                    token.start_ms,
                    token.end_ms,
                    evidence_index,
                    token_index,
                    speaker,
                )
            )
    ordered_tokens.sort()

    observed: dict[str, frozenset[str]] = {}
    active: list[tuple[int, int, int, str]] = []
    speaker_counts: Counter[str] = Counter()
    token_cursor = 0
    previous_span_end: int | None = None
    for span in spans:
        if previous_span_end is not None and span.start_ms < previous_span_end:
            raise AuditPlanError("speaker sweep requires ordered non-overlapping spans")
        while token_cursor < len(ordered_tokens):
            start_ms, end_ms, evidence_index, token_index, speaker = ordered_tokens[
                token_cursor
            ]
            if start_ms >= span.end_ms:
                break
            heappush(active, (end_ms, evidence_index, token_index, speaker))
            speaker_counts[speaker] += 1
            token_cursor += 1
        while active and active[0][0] <= span.start_ms:
            _, _, _, speaker = heappop(active)
            speaker_counts[speaker] -= 1
            if speaker_counts[speaker] == 0:
                del speaker_counts[speaker]
        span_id = span.span_id if isinstance(span, SpanAuditTarget) else span.id
        observed[span_id] = frozenset(speaker_counts)
        previous_span_end = span.end_ms
    return MappingProxyType(observed)


def cross_span_repetition(left_text: str, right_text: str) -> bool:
    """Conservative exact repetition/self-repair trigger; never deletes text."""

    maximum = min(8, len(left_text), len(right_text))
    return any(left_text[-width:] == right_text[:width] for width in range(1, maximum + 1))


def _metadata_status(values: Sequence[object | None]) -> Literal["complete", "partial", "absent"]:
    available = sum(value is not None for value in values)
    if available == 0:
        return "absent"
    if available == len(values):
        return "complete"
    return "partial"


def _span_target(
    transcript: CanonicalTranscript,
    span: CanonicalSpan,
    ordinal: int,
    token_positions: dict[str, int],
    basis_hash: str,
) -> SpanAuditTarget:
    selected = tuple(transcript.tokens[token_positions[token_id]] for token_id in span.token_ids)
    text = "".join(token.text for token in selected)
    payload = {
        "schema_version": 1,
        "basis_hash": basis_hash,
        "ordinal": ordinal,
        "span_id": span.id,
        "token_ids": span.token_ids,
        "token_start_index": token_positions[span.token_ids[0]],
        "token_end_index": token_positions[span.token_ids[-1]] + 1,
        "observed_text": text,
        "observed_text_hash": sha256_bytes(text.encode("utf-8")),
        "start_ms": span.start_ms,
        "end_ms": span.end_ms,
    }
    return SpanAuditTarget(id=hash_object(payload), **payload)


def _boundary_target(
    ordinal: int,
    left: SpanAuditTarget | None,
    right: SpanAuditTarget | None,
    *,
    audio_duration_ms: int,
    basis_hash: str,
) -> BoundaryAuditTarget:
    start_ms = left.end_ms if left is not None else 0
    end_ms = right.start_ms if right is not None else audio_duration_ms
    if end_ms < start_ms:
        raise AuditPlanError("Canonical boundary window runs backwards")
    payload = {
        "schema_version": 1,
        "basis_hash": basis_hash,
        "ordinal": ordinal,
        "left_span_id": left.span_id if left is not None else None,
        "right_span_id": right.span_id if right is not None else None,
        "left_token_id": left.token_ids[-1] if left is not None else None,
        "right_token_id": right.token_ids[0] if right is not None else None,
        "window_start_ms": start_ms,
        "window_end_ms": end_ms,
    }
    return BoundaryAuditTarget(id=hash_object(payload), **payload)


def _cell(
    *,
    target_kind: Literal["span", "boundary"],
    target_id: str,
    category: SpanAuditCategory | BoundaryAuditCategory,
    applicability: Literal["required", "not_applicable", "unavailable"],
    reason: str,
    basis_hash: str,
) -> AuditCell:
    payload = {
        "schema_version": 1,
        "basis_hash": basis_hash,
        "target_kind": target_kind,
        "target_id": target_id,
        "category": category,
        "applicability": applicability,
        "applicability_reason": reason,
    }
    return AuditCell(id=hash_object(payload), **payload)


def _span_cell(
    target: SpanAuditTarget,
    category: SpanAuditCategory,
    *,
    reference_status: str,
    reference_receipt: ReferenceRetrievalReceipt | None,
    confidences: Sequence[float | None],
    speakers: Sequence[str | None],
    speaker_disagreement: bool,
    low_confidence_threshold: float,
    basis_hash: str,
) -> AuditCell:
    flags = span_text_flags(target.observed_text)
    if category == "lexical_fidelity":
        return _cell(
            target_kind="span",
            target_id=target.id,
            category=category,
            applicability="required",
            reason="baseline_required",
            basis_hash=basis_hash,
        )
    if category == "reference_name_term":
        if reference_status == "not_enrolled":
            return _cell(
                target_kind="span",
                target_id=target.id,
                category=category,
                applicability="not_applicable",
                reason="no_sources_enrolled",
                basis_hash=basis_hash,
            )
        if reference_receipt is not None and reference_receipt.status == "completed":
            return _cell(
                target_kind="span",
                target_id=target.id,
                category=category,
                applicability="required" if reference_receipt.hits else "not_applicable",
                reason=(
                    "reference_completed" if reference_receipt.hits else "no_reference_support"
                ),
                basis_hash=basis_hash,
            )
        return _cell(
            target_kind="span",
            target_id=target.id,
            category=category,
            applicability="unavailable",
            reason=(
                "upstream_failed"
                if reference_receipt is not None and reference_receipt.status == "failed"
                else "upstream_missing"
            ),
            basis_hash=basis_hash,
        )
    if category == "confidence_integrity":
        requires_review = any(value is None for value in confidences) or any(
            value is not None and value < low_confidence_threshold for value in confidences
        )
        return _cell(
            target_kind="span",
            target_id=target.id,
            category=category,
            applicability="required" if requires_review else "not_applicable",
            reason="pattern_present" if requires_review else "pattern_absent",
            basis_hash=basis_hash,
        )
    if category == "speaker_identity":
        known = [value for value in speakers if value is not None]
        if len(known) != len(speakers):
            applicability, reason = "unavailable", "upstream_missing"
        elif len(set(known)) > 1 or speaker_disagreement:
            applicability, reason = "required", "pattern_present"
        else:
            applicability, reason = "not_applicable", "pattern_absent"
        return _cell(
            target_kind="span",
            target_id=target.id,
            category=category,
            applicability=applicability,
            reason=reason,
            basis_hash=basis_hash,
        )
    flag = {
        "numeric_expression": "numeric",
        "negation_polarity": "negation",
        "latin_code_switch": "latin",
        "acronym_initialism": "acronym",
        "repetition_disfluency": "disfluency",
    }[category]
    return _cell(
        target_kind="span",
        target_id=target.id,
        category=category,
        applicability="required" if flag in flags else "not_applicable",
        reason="pattern_present" if flag in flags else "pattern_absent",
        basis_hash=basis_hash,
    )


def _boundary_cell(
    target: BoundaryAuditTarget,
    category: BoundaryAuditCategory,
    *,
    left: SpanAuditTarget | None,
    right: SpanAuditTarget | None,
    left_speaker: str | None,
    right_speaker: str | None,
    speaker_conflict: bool,
    coverage_status: str,
    speech_coverage: SpeechCoverageReceipt | None,
    seam_status: str,
    owned_seam_observations: Sequence[object],
    basis_hash: str,
) -> AuditCell:
    endpoint = left is None or right is None
    if category == "cross_span_fidelity":
        return _cell(
            target_kind="boundary",
            target_id=target.id,
            category=category,
            applicability="not_applicable" if endpoint else "required",
            reason="endpoint_not_applicable" if endpoint else "baseline_required",
            basis_hash=basis_hash,
        )
    if category == "speaker_transition":
        if endpoint:
            applicability, reason = "not_applicable", "endpoint_not_applicable"
        elif left_speaker is None or right_speaker is None:
            applicability, reason = "unavailable", "upstream_missing"
        elif left_speaker != right_speaker or speaker_conflict:
            applicability, reason = "required", "pattern_present"
        else:
            applicability, reason = "not_applicable", "verified_clean"
        return _cell(
            target_kind="boundary",
            target_id=target.id,
            category=category,
            applicability=applicability,
            reason=reason,
            basis_hash=basis_hash,
        )
    if category == "repetition_self_repair":
        detected = not endpoint and cross_span_repetition(left.observed_text, right.observed_text)
        return _cell(
            target_kind="boundary",
            target_id=target.id,
            category=category,
            applicability="required" if detected else "not_applicable",
            reason=(
                "pattern_present"
                if detected
                else ("endpoint_not_applicable" if endpoint else "pattern_absent")
            ),
            basis_hash=basis_hash,
        )
    if category == "speech_coverage":
        if coverage_status == "absent":
            applicability, reason = "unavailable", "upstream_missing"
        elif coverage_status == "failed":
            applicability, reason = "unavailable", "upstream_failed"
        else:
            if speech_coverage is None:
                raise AuditPlanError("completed coverage state lost its receipt")
            overlap = any(
                (
                    interval.start_ms <= target.window_start_ms < interval.end_ms
                    if target.window_start_ms == target.window_end_ms
                    else interval.start_ms < target.window_end_ms
                    and interval.end_ms > target.window_start_ms
                )
                for interval in speech_coverage.uncovered_intervals
            )
            applicability = "required" if overlap else "not_applicable"
            reason = "explicit_evidence_available" if overlap else "verified_clean"
        return _cell(
            target_kind="boundary",
            target_id=target.id,
            category=category,
            applicability=applicability,
            reason=reason,
            basis_hash=basis_hash,
        )
    if endpoint or seam_status == "unchunked":
        applicability, reason = (
            "not_applicable",
            ("endpoint_not_applicable" if endpoint else "no_matching_seam"),
        )
    elif seam_status == "absent":
        applicability, reason = "unavailable", "upstream_missing"
    elif owned_seam_observations:
        material = any(item.status != "matched" for item in owned_seam_observations)
        applicability = "required" if material else "not_applicable"
        reason = "explicit_evidence_available" if material else "verified_clean"
    else:
        applicability, reason = "not_applicable", "no_matching_seam"
    return _cell(
        target_kind="boundary",
        target_id=target.id,
        category=category,
        applicability=applicability,
        reason=reason,
        basis_hash=basis_hash,
    )


def build_audit_plan(
    transcript: CanonicalTranscript,
    recognition_evidence: Sequence[RecognitionEvidence],
    policy: CorrectionAuditPolicySnapshot,
    *,
    reference_retrievals: Sequence[ReferenceRetrievalReceipt] = (),
    references_enrolled: bool,
    speech_coverage: SpeechCoverageReceipt | None = None,
    boundary_constraints: BoundaryConstraintReceipt | None = None,
    seam_evidence: RecognitionSeamEvidence | None = None,
    origin: Literal["native", "reconstructed"] = "native",
) -> AuditPlan:
    """Build an exact structural plan from immutable upstream evidence."""

    recognitions = tuple(recognition_evidence)
    if not recognitions:
        raise AuditPlanError("Correction AuditPlan requires Recognition Evidence")
    recognition_hashes = tuple(
        sorted(recognition_evidence_content_hash(item) for item in recognitions)
    )
    recognition_set_hash = recognition_evidence_set_hash(list(recognitions))
    if len(set(recognition_hashes)) != len(recognition_hashes):
        raise AuditPlanError("Correction AuditPlan rejects duplicate Recognition Evidence")
    if transcript.evidence_hash != recognition_set_hash:
        raise AuditPlanError(
            "Canonical Recognition Evidence lineage differs from supplied Evidence set"
        )
    for item in recognitions:
        if item.episode_id != transcript.episode_id:
            raise AuditPlanError("Recognition Evidence episode differs from Canonical truth")
        if item.normalized_audio_hash != transcript.normalized_audio_hash:
            raise AuditPlanError("Recognition Evidence audio lineage differs from Canonical truth")

    retrievals = tuple(reference_retrievals)
    if not references_enrolled:
        if retrievals or transcript.reference_evidence_hash != EMPTY_REFERENCE_EVIDENCE_HASH:
            raise AuditPlanError("not-enrolled references cannot carry retrieval/Evidence state")
        reference_status = "not_enrolled"
    else:
        by_span: dict[str, ReferenceRetrievalReceipt] = {}
        known_spans = {span.id for span in transcript.spans}
        for receipt in retrievals:
            if receipt.episode_id != transcript.episode_id:
                raise AuditPlanError("Reference receipt episode differs from Canonical truth")
            if receipt.audio_span_id not in known_spans:
                raise AuditPlanError("Reference receipt targets an unknown Canonical span")
            if receipt.audio_span_id in by_span:
                raise AuditPlanError("Reference receipt set repeats a Canonical span")
            if receipt.context.basis_content_hash != transcript.content_hash:
                raise AuditPlanError("Reference receipt context differs from Canonical truth")
            by_span[receipt.audio_span_id] = receipt
        if any(receipt.status == "failed" for receipt in retrievals):
            reference_status = "failed"
        elif len(by_span) != len(transcript.spans):
            reference_status = "incomplete"
        else:
            reference_status = "completed"
        evidence_by_id = {
            evidence.id: evidence for receipt in retrievals for evidence in receipt.evidence
        }
        if len(evidence_by_id) != sum(len(item.evidence) for item in retrievals):
            duplicates = [evidence.id for receipt in retrievals for evidence in receipt.evidence]
            if len(set(duplicates)) != len(duplicates):
                # The same immutable Evidence may legitimately be retrieved for
                # adjacent spans; identity equality is checked below.
                for evidence_id in set(duplicates):
                    versions = {
                        hash_object(evidence)
                        for receipt in retrievals
                        for evidence in receipt.evidence
                        if evidence.id == evidence_id
                    }
                    if len(versions) != 1:
                        raise AuditPlanError("Reference Evidence ID has conflicting content")
        # Aggregate failed/incomplete is only an execution-status summary.  It
        # never relaxes Canonical lineage: every successful excerpt present in
        # any receipt must be exactly the immutable Reference Evidence union.
        if reference_evidence_set_hash(tuple(evidence_by_id.values())) != (
            transcript.reference_evidence_hash
        ):
            raise AuditPlanError("Reference retrieval union differs from Canonical lineage")

    receipt_hashes = tuple(sorted(hash_object(item) for item in retrievals))
    if len(set(receipt_hashes)) != len(receipt_hashes):
        raise AuditPlanError("Reference retrieval receipt set contains duplicate content")

    if speech_coverage is None:
        coverage_status = "absent"
        coverage_hash = None
        audio_duration_ms = transcript.spans[-1].end_ms
    else:
        if (
            speech_coverage.episode_id != transcript.episode_id
            or (speech_coverage.normalized_audio_hash != transcript.normalized_audio_hash)
            or (speech_coverage.recognition_evidence_hash != recognition_set_hash)
        ):
            raise AuditPlanError("Speech Coverage differs from Canonical lineage")
        coverage_status = speech_coverage.status
        coverage_hash = speech_coverage_receipt_content_hash(speech_coverage)
        audio_duration_ms = speech_coverage.normalized_audio_duration_ms
        if audio_duration_ms < transcript.spans[-1].end_ms:
            raise AuditPlanError("Speech Coverage duration truncates Canonical spans")

    if boundary_constraints is None:
        boundary_status = "absent"
        boundary_hash = None
    else:
        if (
            boundary_constraints.episode_id != transcript.episode_id
            or boundary_constraints.generation_id != transcript.generation_id
            or boundary_constraints.canonical_content_hash != transcript.content_hash
            or boundary_constraints.policy_hash != transcript.policy_hash
            or boundary_constraints.token_ids != tuple(token.id for token in transcript.tokens)
        ):
            raise AuditPlanError("Boundary Constraint Receipt differs from Canonical truth")
        boundary_status = "verified"
        boundary_hash = boundary_constraint_receipt_hash(boundary_constraints)

    if seam_evidence is None:
        seam_status = "absent"
        seam_hash = None
    else:
        if seam_evidence.normalized_audio_hash != transcript.normalized_audio_hash:
            raise AuditPlanError("seam evidence differs from Canonical audio lineage")
        if seam_evidence.recognition_evidence_hashes != recognition_hashes:
            raise AuditPlanError("seam evidence differs from Recognition Evidence set")
        if seam_evidence.raw_artifact_hash not in {item.raw_output_hash for item in recognitions}:
            raise AuditPlanError("seam raw artifact is not owned by Recognition Evidence")
        seam_status = seam_evidence.status
        seam_hash = hash_object(seam_evidence)

    confidence_status = _metadata_status([token.confidence for token in transcript.tokens])
    speaker_status = _metadata_status([token.speaker for token in transcript.tokens])
    input_payload = {
        "schema_version": 1,
        "canonical_transcript_hash": hash_object(transcript),
        "canonical_content_hash": transcript.content_hash,
        "normalized_audio_hash": transcript.normalized_audio_hash,
        "recognition_evidence_hashes": recognition_hashes,
        "recognition_evidence_set_hash": recognition_set_hash,
        "confidence_status": confidence_status,
        "speaker_status": speaker_status,
        "reference_evidence_hash": transcript.reference_evidence_hash,
        "reference_status": reference_status,
        "reference_retrieval_receipt_hashes": receipt_hashes,
        "reference_retrieval_receipt_set_hash": hash_object(receipt_hashes),
        "speech_coverage_status": coverage_status,
        "speech_coverage_receipt_hash": coverage_hash,
        "boundary_constraint_status": boundary_status,
        "boundary_constraint_receipt_hash": boundary_hash,
        "seam_status": seam_status,
        "seam_evidence_hash": seam_hash,
    }
    inputs = AuditInputBindings(**input_payload, content_hash=hash_object(input_payload))
    policy_hash = hash_object(policy)
    builder_code_hash = audit_plan_builder_code_hash()
    basis_hash = hash_object(
        {
            "episode_id": transcript.episode_id,
            "generation_id": transcript.generation_id,
            "canonical_transcript_hash": inputs.canonical_transcript_hash,
            "canonical_content_hash": inputs.canonical_content_hash,
            "normalized_audio_hash": inputs.normalized_audio_hash,
            "policy_hash": policy_hash,
            "input_binding_hash": inputs.content_hash,
            "builder_id": AUDIT_PLAN_BUILDER_ID,
            "builder_version": AUDIT_PLAN_BUILDER_VERSION,
            "builder_code_hash": builder_code_hash,
        }
    )

    positions = {token.id: index for index, token in enumerate(transcript.tokens)}
    spans = tuple(
        _span_target(transcript, span, index, positions, basis_hash)
        for index, span in enumerate(transcript.spans)
    )
    boundaries = tuple(
        _boundary_target(
            index,
            None if index == 0 else spans[index - 1],
            None if index == len(spans) else spans[index],
            audio_duration_ms=audio_duration_ms,
            basis_hash=basis_hash,
        )
        for index in range(len(spans) + 1)
    )
    seam_ownership: dict[str, tuple[object, ...]] = {target.id: () for target in boundaries}
    if seam_evidence is not None and seam_evidence.status != "unchunked":
        internal = boundaries[1:-1]
        owned: dict[str, list[object]] = {target.id: [] for target in internal}
        for observation in seam_evidence.observations:
            distances = tuple(
                (
                    (
                        target.window_start_ms - observation.seam_ms
                        if observation.seam_ms < target.window_start_ms
                        else (
                            observation.seam_ms - target.window_end_ms
                            if observation.seam_ms > target.window_end_ms
                            else 0
                        )
                    ),
                    target,
                )
                for target in internal
            )
            if not distances:
                raise AuditPlanError("seam observation has no internal Canonical boundary")
            nearest_distance = min(item[0] for item in distances)
            nearest = tuple(
                target for distance, target in distances if distance == nearest_distance
            )
            if len(nearest) != 1:
                raise AuditPlanError("seam observation is equidistant from multiple boundaries")
            if nearest_distance > policy.seam_match_tolerance_ms:
                raise AuditPlanError("seam observation exceeds every Canonical boundary tolerance")
            owned[nearest[0].id].append(observation)
        seam_ownership.update({key: tuple(value) for key, value in owned.items()})
    receipt_by_span = {item.audio_span_id: item for item in retrievals}
    canonical_by_id = {item.id: item for item in transcript.tokens}
    observed_speakers_by_span = _observed_speakers_by_span(spans, recognitions)

    def span_speaker_disagreement(target: SpanAuditTarget) -> bool:
        # Span identity is intentionally a coarse anomaly trigger.  It detects
        # an out-of-set recognizer label; it is not a per-token alignment proof.
        # Mixed Canonical labels are independently required for review below.
        canonical_speakers = {
            canonical_by_id[token_id].speaker
            for token_id in target.token_ids
            if canonical_by_id[token_id].speaker is not None
        }
        observed_speakers = observed_speakers_by_span[target.span_id]
        return bool(canonical_speakers and observed_speakers - canonical_speakers)

    def boundary_speaker_conflict(
        left: SpanAuditTarget | None, right: SpanAuditTarget | None
    ) -> bool:
        if left is None or right is None:
            return False
        left_observed = observed_speakers_by_span[left.span_id]
        right_observed = observed_speakers_by_span[right.span_id]
        left_canonical = canonical_by_id[left.token_ids[-1]].speaker
        right_canonical = canonical_by_id[right.token_ids[0]].speaker
        return bool(
            (left_observed and left_observed != {left_canonical})
            or (right_observed and right_observed != {right_canonical})
        )

    span_speaker_disagreements = {
        target.id: span_speaker_disagreement(target) for target in spans
    }
    boundary_speaker_conflicts = {
        target.id: boundary_speaker_conflict(
            None if target.ordinal == 0 else spans[target.ordinal - 1],
            None if target.ordinal == len(spans) else spans[target.ordinal],
        )
        for target in boundaries
    }

    cells = tuple(
        _span_cell(
            target,
            category,
            reference_status=reference_status,
            reference_receipt=receipt_by_span.get(target.span_id),
            confidences=tuple(
                canonical_by_id[token_id].confidence for token_id in target.token_ids
            ),
            speakers=tuple(canonical_by_id[token_id].speaker for token_id in target.token_ids),
            speaker_disagreement=span_speaker_disagreements[target.id],
            low_confidence_threshold=policy.low_confidence_threshold,
            basis_hash=basis_hash,
        )
        for target in spans
        for category in policy.span_categories
    ) + tuple(
        _boundary_cell(
            target,
            category,
            left=None if target.ordinal == 0 else spans[target.ordinal - 1],
            right=None if target.ordinal == len(spans) else spans[target.ordinal],
            left_speaker=(
                None
                if target.ordinal == 0
                else canonical_by_id[spans[target.ordinal - 1].token_ids[-1]].speaker
            ),
            right_speaker=(
                None
                if target.ordinal == len(spans)
                else canonical_by_id[spans[target.ordinal].token_ids[0]].speaker
            ),
            speaker_conflict=boundary_speaker_conflicts[target.id],
            coverage_status=coverage_status,
            speech_coverage=speech_coverage,
            seam_status=seam_status,
            owned_seam_observations=seam_ownership[target.id],
            basis_hash=basis_hash,
        )
        for target in boundaries
        for category in policy.boundary_categories
    )
    payload = {
        "schema_version": 1,
        "origin": origin,
        "episode_id": transcript.episode_id,
        "generation_id": transcript.generation_id,
        "policy": policy,
        "policy_hash": policy_hash,
        "inputs": inputs,
        "basis_hash": basis_hash,
        "builder_id": AUDIT_PLAN_BUILDER_ID,
        "builder_version": AUDIT_PLAN_BUILDER_VERSION,
        "builder_code_hash": builder_code_hash,
        "span_targets": spans,
        "boundary_targets": boundaries,
        "cells": cells,
        "execution_status": "dormant_not_executed",
        "completeness_statement": ("structural_completeness_only_not_semantic_recall_proof"),
    }
    return AuditPlan(**payload, content_hash=hash_object(payload))


def assert_audit_plan(
    stored: AuditPlan,
    transcript: CanonicalTranscript,
    recognition_evidence: Sequence[RecognitionEvidence],
    policy: CorrectionAuditPolicySnapshot,
    *,
    reference_retrievals: Sequence[ReferenceRetrievalReceipt] = (),
    references_enrolled: bool,
    speech_coverage: SpeechCoverageReceipt | None = None,
    boundary_constraints: BoundaryConstraintReceipt | None = None,
    seam_evidence: RecognitionSeamEvidence | None = None,
) -> AuditPlan:
    expected = build_audit_plan(
        transcript,
        recognition_evidence,
        policy,
        reference_retrievals=reference_retrievals,
        references_enrolled=references_enrolled,
        speech_coverage=speech_coverage,
        boundary_constraints=boundary_constraints,
        seam_evidence=seam_evidence,
        origin=stored.origin,
    )
    if stored != expected:
        raise AuditPlanError("AuditPlan is not reproducible from exact immutable inputs")
    return expected


__all__ = [
    "AUDIT_PLAN_BUILDER_ID",
    "AUDIT_PLAN_BUILDER_VERSION",
    "AUDIT_SIGNAL_RULE_SET_ID",
    "AUDIT_SIGNAL_RULE_SET_VERSION",
    "AuditPlanError",
    "assert_audit_plan",
    "audit_plan_builder_code_hash",
    "audit_plan_bytes",
    "audit_plan_digest",
    "build_audit_plan",
    "correction_audit_rule_set_hash",
    "cross_span_repetition",
    "default_correction_audit_policy",
    "span_text_flags",
]
