"""Deterministic correction discovery for a complete AuditPlan.

Signals are non-authoritative review triggers.  Candidate options are retained
only when immutable Reference Evidence supplies an exact candidate term and the
query support maps to an exact Canonical token range.  No object in this module
is Audio Evidence, a Correction Proposal, or permission to mutate text.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

from shared.schemas.podcast_subtitles_v2 import (
    AuditCell,
    AuditPlan,
    BoundaryConstraintReceipt,
    CandidateGroup,
    CandidateGroupSet,
    CandidateOption,
    CandidateSignal,
    CandidateSignalCode,
    CandidateSignalEvidenceKind,
    CandidateSignalSet,
    CandidateSourceBinding,
    CandidateSourceBindingKind,
    CanonicalTranscript,
    RecognitionEvidence,
    RecognitionSeamEvidence,
    ReferenceRetrievalHit,
    ReferenceRetrievalReceipt,
    SpanAuditTarget,
    SpeechCoverageReceipt,
    boundary_constraint_receipt_hash,
    recognition_evidence_content_hash,
    speech_coverage_receipt_content_hash,
)

from .audit_plan import (
    AuditPlanError,
    assert_audit_plan,
    audit_plan_digest,
    cross_span_repetition,
)
from .hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes

CANDIDATE_SIGNAL_BUILDER_ID = "nakama-correction-candidate-signals"
CANDIDATE_SIGNAL_BUILDER_VERSION = 1
CANDIDATE_GROUP_BUILDER_ID = "nakama-correction-candidate-groups"
CANDIDATE_GROUP_BUILDER_VERSION = 1

_ARABIC_RE = re.compile(r"\d")
_HAN_NUMBER_RE = re.compile(r"[零〇一二三四五六七八九十百千萬億兩]")
_DECIMAL_RE = re.compile(r"\d+[.．]\d+")
_PERCENT_RE = re.compile(r"\d+(?:[.．]\d+)?\s*[%％]")
_RANGE_RE = re.compile(r"\d+\s*[-–—~～至]\s*\d+")
_DATE_TIME_RE = re.compile(r"\d{1,4}(?:年|月|日|[/：:]\d)")
_VERSION_RE = re.compile(r"(?:v|V)?\d+(?:\.\d+){1,}")
_UNIT_RE = re.compile(
    r"\d+\s*(?:kg|g|mg|μg|ug|km|cm|mm|mL|ml|L|個|位|名|本|篇|份|項|種|次|年|月|天)"
)
_NEGATION_RE = re.compile(r"(?:沒有|不是|不能|不會|不|沒|無|非|未|別)")
_LATIN_RE = re.compile(r"[A-Za-z]")
_ACRONYM_RE = re.compile(r"(?<![A-Za-z])[A-Z]{2,}(?![A-Za-z])")
_FILLER_RE = re.compile(r"(?:嗯|呃|額|欸|誒|啊|那個|就是)")
_SELF_REPAIR_RE = re.compile(r"(?:不是|不對|我是說|應該說|改口|更正)")


class CandidateGenerationError(ValueError):
    """Stored discovery artifacts or their exact upstream inputs drifted."""


def candidate_signal_builder_code_hash() -> str:
    return hash_file(Path(__file__))


def candidate_group_builder_code_hash() -> str:
    """Identity of the grouping builder co-located in this exact source file."""

    return hash_file(Path(__file__))


def candidate_signal_set_bytes(signal_set: CandidateSignalSet) -> bytes:
    return canonical_json_bytes(signal_set)


def candidate_signal_set_digest(signal_set: CandidateSignalSet) -> str:
    return sha256_bytes(candidate_signal_set_bytes(signal_set))


def candidate_group_set_bytes(group_set: CandidateGroupSet) -> bytes:
    return canonical_json_bytes(group_set)


def candidate_group_set_digest(group_set: CandidateGroupSet) -> str:
    return sha256_bytes(candidate_group_set_bytes(group_set))


def _cell_index(plan: AuditPlan) -> dict[tuple[str, str, str], AuditCell]:
    return {(cell.target_kind, cell.target_id, cell.category): cell for cell in plan.cells}


def _span_index(plan: AuditPlan) -> dict[str, SpanAuditTarget]:
    return {item.span_id: item for item in plan.span_targets}


def _token_text(transcript: CanonicalTranscript, start: int, end: int) -> str:
    return "".join(token.text for token in transcript.tokens[start:end])


def _source_binding(
    *,
    kind: CandidateSourceBindingKind,
    source_id: str,
    content_hash: str,
    record_ids: Iterable[str],
) -> CandidateSourceBinding:
    records = tuple(sorted(set(record_ids)))
    if not records:
        raise CandidateGenerationError("candidate signal source cannot be empty")
    return CandidateSourceBinding(
        kind=kind,
        id=source_id,
        content_hash=content_hash,
        record_ids=records,
    )


def _artifact_source(
    *,
    kind: CandidateSourceBindingKind,
    record_ids: Iterable[str],
    artifact_hashes: Iterable[str],
) -> tuple[CandidateSourceBinding, ...]:
    records = tuple(sorted(set(record_ids)))
    hashes = tuple(sorted(set(artifact_hashes)))
    if not records or not hashes:
        raise CandidateGenerationError("candidate signal source cannot be empty")
    return tuple(
        _source_binding(
            kind=kind,
            source_id=f"{kind}:{digest}",
            content_hash=digest,
            record_ids=records,
        )
        for digest in hashes
    )


def _make_signal(
    *,
    plan: AuditPlan,
    cell: AuditCell,
    span_ids: Sequence[str],
    token_ids: Sequence[str],
    token_start_index: int,
    token_end_index: int,
    observed_text: str,
    code: CandidateSignalCode,
    evidence_kind: CandidateSignalEvidenceKind,
    source_bindings: Sequence[CandidateSourceBinding],
    reference_evidence_ids: Sequence[str] = (),
    reference_support_kind: str | None = None,
    reference_query_id: str | None = None,
    reference_query_support_start: int | None = None,
    reference_query_support_end: int | None = None,
    candidate_text: str | None = None,
    detail: object,
) -> CandidateSignal:
    plan_hash = audit_plan_digest(plan)
    builder_code_hash = candidate_signal_builder_code_hash()
    payload = {
        "schema_version": 1,
        "audit_plan_hash": plan_hash,
        "target_kind": cell.target_kind,
        "target_id": cell.target_id,
        "audit_cell_id": cell.id,
        "category": cell.category,
        "span_ids": tuple(span_ids),
        "token_ids": tuple(token_ids),
        "token_start_index": token_start_index,
        "token_end_index": token_end_index,
        "observed_text": observed_text,
        "code": code,
        "authority": "review_trigger_only",
        "generator_id": CANDIDATE_SIGNAL_BUILDER_ID,
        "generator_version": CANDIDATE_SIGNAL_BUILDER_VERSION,
        "generator_code_hash": builder_code_hash,
        "rule_set_hash": plan.policy.rule_set_hash,
        "evidence_kind": evidence_kind,
        "source_bindings": tuple(
            sorted(
                set(source_bindings),
                key=lambda item: (item.kind, item.id, item.content_hash, item.record_ids),
            )
        ),
        "reference_evidence_ids": tuple(sorted(set(reference_evidence_ids))),
        "reference_support_kind": reference_support_kind,
        "reference_query_id": reference_query_id,
        "reference_query_support_start": reference_query_support_start,
        "reference_query_support_end": reference_query_support_end,
        "candidate_text": candidate_text,
        "candidate_text_hash": (
            sha256_bytes(candidate_text.encode("utf-8")) if candidate_text is not None else None
        ),
        "detail_hash": hash_object(detail),
        "is_audio_evidence": False,
        "proposal_created": False,
    }
    return CandidateSignal(id=hash_object(payload), **payload)


def _regex_signal(
    *,
    plan: AuditPlan,
    cell: AuditCell,
    target: SpanAuditTarget,
    transcript: CanonicalTranscript,
    pattern: re.Pattern[str],
    code: CandidateSignalCode,
) -> tuple[CandidateSignal, ...]:
    matches = tuple(pattern.finditer(target.observed_text))
    if not matches:
        return ()
    offsets = [0]
    for token in transcript.tokens[target.token_start_index : target.token_end_index]:
        offsets.append(offsets[-1] + len(token.text))
    offset_to_index = {
        offset: target.token_start_index + index for index, offset in enumerate(offsets)
    }
    source_bindings = _artifact_source(
        kind="canonical_transcript",
        record_ids=(transcript.generation_id,),
        artifact_hashes=(plan.inputs.canonical_transcript_hash,),
    )
    signals: list[CandidateSignal] = []
    for match in matches:
        # A regex may select inside a multi-scalar Canonical token.  Expand to
        # the smallest complete token range; never invent a sub-token identity.
        local_start = max(offset for offset in offsets if offset <= match.start())
        local_end = min(offset for offset in offsets if offset >= match.end())
        start = offset_to_index[local_start]
        end = offset_to_index[local_end]
        token_ids = tuple(token.id for token in transcript.tokens[start:end])
        signals.append(
            _make_signal(
                plan=plan,
                cell=cell,
                span_ids=(target.span_id,),
                token_ids=token_ids,
                token_start_index=start,
                token_end_index=end,
                observed_text=_token_text(transcript, start, end),
                code=code,
                evidence_kind="deterministic_text",
                source_bindings=source_bindings,
                detail={
                    "code": code,
                    "match_start": match.start(),
                    "match_end": match.end(),
                    "match_text_hash": sha256_bytes(match.group().encode("utf-8")),
                },
            )
        )
    return tuple(signals)


def _reference_token_range(
    target: SpanAuditTarget,
    transcript: CanonicalTranscript,
    receipt: ReferenceRetrievalReceipt,
    hit: ReferenceRetrievalHit,
) -> tuple[int, int] | None:
    start = max(hit.query_support_start, receipt.context.anchor_query_start)
    end = min(hit.query_support_end, receipt.context.anchor_query_end)
    if end <= start:
        return None
    local_start = start - receipt.context.anchor_query_start
    local_end = end - receipt.context.anchor_query_start
    offsets = [0]
    for token in transcript.tokens[target.token_start_index : target.token_end_index]:
        offsets.append(offsets[-1] + len(token.text))
    if local_start not in offsets or local_end not in offsets:
        return None
    return (
        target.token_start_index + offsets.index(local_start),
        target.token_start_index + offsets.index(local_end),
    )


def _reference_signals(
    *,
    plan: AuditPlan,
    target: SpanAuditTarget,
    transcript: CanonicalTranscript,
    receipt: ReferenceRetrievalReceipt | None,
) -> tuple[CandidateSignal, ...]:
    cell = _cell_index(plan)[("span", target.id, "reference_name_term")]
    if plan.inputs.reference_status == "not_enrolled":
        return ()
    if receipt is None:
        code: CandidateSignalCode = "reference_retrieval_incomplete"
        source_bindings = _artifact_source(
            kind="reference_retrieval_receipt_set",
            record_ids=(target.span_id,),
            artifact_hashes=(plan.inputs.reference_retrieval_receipt_set_hash,),
        )
        return (
            _make_signal(
                plan=plan,
                cell=cell,
                span_ids=(target.span_id,),
                token_ids=target.token_ids,
                token_start_index=target.token_start_index,
                token_end_index=target.token_end_index,
                observed_text=target.observed_text,
                code=code,
                evidence_kind="reference_evidence",
                source_bindings=source_bindings,
                detail={"reference_status": plan.inputs.reference_status},
            ),
        )
    receipt_hash = hash_object(receipt)
    if receipt.status == "failed":
        source_bindings = _artifact_source(
            kind="reference_retrieval_receipt",
            record_ids=(receipt.query_id,),
            artifact_hashes=(receipt_hash,),
        )
        return (
            _make_signal(
                plan=plan,
                cell=cell,
                span_ids=(target.span_id,),
                token_ids=target.token_ids,
                token_start_index=target.token_start_index,
                token_end_index=target.token_end_index,
                observed_text=target.observed_text,
                code="reference_retrieval_failed",
                evidence_kind="reference_evidence",
                source_bindings=source_bindings,
                detail={
                    "failure_reason_hash": sha256_bytes(receipt.failure_reason.encode("utf-8"))
                },
            ),
        )
    evidence_by_id = {item.id: item for item in receipt.evidence}
    signals: list[CandidateSignal] = []
    for hit in receipt.hits:
        if not (
            hit.query_support_start < receipt.context.anchor_query_end
            and hit.query_support_end > receipt.context.anchor_query_start
        ):
            raise CandidateGenerationError(
                "Reference hit support does not overlap its exact anchor"
            )
        evidence = evidence_by_id[hit.evidence_id]
        token_range = _reference_token_range(target, transcript, receipt, hit)
        if token_range is None:
            token_start, token_end = target.token_start_index, target.token_end_index
        else:
            token_start, token_end = token_range
        candidate_text: str | None = None
        if hit.support_kind == "candidate_term_exact":
            code = "reference_candidate_term_exact"
            if hit.candidate_term_index is None:
                raise CandidateGenerationError("candidate-term hit lost its candidate index")
            # The v2 ReviewIssue contract has no exact token range for a
            # candidate.  A query-support range (including the Adapter's whole
            # anchor fallback) is retrieval provenance, never a replacement
            # target.  Until a later typed discovery disposition supplies an
            # exact range, retain the term only in the detail hash and require
            # discovery; do not create CandidateOption.
            term = receipt.candidate_terms[hit.candidate_term_index]
        elif hit.support_kind in {"phonetic_exact", "phonetic_near"}:
            code = "reference_phonetic_support_without_exact_candidate"
        else:
            code = "reference_lexical_support_without_exact_candidate"
        source_bindings = (
            _source_binding(
                kind="reference_excerpt",
                source_id=f"reference-excerpt:{evidence.id}",
                content_hash=evidence.excerpt_hash,
                record_ids=(evidence.id,),
            ),
            _source_binding(
                kind="reference_retrieval_receipt",
                source_id=f"reference-retrieval:{receipt.query_id}",
                content_hash=receipt_hash,
                record_ids=(receipt.query_id, hit.evidence_id),
            ),
            _source_binding(
                kind="reference_source_artifact",
                source_id=f"reference-source:{evidence.artifact.source_id}",
                content_hash=evidence.artifact.digest.sha256,
                record_ids=(evidence.artifact.source_id, evidence.id),
            ),
        )
        signals.append(
            _make_signal(
                plan=plan,
                cell=cell,
                span_ids=(target.span_id,),
                token_ids=tuple(token.id for token in transcript.tokens[token_start:token_end]),
                token_start_index=token_start,
                token_end_index=token_end,
                observed_text=_token_text(transcript, token_start, token_end),
                code=code,
                evidence_kind="reference_evidence",
                source_bindings=source_bindings,
                reference_evidence_ids=(hit.evidence_id,),
                reference_support_kind=hit.support_kind,
                reference_query_id=receipt.query_id,
                reference_query_support_start=hit.query_support_start,
                reference_query_support_end=hit.query_support_end,
                candidate_text=candidate_text,
                detail={
                    "rank": hit.rank,
                    "relevance": hit.relevance,
                    "candidate_term_index": hit.candidate_term_index,
                    "candidate_term_hash": (
                        sha256_bytes(term.encode("utf-8"))
                        if hit.support_kind == "candidate_term_exact"
                        else None
                    ),
                    "exact_token_range": token_range,
                },
            )
        )
    return tuple(signals)


def _recognition_metadata_signals(
    *,
    plan: AuditPlan,
    target: SpanAuditTarget,
    transcript: CanonicalTranscript,
    recognitions: Sequence[RecognitionEvidence],
) -> tuple[CandidateSignal, ...]:
    signals: list[CandidateSignal] = []
    canonical = transcript.tokens[target.token_start_index : target.token_end_index]
    recognition_bindings = tuple(
        sorted(
            (
                binding
                for item in recognitions
                for binding in (
                    _source_binding(
                        kind="recognition_evidence",
                        source_id=f"recognition-evidence:{item.invocation_id}",
                        content_hash=recognition_evidence_content_hash(item),
                        record_ids=(item.invocation_id,),
                    ),
                    _source_binding(
                        kind="recognition_raw_artifact",
                        source_id=f"recognition-raw:{item.raw_output_hash}",
                        content_hash=item.raw_output_hash,
                        record_ids=(item.invocation_id,),
                    ),
                )
            ),
            key=lambda binding: (
                binding.kind,
                binding.id,
                binding.content_hash,
                binding.record_ids,
            ),
        )
    )
    confidence_cell = _cell_index(plan)[("span", target.id, "confidence_integrity")]
    for offset, token in enumerate(canonical, start=target.token_start_index):
        if token.confidence is None:
            code: CandidateSignalCode = "recognition_confidence_missing"
        elif token.confidence < plan.policy.low_confidence_threshold:
            code = "recognition_low_confidence"
        else:
            continue
        signals.append(
            _make_signal(
                plan=plan,
                cell=confidence_cell,
                span_ids=(target.span_id,),
                token_ids=(token.id,),
                token_start_index=offset,
                token_end_index=offset + 1,
                observed_text=token.text,
                code=code,
                evidence_kind="recognition_metadata",
                source_bindings=recognition_bindings,
                detail={"confidence": token.confidence},
            )
        )
    speaker_cell = _cell_index(plan)[("span", target.id, "speaker_identity")]
    missing = tuple(token for token in canonical if token.speaker is None)
    if missing:
        for token in missing:
            index = next(
                index
                for index in range(target.token_start_index, target.token_end_index)
                if transcript.tokens[index].id == token.id
            )
            signals.append(
                _make_signal(
                    plan=plan,
                    cell=speaker_cell,
                    span_ids=(target.span_id,),
                    token_ids=(token.id,),
                    token_start_index=index,
                    token_end_index=index + 1,
                    observed_text=token.text,
                    code="speaker_identity_missing",
                    evidence_kind="speaker_metadata",
                    source_bindings=_artifact_source(
                        kind="canonical_transcript",
                        record_ids=(token.id,),
                        artifact_hashes=(plan.inputs.canonical_transcript_hash,),
                    ),
                    detail={"speaker": None},
                )
            )
    known = {token.speaker for token in canonical if token.speaker is not None}
    if len(known) > 1:
        signals.append(
            _make_signal(
                plan=plan,
                cell=speaker_cell,
                span_ids=(target.span_id,),
                token_ids=target.token_ids,
                token_start_index=target.token_start_index,
                token_end_index=target.token_end_index,
                observed_text=target.observed_text,
                code="speaker_identity_mixed",
                evidence_kind="speaker_metadata",
                source_bindings=_artifact_source(
                    kind="canonical_transcript",
                    record_ids=tuple(token.id for token in canonical),
                    artifact_hashes=(plan.inputs.canonical_transcript_hash,),
                ),
                detail={"speaker_hashes": tuple(sorted(hash_object(item) for item in known))},
            )
        )
    observed = {
        token.speaker
        for evidence in recognitions
        for token in evidence.tokens
        if token.speaker is not None
        and token.start_ms < target.end_ms
        and token.end_ms > target.start_ms
    }
    if known and observed - known:
        signals.append(
            _make_signal(
                plan=plan,
                cell=speaker_cell,
                span_ids=(target.span_id,),
                token_ids=target.token_ids,
                token_start_index=target.token_start_index,
                token_end_index=target.token_end_index,
                observed_text=target.observed_text,
                code="speaker_identity_disagreement",
                evidence_kind="speaker_metadata",
                source_bindings=recognition_bindings,
                detail={
                    "canonical_speaker_hashes": tuple(sorted(hash_object(item) for item in known)),
                    "observed_speaker_hashes": tuple(
                        sorted(hash_object(item) for item in observed)
                    ),
                },
            )
        )
    return tuple(signals)


def _coverage_span_signals(
    *,
    plan: AuditPlan,
    target: SpanAuditTarget,
    transcript: CanonicalTranscript,
    coverage: SpeechCoverageReceipt | None,
) -> tuple[CandidateSignal, ...]:
    if coverage is None or coverage.status != "completed":
        return ()
    cell = _cell_index(plan)[("span", target.id, "lexical_fidelity")]
    receipt_hash = speech_coverage_receipt_content_hash(coverage)
    signals: list[CandidateSignal] = []
    for interval in coverage.uncovered_intervals:
        if interval.start_ms >= target.end_ms or interval.end_ms <= target.start_ms:
            continue
        signals.append(
            _make_signal(
                plan=plan,
                cell=cell,
                span_ids=(target.span_id,),
                token_ids=target.token_ids,
                token_start_index=target.token_start_index,
                token_end_index=target.token_end_index,
                observed_text=target.observed_text,
                code="speech_coverage_uncovered_inside_span",
                evidence_kind="speech_coverage",
                source_bindings=tuple(
                    sorted(
                        (
                            _source_binding(
                                kind="speech_coverage_raw_artifact",
                                source_id=f"speech-coverage-raw:{coverage.raw_output_hash}",
                                content_hash=coverage.raw_output_hash,
                                record_ids=(interval.id,),
                            ),
                            _source_binding(
                                kind="speech_coverage_receipt",
                                source_id=f"speech-coverage:{coverage.invocation_id}",
                                content_hash=receipt_hash,
                                record_ids=(coverage.invocation_id, interval.id),
                            ),
                        ),
                        key=lambda binding: (binding.kind, binding.id),
                    )
                ),
                detail={
                    "uncovered_start_ms": interval.start_ms,
                    "uncovered_end_ms": interval.end_ms,
                },
            )
        )
    return tuple(signals)


def _seam_ownership(
    plan: AuditPlan, seam_evidence: RecognitionSeamEvidence | None
) -> dict[str, tuple[object, ...]]:
    ownership: dict[str, list[object]] = {target.id: [] for target in plan.boundary_targets}
    if seam_evidence is None or seam_evidence.status == "unchunked":
        return {key: tuple(value) for key, value in ownership.items()}
    internal = plan.boundary_targets[1:-1]
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
            raise CandidateGenerationError("seam observation has no internal boundary")
        nearest_distance = min(item[0] for item in distances)
        nearest = tuple(target for distance, target in distances if distance == nearest_distance)
        if len(nearest) != 1 or nearest_distance > plan.policy.seam_match_tolerance_ms:
            raise CandidateGenerationError("seam ownership cannot be reproduced uniquely")
        ownership[nearest[0].id].append(observation)
    return {key: tuple(value) for key, value in ownership.items()}


def _boundary_signals(
    *,
    plan: AuditPlan,
    transcript: CanonicalTranscript,
    coverage: SpeechCoverageReceipt | None,
    boundary_constraints: BoundaryConstraintReceipt | None,
    seam_evidence: RecognitionSeamEvidence | None,
) -> tuple[CandidateSignal, ...]:
    positions = {token.id: index for index, token in enumerate(transcript.tokens)}
    spans = _span_index(plan)
    seam_owned = _seam_ownership(plan, seam_evidence)
    signals: list[CandidateSignal] = []
    for target in plan.boundary_targets:
        left = spans[target.left_span_id] if target.left_span_id is not None else None
        right = spans[target.right_span_id] if target.right_span_id is not None else None
        if left is None:
            if right is None:
                raise CandidateGenerationError("boundary target lost both adjacent spans")
            start, end = right.token_start_index, right.token_start_index + 1
        elif right is None:
            start, end = left.token_end_index - 1, left.token_end_index
        else:
            start, end = left.token_end_index - 1, right.token_start_index + 1
        token_ids = tuple(token.id for token in transcript.tokens[start:end])
        span_ids = tuple(item.span_id for item in (left, right) if item is not None)
        observed_text = _token_text(transcript, start, end)

        coverage_cell = _cell_index(plan)[("boundary", target.id, "speech_coverage")]
        if coverage_cell.applicability == "unavailable":
            hash_value = (
                plan.inputs.speech_coverage_receipt_hash
                or plan.inputs.recognition_evidence_set_hash
            )
            signals.append(
                _make_signal(
                    plan=plan,
                    cell=coverage_cell,
                    span_ids=span_ids,
                    token_ids=token_ids,
                    token_start_index=start,
                    token_end_index=end,
                    observed_text=observed_text,
                    code="speech_coverage_unavailable",
                    evidence_kind="speech_coverage",
                    source_bindings=_artifact_source(
                        kind=(
                            "speech_coverage_receipt"
                            if plan.inputs.speech_coverage_receipt_hash is not None
                            else "recognition_evidence_set"
                        ),
                        record_ids=(target.id,),
                        artifact_hashes=(hash_value,),
                    ),
                    detail={"coverage_status": plan.inputs.speech_coverage_status},
                )
            )
        elif coverage is not None:
            coverage_hash = speech_coverage_receipt_content_hash(coverage)
            for interval in coverage.uncovered_intervals:
                overlaps = (
                    interval.start_ms <= target.window_start_ms < interval.end_ms
                    if target.window_start_ms == target.window_end_ms
                    else interval.start_ms < target.window_end_ms
                    and interval.end_ms > target.window_start_ms
                )
                if not overlaps:
                    continue
                signals.append(
                    _make_signal(
                        plan=plan,
                        cell=coverage_cell,
                        span_ids=span_ids,
                        token_ids=token_ids,
                        token_start_index=start,
                        token_end_index=end,
                        observed_text=observed_text,
                        code="speech_coverage_uncovered_at_boundary",
                        evidence_kind="speech_coverage",
                        source_bindings=tuple(
                            sorted(
                                (
                                    _source_binding(
                                        kind="speech_coverage_raw_artifact",
                                        source_id=(
                                            f"speech-coverage-raw:{coverage.raw_output_hash}"
                                        ),
                                        content_hash=coverage.raw_output_hash,
                                        record_ids=(interval.id,),
                                    ),
                                    _source_binding(
                                        kind="speech_coverage_receipt",
                                        source_id=(f"speech-coverage:{coverage.invocation_id}"),
                                        content_hash=coverage_hash,
                                        record_ids=(coverage.invocation_id, interval.id),
                                    ),
                                ),
                                key=lambda binding: (binding.kind, binding.id),
                            )
                        ),
                        detail={
                            "uncovered_start_ms": interval.start_ms,
                            "uncovered_end_ms": interval.end_ms,
                        },
                    )
                )

        if left is None or right is None:
            continue

        semantic_cell = _cell_index(plan)[("boundary", target.id, "cross_span_fidelity")]
        if boundary_constraints is not None:
            edge = next(
                (
                    item
                    for item in boundary_constraints.edges
                    if item.left_token_id == target.left_token_id
                    and item.right_token_id == target.right_token_id
                ),
                None,
            )
            if edge is not None and (edge.material_uncertainty or edge.cue_relation == "forbidden"):
                code: CandidateSignalCode = (
                    "semantic_boundary_uncertain"
                    if edge.material_uncertainty
                    else "semantic_boundary_forbidden"
                )
                boundary_hash = boundary_constraint_receipt_hash(boundary_constraints)
                signals.append(
                    _make_signal(
                        plan=plan,
                        cell=semantic_cell,
                        span_ids=span_ids,
                        token_ids=token_ids,
                        token_start_index=start,
                        token_end_index=end,
                        observed_text=observed_text,
                        code=code,
                        evidence_kind="semantic_constraint",
                        source_bindings=_artifact_source(
                            kind="boundary_constraint_receipt",
                            record_ids=(f"boundary-edge:{edge.edge_index}",),
                            artifact_hashes=(boundary_hash,),
                        ),
                        detail={
                            "cue_relation": edge.cue_relation,
                            "line_relation": edge.line_relation,
                            "reason_codes": edge.reason_codes,
                        },
                    )
                )

        seam_cell = _cell_index(plan)[("boundary", target.id, "chunk_seam")]
        if seam_cell.applicability == "unavailable":
            hashes = (
                (plan.inputs.seam_evidence_hash,)
                if plan.inputs.seam_evidence_hash is not None
                else (plan.inputs.recognition_evidence_set_hash,)
            )
            signals.append(
                _make_signal(
                    plan=plan,
                    cell=seam_cell,
                    span_ids=span_ids,
                    token_ids=token_ids,
                    token_start_index=start,
                    token_end_index=end,
                    observed_text=observed_text,
                    code="chunk_seam_unavailable",
                    evidence_kind="seam_observation",
                    source_bindings=_artifact_source(
                        kind=(
                            "seam_evidence"
                            if plan.inputs.seam_evidence_hash is not None
                            else "recognition_evidence_set"
                        ),
                        record_ids=(target.id,),
                        artifact_hashes=hashes,
                    ),
                    detail={"seam_status": plan.inputs.seam_status},
                )
            )
        for observation in seam_owned[target.id]:
            if observation.status == "matched":
                continue
            signals.append(
                _make_signal(
                    plan=plan,
                    cell=seam_cell,
                    span_ids=span_ids,
                    token_ids=token_ids,
                    token_start_index=start,
                    token_end_index=end,
                    observed_text=observed_text,
                    code=(
                        "chunk_seam_duplicate"
                        if observation.status == "duplicate"
                        else "chunk_seam_conflict"
                    ),
                    evidence_kind="seam_observation",
                    source_bindings=tuple(
                        sorted(
                            (
                                _source_binding(
                                    kind="recognition_raw_artifact",
                                    source_id=(f"recognition-raw:{observation.raw_artifact_hash}"),
                                    content_hash=observation.raw_artifact_hash,
                                    record_ids=(observation.id,),
                                ),
                                _source_binding(
                                    kind="seam_observation",
                                    source_id=f"seam-observation:{observation.id}",
                                    content_hash=observation.observation_hash,
                                    record_ids=(observation.id,),
                                ),
                                _source_binding(
                                    kind="seam_evidence",
                                    source_id="seam-evidence:wrapper",
                                    content_hash=plan.inputs.seam_evidence_hash,
                                    record_ids=(observation.id,),
                                ),
                            ),
                            key=lambda binding: (binding.kind, binding.id),
                        )
                    ),
                    detail={
                        "left_chunk_id": observation.left_chunk_id,
                        "right_chunk_id": observation.right_chunk_id,
                        "seam_ms": observation.seam_ms,
                        "status": observation.status,
                    },
                )
            )

        speaker_cell = _cell_index(plan)[("boundary", target.id, "speaker_transition")]
        left_speaker = transcript.tokens[positions[target.left_token_id]].speaker
        right_speaker = transcript.tokens[positions[target.right_token_id]].speaker
        if speaker_cell.applicability == "required":
            code = (
                "speaker_change_detected"
                if left_speaker is not None
                and right_speaker is not None
                and left_speaker != right_speaker
                else "speaker_transition_ambiguous"
            )
            signals.append(
                _make_signal(
                    plan=plan,
                    cell=speaker_cell,
                    span_ids=span_ids,
                    token_ids=token_ids,
                    token_start_index=start,
                    token_end_index=end,
                    observed_text=observed_text,
                    code=code,
                    evidence_kind="speaker_metadata",
                    source_bindings=_artifact_source(
                        kind="canonical_transcript",
                        record_ids=token_ids,
                        artifact_hashes=(plan.inputs.canonical_transcript_hash,),
                    ),
                    detail={
                        "left_speaker_hash": (
                            hash_object(left_speaker) if left_speaker is not None else None
                        ),
                        "right_speaker_hash": (
                            hash_object(right_speaker) if right_speaker is not None else None
                        ),
                    },
                )
            )

        repetition_cell = _cell_index(plan)[("boundary", target.id, "repetition_self_repair")]
        if cross_span_repetition(left.observed_text, right.observed_text):
            signals.append(
                _make_signal(
                    plan=plan,
                    cell=repetition_cell,
                    span_ids=span_ids,
                    token_ids=token_ids,
                    token_start_index=start,
                    token_end_index=end,
                    observed_text=observed_text,
                    code=(
                        "cross_span_self_repair_detected"
                        if _SELF_REPAIR_RE.search(observed_text)
                        else "cross_span_repetition_detected"
                    ),
                    evidence_kind="deterministic_text",
                    source_bindings=_artifact_source(
                        kind="canonical_transcript",
                        record_ids=token_ids,
                        artifact_hashes=(plan.inputs.canonical_transcript_hash,),
                    ),
                    detail={
                        "left_text_hash": left.observed_text_hash,
                        "right_text_hash": right.observed_text_hash,
                    },
                )
            )
    return tuple(signals)


def _assert_signal_cross_bindings(plan: AuditPlan, signal: CandidateSignal) -> None:
    cells = _cell_index(plan)
    key = (signal.target_kind, signal.target_id, signal.category)
    cell = cells.get(key)
    if cell is None or cell.id != signal.audit_cell_id:
        raise CandidateGenerationError("CandidateSignal does not bind its exact AuditCell")
    if signal.audit_plan_hash != audit_plan_digest(plan):
        raise CandidateGenerationError("CandidateSignal audit_plan_hash mismatch")
    if signal.rule_set_hash != plan.policy.rule_set_hash:
        raise CandidateGenerationError("CandidateSignal rule-set identity mismatch")
    if (
        signal.generator_id != CANDIDATE_SIGNAL_BUILDER_ID
        or signal.generator_version != CANDIDATE_SIGNAL_BUILDER_VERSION
        or signal.generator_code_hash != candidate_signal_builder_code_hash()
    ):
        raise CandidateGenerationError("CandidateSignal generator identity mismatch")
    if signal.target_kind == "span":
        target = next(item for item in plan.span_targets if item.id == signal.target_id)
        if signal.span_ids != (target.span_id,):
            raise CandidateGenerationError("span signal carries the wrong Canonical span")
        if not (
            target.token_start_index
            <= signal.token_start_index
            < signal.token_end_index
            <= target.token_end_index
        ):
            raise CandidateGenerationError("span signal range escapes its AuditTarget")
    else:
        target = next(item for item in plan.boundary_targets if item.id == signal.target_id)
        expected_span_ids = tuple(
            item for item in (target.left_span_id, target.right_span_id) if item is not None
        )
        if signal.span_ids != expected_span_ids:
            raise CandidateGenerationError("boundary signal carries the wrong Canonical spans")


def derive_candidate_signal_set(
    plan: AuditPlan,
    transcript: CanonicalTranscript,
    recognition_evidence: Sequence[RecognitionEvidence],
    *,
    reference_retrievals: Sequence[ReferenceRetrievalReceipt] = (),
    references_enrolled: bool,
    speech_coverage: SpeechCoverageReceipt | None = None,
    boundary_constraints: BoundaryConstraintReceipt | None = None,
    seam_evidence: RecognitionSeamEvidence | None = None,
) -> CandidateSignalSet:
    """Rebuild the complete deterministic discovery set from exact inputs."""

    try:
        assert_audit_plan(
            plan,
            transcript,
            recognition_evidence,
            plan.policy,
            reference_retrievals=reference_retrievals,
            references_enrolled=references_enrolled,
            speech_coverage=speech_coverage,
            boundary_constraints=boundary_constraints,
            seam_evidence=seam_evidence,
        )
    except AuditPlanError as exc:
        raise CandidateGenerationError(str(exc)) from exc

    retrieval_by_span = {item.audio_span_id: item for item in reference_retrievals}
    cells = _cell_index(plan)
    signals: list[CandidateSignal] = []
    regex_rules: tuple[tuple[str, re.Pattern[str], CandidateSignalCode], ...] = (
        ("numeric_expression", _ARABIC_RE, "numeric_arabic_digits_detected"),
        ("numeric_expression", _HAN_NUMBER_RE, "numeric_han_number_detected"),
        ("numeric_expression", _DECIMAL_RE, "numeric_decimal_detected"),
        ("numeric_expression", _PERCENT_RE, "numeric_percent_detected"),
        ("numeric_expression", _RANGE_RE, "numeric_range_detected"),
        ("numeric_expression", _DATE_TIME_RE, "numeric_date_time_detected"),
        ("numeric_expression", _VERSION_RE, "numeric_version_detected"),
        ("numeric_expression", _UNIT_RE, "numeric_unit_or_classifier_detected"),
        ("negation_polarity", _NEGATION_RE, "negation_detected"),
        ("latin_code_switch", _LATIN_RE, "latin_code_switch_detected"),
        ("acronym_initialism", _ACRONYM_RE, "acronym_initialism_detected"),
        ("repetition_disfluency", _FILLER_RE, "filler_detected"),
        ("repetition_disfluency", _SELF_REPAIR_RE, "self_repair_detected"),
    )
    for target in plan.span_targets:
        for category, pattern, code in regex_rules:
            cell = cells[("span", target.id, category)]
            if cell.applicability != "required":
                continue
            signals.extend(
                _regex_signal(
                    plan=plan,
                    cell=cell,
                    target=target,
                    transcript=transcript,
                    pattern=pattern,
                    code=code,
                )
            )
        # Exact repeated Canonical token text is a non-destructive trigger.
        canonical = transcript.tokens[target.token_start_index : target.token_end_index]
        for index in range(1, len(canonical)):
            if canonical[index - 1].text != canonical[index].text:
                continue
            cell = cells[("span", target.id, "repetition_disfluency")]
            source_bindings = _artifact_source(
                kind="canonical_transcript",
                record_ids=(canonical[index - 1].id, canonical[index].id),
                artifact_hashes=(plan.inputs.canonical_transcript_hash,),
            )
            start = target.token_start_index + index - 1
            signals.append(
                _make_signal(
                    plan=plan,
                    cell=cell,
                    span_ids=(target.span_id,),
                    token_ids=(canonical[index - 1].id, canonical[index].id),
                    token_start_index=start,
                    token_end_index=start + 2,
                    observed_text=canonical[index - 1].text + canonical[index].text,
                    code="repetition_detected",
                    evidence_kind="deterministic_text",
                    source_bindings=source_bindings,
                    detail={"repeated_token_text_hash": hash_object(canonical[index].text)},
                )
            )
        signals.extend(
            _reference_signals(
                plan=plan,
                target=target,
                transcript=transcript,
                receipt=retrieval_by_span.get(target.span_id),
            )
        )
        signals.extend(
            _recognition_metadata_signals(
                plan=plan,
                target=target,
                transcript=transcript,
                recognitions=recognition_evidence,
            )
        )
        signals.extend(
            _coverage_span_signals(
                plan=plan,
                target=target,
                transcript=transcript,
                coverage=speech_coverage,
            )
        )
    signals.extend(
        _boundary_signals(
            plan=plan,
            transcript=transcript,
            coverage=speech_coverage,
            boundary_constraints=boundary_constraints,
            seam_evidence=seam_evidence,
        )
    )
    ordered = tuple(sorted(signals, key=lambda item: item.id))
    if len({item.id for item in ordered}) != len(ordered):
        raise CandidateGenerationError("deterministic signal derivation produced duplicates")
    for signal in ordered:
        _assert_signal_cross_bindings(plan, signal)
        if signal.observed_text != _token_text(
            transcript, signal.token_start_index, signal.token_end_index
        ):
            raise CandidateGenerationError(
                "CandidateSignal observed_text is not exact Canonical text"
            )
        if signal.token_ids != tuple(
            token.id
            for token in transcript.tokens[signal.token_start_index : signal.token_end_index]
        ):
            raise CandidateGenerationError(
                "CandidateSignal token range is not exact Canonical order"
            )
    payload = {
        "schema_version": 1,
        "audit_plan_hash": audit_plan_digest(plan),
        "audit_plan_content_hash": plan.content_hash,
        "audit_input_hash": plan.inputs.content_hash,
        "builder_id": CANDIDATE_SIGNAL_BUILDER_ID,
        "builder_version": CANDIDATE_SIGNAL_BUILDER_VERSION,
        "builder_code_hash": candidate_signal_builder_code_hash(),
        "rule_set_hash": plan.policy.rule_set_hash,
        "authority": "review_trigger_only",
        "derivation_status": "complete_recomputation",
        "recall_statement": "signals_are_not_semantic_recall_proof",
        "signals": ordered,
    }
    return CandidateSignalSet(**payload, content_hash=hash_object(payload))


def assert_candidate_signal_set(
    stored: CandidateSignalSet,
    plan: AuditPlan,
    transcript: CanonicalTranscript,
    recognition_evidence: Sequence[RecognitionEvidence],
    *,
    reference_retrievals: Sequence[ReferenceRetrievalReceipt] = (),
    references_enrolled: bool,
    speech_coverage: SpeechCoverageReceipt | None = None,
    boundary_constraints: BoundaryConstraintReceipt | None = None,
    seam_evidence: RecognitionSeamEvidence | None = None,
) -> CandidateSignalSet:
    expected = derive_candidate_signal_set(
        plan,
        transcript,
        recognition_evidence,
        reference_retrievals=reference_retrievals,
        references_enrolled=references_enrolled,
        speech_coverage=speech_coverage,
        boundary_constraints=boundary_constraints,
        seam_evidence=seam_evidence,
    )
    if stored != expected:
        raise CandidateGenerationError(
            "CandidateSignalSet is not reproducible from exact immutable inputs"
        )
    return expected


def _candidate_options(
    plan: AuditPlan,
    signals: Sequence[CandidateSignal],
    transcript: CanonicalTranscript,
) -> tuple[CandidateOption, ...]:
    grouped: dict[tuple[int, int, str], list[CandidateSignal]] = defaultdict(list)
    for signal in signals:
        if signal.candidate_text is not None:
            grouped[
                (
                    signal.token_start_index,
                    signal.token_end_index,
                    signal.candidate_text,
                )
            ].append(signal)
    options: list[CandidateOption] = []
    for (start, end, candidate_text), sources in grouped.items():
        signal_ids = tuple(sorted(item.id for item in sources))
        span_order = {item.span_id: item.ordinal for item in plan.span_targets}
        span_ids = tuple(
            sorted(
                {span_id for item in sources for span_id in item.span_ids},
                key=span_order.__getitem__,
            )
        )
        reference_ids = tuple(
            sorted({evidence_id for item in sources for evidence_id in item.reference_evidence_ids})
        )
        payload = {
            "schema_version": 1,
            "token_start_index": start,
            "token_end_index": end,
            "target_span_ids": span_ids,
            "observed_text": _token_text(transcript, start, end),
            "candidate_text": candidate_text,
            "candidate_text_hash": sha256_bytes(candidate_text.encode("utf-8")),
            "signal_ids": signal_ids,
            "reference_evidence_ids": reference_ids,
            "authority": "review_trigger_only",
            "requires_audio_confirmation": True,
            "is_audio_evidence": False,
            "proposal_created": False,
        }
        options.append(CandidateOption(id=hash_object(payload), **payload))
    return tuple(sorted(options, key=lambda item: item.id))


def derive_candidate_group_set(
    plan: AuditPlan,
    signal_set: CandidateSignalSet,
    transcript: CanonicalTranscript,
) -> CandidateGroupSet:
    """Partition every signal into one overlap component without dropping it."""

    if signal_set.audit_plan_hash != audit_plan_digest(plan):
        raise CandidateGenerationError("CandidateSignalSet belongs to another AuditPlan")
    if signal_set.audit_plan_content_hash != plan.content_hash:
        raise CandidateGenerationError("CandidateSignalSet AuditPlan content drift")
    if signal_set.audit_input_hash != plan.inputs.content_hash:
        raise CandidateGenerationError("CandidateSignalSet input binding drift")
    if signal_set.rule_set_hash != plan.policy.rule_set_hash:
        raise CandidateGenerationError("CandidateSignalSet rule-set identity drift")
    if (
        signal_set.builder_id != CANDIDATE_SIGNAL_BUILDER_ID
        or signal_set.builder_version != CANDIDATE_SIGNAL_BUILDER_VERSION
        or signal_set.builder_code_hash != candidate_signal_builder_code_hash()
    ):
        raise CandidateGenerationError("CandidateSignalSet builder identity drift")
    for signal in signal_set.signals:
        _assert_signal_cross_bindings(plan, signal)
    options = _candidate_options(plan, signal_set.signals, transcript)
    option_by_signal: dict[str, list[CandidateOption]] = defaultdict(list)
    for option in options:
        for signal_id in option.signal_ids:
            option_by_signal[signal_id].append(option)

    by_range = sorted(
        signal_set.signals,
        key=lambda item: (item.token_start_index, item.token_end_index, item.id),
    )
    components: list[list[CandidateSignal]] = []
    for signal in by_range:
        if not components:
            components.append([signal])
            continue
        current_end = max(item.token_end_index for item in components[-1])
        if signal.token_start_index < current_end:
            components[-1].append(signal)
        else:
            components.append([signal])

    groups: list[CandidateGroup] = []
    span_order = {item.span_id: item.ordinal for item in plan.span_targets}
    for component_index, component in enumerate(components):
        signal_ids = tuple(sorted(item.id for item in component))
        start = min(item.token_start_index for item in component)
        end = max(item.token_end_index for item in component)
        component_options = tuple(
            sorted(
                {
                    option.id: option
                    for signal in component
                    for option in option_by_signal[signal.id]
                }.values(),
                key=lambda item: item.id,
            )
        )
        span_ids = tuple(
            sorted(
                {span_id for item in component for span_id in item.span_ids},
                key=span_order.__getitem__,
            )
        )
        overlap_id = hash_object(
            {
                "token_start_index": start,
                "token_end_index": end,
                "signal_ids": signal_ids,
            }
        )
        option_signal_ids = {
            signal_id for option in component_options for signal_id in option.signal_ids
        }
        payload = {
            "schema_version": 1,
            "component_index": component_index,
            "overlap_component_id": overlap_id,
            "token_start_index": start,
            "token_end_index": end,
            "target_span_ids": span_ids,
            "observed_text": _token_text(transcript, start, end),
            "signal_ids": signal_ids,
            "options": component_options,
            "discovery_required": bool(set(signal_ids) - option_signal_ids),
        }
        groups.append(CandidateGroup(id=hash_object(payload), **payload))
    payload = {
        "schema_version": 1,
        "signal_set_hash": candidate_signal_set_digest(signal_set),
        "signal_set_content_hash": signal_set.content_hash,
        "audit_plan_hash": audit_plan_digest(plan),
        "audit_plan_content_hash": plan.content_hash,
        "audit_input_hash": plan.inputs.content_hash,
        "grouping_algorithm": "overlap-connected-components-v1",
        "builder_id": CANDIDATE_GROUP_BUILDER_ID,
        "builder_version": CANDIDATE_GROUP_BUILDER_VERSION,
        "builder_code_hash": candidate_group_builder_code_hash(),
        "signal_ids": tuple(sorted(item.id for item in signal_set.signals)),
        "groups": tuple(groups),
        "proposal_status": "dormant_no_proposals_created",
    }
    return CandidateGroupSet(**payload, content_hash=hash_object(payload))


def assert_candidate_group_set(
    stored: CandidateGroupSet,
    plan: AuditPlan,
    signal_set: CandidateSignalSet,
    transcript: CanonicalTranscript,
) -> CandidateGroupSet:
    expected = derive_candidate_group_set(plan, signal_set, transcript)
    if stored != expected:
        raise CandidateGenerationError(
            "CandidateGroupSet is not reproducible from exact signals and Canonical truth"
        )
    return expected


__all__ = [
    "CANDIDATE_GROUP_BUILDER_ID",
    "CANDIDATE_GROUP_BUILDER_VERSION",
    "CANDIDATE_SIGNAL_BUILDER_ID",
    "CANDIDATE_SIGNAL_BUILDER_VERSION",
    "CandidateGenerationError",
    "assert_candidate_group_set",
    "assert_candidate_signal_set",
    "candidate_group_set_bytes",
    "candidate_group_set_digest",
    "candidate_group_builder_code_hash",
    "candidate_signal_builder_code_hash",
    "candidate_signal_set_bytes",
    "candidate_signal_set_digest",
    "derive_candidate_group_set",
    "derive_candidate_signal_set",
]
