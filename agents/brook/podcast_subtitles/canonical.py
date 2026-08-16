"""Deterministic Evidence-to-Canonical reconciliation for Podcast Subtitle V2.

The public function in this module consumes immutable recognition Evidence and
returns one content-addressed Canonical Transcript.  Display cues, SRT indexes,
provider payloads, and model prompts never enter this layer.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from typing import Literal, Mapping, Sequence

from shared.schemas.podcast_subtitles_v2 import (
    AcceptancePolicySnapshot,
    AudioAuditReceipt,
    CanonicalSpan,
    CanonicalToken,
    CanonicalTranscript,
    CorrectionDecision,
    EvidenceToken,
    GenerationWarning,
    RecognitionEvidence,
    ReferenceEvidence,
    ReviewIssue,
    SpeechCoverageReceipt,
    audio_audit_receipt_set_hash,
    canonical_content_hash,
    recognition_evidence_content_hash,
    recognition_evidence_set_hash,
    reference_evidence_set_hash,
)
from shared.schemas.podcast_subtitles_v2_audio_audit import AudioDiscoveredCandidateV2
from shared.schemas.podcast_subtitles_v2_text_audit import TextDiscoveredCandidateV2

from .errors import StaleFingerprintError
from .hashing import hash_object, sha256_bytes, target_fingerprint
from .ledger import GENESIS_HASH, CorrectionLedger, LedgerEntry
from .native_resolution import NativeCorrectionDecisionV2
from .orthographic_projection import (
    OrthographicProjectionEvidenceV1,
    orthographic_projection_evidence_set_hash,
    verify_orthographic_projection,
)
from .ports import CorrectionProposal
from .risk import (
    ReconciliationCandidate,
    RiskRecord,
    SpanObservation,
    TermHint,
    apply_reference_hints,
    discover_editorial_risks,
    discover_intrinsic_risks,
    discover_recognition_risks,
)
from .speech_coverage import speech_coverage_risks, verified_speech_coverage_hash

CanonicalOutcome = Literal["accepted", "needs_review"]


@dataclass(frozen=True, slots=True)
class CanonicalBuildResult:
    """Observable result of reconciling Evidence into canonical truth."""

    outcome: CanonicalOutcome
    transcript: CanonicalTranscript
    candidates: tuple[SpanObservation, ...]
    risks: tuple[RiskRecord, ...]
    reference_evidence_hash: str
    generation_warnings: tuple[GenerationWarning, ...] = ()


def _evidence_identity(evidence: RecognitionEvidence) -> str:
    return recognition_evidence_content_hash(evidence)


def _audio_span_id(normalized_audio_hash: str, start_ms: int, end_ms: int) -> str:
    digest = hash_object(
        {
            "normalized_audio_hash": normalized_audio_hash,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
    )
    return f"audio-span-{digest}"


@dataclass(frozen=True, slots=True)
class _EvidenceNode:
    evidence_index: int
    token_index: int
    token: EvidenceToken
    text: str
    node_index: int
    ref: str


def _overlaps(left: EvidenceToken, right: EvidenceToken) -> bool:
    return left.start_ms < right.end_ms and right.start_ms < left.end_ms


def _build_span_observations(
    primary: RecognitionEvidence,
    corroborating: Sequence[RecognitionEvidence],
    span_ids: Sequence[str],
    projected_text_by_ref: Mapping[str, str] | None = None,
) -> tuple[tuple[SpanObservation, ...], tuple[tuple[str, ...], ...]]:
    """Align tokenization variants through cross-Adapter overlap components.

    A corroborating token that overlaps two primary tokens connects those
    primary spans into one comparison group.  This makes ``心理`` + ``健康``
    equivalent to a single ``心理健康`` hypothesis while retaining direct
    Evidence lineage on each primary Canonical Lexeme.
    """

    all_evidence = (primary, *tuple(corroborating))
    projected_text = projected_text_by_ref or {}
    adapter_ids = tuple(_evidence_identity(evidence) for evidence in all_evidence)
    nodes_by_evidence: list[list[_EvidenceNode]] = []
    all_nodes: list[_EvidenceNode] = []
    for evidence_index, evidence in enumerate(all_evidence):
        evidence_nodes: list[_EvidenceNode] = []
        for token_index, token in enumerate(evidence.tokens):
            ref = f"evidence:{adapter_ids[evidence_index]}:{token.id}"
            node = _EvidenceNode(
                evidence_index=evidence_index,
                token_index=token_index,
                token=token,
                text=projected_text.get(ref, token.text),
                node_index=len(all_nodes),
                ref=ref,
            )
            all_nodes.append(node)
            evidence_nodes.append(node)
        nodes_by_evidence.append(evidence_nodes)

    parent = list(range(len(all_nodes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    primary_nodes = nodes_by_evidence[0]
    lineage: list[set[str]] = [{node.ref} for node in primary_nodes]
    for other_nodes in nodes_by_evidence[1:]:
        primary_index = 0
        other_index = 0
        while primary_index < len(primary_nodes) and other_index < len(other_nodes):
            primary_node = primary_nodes[primary_index]
            other_node = other_nodes[other_index]
            if _overlaps(primary_node.token, other_node.token):
                union(primary_node.node_index, other_node.node_index)
                lineage[primary_index].add(other_node.ref)
                if primary_node.token.end_ms < other_node.token.end_ms:
                    primary_index += 1
                elif other_node.token.end_ms < primary_node.token.end_ms:
                    other_index += 1
                else:
                    primary_index += 1
                    other_index += 1
            elif primary_node.token.end_ms <= other_node.token.start_ms:
                primary_index += 1
            else:
                other_index += 1

    primary_groups: dict[int, list[int]] = {}
    for primary_index, node in enumerate(primary_nodes):
        primary_groups.setdefault(find(node.node_index), []).append(primary_index)
    component_nodes_by_root: dict[int, list[_EvidenceNode]] = {}
    for node in all_nodes:
        component_nodes_by_root.setdefault(find(node.node_index), []).append(node)

    observations: list[SpanObservation] = []
    for root, primary_indices in sorted(primary_groups.items(), key=lambda item: item[1][0]):
        component_nodes = component_nodes_by_root[root]
        candidates: list[ReconciliationCandidate] = []
        for evidence_index in range(len(all_evidence)):
            evidence_nodes = sorted(
                (node for node in component_nodes if node.evidence_index == evidence_index),
                key=lambda node: node.token_index,
            )
            if not evidence_nodes:
                continue
            confidences = [
                node.token.confidence
                for node in evidence_nodes
                if node.token.confidence is not None
            ]
            candidates.append(
                ReconciliationCandidate(
                    text="".join(node.text for node in evidence_nodes),
                    evidence_ids=tuple(node.ref for node in evidence_nodes),
                    confidence=min(confidences) if confidences else None,
                    speaker_labels=tuple(node.token.speaker for node in evidence_nodes),
                    adapter_id=adapter_ids[evidence_index],
                )
            )
        observations.append(
            SpanObservation(
                audio_span_ids=tuple(span_ids[index] for index in primary_indices),
                candidates=tuple(candidates),
                expected_adapter_ids=adapter_ids,
            )
        )
    primary_roots = set(primary_groups)
    primary_end_times = tuple(node.token.end_ms for node in primary_nodes)
    primary_start_times = tuple(node.token.start_ms for node in primary_nodes)
    orphan_groups: dict[tuple[int, tuple[int, ...]], list[_EvidenceNode]] = {}
    for evidence_index, evidence_nodes in enumerate(nodes_by_evidence[1:], start=1):
        for node in evidence_nodes:
            if find(node.node_index) in primary_roots:
                continue
            previous = bisect_right(primary_end_times, node.token.start_ms) - 1
            following = bisect_left(primary_start_times, node.token.end_ms)
            if previous >= 0 and following < len(primary_nodes):
                attached = (previous, following)
            elif previous >= 0:
                attached = (previous,)
            else:
                attached = (following,)
            orphan_groups.setdefault((evidence_index, attached), []).append(node)

    for (evidence_index, attached), orphan_nodes in sorted(
        orphan_groups.items(),
        key=lambda item: (
            item[0][1][0],
            min(node.token.start_ms for node in item[1]),
            item[0][0],
        ),
    ):
        orphan_nodes.sort(key=lambda node: node.token_index)
        primary_texts = [primary_nodes[index].text for index in attached]
        inserted_text = "".join(node.text for node in orphan_nodes)
        if len(attached) == 2:
            alternative_text = f"{primary_texts[0]}{inserted_text}{primary_texts[1]}"
        elif orphan_nodes[-1].token.end_ms <= primary_nodes[attached[0]].token.start_ms:
            alternative_text = f"{inserted_text}{primary_texts[0]}"
        else:
            alternative_text = f"{primary_texts[0]}{inserted_text}"
        primary_refs = tuple(primary_nodes[index].ref for index in attached)
        primary_confidences = [
            primary_nodes[index].token.confidence
            for index in attached
            if primary_nodes[index].token.confidence is not None
        ]
        orphan_confidences = [
            node.token.confidence for node in orphan_nodes if node.token.confidence is not None
        ]
        observations.append(
            SpanObservation(
                audio_span_ids=tuple(span_ids[index] for index in attached),
                candidates=(
                    ReconciliationCandidate(
                        text="".join(primary_texts),
                        evidence_ids=primary_refs,
                        confidence=(min(primary_confidences) if primary_confidences else None),
                        speaker_labels=tuple(
                            primary_nodes[index].token.speaker for index in attached
                        ),
                        adapter_id=adapter_ids[0],
                    ),
                    ReconciliationCandidate(
                        text=alternative_text,
                        evidence_ids=tuple((*primary_refs, *(node.ref for node in orphan_nodes))),
                        confidence=(min(orphan_confidences) if orphan_confidences else None),
                        speaker_labels=tuple(node.token.speaker for node in orphan_nodes),
                        adapter_id=adapter_ids[evidence_index],
                    ),
                ),
                expected_adapter_ids=adapter_ids,
                coverage_code="recognition_coverage_gap",
            )
        )
    span_order = {span_id: index for index, span_id in enumerate(span_ids)}
    observations.sort(
        key=lambda observation: (
            span_order[observation.audio_span_ids[0]],
            1 if observation.coverage_code else 0,
        )
    )
    return tuple(observations), tuple(tuple(sorted(refs)) for refs in lineage)


def _verified_orthographic_text(
    evidence: Sequence[RecognitionEvidence],
    projections: Sequence[OrthographicProjectionEvidenceV1],
) -> tuple[dict[str, str], str | None]:
    """Replay a complete projection set and index text by raw token reference."""

    projection_tuple = tuple(projections)
    if not projection_tuple:
        return {}, None
    evidence_tuple = tuple(evidence)
    evidence_by_hash = {
        recognition_evidence_content_hash(item): item for item in evidence_tuple
    }
    if len(evidence_by_hash) != len(evidence_tuple):
        raise ValueError("Recognition Evidence content identities must be unique")
    projection_by_source = {
        item.source_recognition_evidence_hash: item for item in projection_tuple
    }
    if len(projection_by_source) != len(projection_tuple):
        raise ValueError("Orthographic Projection Evidence sources must be unique")
    if set(projection_by_source) != set(evidence_by_hash):
        raise ValueError(
            "Orthographic Projection Evidence must cover the exact Recognition Evidence set"
        )

    projected_text: dict[str, str] = {}
    for evidence_hash in sorted(evidence_by_hash):
        projection = projection_by_source[evidence_hash]
        verify_orthographic_projection(projection, evidence_tuple)
        raw = evidence_by_hash[evidence_hash]
        if len(projection.tokens) != len(raw.tokens):  # schema/replay defense in depth
            raise ValueError("Orthographic Projection token coverage mismatch")
        for raw_token, projected_token in zip(raw.tokens, projection.tokens, strict=True):
            if (
                projected_token.source_token_id != raw_token.id
                or projected_token.source_text != raw_token.text
            ):
                raise ValueError("Orthographic Projection token lineage mismatch")
            ref = f"evidence:{evidence_hash}:{raw_token.id}"
            projected_text[ref] = projected_token.projected_text
    return projected_text, orthographic_projection_evidence_set_hash(projection_tuple)


def _orthographic_ambiguity_risks(
    *,
    evidence: Sequence[RecognitionEvidence],
    projections: Sequence[OrthographicProjectionEvidenceV1],
    primary_span_ids: Sequence[str],
) -> tuple[RiskRecord, ...]:
    """Surface every selected multi-option dictionary entry as unresolved review."""

    evidence_by_hash = {
        recognition_evidence_content_hash(item): item for item in evidence
    }
    primary = tuple(evidence)[0]
    primary_intervals = tuple(
        (token.start_ms, token.end_ms, primary_span_ids[index])
        for index, token in enumerate(primary.tokens)
    )
    risks: list[RiskRecord] = []
    for projection in sorted(
        projections, key=lambda item: item.source_recognition_evidence_hash
    ):
        source = evidence_by_hash[projection.source_recognition_evidence_hash]
        source_by_id = {token.id: token for token in source.tokens}
        source_hash = recognition_evidence_content_hash(source)
        for ambiguity in projection.ambiguities:
            source_tokens = tuple(source_by_id[token_id] for token_id in ambiguity.source_token_ids)
            start_ms = min(token.start_ms for token in source_tokens)
            end_ms = max(token.end_ms for token in source_tokens)
            affected_spans = tuple(
                span_id
                for primary_start, primary_end, span_id in primary_intervals
                if primary_start < end_ms and start_ms < primary_end
            )
            if not affected_spans:
                affected_spans = (
                    min(
                        primary_intervals,
                        key=lambda item: min(
                            abs(item[0] - end_ms), abs(start_ms - item[1])
                        ),
                    )[2],
                )
            evidence_ids = tuple(
                f"evidence:{source_hash}:{token.id}" for token in source_tokens
            )
            issue_payload = {
                "code": "orthographic_multi_option_ambiguity",
                "projection_id": projection.id,
                "dictionary_path": ambiguity.dictionary_path,
                "source_start_offset": ambiguity.source_start_offset,
                "source_end_offset": ambiguity.source_end_offset,
                "span_ids": affected_spans,
                "candidates": ambiguity.candidates,
            }
            issue = ReviewIssue(
                id=f"issue-{hash_object(issue_payload)}",
                risk="text",
                severity="medium",
                code="orthographic_multi_option_ambiguity",
                span_ids=affected_spans,
                audio_evidence_ids=evidence_ids,
                candidates=ambiguity.candidates,
                status="unresolved",
            )
            risks.append(
                RiskRecord(
                    issue=issue,
                    audio_span_ids=affected_spans,
                    evidence_ids=evidence_ids,
                )
            )
    return tuple(risks)


def _material_risks(
    risks: Sequence[RiskRecord],
    acceptance_policy: AcceptancePolicySnapshot,
) -> tuple[RiskRecord, ...]:
    blocking_severities = {"medium", "high", "blocking"}
    if not acceptance_policy.permit_unresolved_low_risk:
        blocking_severities.add("low")
    return tuple(
        risk
        for risk in risks
        if risk.issue.status == "unresolved"
        and risk.issue.severity in blocking_severities
    )


def _generation_identity_payload(
    *,
    episode_id: str,
    revision: int,
    status: str,
    source_audio_hash: str,
    normalized_audio_hash: str,
    normalization_receipt_hash: str,
    evidence_hash: str,
    orthographic_projection_evidence_hash: str | None,
    reference_evidence_hash: str,
    ledger_hash: str,
    policy_hash: str,
    acceptance_policy: AcceptancePolicySnapshot,
    content_hash: str,
    spans: Sequence[CanonicalSpan],
    review_issues: Sequence[ReviewIssue],
    generation_warnings: Sequence[GenerationWarning],
    full_audit_receipt_set_hash: str | None,
    verified_speech_coverage_receipt_hash: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "episode_id": episode_id,
        "revision": revision,
        "status": status,
        "source_audio_hash": source_audio_hash,
        "normalized_audio_hash": normalized_audio_hash,
        "normalization_receipt_hash": normalization_receipt_hash,
        "evidence_hash": evidence_hash,
        "reference_evidence_hash": reference_evidence_hash,
        "ledger_hash": ledger_hash,
        "policy_hash": policy_hash,
        "acceptance_policy": acceptance_policy,
        "content_hash": content_hash,
        "span_hash": hash_object(tuple(spans)),
        "risk_hash": hash_object(tuple(review_issues)),
        "generation_warning_hash": hash_object(tuple(generation_warnings)),
        "full_audit_receipt_set_hash": full_audit_receipt_set_hash,
        "verified_speech_coverage_receipt_hash": (
            verified_speech_coverage_receipt_hash
        ),
    }
    if orthographic_projection_evidence_hash is not None:
        payload["orthographic_projection_evidence_hash"] = (
            orthographic_projection_evidence_hash
        )
    return payload


def canonical_generation_id(transcript: CanonicalTranscript) -> str:
    """Recompute the sealed Generation identity from persisted truth."""

    payload = _generation_identity_payload(
        episode_id=transcript.episode_id,
        revision=transcript.revision,
        status=transcript.status,
        source_audio_hash=transcript.source_audio_hash,
        normalized_audio_hash=transcript.normalized_audio_hash,
        normalization_receipt_hash=transcript.normalization_receipt_hash,
        evidence_hash=transcript.evidence_hash,
        orthographic_projection_evidence_hash=(
            transcript.orthographic_projection_evidence_hash
        ),
        reference_evidence_hash=transcript.reference_evidence_hash,
        ledger_hash=transcript.ledger_hash,
        policy_hash=transcript.policy_hash,
        acceptance_policy=transcript.acceptance_policy,
        content_hash=transcript.content_hash,
        spans=transcript.spans,
        review_issues=transcript.review_issues,
        generation_warnings=transcript.generation_warnings,
        full_audit_receipt_set_hash=transcript.full_audit_receipt_set_hash,
        verified_speech_coverage_receipt_hash=(
            transcript.verified_speech_coverage_receipt_hash
        ),
    )
    return f"generation-{hash_object(payload)}"


def _seal_transcript(
    *,
    episode_id: str,
    revision: int,
    source_audio_hash: str,
    normalized_audio_hash: str,
    normalization_receipt_hash: str,
    evidence_hash: str,
    orthographic_projection_evidence_hash: str | None = None,
    reference_evidence_hash: str,
    ledger_hash: str,
    policy_hash: str,
    acceptance_policy: AcceptancePolicySnapshot,
    tokens: Sequence[CanonicalToken],
    spans: Sequence[CanonicalSpan],
    risks: Sequence[RiskRecord],
    generation_warnings: Sequence[GenerationWarning] = (),
    full_audit_receipt_set_hash: str | None = None,
    verified_speech_coverage_receipt_hash: str | None = None,
) -> CanonicalTranscript:
    """Construct CanonicalTranscript in one place so schema invariants cannot drift."""

    token_tuple = tuple(tokens)
    span_tuple = tuple(spans)
    risk_tuple = tuple(risks)
    warning_tuple = tuple(generation_warnings)
    content_hash = canonical_content_hash(token_tuple)
    pending_warning = any(warning.status == "requires_full_audit" for warning in warning_tuple)
    # Audio review is a required stage for every Generation, not merely a
    # mitigation for recognizers that omit confidence.  Reconciliation is
    # therefore always pre-audit/draft until a complete audio receipt set is
    # attached by ``attest_full_audit``.
    status = (
        "draft"
        if _material_risks(risk_tuple, acceptance_policy)
        or pending_warning
        or full_audit_receipt_set_hash is None
        or verified_speech_coverage_receipt_hash is None
        else "accepted"
    )
    generation_payload = _generation_identity_payload(
        episode_id=episode_id,
        revision=revision,
        status=status,
        source_audio_hash=source_audio_hash,
        normalized_audio_hash=normalized_audio_hash,
        normalization_receipt_hash=normalization_receipt_hash,
        evidence_hash=evidence_hash,
        orthographic_projection_evidence_hash=orthographic_projection_evidence_hash,
        reference_evidence_hash=reference_evidence_hash,
        ledger_hash=ledger_hash,
        policy_hash=policy_hash,
        acceptance_policy=acceptance_policy,
        content_hash=content_hash,
        spans=span_tuple,
        review_issues=tuple(risk.issue for risk in risk_tuple),
        generation_warnings=warning_tuple,
        full_audit_receipt_set_hash=full_audit_receipt_set_hash,
        verified_speech_coverage_receipt_hash=(
            verified_speech_coverage_receipt_hash
        ),
    )
    return CanonicalTranscript(
        schema_version=2 if orthographic_projection_evidence_hash is not None else 1,
        episode_id=episode_id,
        generation_id=f"generation-{hash_object(generation_payload)}",
        revision=revision,
        status=status,
        source_audio_hash=source_audio_hash,
        normalized_audio_hash=normalized_audio_hash,
        normalization_receipt_hash=normalization_receipt_hash,
        evidence_hash=evidence_hash,
        orthographic_projection_evidence_hash=orthographic_projection_evidence_hash,
        reference_evidence_hash=reference_evidence_hash,
        ledger_hash=ledger_hash,
        policy_hash=policy_hash,
        acceptance_policy=acceptance_policy,
        tokens=tuple(tokens),
        spans=span_tuple,
        review_issues=tuple(risk.issue for risk in risk_tuple),
        generation_warnings=warning_tuple,
        full_audit_receipt_set_hash=full_audit_receipt_set_hash,
        verified_speech_coverage_receipt_hash=(
            verified_speech_coverage_receipt_hash
        ),
        content_hash=content_hash,
    )


def review_target_fingerprint(
    transcript: CanonicalTranscript,
    span_ids: Sequence[str],
) -> str:
    """Fingerprint the exact stable spans and lexemes a reviewer heard."""

    requested = tuple(span_ids)
    if not requested:
        raise ValueError("review target requires stable span IDs")
    spans = {span.id: span for span in transcript.spans}
    tokens = {token.id: token for token in transcript.tokens}
    try:
        selected_spans = tuple(spans[span_id] for span_id in requested)
    except KeyError as exc:
        raise ValueError(f"unknown review span ID: {exc.args[0]!r}") from exc
    token_ids = tuple(token_id for span in selected_spans for token_id in span.token_ids)
    lexemes = "".join(tokens[token_id].text for token_id in token_ids)
    return target_fingerprint(
        audio_span_id="|".join(requested),
        token_ids=token_ids,
        lexemes=lexemes,
    )


def reconcile_canonical(
    *,
    primary: RecognitionEvidence,
    source_audio_hash: str,
    normalization_receipt_hash: str,
    policy_hash: str,
    acceptance_policy: AcceptancePolicySnapshot | None = None,
    corroborating: Sequence[RecognitionEvidence] = (),
    orthographic_projections: Sequence[OrthographicProjectionEvidenceV1] = (),
    references: Sequence[ReferenceEvidence] = (),
    term_hints: Sequence[TermHint] = (),
    ledger: CorrectionLedger | None = None,
    revision: int = 1,
) -> CanonicalBuildResult:
    """Reconcile recognition Evidence and seal a fail-closed transcript.

    The first vertical slice establishes stable content identity.  Risk
    discovery, references, and Correction Ledger replay are layered onto the
    same Interface in subsequent slices.
    """

    acceptance_snapshot = acceptance_policy or AcceptancePolicySnapshot()
    evidence = (primary, *tuple(corroborating))
    normalized_hashes = {item.normalized_audio_hash for item in evidence}
    if len(normalized_hashes) != 1:
        raise ValueError("all recognition Evidence must use one Normalized Audio hash")
    if any(item.episode_id != primary.episode_id for item in evidence):
        raise ValueError("all recognition Evidence must belong to one episode")

    normalized_audio_hash = primary.normalized_audio_hash
    evidence_hash = recognition_evidence_set_hash(evidence)
    # Recognition Evidence identity covers the complete token stream.  Computing
    # it inside the token loop turns an otherwise linear reconciliation into
    # O(n^2) full-Evidence serialisation for episode-sized inputs.
    primary_evidence_identity = _evidence_identity(primary)
    projected_text_by_ref, orthographic_projection_evidence_hash = (
        _verified_orthographic_text(evidence, orthographic_projections)
    )
    reference_evidence_hash = reference_evidence_set_hash(tuple(references))

    ordered_corroborating = tuple(sorted(corroborating, key=_evidence_identity))
    span_ids = tuple(
        _audio_span_id(
            normalized_audio_hash,
            evidence_token.start_ms,
            evidence_token.end_ms,
        )
        for evidence_token in primary.tokens
    )
    base_observations, token_lineage = _build_span_observations(
        primary,
        ordered_corroborating,
        span_ids,
        projected_text_by_ref,
    )
    tokens: list[CanonicalToken] = []
    spans: list[CanonicalSpan] = []
    observations: list[SpanObservation] = []
    risks: list[RiskRecord] = []
    missing_confidence_evidence = tuple(
        sorted(
            _evidence_identity(item)
            for item in evidence
            if any(token.confidence is None for token in item.tokens)
        )
    )
    generation_warnings: tuple[GenerationWarning, ...] = ()
    if missing_confidence_evidence:
        warning_payload = {
            "code": "recognizer_confidence_unavailable",
            "evidence_hashes": missing_confidence_evidence,
            "adapter_ids": tuple(
                sorted(
                    item.adapter
                    for item in evidence
                    if any(token.confidence is None for token in item.tokens)
                )
            ),
            "affected_token_count": sum(
                token.confidence is None for item in evidence for token in item.tokens
            ),
        }
        generation_warnings = (
            GenerationWarning(
                id=f"generation-warning-{hash_object(warning_payload)}",
                **warning_payload,
            ),
        )
    for index, evidence_token in enumerate(primary.tokens):
        span_id = span_ids[index]
        token_id = f"canonical-{span_id.removeprefix('audio-span-')}"
        tokens.append(
            CanonicalToken(
                id=token_id,
                text=projected_text_by_ref.get(
                    f"evidence:{primary_evidence_identity}:{evidence_token.id}",
                    evidence_token.text,
                ),
                start_ms=evidence_token.start_ms,
                end_ms=evidence_token.end_ms,
                timing_basis="recognition",
                alignment_evidence_ids=token_lineage[index],
                speaker=evidence_token.speaker,
                evidence_ids=token_lineage[index],
                confidence=evidence_token.confidence,
            )
        )
        spans.append(
            CanonicalSpan(
                id=span_id,
                token_ids=(token_id,),
                start_ms=evidence_token.start_ms,
                end_ms=evidence_token.end_ms,
                alignment="lexeme_exact",
                alignment_evidence_ids=token_lineage[index],
            )
        )

    for observation in base_observations:
        risks.extend(discover_intrinsic_risks(observation))
        risks.extend(discover_recognition_risks(observation))
        observation, reference_risks = apply_reference_hints(
            observation,
            references,
            term_hints,
        )
        observations.append(observation)
        risks.extend(reference_risks)

    risks.extend(
        _orthographic_ambiguity_risks(
            evidence=evidence,
            projections=orthographic_projections,
            primary_span_ids=span_ids,
        )
    )

    # Editorial policy is evaluated over the continuous Canonical stream.  A
    # house delimiter may span Recognition token boundaries, and OpenCC is a
    # detector only: this step must never rewrite a Canonical lexeme.
    risks.extend(discover_editorial_risks(observations))

    transcript = _seal_transcript(
        episode_id=primary.episode_id,
        revision=revision,
        source_audio_hash=source_audio_hash,
        normalized_audio_hash=normalized_audio_hash,
        normalization_receipt_hash=normalization_receipt_hash,
        evidence_hash=evidence_hash,
        orthographic_projection_evidence_hash=(
            orthographic_projection_evidence_hash
        ),
        reference_evidence_hash=reference_evidence_hash,
        ledger_hash=GENESIS_HASH,
        policy_hash=policy_hash,
        acceptance_policy=acceptance_snapshot,
        tokens=tuple(tokens),
        spans=tuple(spans),
        risks=tuple(risks),
        generation_warnings=generation_warnings,
    )
    result = CanonicalBuildResult(
        outcome="accepted" if transcript.status == "accepted" else "needs_review",
        transcript=transcript,
        candidates=tuple(observations),
        risks=tuple(risks),
        reference_evidence_hash=reference_evidence_hash,
        generation_warnings=generation_warnings,
    )
    if ledger is None:
        return result
    return replay_correction_ledger(
        result,
        ledger,
        allowed_reference_ids=tuple(item.id for item in references),
    )


def _resolved_issue(
    issue: ReviewIssue,
    *,
    target_span_ids: tuple[str, ...],
    replacement_span_id: str | None,
) -> ReviewIssue:
    target = set(target_span_ids)
    issue_spans = set(issue.span_ids)
    overlap = target.intersection(issue_spans)
    if not overlap:
        return issue
    if not issue_spans.issubset(target):
        raise ValueError(f"decision must cover the complete issue span set for {issue.id!r}")
    new_span_ids = (replacement_span_id,) if replacement_span_id is not None else issue.span_ids
    return ReviewIssue.model_validate(
        {
            **issue.model_dump(mode="json"),
            "span_ids": new_span_ids,
            "status": "resolved",
        }
    )


def add_review_risks(
    current: CanonicalBuildResult,
    risks: Sequence[RiskRecord],
) -> CanonicalBuildResult:
    """Seal additional discovered risks without changing canonical lexemes."""

    existing_ids = {risk.issue.id for risk in current.risks}
    additions = tuple(risks)
    duplicate_ids = existing_ids.intersection(risk.issue.id for risk in additions)
    if duplicate_ids:
        raise ValueError(f"duplicate Review Issue IDs: {sorted(duplicate_ids)!r}")
    known_spans = {span.id for span in current.transcript.spans}
    for risk in additions:
        unknown = set(risk.issue.span_ids) - known_spans
        if unknown:
            raise ValueError(f"Review Issue references unknown spans: {sorted(unknown)!r}")
    combined = (*current.risks, *additions)
    transcript = current.transcript
    resealed = _seal_transcript(
        episode_id=transcript.episode_id,
        revision=transcript.revision,
        source_audio_hash=transcript.source_audio_hash,
        normalized_audio_hash=transcript.normalized_audio_hash,
        normalization_receipt_hash=transcript.normalization_receipt_hash,
        evidence_hash=transcript.evidence_hash,
        orthographic_projection_evidence_hash=(
            transcript.orthographic_projection_evidence_hash
        ),
        reference_evidence_hash=transcript.reference_evidence_hash,
        ledger_hash=transcript.ledger_hash,
        policy_hash=transcript.policy_hash,
        acceptance_policy=transcript.acceptance_policy,
        tokens=transcript.tokens,
        spans=transcript.spans,
        risks=combined,
        generation_warnings=current.generation_warnings,
        full_audit_receipt_set_hash=transcript.full_audit_receipt_set_hash,
        verified_speech_coverage_receipt_hash=(
            transcript.verified_speech_coverage_receipt_hash
        ),
    )
    return CanonicalBuildResult(
        outcome="accepted" if resealed.status == "accepted" else "needs_review",
        transcript=resealed,
        candidates=current.candidates,
        risks=tuple(combined),
        reference_evidence_hash=current.reference_evidence_hash,
        generation_warnings=current.generation_warnings,
    )


def without_native_resolution_risk(
    current: CanonicalBuildResult,
) -> CanonicalBuildResult:
    """Remove only the native-discovery gate and reseal the exact same truth.

    Native resolution children are audited from this risk-free basis.  A fresh
    gate, when warranted, is derived only after both child audit records exist.
    """

    retained = tuple(
        risk
        for risk in current.risks
        if risk.issue.code != "native_full_audit_requires_resolution"
    )
    if retained == current.risks:
        return current
    transcript = current.transcript
    resealed = _seal_transcript(
        episode_id=transcript.episode_id,
        revision=transcript.revision,
        source_audio_hash=transcript.source_audio_hash,
        normalized_audio_hash=transcript.normalized_audio_hash,
        normalization_receipt_hash=transcript.normalization_receipt_hash,
        evidence_hash=transcript.evidence_hash,
        orthographic_projection_evidence_hash=(
            transcript.orthographic_projection_evidence_hash
        ),
        reference_evidence_hash=transcript.reference_evidence_hash,
        ledger_hash=transcript.ledger_hash,
        policy_hash=transcript.policy_hash,
        acceptance_policy=transcript.acceptance_policy,
        tokens=transcript.tokens,
        spans=transcript.spans,
        risks=retained,
        generation_warnings=current.generation_warnings,
        full_audit_receipt_set_hash=transcript.full_audit_receipt_set_hash,
        verified_speech_coverage_receipt_hash=(
            transcript.verified_speech_coverage_receipt_hash
        ),
    )
    return CanonicalBuildResult(
        outcome="accepted" if resealed.status == "accepted" else "needs_review",
        transcript=resealed,
        candidates=current.candidates,
        risks=retained,
        reference_evidence_hash=current.reference_evidence_hash,
        generation_warnings=current.generation_warnings,
    )


def attest_speech_coverage(
    current: CanonicalBuildResult,
    receipt: SpeechCoverageReceipt | None,
) -> CanonicalBuildResult:
    """Attach independent audio-activity coverage without inventing text."""

    risks = speech_coverage_risks(current.transcript, receipt)
    covered = add_review_risks(current, risks) if risks else current
    transcript = covered.transcript
    resealed = _seal_transcript(
        episode_id=transcript.episode_id,
        revision=transcript.revision,
        source_audio_hash=transcript.source_audio_hash,
        normalized_audio_hash=transcript.normalized_audio_hash,
        normalization_receipt_hash=transcript.normalization_receipt_hash,
        evidence_hash=transcript.evidence_hash,
        orthographic_projection_evidence_hash=(
            transcript.orthographic_projection_evidence_hash
        ),
        reference_evidence_hash=transcript.reference_evidence_hash,
        ledger_hash=transcript.ledger_hash,
        policy_hash=transcript.policy_hash,
        acceptance_policy=transcript.acceptance_policy,
        tokens=transcript.tokens,
        spans=transcript.spans,
        risks=covered.risks,
        generation_warnings=covered.generation_warnings,
        full_audit_receipt_set_hash=transcript.full_audit_receipt_set_hash,
        verified_speech_coverage_receipt_hash=verified_speech_coverage_hash(receipt),
    )
    return CanonicalBuildResult(
        outcome="accepted" if resealed.status == "accepted" else "needs_review",
        transcript=resealed,
        candidates=covered.candidates,
        risks=covered.risks,
        reference_evidence_hash=covered.reference_evidence_hash,
        generation_warnings=covered.generation_warnings,
    )


def add_correction_proposals(
    current: CanonicalBuildResult,
    proposals: Sequence[CorrectionProposal],
    *,
    allowed_reference_ids: Sequence[str] = (),
) -> CanonicalBuildResult:
    """Validate Corrector proposals and place changed readings behind review."""

    spans = {span.id: span for span in current.transcript.spans}
    span_positions = {span.id: index for index, span in enumerate(current.transcript.spans)}
    tokens = {token.id: token for token in current.transcript.tokens}
    allowed_references = set(allowed_reference_ids)
    risks: list[RiskRecord] = []
    for proposal in proposals:
        if proposal.observed_text == proposal.candidate_text:
            continue
        try:
            selected_spans = tuple(spans[span_id] for span_id in proposal.audio_span_ids)
        except KeyError as exc:
            raise ValueError(
                f"Correction Proposal references unknown span {exc.args[0]!r}"
            ) from exc
        positions = [span_positions[span.id] for span in selected_spans]
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise ValueError("Correction Proposal spans must be ordered and contiguous")
        selected_tokens = tuple(
            tokens[token_id] for span in selected_spans for token_id in span.token_ids
        )
        if (
            proposal.start_ms != selected_spans[0].start_ms
            or proposal.end_ms != selected_spans[-1].end_ms
        ):
            raise ValueError("Correction Proposal audio range must exactly match target spans")
        observed = "".join(token.text for token in selected_tokens)
        if proposal.observed_text != observed:
            raise ValueError(
                "Correction Proposal observed_text does not match Canonical Transcript"
            )
        lineage_ids = {
            evidence_id for token in selected_tokens for evidence_id in token.evidence_ids
        }
        lineage_by_raw_id: dict[str, set[str]] = {}
        for evidence_id in lineage_ids:
            raw_id = evidence_id.split(":", maxsplit=2)[-1]
            lineage_by_raw_id.setdefault(raw_id, set()).add(evidence_id)
        unknown_evidence = set(proposal.evidence_token_ids) - set(lineage_by_raw_id)
        if unknown_evidence:
            raise ValueError(
                "Correction Proposal references Evidence outside target spans: "
                f"{sorted(unknown_evidence)!r}"
            )
        qualified_evidence_ids = tuple(
            sorted(
                evidence_id
                for raw_id in proposal.evidence_token_ids
                for evidence_id in lineage_by_raw_id[raw_id]
            )
        )
        unknown_references = set(proposal.reference_evidence_ids) - allowed_references
        if unknown_references:
            raise ValueError(
                "Correction Proposal references unavailable Reference Evidence: "
                f"{sorted(unknown_references)!r}"
            )
        severity = "high" if proposal.confidence is None or proposal.confidence < 0.75 else "medium"
        issue_identity = {
            "code": "correction_proposal",
            "proposal_id": proposal.id,
            "span_ids": proposal.audio_span_ids,
            "candidate": proposal.candidate_text,
        }
        issue = ReviewIssue(
            id=f"issue-{hash_object(issue_identity)}",
            risk="text",
            severity=severity,
            code="correction_proposal",
            span_ids=proposal.audio_span_ids,
            audio_evidence_ids=qualified_evidence_ids,
            reference_evidence_ids=tuple(sorted(proposal.reference_evidence_ids)),
            candidates=(proposal.observed_text, proposal.candidate_text),
            status="unresolved",
        )
        risks.append(
            RiskRecord(
                issue=issue,
                audio_span_ids=proposal.audio_span_ids,
                evidence_ids=qualified_evidence_ids,
                supporting_reference_ids=tuple(sorted(proposal.reference_evidence_ids)),
            )
        )
    return add_review_risks(current, risks) if risks else current


def attest_full_audit(
    current: CanonicalBuildResult,
    receipts: Sequence[AudioAuditReceipt],
) -> CanonicalBuildResult:
    """Attach exact audio-listening proof after complete, ordered coverage.

    This does not resolve any span-level Review Issue.  An unresolved audio
    window becomes a blocking stable-span Issue; confirmed listening may also
    mitigate a recognizer-confidence capability warning.
    """

    receipt_tuple = tuple(receipts)
    if not receipt_tuple:
        raise ValueError("full audit requires persisted execution receipts")
    transcript = current.transcript
    expected_spans = tuple(span.id for span in transcript.spans)
    covered_spans = tuple(
        span_id for receipt in receipt_tuple for span_id in receipt.target_span_ids
    )
    if covered_spans != expected_spans or len(set(covered_spans)) != len(covered_spans):
        raise ValueError("full-audit receipts must exactly partition every Canonical span")
    expected_binding = (
        transcript.content_hash,
        transcript.evidence_hash,
        transcript.reference_evidence_hash,
        transcript.policy_hash,
        transcript.normalized_audio_hash,
    )
    preaudit_generation_ids: set[str] = set()
    adapter_hashes: set[str] = set()
    for receipt in receipt_tuple:
        actual_binding = (
            receipt.preaudit_content_hash,
            receipt.evidence_hash,
            receipt.reference_evidence_hash,
            receipt.policy_hash,
            receipt.normalized_audio_hash,
        )
        if actual_binding != expected_binding:
            raise ValueError("full-audit receipt belongs to another immutable transcript truth")
        if receipt.episode_id != transcript.episode_id:
            raise ValueError("full-audit receipt belongs to another episode")
        preaudit_generation_ids.add(receipt.preaudit_generation_id)
        adapter_hashes.add(receipt.adapter_identity_hash)
    if len(adapter_hashes) != 1:
        raise ValueError("one full audit cannot mix Corrector Adapter identities")
    if len(preaudit_generation_ids) != 1:
        raise ValueError("one full audit cannot mix pre-audit Generations")
    if preaudit_generation_ids != {transcript.generation_id}:
        raise ValueError("audio full audit targeted another pre-audit Generation")

    span_by_id = {span.id: span for span in transcript.spans}
    unresolved_risks: list[RiskRecord] = []
    for receipt in receipt_tuple:
        selected = tuple(span_by_id[span_id] for span_id in receipt.target_span_ids)
        if (
            receipt.window_start_ms != selected[0].start_ms
            or receipt.window_end_ms != selected[-1].end_ms
        ):
            raise ValueError("audio full-audit receipt window differs from target spans")
        if receipt.status != "unresolved":
            continue
        evidence_ids = tuple(
            sorted(
                {
                    evidence_id
                    for span in selected
                    for token_id in span.token_ids
                    for token in transcript.tokens
                    if token.id == token_id
                    for evidence_id in token.evidence_ids
                }
            )
        )
        issue_identity = {
            "code": "audio_audit_unresolved",
            "receipt_id": receipt.id,
            "span_ids": receipt.target_span_ids,
        }
        issue = ReviewIssue(
            id=f"issue-{hash_object(issue_identity)}",
            risk="provenance",
            severity="blocking",
            code="audio_audit_unresolved",
            span_ids=receipt.target_span_ids,
            audio_evidence_ids=evidence_ids,
            candidates=(),
            status="unresolved",
        )
        unresolved_risks.append(
            RiskRecord(
                issue=issue,
                audio_span_ids=receipt.target_span_ids,
                evidence_ids=evidence_ids,
            )
        )
    audited = add_review_risks(current, unresolved_risks) if unresolved_risks else current
    transcript = audited.transcript

    receipt_set_hash = audio_audit_receipt_set_hash(list(receipt_tuple))
    warnings = tuple(
        GenerationWarning.model_validate(
            {
                **warning.model_dump(mode="json"),
                "status": "mitigated",
                "mitigation_receipt_set_hash": receipt_set_hash,
            }
        )
        if warning.code == "recognizer_confidence_unavailable"
        and warning.status == "requires_full_audit"
        else warning
        for warning in audited.generation_warnings
    )
    resealed = _seal_transcript(
        episode_id=transcript.episode_id,
        revision=transcript.revision,
        source_audio_hash=transcript.source_audio_hash,
        normalized_audio_hash=transcript.normalized_audio_hash,
        normalization_receipt_hash=transcript.normalization_receipt_hash,
        evidence_hash=transcript.evidence_hash,
        orthographic_projection_evidence_hash=(
            transcript.orthographic_projection_evidence_hash
        ),
        reference_evidence_hash=transcript.reference_evidence_hash,
        ledger_hash=transcript.ledger_hash,
        policy_hash=transcript.policy_hash,
        acceptance_policy=transcript.acceptance_policy,
        tokens=transcript.tokens,
        spans=transcript.spans,
        risks=audited.risks,
        generation_warnings=warnings,
        full_audit_receipt_set_hash=receipt_set_hash,
        verified_speech_coverage_receipt_hash=(
            transcript.verified_speech_coverage_receipt_hash
        ),
    )
    return CanonicalBuildResult(
        outcome="accepted" if resealed.status == "accepted" else "needs_review",
        transcript=resealed,
        candidates=audited.candidates,
        risks=audited.risks,
        reference_evidence_hash=audited.reference_evidence_hash,
        generation_warnings=warnings,
    )


def _apply_decision(
    current: CanonicalBuildResult,
    decision: CorrectionDecision,
    *,
    ledger_hash: str,
    require_generation_match: bool = True,
) -> CanonicalBuildResult:
    transcript = current.transcript
    if decision.replacement_alignment:
        raise ValueError(
            "replacement_alignment is disabled until a typed "
            "ReplacementAlignmentReceipt contract is implemented"
        )
    if decision.episode_id != transcript.episode_id:
        raise ValueError("Correction Decision belongs to another episode")
    if require_generation_match and decision.generation_id != transcript.generation_id:
        raise ValueError("Correction Decision belongs to a stale Generation")

    span_positions = {span.id: index for index, span in enumerate(transcript.spans)}
    try:
        positions = [span_positions[span_id] for span_id in decision.target_span_ids]
    except KeyError as exc:
        raise ValueError(f"Correction Decision targets unknown span {exc.args[0]!r}") from exc
    if positions != list(range(positions[0], positions[-1] + 1)):
        raise ValueError("Correction Decision target spans must be ordered and contiguous")

    token_positions = {token.id: index for index, token in enumerate(transcript.tokens)}
    selected_spans = tuple(transcript.spans[position] for position in positions)
    selected_token_ids = tuple(token_id for span in selected_spans for token_id in span.token_ids)
    selected_positions = [token_positions[token_id] for token_id in selected_token_ids]
    if selected_positions != list(range(selected_positions[0], selected_positions[-1] + 1)):
        raise ValueError("Correction Decision target tokens must be ordered and contiguous")
    selected_tokens = tuple(transcript.tokens[position] for position in selected_positions)
    if (
        decision.target_start_ms != selected_spans[0].start_ms
        or decision.target_end_ms != selected_spans[-1].end_ms
    ):
        raise ValueError("Correction Decision audio range does not match target spans")

    issue_by_id = {risk.issue.id: risk.issue for risk in current.risks}
    unknown_issue_ids = set(decision.issue_ids) - set(issue_by_id)
    if unknown_issue_ids:
        raise ValueError(
            f"Correction Decision references unknown issues: {sorted(unknown_issue_ids)!r}"
        )
    target_span_set = set(decision.target_span_ids)
    for issue_id in decision.issue_ids:
        issue = issue_by_id[issue_id]
        if issue.status != "unresolved":
            raise ValueError(f"Correction Decision issue is not unresolved: {issue_id!r}")
        if not set(issue.span_ids).issubset(target_span_set):
            raise ValueError(f"Correction Decision target must fully contain issue {issue_id!r}")
    if decision.action in {"accept_candidate", "reject_candidate"}:
        bound_candidates = {
            candidate
            for issue_id in decision.issue_ids
            for candidate in issue_by_id[issue_id].candidates
        }
        if decision.selected_candidate not in bound_candidates:
            raise ValueError("Correction Decision selected text is not a bound issue candidate")

    target_evidence_ids = {
        evidence_id for token in selected_tokens for evidence_id in token.evidence_ids
    }
    if decision.action in {
        "confirm_original",
        "replace",
        "accept_candidate",
        "reject_candidate",
    }:
        if not decision.audio_evidence_ids:
            raise ValueError("Correction Decision requires target Audio Evidence")
        unknown_decision_evidence = set(decision.audio_evidence_ids) - target_evidence_ids
        if unknown_decision_evidence:
            raise ValueError(
                "Correction Decision cites Audio Evidence outside target spans: "
                f"{sorted(unknown_decision_evidence)!r}"
            )
    if decision.evidence_basis == "administrative" and decision.action != "defer":
        raise ValueError("administrative Correction Decisions may only defer")

    replacement_text: str | None = None
    if decision.action == "replace":
        replacement_text = decision.replacement_text
    elif decision.action == "accept_candidate":
        replacement_text = decision.selected_candidate

    tokens = list(transcript.tokens)
    spans = list(transcript.spans)
    replacement_span_id: str | None = None
    if replacement_text is not None:
        speakers = {token.speaker for token in selected_tokens}
        if len(speakers) != 1:
            raise ValueError("one Correction Decision cannot merge multiple speakers")
        evidence_ids = tuple(
            sorted(
                {
                    *decision.audio_evidence_ids,
                    *(
                        evidence_id
                        for token in selected_tokens
                        for evidence_id in token.evidence_ids
                    ),
                }
            )
        )
        replacement_span_id = _audio_span_id(
            transcript.normalized_audio_hash,
            decision.target_start_ms,
            decision.target_end_ms,
        )
        confidences = [
            token.confidence for token in selected_tokens if token.confidence is not None
        ]
        replacement_tokens: list[CanonicalToken] = []
        for index, lexeme in enumerate(decision.replacement_lexemes):
            replacement_token_identity = {
                "span_id": replacement_span_id,
                "decision_event_id": decision.event_id,
                "lexeme_index": index,
                "lexeme": lexeme,
                "timing_basis": "coarse_span",
                "alignment": None,
            }
            replacement_tokens.append(
                CanonicalToken(
                    id=f"canonical-{hash_object(replacement_token_identity)}",
                    text=lexeme,
                    start_ms=None,
                    end_ms=None,
                    timing_basis="coarse_span",
                    alignment_evidence_ids=(),
                    speaker=selected_tokens[0].speaker,
                    evidence_ids=evidence_ids,
                    confidence=min(confidences) if confidences else None,
                )
            )
        tokens[selected_positions[0] : selected_positions[-1] + 1] = replacement_tokens
        replacement_span = CanonicalSpan(
            id=replacement_span_id,
            token_ids=tuple(token.id for token in replacement_tokens),
            start_ms=decision.target_start_ms,
            end_ms=decision.target_end_ms,
            alignment="coarse",
            alignment_evidence_ids=(),
            lineage=tuple(
                dict.fromkeys(
                    ancestor_id
                    for span in selected_spans
                    for ancestor_id in (span.id, *span.lineage)
                    if ancestor_id != replacement_span_id
                )
            ),
        )
        spans[positions[0] : positions[-1] + 1] = [replacement_span]

    resolves_issue = decision.action in {
        "confirm_original",
        "replace",
        "accept_candidate",
    }
    risks: list[RiskRecord] = []
    span_replacements = (
        {span_id: replacement_span_id for span_id in decision.target_span_ids}
        if replacement_span_id is not None
        else {}
    )
    for risk in current.risks:
        # Corrector and AudioAuditor findings are execution-derived state.  A
        # Decision always creates another immutable Generation, so even a
        # no-text-change child must obtain fresh child-bound findings instead
        # of carrying a parent's proposal/audit proof forward.
        if risk.issue.code in {"correction_proposal", "audio_audit_unresolved"}:
            continue
        issue = risk.issue
        if resolves_issue and issue.id in decision.issue_ids:
            issue = _resolved_issue(
                issue,
                target_span_ids=decision.target_span_ids,
                replacement_span_id=replacement_span_id,
            )
        remapped_audio_spans = tuple(
            dict.fromkeys(
                span_replacements.get(span_id, span_id)
                for span_id in risk.audio_span_ids
            )
        )
        remapped_issue_spans = tuple(
            dict.fromkeys(
                span_replacements.get(span_id, span_id)
                for span_id in issue.span_ids
            )
        )
        if issue.span_ids != remapped_issue_spans:
            issue = issue.model_copy(update={"span_ids": remapped_issue_spans})
        risks.append(
            replace(
                risk,
                issue=issue,
                audio_span_ids=remapped_audio_spans,
                resolution_event_id=(
                    decision.event_id if issue.status == "resolved" else risk.resolution_event_id
                ),
            )
        )

    generation_warnings = tuple(
        GenerationWarning.model_validate(
            {
                **warning.model_dump(mode="json"),
                "status": "requires_full_audit",
                "mitigation_receipt_set_hash": None,
            }
        )
        if warning.status == "mitigated"
        else warning
        for warning in current.generation_warnings
    )
    new_transcript = _seal_transcript(
        episode_id=transcript.episode_id,
        revision=transcript.revision + 1,
        source_audio_hash=transcript.source_audio_hash,
        normalized_audio_hash=transcript.normalized_audio_hash,
        normalization_receipt_hash=transcript.normalization_receipt_hash,
        evidence_hash=transcript.evidence_hash,
        orthographic_projection_evidence_hash=(
            transcript.orthographic_projection_evidence_hash
        ),
        reference_evidence_hash=transcript.reference_evidence_hash,
        ledger_hash=ledger_hash,
        policy_hash=transcript.policy_hash,
        acceptance_policy=transcript.acceptance_policy,
        tokens=tokens,
        spans=spans,
        risks=risks,
        generation_warnings=generation_warnings,
        # Revision, Ledger head, risk state, and possibly token truth all belong
        # to the child.  Parent full-audit receipts therefore never attest a
        # Decision child, including confirm/reject/defer decisions that keep
        # the rendered text unchanged.
        full_audit_receipt_set_hash=None,
        verified_speech_coverage_receipt_hash=(
            transcript.verified_speech_coverage_receipt_hash
        ),
    )
    return CanonicalBuildResult(
        outcome=("accepted" if new_transcript.status == "accepted" else "needs_review"),
        transcript=new_transcript,
        candidates=current.candidates,
        risks=tuple(risks),
        reference_evidence_hash=current.reference_evidence_hash,
        generation_warnings=generation_warnings,
    )


def derive_canonical_resolution(
    current: CanonicalBuildResult,
    decision: CorrectionDecision,
    *,
    ledger_hash: str,
) -> CanonicalBuildResult:
    """Derive a reviewed child transcript against one exact Ledger entry hash."""

    fingerprint = review_target_fingerprint(
        current.transcript,
        decision.target_span_ids,
    )
    if decision.evidence_fingerprint != fingerprint:
        raise StaleFingerprintError(
            f"decision {decision.event_id!r} reviewed {decision.evidence_fingerprint}, "
            f"current target is {fingerprint}"
        )
    return _apply_decision(current, decision, ledger_hash=ledger_hash)


def derive_native_canonical_resolution(
    current: CanonicalBuildResult,
    decision: NativeCorrectionDecisionV2,
    candidate: TextDiscoveredCandidateV2 | AudioDiscoveredCandidateV2,
    *,
    ledger_hash: str,
) -> CanonicalBuildResult:
    """Project one authorised native discovery onto its exact token range.

    Native Full Audit discoveries are not free-form edits.  The persisted
    decision must bind one exact discovery artifact, and the only mutating
    action replaces precisely ``affected_token_ids``.  Tokens outside that
    range remain byte-for-byte/model-for-model identical.
    """

    transcript = current.transcript
    if decision.episode_id != transcript.episode_id:
        raise ValueError("native decision belongs to another episode")
    if decision.generation_id != transcript.generation_id:
        raise ValueError("native decision belongs to a stale Generation")
    if decision.normalized_audio_hash != transcript.normalized_audio_hash:
        raise ValueError("native decision belongs to different normalized audio")
    if (
        decision.candidate_discovery_id != candidate.id
        or decision.candidate_discovery_hash != candidate.content_hash
        or decision.target_span_ids != candidate.cited_span_ids
        or decision.affected_token_ids != candidate.affected_token_ids
        or decision.cited_recognition_evidence_ids
        != candidate.cited_recognition_evidence_ids
    ):
        raise ValueError("native decision differs from exact discovery artifact")
    original_hash = sha256_bytes(candidate.observed_text.encode("utf-8"))
    candidate_hash = sha256_bytes(candidate.candidate_text.encode("utf-8"))
    if (
        decision.original_literal_sha256 != original_hash
        or decision.candidate_literal_sha256 != candidate_hash
    ):
        raise ValueError("native decision literal hashes differ from discovery")
    if decision.action == "accept_exact_candidate":
        if (
            not decision.mutates_text
            or decision.authorized_literal_sha256 != candidate_hash
        ):
            raise ValueError("native acceptance lacks exact candidate authority")
    elif decision.mutates_text or decision.authorized_literal_sha256 not in {
        None,
        original_hash,
    }:
        raise ValueError("native no-text decision carries mutation authority")

    span_positions = {span.id: index for index, span in enumerate(transcript.spans)}
    try:
        positions = [span_positions[span_id] for span_id in decision.target_span_ids]
    except KeyError as exc:
        raise ValueError(f"native decision targets unknown span {exc.args[0]!r}") from exc
    if positions != list(range(positions[0], positions[-1] + 1)):
        raise ValueError("native decision target spans must be ordered and contiguous")
    selected_spans = tuple(transcript.spans[position] for position in positions)
    if (
        decision.target_start_ms != selected_spans[0].start_ms
        or decision.target_end_ms != selected_spans[-1].end_ms
    ):
        raise ValueError("native decision audio range does not match cited spans")
    fingerprint = review_target_fingerprint(transcript, decision.target_span_ids)
    if decision.reviewed_fingerprint != fingerprint:
        raise StaleFingerprintError(
            f"native decision {decision.event_id!r} reviewed "
            f"{decision.reviewed_fingerprint}, current target is {fingerprint}"
        )

    token_positions = {token.id: index for index, token in enumerate(transcript.tokens)}
    selected_token_ids = tuple(
        token_id for span in selected_spans for token_id in span.token_ids
    )
    try:
        selected_token_positions = [token_positions[token_id] for token_id in selected_token_ids]
        affected_positions = [
            token_positions[token_id] for token_id in decision.affected_token_ids
        ]
    except KeyError as exc:
        raise ValueError(f"native decision targets unknown token {exc.args[0]!r}") from exc
    if selected_token_positions != list(
        range(selected_token_positions[0], selected_token_positions[-1] + 1)
    ):
        raise ValueError("native decision cited span tokens must be ordered and contiguous")
    if affected_positions != list(range(affected_positions[0], affected_positions[-1] + 1)):
        raise ValueError("native decision affected tokens must be ordered and contiguous")
    if not set(affected_positions).issubset(selected_token_positions):
        raise ValueError("native decision affected tokens escape cited spans")
    affected_tokens = tuple(transcript.tokens[position] for position in affected_positions)
    if "".join(token.text for token in affected_tokens) != candidate.observed_text:
        raise ValueError("native discovery observed text differs from affected tokens")
    affected_evidence = tuple(
        dict.fromkeys(
            evidence_id
            for token in affected_tokens
            for evidence_id in token.evidence_ids
        )
    )
    # Canonical lineage prefixes recognition references with ``evidence:``;
    # native audit slices expose the same qualified ``source_hash:token_id``
    # reference without that namespace prefix.
    qualified_affected_evidence = {
        evidence_id.removeprefix("evidence:") for evidence_id in affected_evidence
    }
    if not set(candidate.cited_recognition_evidence_ids).issubset(
        qualified_affected_evidence
    ):
        raise ValueError("native discovery cites Recognition Evidence outside affected tokens")

    tokens = list(transcript.tokens)
    spans = list(transcript.spans)
    replacement_span_id: str | None = None
    if decision.action == "accept_exact_candidate":
        speakers = {token.speaker for token in affected_tokens}
        if len(speakers) != 1:
            raise ValueError("one native decision cannot merge multiple speakers")
        replacement_span_id = _audio_span_id(
            transcript.normalized_audio_hash,
            decision.target_start_ms,
            decision.target_end_ms,
        )
        replacement_identity = {
            "span_id": replacement_span_id,
            "decision_event_id": decision.event_id,
            "affected_token_ids": decision.affected_token_ids,
            "candidate_discovery_id": candidate.id,
            "candidate_text": candidate.candidate_text,
            "timing_basis": "coarse_span",
        }
        confidences = [
            token.confidence for token in affected_tokens if token.confidence is not None
        ]
        replacement_token = CanonicalToken(
            id=f"canonical-{hash_object(replacement_identity)}",
            text=candidate.candidate_text,
            start_ms=None,
            end_ms=None,
            timing_basis="coarse_span",
            alignment_evidence_ids=(),
            speaker=affected_tokens[0].speaker,
            evidence_ids=affected_evidence,
            confidence=min(confidences) if confidences else None,
        )
        prefix = tuple(
            transcript.tokens[position]
            for position in selected_token_positions
            if position < affected_positions[0]
        )
        suffix = tuple(
            transcript.tokens[position]
            for position in selected_token_positions
            if position > affected_positions[-1]
        )
        replacement_span_tokens = (*prefix, replacement_token, *suffix)
        tokens[affected_positions[0] : affected_positions[-1] + 1] = [replacement_token]
        replacement_span = CanonicalSpan(
            id=replacement_span_id,
            token_ids=tuple(token.id for token in replacement_span_tokens),
            start_ms=decision.target_start_ms,
            end_ms=decision.target_end_ms,
            alignment="coarse",
            alignment_evidence_ids=(),
            lineage=tuple(
                dict.fromkeys(
                    ancestor_id
                    for span in selected_spans
                    for ancestor_id in (span.id, *span.lineage)
                    if ancestor_id != replacement_span_id
                )
            ),
        )
        spans[positions[0] : positions[-1] + 1] = [replacement_span]

    span_replacements = (
        {span_id: replacement_span_id for span_id in decision.target_span_ids}
        if replacement_span_id is not None
        else {}
    )
    risks: list[RiskRecord] = []
    for risk in current.risks:
        if risk.issue.code == "native_full_audit_requires_resolution":
            continue
        issue = risk.issue
        remapped_issue_spans = tuple(
            dict.fromkeys(span_replacements.get(span_id, span_id) for span_id in issue.span_ids)
        )
        if issue.span_ids != remapped_issue_spans:
            issue = issue.model_copy(update={"span_ids": remapped_issue_spans})
        risks.append(
            replace(
                risk,
                issue=issue,
                audio_span_ids=tuple(
                    dict.fromkeys(
                        span_replacements.get(span_id, span_id)
                        for span_id in risk.audio_span_ids
                    )
                ),
            )
        )
    generation_warnings = tuple(
        GenerationWarning.model_validate(
            {
                **warning.model_dump(mode="json"),
                "status": "requires_full_audit",
                "mitigation_receipt_set_hash": None,
            }
        )
        if warning.status == "mitigated"
        else warning
        for warning in current.generation_warnings
    )
    new_transcript = _seal_transcript(
        episode_id=transcript.episode_id,
        revision=transcript.revision + 1,
        source_audio_hash=transcript.source_audio_hash,
        normalized_audio_hash=transcript.normalized_audio_hash,
        normalization_receipt_hash=transcript.normalization_receipt_hash,
        evidence_hash=transcript.evidence_hash,
        orthographic_projection_evidence_hash=(
            transcript.orthographic_projection_evidence_hash
        ),
        reference_evidence_hash=transcript.reference_evidence_hash,
        ledger_hash=ledger_hash,
        policy_hash=transcript.policy_hash,
        acceptance_policy=transcript.acceptance_policy,
        tokens=tokens,
        spans=spans,
        risks=risks,
        generation_warnings=generation_warnings,
        full_audit_receipt_set_hash=None,
        verified_speech_coverage_receipt_hash=(
            transcript.verified_speech_coverage_receipt_hash
        ),
    )
    return CanonicalBuildResult(
        outcome="accepted" if new_transcript.status == "accepted" else "needs_review",
        transcript=new_transcript,
        candidates=current.candidates,
        risks=tuple(risks),
        reference_evidence_hash=current.reference_evidence_hash,
        generation_warnings=generation_warnings,
    )


def prepare_canonical_resolution(
    current: CanonicalBuildResult,
    decision: CorrectionDecision,
    *,
    ledger: CorrectionLedger,
) -> tuple[CanonicalBuildResult, LedgerEntry]:
    """Prepare an immutable child and its exact not-yet-appended Ledger entry."""

    fingerprint = review_target_fingerprint(
        current.transcript,
        decision.target_span_ids,
    )
    if decision.evidence_fingerprint != fingerprint:
        raise StaleFingerprintError(
            f"decision {decision.event_id!r} reviewed {decision.evidence_fingerprint}, "
            f"current target is {fingerprint}"
        )
    entry = ledger.prepare(
        decision,
        current_fingerprint=fingerprint,
        expected_head=current.transcript.ledger_hash,
    )
    typed = CorrectionDecision.model_validate(entry.decision)
    return (
        derive_canonical_resolution(current, typed, ledger_hash=entry.entry_hash),
        entry,
    )


def resolve_canonical(
    current: CanonicalBuildResult,
    decision: CorrectionDecision,
    *,
    ledger: CorrectionLedger,
) -> CanonicalBuildResult:
    """Append one reviewed decision and derive a new immutable Generation."""

    result, entry = prepare_canonical_resolution(current, decision, ledger=ledger)
    appended = ledger.append_prepared(entry)
    if appended != entry:  # pragma: no cover - ledger verifies this independently
        raise AssertionError("Correction Ledger changed a prepared entry")
    return result


def replay_correction_ledger(
    current: CanonicalBuildResult,
    ledger: CorrectionLedger,
    *,
    allowed_reference_ids: Sequence[str] = (),
) -> CanonicalBuildResult:
    """Replay only Decisions whose exact Evidence still belongs to current truth."""

    result = current
    allowed_references = set(allowed_reference_ids)
    for entry in ledger.entries():
        decision = CorrectionDecision.model_validate(entry.decision)
        if decision.episode_id != result.transcript.episode_id:
            continue
        available_spans = {span.id for span in result.transcript.spans}
        if not set(decision.target_span_ids).issubset(available_spans):
            continue
        current_fingerprint = review_target_fingerprint(
            result.transcript,
            decision.target_span_ids,
        )
        span_tokens = {span.id: span.token_ids for span in result.transcript.spans}
        tokens = {token.id: token for token in result.transcript.tokens}
        target_token_ids = tuple(
            token_id
            for span_id in decision.target_span_ids
            for token_id in span_tokens[span_id]
        )
        current_target_evidence = {
            evidence_id
            for token_id in target_token_ids
            for evidence_id in tokens[token_id].evidence_ids
        }
        retained_audio_evidence = tuple(
            evidence_id
            for evidence_id in decision.audio_evidence_ids
            if evidence_id in current_target_evidence
        )
        retained_reference_evidence = tuple(
            evidence_id
            for evidence_id in decision.reference_evidence_ids
            if evidence_id in allowed_references
        )
        stale_reasons = tuple(
            reason
            for reason, stale in (
                (
                    "target_fingerprint",
                    current_fingerprint != decision.evidence_fingerprint,
                ),
                (
                    "audio_evidence_membership",
                    len(retained_audio_evidence) != len(decision.audio_evidence_ids),
                ),
                (
                    "reference_evidence_membership",
                    len(retained_reference_evidence)
                    != len(decision.reference_evidence_ids),
                ),
            )
            if stale
        )
        if stale_reasons:
            current_text = "".join(tokens[token_id].text for token_id in target_token_ids)
            stale_issue_identity = {
                "code": "ledger_replay_stale",
                "event_id": decision.event_id,
                "span_ids": decision.target_span_ids,
                "fingerprint": current_fingerprint,
                "reasons": stale_reasons,
                "current_target_evidence_hash": hash_object(
                    tuple(sorted(current_target_evidence))
                ),
                "current_reference_membership_hash": hash_object(
                    tuple(sorted(allowed_references))
                ),
            }
            issue = ReviewIssue(
                id=f"issue-{hash_object(stale_issue_identity)}",
                risk="text",
                severity="high",
                code="ledger_replay_stale",
                span_ids=decision.target_span_ids,
                audio_evidence_ids=retained_audio_evidence,
                reference_evidence_ids=retained_reference_evidence,
                candidates=(current_text,),
                status="unresolved",
            )
            result = add_review_risks(
                result,
                (
                    RiskRecord(
                        issue=issue,
                        audio_span_ids=decision.target_span_ids,
                        evidence_ids=retained_audio_evidence,
                        supporting_reference_ids=retained_reference_evidence,
                    ),
                ),
            )
            continue
        result = _apply_decision(
            result,
            decision,
            ledger_hash=entry.entry_hash,
            require_generation_match=False,
        )
    return result


__all__ = [
    "CanonicalBuildResult",
    "CanonicalOutcome",
    "add_correction_proposals",
    "add_review_risks",
    "attest_full_audit",
    "canonical_generation_id",
    "derive_canonical_resolution",
    "derive_native_canonical_resolution",
    "prepare_canonical_resolution",
    "reconcile_canonical",
    "replay_correction_ledger",
    "resolve_canonical",
    "review_target_fingerprint",
    "without_native_resolution_risk",
]
