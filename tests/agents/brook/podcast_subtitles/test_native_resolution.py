from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles.adapters.reference import (
    LocalReferenceRetriever,
    ReferenceExactLookupRequest,
)
from agents.brook.podcast_subtitles.canonical import (
    CanonicalBuildResult,
    derive_native_canonical_resolution,
    review_target_fingerprint,
)
from agents.brook.podcast_subtitles.correction_acceptance import (
    build_correction_acceptance_verdict,
    correction_acceptance_policy_bytes,
    correction_acceptance_verdict_bytes,
    default_correction_acceptance_policy,
    human_audio_review_receipt_bytes,
)
from agents.brook.podcast_subtitles.hashing import (
    canonical_json_bytes,
    hash_object,
    sha256_bytes,
)
from agents.brook.podcast_subtitles.native_resolution import (
    HumanOriginalConfirmationReceiptV2,
    NativeCorrectionDecisionV2,
    NativeResolutionError,
    NativeResolveCheckpointV2,
    ResolveNativeRequest,
    build_native_correction_decision,
    build_native_resolve_checkpoint,
    build_original_confirmation_authorization,
    default_original_confirmation_policy,
    human_original_confirmation_receipt_bytes,
    native_correction_decision_bytes,
    original_confirmation_authorization_bytes,
    original_confirmation_policy_bytes,
    verify_human_original_confirmation_receipt,
    verify_native_correction_decision,
    verify_native_reference_authority_proof,
    verify_original_confirmation_authorization,
)
from agents.brook.podcast_subtitles.reference_claims import (
    ReferenceAuthorityTargetV2,
    ReferenceExactClaimSpecV2,
    build_reference_authority_proof,
    reference_authority_proof_bytes,
)
from tests.agents.brook.podcast_subtitles.test_correction_acceptance import (
    _authoritative_proof,
    _human_audio_receipt,
    _proof_from_sources,
)
from tests.agents.brook.podcast_subtitles.test_correction_execution import build_fixture
from tests.agents.brook.podcast_subtitles.test_full_audit_attestation import (
    _build as build_full_audit,
)
from tests.agents.brook.podcast_subtitles.test_full_audit_attestation import (
    _complete_full_audit,
)
from tests.agents.brook.podcast_subtitles.test_reference_claims import _published_book


def _addressed(model_type, payload: dict[str, object]):
    digest = hash_object(payload)
    return model_type(**payload, id=digest, content_hash=digest)


def _authoritative_proof_context(tmp_path: Path, literal: str):
    source = _published_book(
        tmp_path / "native-authority.md",
        source_id="native-authority",
        text=literal,
    )
    retriever = LocalReferenceRetriever(tmp_path / "native-reference-cas", (source,))
    evidence = retriever.lookup_exact(
        (
            ReferenceExactLookupRequest(
                source_id="native-authority",
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(literal),
            ),
        )
    )
    target = ReferenceAuthorityTargetV2(
        resolution_key="term:exact-candidate",
        scopes=("literal_terminology",),
    )
    proof = build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=(
            ReferenceExactClaimSpecV2(
                resolution_key=target.resolution_key,
                scope="literal_terminology",
                claimed_text=literal,
                origin="exact_extracted_evidence",
                evidence_id=evidence[0].id,
                claim_start=0,
                claim_end=len(literal),
            ),
        ),
        bounded_retrieval_evidence_ids=(evidence[0].id,),
    )
    return retriever, evidence, proof


def _portable_evidence(evidence):
    return tuple(
        item.model_copy(
            update={
                "artifact": item.artifact.model_copy(
                    update={
                        "digest": item.artifact.digest.model_copy(
                            update={
                                "uri": "generation-artifact://reference_sources/"
                                f"{item.artifact.digest.sha256}.bin"
                            }
                        ),
                        "extracted_text": item.artifact.extracted_text.model_copy(
                            update={
                                "uri": "generation-artifact://reference_extractions/"
                                f"{item.artifact.extracted_text.sha256}.json"
                            }
                        ),
                    }
                )
            }
        )
        for item in evidence
    )


def test_native_reference_proof_rebuilds_from_stored_source_and_evidence(
    tmp_path: Path,
) -> None:
    retriever, evidence, proof = _authoritative_proof_context(tmp_path, "精確術語")

    verified = verify_native_reference_authority_proof(
        reference_authority_proof_bytes(proof),
        retriever=retriever,
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        stored_reference_evidence=_portable_evidence(evidence),
    )

    assert verified == proof


def test_native_reference_proof_rejects_request_only_authority_not_in_generation(
    tmp_path: Path,
) -> None:
    retriever, _evidence, proof = _authoritative_proof_context(tmp_path, "精確術語")

    with pytest.raises(NativeResolutionError, match="stored Generation"):
        verify_native_reference_authority_proof(
            reference_authority_proof_bytes(proof),
            retriever=retriever,
            resolution_key="term:exact-candidate",
            reference_scope="literal_terminology",
            stored_reference_evidence=(),
        )


def test_native_reference_proof_rejects_scope_drift_before_authorization(
    tmp_path: Path,
) -> None:
    retriever, evidence, proof = _authoritative_proof_context(tmp_path, "精確術語")

    with pytest.raises(NativeResolutionError, match="targets"):
        verify_native_reference_authority_proof(
            reference_authority_proof_bytes(proof),
            retriever=retriever,
            resolution_key="term:other",
            reference_scope="literal_terminology",
            stored_reference_evidence=_portable_evidence(evidence),
        )


@pytest.fixture(scope="module")
def audited_case(tmp_path_factory: pytest.TempPathFactory):
    tmp_path = tmp_path_factory.mktemp("native-resolution-audit")
    fixture = build_fixture(tmp_path)
    complete = _complete_full_audit(tmp_path, findings=True, fixture=fixture)
    aggregate = build_full_audit(complete)
    candidate = complete[4].candidate_discovery_set.candidates[0]
    return fixture, complete, aggregate, candidate


def _original_receipt(
    candidate,
    aggregate,
    *,
    relation: str = "pronunciations_distinct",
    reviewer_id: str = "reviewer:original:1",
    clip_sha256: str = "4" * 64,
):
    from agents.brook.podcast_subtitles.correction_acceptance import (
        HumanReviewerAttestationV2,
    )
    from shared.schemas.podcast_subtitles_v2_audio_audit import AudioDiscoveredCandidateV2

    reviewer = HumanReviewerAttestationV2(
        actor_kind="human",
        reviewer_id=reviewer_id,
        identity_provider="nakama-owner-registry",
        identity_record_sha256="1" * 64,
        attestation_method="authenticated_ui_confirmation",
        attestation_record_sha256="2" * 64,
    )
    is_audio_candidate = isinstance(candidate, AudioDiscoveredCandidateV2)
    payload = {
        "schema_version": 2,
        "candidate_discovery_id": candidate.id,
        "candidate_discovery_hash": candidate.content_hash,
        "full_audit_aggregate_id": aggregate.id,
        "full_audit_aggregate_hash": aggregate.content_hash,
        "normalized_audio_hash": aggregate.normalized_audio_hash,
        "clip_lineage_kind": (
            "audio_audit_candidate_clip" if is_audio_candidate else "recognition_token_window"
        ),
        "clip_lineage_parent_id": candidate.packet_id if is_audio_candidate else candidate.id,
        "clip_extraction_policy_hash": "3" * 64,
        "clip_sha256": candidate.clip_hash if is_audio_candidate else clip_sha256,
        "clip_size_bytes": 128,
        "clip_start_ms": 0,
        "clip_end_ms": 2_000,
        "affected_token_ids": candidate.affected_token_ids,
        "cited_span_ids": candidate.cited_span_ids,
        "cited_recognition_evidence_ids": candidate.cited_recognition_evidence_ids,
        "original_text": candidate.observed_text,
        "rejected_candidate_text": candidate.candidate_text,
        "pronunciation_outcome": "compatible",
        "candidate_pronunciation_relation": relation,
        "reasoning_code": (
            "audible_pronunciation_matches_original_and_differs_candidate"
            if relation == "pronunciations_distinct"
            else "audio_matches_original_but_cannot_choose_orthography"
        ),
        "notes_digest": "5" * 64,
        "reviewer": reviewer,
        "reviewed_at_utc": "2026-08-13T03:04:05Z",
        "authority": "human_pronunciation_review_not_spelling_or_text_mutation_authority",
    }
    return _addressed(HumanOriginalConfirmationReceiptV2, payload)


def _original_arguments(audited_case, receipt, *, proof=None, policy=None):
    fixture, _complete, aggregate, candidate = audited_case
    return {
        "candidate": candidate,
        "full_audit_aggregate": aggregate,
        "recognition_evidence": fixture[7],
        "resolution_key": "term:exact-candidate",
        "reference_scope": "literal_terminology",
        "reference_proof": proof,
        "human_original_receipts": (receipt,),
        "policy": policy or default_original_confirmation_policy(),
    }


def _acceptance_arguments(tmp_path: Path, audited_case, *, outcome: str = "compatible"):
    fixture, _complete, aggregate, candidate = audited_case
    receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome=outcome,
        discriminable=outcome != "indeterminate",
    )
    proof = _authoritative_proof(tmp_path, candidate.candidate_text)
    policy = default_correction_acceptance_policy()
    arguments = {
        "candidate": candidate,
        "full_audit_aggregate": aggregate,
        "recognition_evidence": fixture[7],
        "resolution_key": "term:exact-candidate",
        "reference_scope": "literal_terminology",
        "reference_proof": proof,
        "human_audio_receipts": (receipt,),
        "human_reference_adjudication": None,
        "policy": policy,
    }
    return candidate, receipt, proof, policy, arguments


def test_human_original_confirmation_receipt_is_exact_and_canonical(audited_case) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(candidate, aggregate)

    exact = human_original_confirmation_receipt_bytes(receipt)

    assert verify_human_original_confirmation_receipt(exact) == receipt
    assert exact == canonical_json_bytes(receipt)
    assert receipt.pronunciation_outcome == "compatible"
    assert receipt.authority.endswith("not_spelling_or_text_mutation_authority")


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("pronunciation_outcome",), "incompatible", "literal"),
        (
            ("reasoning_code",),
            "audio_matches_original_but_cannot_choose_orthography",
            "reasoning",
        ),
        (("affected_token_ids",), (), "requires exact token"),
        (("reviewed_at_utc",), "2026-08-13T03:04:05+00:00", "second-precision"),
    ],
)
def test_human_original_receipt_rejects_contradictory_or_weak_claims(
    audited_case,
    path: tuple[str, ...],
    value: object,
    match: str,
) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    payload = _original_receipt(candidate, aggregate).model_dump(mode="python")
    cursor = payload
    for component in path[:-1]:
        cursor = cursor[component]
    cursor[path[-1]] = value
    identity = {key: item for key, item in payload.items() if key not in {"id", "content_hash"}}
    digest = hash_object(identity)
    payload["id"] = digest
    payload["content_hash"] = digest

    with pytest.raises(ValidationError, match=match):
        HumanOriginalConfirmationReceiptV2.model_validate(payload)


def test_original_confirmation_distinct_pronunciation_needs_no_spelling_claim(
    audited_case,
) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(candidate, aggregate, relation="pronunciations_distinct")

    authorization = build_original_confirmation_authorization(
        **_original_arguments(audited_case, receipt)
    )

    assert authorization.action == "confirm_original"
    assert authorization.reasons == ("all_required_evidence_confirms_exact_original",)
    assert authorization.reference_proof_id is None


def test_homophone_original_without_complete_reference_authority_defers(audited_case) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(
        candidate,
        aggregate,
        relation="homophone_or_orthographically_indiscriminable",
    )

    authorization = build_original_confirmation_authorization(
        **_original_arguments(audited_case, receipt)
    )

    assert authorization.action == "defer"
    assert authorization.reasons == ("reference_proof_missing_for_orthography",)


def test_homophone_original_requires_complete_active_authoritative_literal(
    tmp_path: Path,
    audited_case,
) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(
        candidate,
        aggregate,
        relation="homophone_or_orthographically_indiscriminable",
    )
    proof = _authoritative_proof(tmp_path, candidate.observed_text)

    authorization = build_original_confirmation_authorization(
        **_original_arguments(audited_case, receipt, proof=proof)
    )

    assert authorization.action == "confirm_original"
    assert authorization.selected_reference_literal == candidate.observed_text
    assert authorization.reference_coverage_receipt_id == proof.coverage.id
    assert authorization.reference_conflict_set_id == proof.conflicts.id


def test_homophone_candidate_authority_does_not_confirm_original(
    tmp_path: Path,
    audited_case,
) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(
        candidate,
        aggregate,
        relation="homophone_or_orthographically_indiscriminable",
    )
    proof = _authoritative_proof(tmp_path, candidate.candidate_text)

    authorization = build_original_confirmation_authorization(
        **_original_arguments(audited_case, receipt, proof=proof)
    )

    assert authorization.action == "defer"
    assert "reference_authoritative_original_literal_missing" in authorization.reasons


def test_homophone_conflicting_authority_defers_even_with_original_claim(
    tmp_path: Path,
    audited_case,
) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(
        candidate,
        aggregate,
        relation="homophone_or_orthographically_indiscriminable",
    )
    proof = _proof_from_sources(
        tmp_path,
        texts=(candidate.observed_text, candidate.candidate_text),
        source_kinds=("book", "book"),
    )

    authorization = build_original_confirmation_authorization(
        **_original_arguments(audited_case, receipt, proof=proof)
    )

    assert authorization.action == "defer"
    assert "reference_conflict_unresolved" in authorization.reasons


def test_original_confirmation_quorum_is_independent_and_exact(audited_case) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    first = _original_receipt(candidate, aggregate, reviewer_id="reviewer:same")
    second = _original_receipt(candidate, aggregate, reviewer_id="reviewer:same")
    policy = default_original_confirmation_policy(minimum_human_reviewers=2)
    arguments = _original_arguments(audited_case, first, policy=policy)
    arguments["human_original_receipts"] = (first, second)

    authorization = build_original_confirmation_authorization(**arguments)

    assert authorization.action == "defer"
    assert "duplicate_human_reviewer" in authorization.reasons
    assert "human_quorum_not_met" in authorization.reasons


def test_original_confirmation_receipt_lineage_drift_defers(audited_case) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    exact = _original_receipt(candidate, aggregate)
    payload = exact.model_dump(mode="python", exclude={"id", "content_hash"})
    payload["clip_lineage_parent_id"] = "9" * 64
    receipt = _addressed(HumanOriginalConfirmationReceiptV2, payload)

    authorization = build_original_confirmation_authorization(
        **_original_arguments(audited_case, receipt)
    )

    assert authorization.action == "defer"
    assert authorization.reasons == (
        "human_receipt_lineage_mismatch",
        "human_quorum_not_met",
    )


def test_original_authorization_exact_bytes_rebuild_from_frozen_parents(
    tmp_path: Path,
    audited_case,
) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(
        candidate,
        aggregate,
        relation="homophone_or_orthographically_indiscriminable",
    )
    proof = _authoritative_proof(tmp_path, candidate.observed_text)
    arguments = _original_arguments(audited_case, receipt, proof=proof)
    authorization = build_original_confirmation_authorization(**arguments)

    replayed = verify_original_confirmation_authorization(
        original_confirmation_authorization_bytes(authorization),
        **arguments,
    )

    assert replayed == authorization


def test_original_authorization_tamper_and_parent_drift_fail_closed(
    audited_case,
) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(candidate, aggregate)
    arguments = _original_arguments(audited_case, receipt)
    authorization = build_original_confirmation_authorization(**arguments)
    payload = json.loads(original_confirmation_authorization_bytes(authorization))
    payload["original_text"] += " forged"
    forged = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(NativeResolutionError):
        verify_original_confirmation_authorization(forged, **arguments)


@pytest.mark.parametrize(
    ("acceptance_outcome", "expected_action"),
    [
        ("compatible", "accept_exact_candidate"),
        ("indeterminate", "defer"),
        ("incompatible", "reject_candidate"),
    ],
)
def test_native_decision_maps_acceptance_verdict_without_free_text(
    tmp_path: Path,
    audited_case,
    acceptance_outcome: str,
    expected_action: str,
) -> None:
    candidate, _receipt, _proof, _policy, arguments = _acceptance_arguments(
        tmp_path,
        audited_case,
        outcome=acceptance_outcome,
    )
    verdict = build_correction_acceptance_verdict(**arguments)

    event = build_native_correction_decision(
        episode_id="episode-native",
        parent_generation_id="generation-" + "a" * 64,
        reviewed_fingerprint="b" * 64,
        target_start_ms=0,
        target_end_ms=2_000,
        candidate=candidate,
        authorization=verdict,
    )

    assert event.action == expected_action
    assert "replacement_text" not in NativeCorrectionDecisionV2.model_fields
    assert "candidate_text" not in NativeCorrectionDecisionV2.model_fields
    assert (event.authorized_literal_sha256 is not None) == (
        expected_action == "accept_exact_candidate"
    )


def test_native_decision_maps_separate_original_authorization(audited_case) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(candidate, aggregate)
    authorization = build_original_confirmation_authorization(
        **_original_arguments(audited_case, receipt)
    )

    event = build_native_correction_decision(
        episode_id="episode-native",
        parent_generation_id="generation-" + "a" * 64,
        reviewed_fingerprint="b" * 64,
        target_start_ms=0,
        target_end_ms=2_000,
        candidate=candidate,
        authorization=authorization,
    )

    assert event.action == "confirm_original"
    assert event.mutates_text is False
    assert event.authorization_kind == "original_confirmation"


def test_native_decision_round_trip_requires_same_authorization_and_candidate(
    audited_case,
) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(candidate, aggregate)
    authorization = build_original_confirmation_authorization(
        **_original_arguments(audited_case, receipt)
    )
    arguments = {
        "episode_id": "episode-native",
        "parent_generation_id": "generation-" + "a" * 64,
        "reviewed_fingerprint": "b" * 64,
        "target_start_ms": 0,
        "target_end_ms": 2_000,
        "candidate": candidate,
        "authorization": authorization,
    }
    event = build_native_correction_decision(**arguments)

    assert (
        verify_native_correction_decision(native_correction_decision_bytes(event), **arguments)
        == event
    )

    drifted = dict(arguments)
    drifted["target_end_ms"] = 1_999
    with pytest.raises(NativeResolutionError, match="frozen parents"):
        verify_native_correction_decision(native_correction_decision_bytes(event), **drifted)


def _native_parent(audited_case) -> CanonicalBuildResult:
    fixture, _complete, _aggregate, candidate = audited_case
    cited_evidence = tuple(f"evidence:{item}" for item in candidate.cited_recognition_evidence_ids)
    tokens = tuple(
        token.model_copy(update={"evidence_ids": cited_evidence})
        if token.id in candidate.affected_token_ids
        else token
        for token in fixture[5].tokens
    )
    transcript = fixture[5].model_copy(
        update={
            "generation_id": "generation-" + "a" * 64,
            "tokens": tokens,
        }
    )
    return CanonicalBuildResult(
        outcome="needs_review",
        transcript=transcript,
        candidates=(),
        risks=(),
        reference_evidence_hash=transcript.reference_evidence_hash,
    )


def _native_event(parent, candidate, authorization):
    span_by_id = {span.id: span for span in parent.transcript.spans}
    cited = tuple(span_by_id[span_id] for span_id in candidate.cited_span_ids)
    return build_native_correction_decision(
        episode_id=parent.transcript.episode_id,
        parent_generation_id=parent.transcript.generation_id,
        reviewed_fingerprint=review_target_fingerprint(parent.transcript, candidate.cited_span_ids),
        target_start_ms=cited[0].start_ms,
        target_end_ms=cited[-1].end_ms,
        candidate=candidate,
        authorization=authorization,
    )


def test_native_projection_changes_only_exact_affected_token_range(
    tmp_path: Path,
    audited_case,
) -> None:
    candidate, _receipt, _proof, _policy, arguments = _acceptance_arguments(tmp_path, audited_case)
    parent = _native_parent(audited_case)
    verdict = build_correction_acceptance_verdict(**arguments)
    event = _native_event(parent, candidate, verdict)

    child = derive_native_canonical_resolution(
        parent,
        event,
        candidate,
        ledger_hash="e" * 64,
    )

    affected = set(candidate.affected_token_ids)
    parent_unaffected = tuple(
        token for token in parent.transcript.tokens if token.id not in affected
    )
    parent_unaffected_ids = {item.id for item in parent_unaffected}
    child_unaffected = tuple(
        token for token in child.transcript.tokens if token.id in parent_unaffected_ids
    )
    assert child_unaffected == parent_unaffected
    assert candidate.candidate_text in tuple(token.text for token in child.transcript.tokens)
    assert not affected.intersection(token.id for token in child.transcript.tokens)
    assert child.transcript.revision == parent.transcript.revision + 1
    assert child.transcript.ledger_hash == "e" * 64
    assert child.transcript.full_audit_receipt_set_hash is None
    assert child.outcome == "needs_review"


def test_native_no_text_decision_preserves_every_token_and_span(audited_case) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(candidate, aggregate)
    authorization = build_original_confirmation_authorization(
        **_original_arguments(audited_case, receipt)
    )
    parent = _native_parent(audited_case)
    event = _native_event(parent, candidate, authorization)

    child = derive_native_canonical_resolution(
        parent,
        event,
        candidate,
        ledger_hash="f" * 64,
    )

    assert child.transcript.tokens == parent.transcript.tokens
    assert child.transcript.spans == parent.transcript.spans
    assert child.transcript.generation_id != parent.transcript.generation_id


def test_native_projection_rejects_noncontiguous_affected_range(
    tmp_path: Path,
    audited_case,
) -> None:
    candidate, _receipt, _proof, _policy, arguments = _acceptance_arguments(tmp_path, audited_case)
    parent = _native_parent(audited_case)
    middle = parent.transcript.tokens[1]
    payload = candidate.model_dump(mode="python", exclude={"id", "content_hash"})
    payload["affected_token_ids"] = (
        parent.transcript.tokens[0].id,
        parent.transcript.tokens[2].id,
    )
    payload["observed_text"] = parent.transcript.tokens[0].text + parent.transcript.tokens[2].text
    payload["cited_span_ids"] = tuple(span.id for span in parent.transcript.spans[:3])
    payload["cited_recognition_evidence_ids"] = tuple(
        dict.fromkeys(
            (*parent.transcript.tokens[0].evidence_ids, *parent.transcript.tokens[2].evidence_ids)
        )
    )
    drifted = _addressed(type(candidate), payload)
    verdict = build_correction_acceptance_verdict(**arguments)
    valid_event = _native_event(parent, candidate, verdict)
    event_payload = valid_event.model_dump(mode="python", exclude={"event_id", "content_hash"})
    event_payload.update(
        {
            "candidate_discovery_id": drifted.id,
            "candidate_discovery_hash": drifted.content_hash,
            "target_span_ids": drifted.cited_span_ids,
            "affected_token_ids": drifted.affected_token_ids,
            "cited_recognition_evidence_ids": drifted.cited_recognition_evidence_ids,
            "target_start_ms": parent.transcript.spans[0].start_ms,
            "target_end_ms": parent.transcript.spans[2].end_ms,
            "reviewed_fingerprint": review_target_fingerprint(
                parent.transcript, drifted.cited_span_ids
            ),
            "original_literal_sha256": sha256_bytes(drifted.observed_text.encode()),
            "candidate_literal_sha256": sha256_bytes(drifted.candidate_text.encode()),
            "authorized_literal_sha256": sha256_bytes(drifted.candidate_text.encode()),
        }
    )
    digest = hash_object(event_payload)
    drifted_event = NativeCorrectionDecisionV2(
        **event_payload,
        event_id=f"native-resolution-{digest}",
        content_hash=digest,
    )

    assert middle.id not in drifted.affected_token_ids
    with pytest.raises(ValueError, match="affected tokens must be ordered and contiguous"):
        derive_native_canonical_resolution(
            parent,
            drifted_event,
            drifted,
            ledger_hash="e" * 64,
        )


def test_resolve_native_request_has_one_exact_authorization_branch(
    tmp_path: Path,
    audited_case,
) -> None:
    candidate, receipt, proof, policy, arguments = _acceptance_arguments(tmp_path, audited_case)
    verdict = build_correction_acceptance_verdict(**arguments)
    request = ResolveNativeRequest(
        generation_id="generation-" + "a" * 64,
        correction_acceptance_verdict=correction_acceptance_verdict_bytes(verdict),
        correction_acceptance_policy=correction_acceptance_policy_bytes(policy),
        human_audio_receipts=(human_audio_review_receipt_bytes(receipt),),
        reference_authority_proof=canonical_json_bytes(proof),
    )

    assert request.generation_id.endswith("a" * 64)
    request_parameters = set(inspect.signature(ResolveNativeRequest).parameters)
    assert not ({"replacement_text", "candidate_text"} & request_parameters)
    with pytest.raises(TypeError):
        ResolveNativeRequest(  # type: ignore[call-arg]
            generation_id=request.generation_id,
            correction_acceptance_verdict=request.correction_acceptance_verdict,
            replacement_text=candidate.candidate_text,
        )


def test_resolve_native_request_rejects_mixed_branches_and_non_bytes(audited_case) -> None:
    _fixture, _complete, aggregate, candidate = audited_case
    receipt = _original_receipt(candidate, aggregate)
    policy = default_original_confirmation_policy()
    authorization = build_original_confirmation_authorization(
        **_original_arguments(audited_case, receipt, policy=policy)
    )
    exact_authorization = original_confirmation_authorization_bytes(authorization)

    with pytest.raises(ValueError, match="exactly one"):
        ResolveNativeRequest(
            generation_id="generation-" + "a" * 64,
            correction_acceptance_verdict=b"{}",
            original_confirmation_authorization=exact_authorization,
        )
    with pytest.raises(TypeError, match="exact bytes"):
        ResolveNativeRequest(
            generation_id="generation-" + "a" * 64,
            original_confirmation_authorization=bytearray(exact_authorization),  # type: ignore[arg-type]
            original_confirmation_policy=original_confirmation_policy_bytes(policy),
            human_original_confirmation_receipts=(
                human_original_confirmation_receipt_bytes(receipt),
            ),
        )


def _native_checkpoint(
    *,
    stage: str = "audit_basis_ready",
    previous_checkpoint_id: str | None = None,
    artifact_hashes: dict[str, str] | None = None,
):
    return build_native_resolve_checkpoint(
        operation_key="1" * 64,
        episode_id="episode-native",
        stage=stage,
        parent_generation_id="generation-" + "2" * 64,
        parent_manifest_hash="3" * 64,
        expected_active_generation_id="generation-" + "2" * 64,
        expected_ledger_head="4" * 64,
        prepared_ledger_entry_hash="5" * 64,
        decision_event_id="native-resolution-" + "6" * 64,
        decision_content_hash="6" * 64,
        authorization_kind="correction_acceptance",
        authorization_id="7" * 64,
        authorization_hash="7" * 64,
        audit_basis_generation_id="generation-" + "8" * 64,
        audit_basis_transcript_hash="9" * 64,
        policy_hash="a" * 64,
        code_hash="b" * 64,
        reference_enrollment_hash="c" * 64,
        adapter_identities_hash="d" * 64,
        artifact_hashes=artifact_hashes or {"native_resolve/basis.json": "e" * 64},
        previous_checkpoint_id=previous_checkpoint_id,
    )


def test_native_resolve_checkpoint_is_content_addressed_and_stage_typed() -> None:
    basis = _native_checkpoint()
    text = _native_checkpoint(
        stage="text_audit_ready",
        previous_checkpoint_id=basis.id,
        artifact_hashes={"native_resolve/text.json": "f" * 64},
    )

    assert basis.id.startswith("native-resolve-checkpoint-")
    assert (
        NativeResolveCheckpointV2.model_validate_json(canonical_json_bytes(basis), strict=True)
        == basis
    )
    assert text.previous_checkpoint_id == basis.id


def test_native_resolve_checkpoint_rejects_identity_and_artifact_tamper() -> None:
    checkpoint = _native_checkpoint()
    payload = checkpoint.model_dump(mode="python")
    payload["audit_basis_generation_id"] = "generation-" + "f" * 64

    with pytest.raises(ValidationError, match="exact content"):
        NativeResolveCheckpointV2.model_validate(payload)

    with pytest.raises(ValueError, match="sorted"):
        _native_checkpoint(
            artifact_hashes={
                "native_resolve/z.json": "e" * 64,
                "native_resolve/a.json": "f" * 64,
            }
        )


def test_native_resolve_checkpoint_rejects_noncontiguous_stage_shape() -> None:
    with pytest.raises(ValueError, match="first native resolve checkpoint"):
        _native_checkpoint(stage="text_audit_ready")
    with pytest.raises(ValueError, match="must not name a predecessor"):
        _native_checkpoint(
            stage="audit_basis_ready",
            previous_checkpoint_id="native-resolve-checkpoint-" + "f" * 64,
        )
