"""End-to-end tracer tests for native Subtitle V2 resolution publication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from agents.brook.podcast_subtitles.adapters.reference import (
    LocalReferenceRetriever,
    ReferenceExactLookupRequest,
)
from agents.brook.podcast_subtitles.correction_acceptance import (
    HumanAudioReviewReceiptV2,
    HumanReviewerAttestationV2,
    build_correction_acceptance_verdict,
    correction_acceptance_policy_bytes,
    correction_acceptance_verdict_bytes,
    default_correction_acceptance_policy,
    human_audio_review_receipt_bytes,
)
from agents.brook.podcast_subtitles.facade import PodcastSubtitleFacade
from agents.brook.podcast_subtitles.full_audit_attestation import (
    FullAuditAggregateAttestationV2,
)
from agents.brook.podcast_subtitles.hashing import (
    canonical_json_bytes,
    hash_object,
)
from agents.brook.podcast_subtitles.loaded_generation import LoadedGenerationState
from agents.brook.podcast_subtitles.module import (
    AdapterIdentity,
    CreateRequest,
    Interrupted,
    NeedsReview,
    PodcastSubtitleV2,
)
from agents.brook.podcast_subtitles.native_resolution import (
    HumanOriginalConfirmationReceiptV2,
    ResolveNativeRequest,
    build_original_confirmation_authorization,
    default_original_confirmation_policy,
    human_original_confirmation_receipt_bytes,
    original_confirmation_authorization_bytes,
    original_confirmation_policy_bytes,
)
from agents.brook.podcast_subtitles.reference_claims import (
    ReferenceAuthorityTargetV2,
    ReferenceEvidenceOmissionSpecV2,
    ReferenceExactClaimSpecV2,
    build_reference_authority_proof,
    reference_authority_proof_bytes,
)
from shared.schemas.podcast_subtitles_v2 import (
    RecognitionEvidence,
    ReferenceEvidence,
    recognition_evidence_content_hash,
)
from shared.schemas.podcast_subtitles_v2_correction import (
    CorrectionCanonicalTranscriptSliceV2,
)
from shared.schemas.podcast_subtitles_v2_text_audit import (
    TextAuditExecutionRecordV2,
    TextAuditProviderRequestV2,
    TextDiscoveredCandidateV2,
)
from tests.agents.brook.podcast_subtitles.test_audio_audit_execution import (
    _response_bytes as audio_response_bytes,
)
from tests.agents.brook.podcast_subtitles.test_full_audit_module_integration import (
    _native_module,
)
from tests.agents.brook.podcast_subtitles.test_module_reference_enrollment import (
    _reference_setup,
)
from tests.agents.brook.podcast_subtitles.test_text_audit_execution import (
    _response_payload as text_response_payload,
)


def _configured_native_module(
    episode_root: Path,
    *,
    workspace: Path,
    retriever: LocalReferenceRetriever,
) -> tuple[PodcastSubtitleV2, Path]:
    identity = AdapterIdentity(
        name="local-reference",
        version=LocalReferenceRetriever.RETRIEVER_VERSION,
        config_hash=hash_object({"index_hash": retriever.index.index_hash}),
        execution_mode="local",
    )
    module, source, _, _ = _native_module(
        episode_root,
        workspace=workspace,
        reference_retriever=retriever,
        reference_retriever_identity=identity,
    )
    return module, source


def _write_root_text_responses(
    pending: Interrupted,
    *,
    candidate_literal: str,
) -> None:
    finding_written = False
    for request_path, response_path in zip(
        pending.packet_paths, pending.response_paths, strict=True
    ):
        payload = text_response_payload(request_path.read_bytes())
        if not finding_written:
            for assessment in payload["assessments"]:  # type: ignore[index]
                if assessment["status"] != "verified_clean_within_text_evidence":
                    continue
                observed = assessment["cited_token_ids"][0]
                # The provider response helper has already bound cited_token_ids; its
                # observed literal is available only in the canonical source packet.
                request = TextAuditProviderRequestV2.model_validate_json(request_path.read_bytes())
                canonical_source = next(
                    item
                    for item in request.untrusted_source_documents
                    if item.binding.kind == "canonical_transcript"
                )
                canonical = CorrectionCanonicalTranscriptSliceV2.model_validate_json(
                    canonical_json_bytes(canonical_source.payload)
                )
                token_by_id = {item.id: item for item in canonical.tokens}
                assessment.update(
                    {
                        "status": "finding",
                        "rationale": "Frozen recognition text conflicts with exact authority.",
                        "affected_token_ids": (assessment["cited_token_ids"][0],),
                        "observed_text": token_by_id[observed].text,
                        "candidate_text": candidate_literal,
                        "evidence_basis": "recognition_text",
                    }
                )
                finding_written = True
                break
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(canonical_json_bytes(payload))
    assert finding_written


def _human_audio_receipt(
    candidate,
    aggregate,
    evidence: tuple[RecognitionEvidence, ...],
) -> HumanAudioReviewReceiptV2:
    registry = {
        f"{recognition_evidence_content_hash(source)}:{token.id}": token
        for source in evidence
        for token in source.tokens
    }
    cited = tuple(registry[item] for item in candidate.cited_recognition_evidence_ids)
    reviewer = HumanReviewerAttestationV2(
        actor_kind="human",
        reviewer_id="reviewer:audio:integration",
        identity_provider="nakama-owner-registry",
        identity_record_sha256="1" * 64,
        attestation_method="authenticated_ui_confirmation",
        attestation_record_sha256="2" * 64,
    )
    payload = {
        "schema_version": 2,
        "candidate_discovery_id": candidate.id,
        "candidate_discovery_hash": candidate.content_hash,
        "full_audit_aggregate_id": aggregate.id,
        "full_audit_aggregate_hash": aggregate.content_hash,
        "normalized_audio_hash": aggregate.normalized_audio_hash,
        "clip_lineage_kind": "recognition_token_window",
        "clip_lineage_parent_id": candidate.id,
        "clip_extraction_policy_hash": "3" * 64,
        "clip_sha256": "4" * 64,
        "clip_size_bytes": 128,
        "clip_start_ms": min(item.start_ms for item in cited),
        "clip_end_ms": max(item.end_ms for item in cited),
        "affected_token_ids": candidate.affected_token_ids,
        "cited_span_ids": candidate.cited_span_ids,
        "cited_recognition_evidence_ids": candidate.cited_recognition_evidence_ids,
        "recognized_text": candidate.observed_text,
        "candidate_text": candidate.candidate_text,
        "pronunciation_outcome": "compatible",
        "discriminable": True,
        "reasoning_code": "audible_pronunciation_matches_exact_candidate",
        "notes_digest": "5" * 64,
        "reviewer": reviewer,
        "reviewed_at_utc": "2026-08-13T01:02:03Z",
        "authority": "human_pronunciation_review_not_spelling_authority",
    }
    digest = hash_object(payload)
    return HumanAudioReviewReceiptV2(**payload, id=digest, content_hash=digest)


def _human_original_receipt(
    candidate: TextDiscoveredCandidateV2,
    aggregate: FullAuditAggregateAttestationV2,
    evidence: tuple[RecognitionEvidence, ...],
) -> HumanOriginalConfirmationReceiptV2:
    registry = {
        f"{recognition_evidence_content_hash(source)}:{token.id}": token
        for source in evidence
        for token in source.tokens
    }
    cited = tuple(registry[item] for item in candidate.cited_recognition_evidence_ids)
    reviewer = HumanReviewerAttestationV2(
        actor_kind="human",
        reviewer_id="reviewer:original:integration",
        identity_provider="nakama-owner-registry",
        identity_record_sha256="6" * 64,
        attestation_method="authenticated_ui_confirmation",
        attestation_record_sha256="7" * 64,
    )
    payload = {
        "schema_version": 2,
        "candidate_discovery_id": candidate.id,
        "candidate_discovery_hash": candidate.content_hash,
        "full_audit_aggregate_id": aggregate.id,
        "full_audit_aggregate_hash": aggregate.content_hash,
        "normalized_audio_hash": aggregate.normalized_audio_hash,
        "clip_lineage_kind": "recognition_token_window",
        "clip_lineage_parent_id": candidate.id,
        "clip_extraction_policy_hash": "8" * 64,
        "clip_sha256": "9" * 64,
        "clip_size_bytes": 128,
        "clip_start_ms": min(item.start_ms for item in cited),
        "clip_end_ms": max(item.end_ms for item in cited),
        "affected_token_ids": candidate.affected_token_ids,
        "cited_span_ids": candidate.cited_span_ids,
        "cited_recognition_evidence_ids": candidate.cited_recognition_evidence_ids,
        "original_text": candidate.observed_text,
        "rejected_candidate_text": candidate.candidate_text,
        "pronunciation_outcome": "compatible",
        "candidate_pronunciation_relation": "pronunciations_distinct",
        "reasoning_code": "audible_pronunciation_matches_original_and_differs_candidate",
        "notes_digest": "a" * 64,
        "reviewer": reviewer,
        "reviewed_at_utc": "2026-08-13T01:02:03Z",
        "authority": "human_pronunciation_review_not_spelling_or_text_mutation_authority",
    }
    digest = hash_object(payload)
    return HumanOriginalConfirmationReceiptV2(
        **payload,
        id=digest,
        content_hash=digest,
    )


@dataclass(frozen=True, slots=True)
class _ParentScenario:
    episode_root: Path
    workspace: Path
    retriever: LocalReferenceRetriever
    parent_module: PodcastSubtitleV2
    parent: NeedsReview
    loaded_parent: LoadedGenerationState
    candidate: TextDiscoveredCandidateV2
    aggregate: FullAuditAggregateAttestationV2


def _write_clean_text_responses(pending: Interrupted) -> None:
    for request_path, response_path in zip(
        pending.packet_paths,
        pending.response_paths,
        strict=True,
    ):
        payload = text_response_payload(request_path.read_bytes())
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(canonical_json_bytes(payload))


def _write_clean_audio_responses(pending: Interrupted) -> None:
    for request_path, clip_path, response_path in zip(
        pending.packet_paths,
        pending.clip_paths,
        pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(
            audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes())
        )


def _prepare_parent_scenario(tmp_path: Path, *, episode_id: str) -> _ParentScenario:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    retriever, enrollment = _reference_setup(tmp_path / "references")
    first, source = _configured_native_module(
        episode_root,
        workspace=workspace,
        retriever=retriever,
    )
    request = CreateRequest(
        episode_id=episode_id,
        source_audio=source,
        reference_enrollments=(enrollment,),
        vocabulary=("å“¥å¤§ç•¢æ¥­å…¸ç¦®",),
    )

    text_pending = first.create(request)
    assert isinstance(text_pending, Interrupted)
    checkpoint = first.store.load_latest_create_checkpoint()
    assert checkpoint is not None
    references = TypeAdapter(tuple[ReferenceEvidence, ...]).validate_json(
        first.store.read_create_checkpoint_artifact(checkpoint, "reference_evidence.json"),
        strict=True,
    )
    assert references
    _write_root_text_responses(text_pending, candidate_literal=references[0].excerpt)

    second, _ = _configured_native_module(
        episode_root,
        workspace=workspace,
        retriever=retriever,
    )
    audio_pending = second.create(request)
    assert isinstance(audio_pending, Interrupted)
    _write_clean_audio_responses(audio_pending)

    parent_module, _ = _configured_native_module(
        episode_root,
        workspace=workspace,
        retriever=retriever,
    )
    parent = parent_module.create(request)
    assert isinstance(parent, NeedsReview)
    loaded_parent = parent_module._load_generation(parent.generation_id, require_active=True)
    text_record = TextAuditExecutionRecordV2.model_validate_json(
        parent_module.store.read_artifact(
            parent.generation_id,
            "native_full_audit/text_audit_execution_record.json",
        ),
        strict=True,
    )
    candidate = text_record.candidate_discovery_set.candidates[0]
    aggregate = parent_module._load_native_generation_model(
        parent.generation_id,
        "native_full_audit/full_audit_aggregate.json",
        FullAuditAggregateAttestationV2,
    )
    assert isinstance(aggregate, FullAuditAggregateAttestationV2)
    return _ParentScenario(
        episode_root=episode_root,
        workspace=workspace,
        retriever=retriever,
        parent_module=parent_module,
        parent=parent,
        loaded_parent=loaded_parent,
        candidate=candidate,
        aggregate=aggregate,
    )


def _acceptance_request(scenario: _ParentScenario) -> ResolveNativeRequest:
    candidate = scenario.candidate
    aggregate = scenario.aggregate
    loaded_parent = scenario.loaded_parent
    receipt = _human_audio_receipt(candidate, aggregate, loaded_parent.recognition.evidence)
    resolution_key = "term:exact-candidate"
    target = ReferenceAuthorityTargetV2(
        resolution_key=resolution_key,
        scopes=("literal_terminology",),
    )
    authority_evidence = tuple(
        scenario.retriever.lookup_exact(
            (
                ReferenceExactLookupRequest(
                    source_id=item.artifact.source_id,
                    extraction_block_index=item.extraction_block_index,
                    excerpt_start=item.excerpt_start,
                    excerpt_end=item.excerpt_end,
                ),
            )
        )[0]
        for item in loaded_parent.references.evidence
    )
    selected = next(item for item in authority_evidence if candidate.candidate_text in item.excerpt)
    start = selected.excerpt.index(candidate.candidate_text)
    proof = build_reference_authority_proof(
        retriever=scenario.retriever,
        targets=(target,),
        evidence=authority_evidence,
        claim_specs=(
            ReferenceExactClaimSpecV2(
                resolution_key=resolution_key,
                scope="literal_terminology",
                claimed_text=candidate.candidate_text,
                origin="exact_extracted_evidence",
                evidence_id=selected.id,
                claim_start=start,
                claim_end=start + len(candidate.candidate_text),
            ),
        ),
        evidence_omissions=tuple(
            ReferenceEvidenceOmissionSpecV2(
                evidence_id=item.id,
                resolution_key=resolution_key,
                scopes=("literal_terminology",),
                reason="no_exact_claim_for_target",
            )
            for item in authority_evidence
            if item.id != selected.id
        ),
        bounded_retrieval_evidence_ids=tuple(item.id for item in authority_evidence),
    )
    policy = default_correction_acceptance_policy()
    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=loaded_parent.recognition.evidence,
        resolution_key=resolution_key,
        reference_scope="literal_terminology",
        reference_proof=proof,
        human_audio_receipts=(receipt,),
        human_reference_adjudication=None,
        policy=policy,
    )
    assert verdict.action == "accept_exact_candidate"
    return ResolveNativeRequest(
        generation_id=scenario.parent.generation_id,
        correction_acceptance_verdict=correction_acceptance_verdict_bytes(verdict),
        correction_acceptance_policy=correction_acceptance_policy_bytes(policy),
        human_audio_receipts=(human_audio_review_receipt_bytes(receipt),),
        reference_authority_proof=reference_authority_proof_bytes(proof),
    )


def _original_confirmation_request(scenario: _ParentScenario) -> ResolveNativeRequest:
    receipt = _human_original_receipt(
        scenario.candidate,
        scenario.aggregate,
        scenario.loaded_parent.recognition.evidence,
    )
    policy = default_original_confirmation_policy()
    authorization = build_original_confirmation_authorization(
        candidate=scenario.candidate,
        full_audit_aggregate=scenario.aggregate,
        recognition_evidence=scenario.loaded_parent.recognition.evidence,
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=None,
        human_original_receipts=(receipt,),
        policy=policy,
    )
    assert authorization.action == "confirm_original"
    return ResolveNativeRequest(
        generation_id=scenario.parent.generation_id,
        original_confirmation_authorization=original_confirmation_authorization_bytes(
            authorization
        ),
        original_confirmation_policy=original_confirmation_policy_bytes(policy),
        human_original_confirmation_receipts=(human_original_confirmation_receipt_bytes(receipt),),
    )


def _prepare_child_for_publication(
    scenario: _ParentScenario,
    native_request: ResolveNativeRequest,
    *,
    child_candidate_literal: str | None = None,
) -> tuple[Interrupted, PodcastSubtitleV2]:
    child_text_pending = scenario.parent_module.resolve_native(native_request)
    assert isinstance(child_text_pending, Interrupted)
    if child_candidate_literal is None:
        _write_clean_text_responses(child_text_pending)
    else:
        _write_root_text_responses(
            child_text_pending,
            candidate_literal=child_candidate_literal,
        )

    child_audio_module, _ = _configured_native_module(
        scenario.episode_root,
        workspace=scenario.workspace,
        retriever=scenario.retriever,
    )
    child_audio_pending = child_audio_module.resolve_native(native_request)
    assert isinstance(child_audio_pending, Interrupted)
    _write_clean_audio_responses(child_audio_pending)

    final_module, _ = _configured_native_module(
        scenario.episode_root,
        workspace=scenario.workspace,
        retriever=scenario.retriever,
    )
    return child_text_pending, final_module


def test_accept_exact_candidate_reaudits_and_publishes_verified_child(
    tmp_path: Path,
) -> None:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    retriever, enrollment = _reference_setup(tmp_path / "references")
    first, source = _configured_native_module(
        episode_root, workspace=workspace, retriever=retriever
    )
    request = CreateRequest(
        episode_id="episode-native-resolution",
        source_audio=source,
        reference_enrollments=(enrollment,),
        vocabulary=("哥大畢業典禮",),
    )

    text_pending = first.create(request)
    assert isinstance(text_pending, Interrupted)
    checkpoint = first.store.load_latest_create_checkpoint()
    assert checkpoint is not None
    references = TypeAdapter(tuple[ReferenceEvidence, ...]).validate_json(
        first.store.read_create_checkpoint_artifact(checkpoint, "reference_evidence.json"),
        strict=True,
    )
    assert references
    candidate_literal = references[0].excerpt
    _write_root_text_responses(text_pending, candidate_literal=candidate_literal)

    second, _ = _configured_native_module(episode_root, workspace=workspace, retriever=retriever)
    audio_pending = second.create(request)
    assert isinstance(audio_pending, Interrupted)
    for request_path, clip_path, response_path in zip(
        audio_pending.packet_paths,
        audio_pending.clip_paths,
        audio_pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(
            audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes())
        )

    parent_module, _ = _configured_native_module(
        episode_root, workspace=workspace, retriever=retriever
    )
    parent = parent_module.create(request)
    assert isinstance(parent, NeedsReview)
    loaded_parent = parent_module._load_generation(parent.generation_id, require_active=True)
    text_record = TextAuditExecutionRecordV2.model_validate_json(
        parent_module.store.read_artifact(
            parent.generation_id,
            "native_full_audit/text_audit_execution_record.json",
        ),
        strict=True,
    )
    candidate = text_record.candidate_discovery_set.candidates[0]
    aggregate = parent_module._load_native_generation_model(
        parent.generation_id,
        "native_full_audit/full_audit_aggregate.json",
        __import__(
            "agents.brook.podcast_subtitles.full_audit_attestation",
            fromlist=["FullAuditAggregateAttestationV2"],
        ).FullAuditAggregateAttestationV2,
    )
    receipt = _human_audio_receipt(candidate, aggregate, loaded_parent.recognition.evidence)
    resolution_key = "term:exact-candidate"
    target = ReferenceAuthorityTargetV2(
        resolution_key=resolution_key,
        scopes=("literal_terminology",),
    )
    stored_authority_evidence = loaded_parent.references.evidence
    authority_evidence = tuple(
        retriever.lookup_exact(
            (
                ReferenceExactLookupRequest(
                    source_id=item.artifact.source_id,
                    extraction_block_index=item.extraction_block_index,
                    excerpt_start=item.excerpt_start,
                    excerpt_end=item.excerpt_end,
                ),
            )
        )[0]
        for item in stored_authority_evidence
    )
    selected = next(item for item in authority_evidence if candidate.candidate_text in item.excerpt)
    start = selected.excerpt.index(candidate.candidate_text)
    proof = build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=authority_evidence,
        claim_specs=(
            ReferenceExactClaimSpecV2(
                resolution_key=resolution_key,
                scope="literal_terminology",
                claimed_text=candidate.candidate_text,
                origin="exact_extracted_evidence",
                evidence_id=selected.id,
                claim_start=start,
                claim_end=start + len(candidate.candidate_text),
            ),
        ),
        evidence_omissions=tuple(
            ReferenceEvidenceOmissionSpecV2(
                evidence_id=item.id,
                resolution_key=resolution_key,
                scopes=("literal_terminology",),
                reason="no_exact_claim_for_target",
            )
            for item in authority_evidence
            if item.id != selected.id
        ),
        bounded_retrieval_evidence_ids=tuple(item.id for item in authority_evidence),
    )
    policy = default_correction_acceptance_policy()
    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=loaded_parent.recognition.evidence,
        resolution_key=resolution_key,
        reference_scope="literal_terminology",
        reference_proof=proof,
        human_audio_receipts=(receipt,),
        human_reference_adjudication=None,
        policy=policy,
    )
    native_request = ResolveNativeRequest(
        generation_id=parent.generation_id,
        correction_acceptance_verdict=correction_acceptance_verdict_bytes(verdict),
        correction_acceptance_policy=correction_acceptance_policy_bytes(policy),
        human_audio_receipts=(human_audio_review_receipt_bytes(receipt),),
        reference_authority_proof=reference_authority_proof_bytes(proof),
    )

    child_text_pending = parent_module.resolve_native(native_request)
    assert isinstance(child_text_pending, Interrupted)
    for request_path, response_path in zip(
        child_text_pending.packet_paths,
        child_text_pending.response_paths,
        strict=True,
    ):
        payload = text_response_payload(request_path.read_bytes())
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(canonical_json_bytes(payload))

    child_audio_module, _ = _configured_native_module(
        episode_root, workspace=workspace, retriever=retriever
    )
    child_audio_pending = child_audio_module.resolve_native(native_request)
    assert isinstance(child_audio_pending, Interrupted)
    for request_path, clip_path, response_path in zip(
        child_audio_pending.packet_paths,
        child_audio_pending.clip_paths,
        child_audio_pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(
            audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes())
        )

    final_module, _ = _configured_native_module(
        episode_root, workspace=workspace, retriever=retriever
    )
    child = final_module.resolve_native(native_request)
    assert isinstance(child, NeedsReview)
    assert child.generation_id == child_text_pending.audit_basis_generation_id
    assert child.generation_id != parent.generation_id
    assert child.manifest.parent_manifest_hash == hash_object(parent.manifest)
    assert child.transcript.revision == parent.transcript.revision + 1
    assert candidate.candidate_text in "".join(item.text for item in child.transcript.tokens)
    assert final_module.store.active_generation_id() == child.generation_id
    assert final_module.ledger.head_hash == child.transcript.ledger_hash
    assert (
        final_module._load_generation(child.generation_id, require_active=True).result.transcript
        == child.transcript
    )
    assert final_module._generation_descends_from(
        child.transcript,
        ancestor_generation_id=parent.generation_id,
    )
    assert PodcastSubtitleFacade(final_module).status().state == "revised"
    assert not any(
        issue.code == "native_full_audit_requires_resolution"
        for issue in child.transcript.review_issues
    )
    ledger_after_publish = final_module.ledger.entries()
    retry_module, _ = _configured_native_module(
        episode_root, workspace=workspace, retriever=retriever
    )
    retried = retry_module.resolve_native(native_request)
    assert retried == child
    assert retry_module.ledger.entries() == ledger_after_publish


def test_confirm_original_preserves_exact_text_and_gates_fresh_child_discovery(
    tmp_path: Path,
) -> None:
    scenario = _prepare_parent_scenario(
        tmp_path,
        episode_id="episode-native-confirm-original",
    )
    native_request = _original_confirmation_request(scenario)
    parent_tokens = scenario.parent.transcript.tokens
    parent_spans = scenario.parent.transcript.spans
    child_text_pending, final_module = _prepare_child_for_publication(
        scenario,
        native_request,
        child_candidate_literal="fresh-child-discovery",
    )

    child = final_module.resolve_native(native_request)

    assert isinstance(child, NeedsReview)
    assert child.generation_id != child_text_pending.audit_basis_generation_id
    assert child.generation_id != scenario.parent.generation_id
    assert child.transcript.tokens == parent_tokens
    assert child.transcript.spans == parent_spans
    child_text_record = TextAuditExecutionRecordV2.model_validate_json(
        final_module.store.read_artifact(
            child.generation_id,
            "native_full_audit/text_audit_execution_record.json",
        ),
        strict=True,
    )
    fresh_candidates = child_text_record.candidate_discovery_set.candidates
    assert fresh_candidates
    native_gates = tuple(
        issue
        for issue in child.transcript.review_issues
        if issue.code == "native_full_audit_requires_resolution"
    )
    assert len(native_gates) == 1
    assert native_gates[0].candidates == tuple(
        item.id for item in sorted(fresh_candidates, key=lambda item: item.id)
    )
    assert set(native_gates[0].span_ids) == {
        span_id for item in fresh_candidates for span_id in item.cited_span_ids
    }
    reloaded = final_module._load_generation(child.generation_id, require_active=True).result
    assert reloaded.transcript == child.transcript


@pytest.mark.parametrize(
    "crash_stage",
    ("before_ledger_append", "before_active_cas", "before_completed_journal"),
)
def test_native_publish_crash_recovers_exact_child_and_retry_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    scenario = _prepare_parent_scenario(
        tmp_path,
        episode_id=f"episode-native-crash-{crash_stage}",
    )
    native_request = _acceptance_request(scenario)
    _, final_module = _prepare_child_for_publication(scenario, native_request)

    if crash_stage == "before_ledger_append":

        def crash_before_ledger_append(*_args, **_kwargs) -> None:
            raise RuntimeError("injected native publish crash")

        monkeypatch.setattr(
            final_module.ledger,
            "append_prepared",
            crash_before_ledger_append,
        )
    elif crash_stage == "before_active_cas":

        def crash_before_active_cas(*_args, **_kwargs) -> None:
            raise RuntimeError("injected native publish crash")

        monkeypatch.setattr(
            final_module.store,
            "set_active_cas_locked",
            crash_before_active_cas,
        )
    else:
        original_write_journal = final_module.store.write_resolution_journal_locked

        def crash_before_completed_journal(payload) -> None:
            if payload["state"] == "completed":
                raise RuntimeError("injected native publish crash")
            original_write_journal(payload)

        monkeypatch.setattr(
            final_module.store,
            "write_resolution_journal_locked",
            crash_before_completed_journal,
        )

    with pytest.raises(RuntimeError, match="injected native publish crash"):
        final_module.resolve_native(native_request)

    with final_module.store.episode_transaction():
        prepared = final_module.store.read_resolution_journal_locked()
    assert prepared is not None and prepared["state"] == "prepared"
    child_generation_id = str(prepared["child_generation_id"])

    recovered_module, _ = _configured_native_module(
        scenario.episode_root,
        workspace=scenario.workspace,
        retriever=scenario.retriever,
    )
    assert recovered_module.store.active_generation_id() == child_generation_id
    assert recovered_module.ledger.head_hash == prepared["child_ledger_hash"]
    recovered = recovered_module._load_generation(
        child_generation_id,
        require_active=True,
    ).result
    with recovered_module.store.episode_transaction():
        completed = recovered_module.store.read_resolution_journal_locked()
    assert completed is not None and completed["state"] == "completed"
    ledger_after_recovery = recovered_module.ledger.entries()

    retried = recovered_module.resolve_native(native_request)

    assert isinstance(retried, NeedsReview)
    assert retried.generation_id == child_generation_id
    assert retried.transcript == recovered.transcript
    assert recovered_module.ledger.entries() == ledger_after_recovery
