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
from agents.brook.podcast_subtitles.correction_acceptance import (
    HumanReviewerAttestationV2,
)
from agents.brook.podcast_subtitles.correction_authority import (
    CorrectionAuthorityError,
    CorrectionAuthorityPolicyV3,
    HumanPronunciationRelationReceiptV3,
    StoredReferenceEnrollmentSnapshotV3,
    StoredReferenceEnrollmentV3,
    build_correction_authority_verdict,
    build_human_pronunciation_relation_receipt,
    build_reference_lookup_state,
    correction_authority_verdict_bytes,
    default_correction_authority_policy,
    pronunciation_relation_receipt_bytes,
    reference_lookup_state_bytes,
    verify_correction_authority_verdict,
    verify_pronunciation_relation_receipt,
    verify_reference_lookup_state,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object
from agents.brook.podcast_subtitles.reference_claims import (
    ReferenceAuthorityTargetV2,
    ReferenceExactClaimSpecV2,
    build_reference_authority_proof,
)
from tests.agents.brook.podcast_subtitles.test_correction_acceptance import (
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

H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64
H5 = "5" * 64
H6 = "6" * 64
H7 = "7" * 64


def _addressed(model_type, payload: dict[str, object]):
    digest = hash_object(payload)
    return model_type(**payload, id=digest, content_hash=digest)


@pytest.fixture(scope="module")
def audited_case(tmp_path_factory: pytest.TempPathFactory):
    tmp_path = tmp_path_factory.mktemp("correction-authority-audit")
    fixture = build_fixture(tmp_path)
    complete = _complete_full_audit(tmp_path, findings=True, fixture=fixture)
    aggregate = build_full_audit(complete)
    candidate = complete[4].candidate_discovery_set.candidates[0]
    return fixture, aggregate, candidate


def _named_case(audited_case, *, original: str, candidate_text: str):
    fixture, aggregate, candidate = audited_case
    candidate_payload = candidate.model_dump(mode="python", exclude={"id", "content_hash"})
    candidate_payload.update(observed_text=original, candidate_text=candidate_text)
    renamed = _addressed(type(candidate), candidate_payload)
    aggregate_payload = aggregate.model_dump(mode="python", exclude={"id", "content_hash"})
    aggregate_payload["text_discovery_ids"] = tuple(
        renamed.id if item == candidate.id else item for item in aggregate.text_discovery_ids
    )
    renamed_aggregate = _addressed(type(aggregate), aggregate_payload)
    return fixture, renamed_aggregate, renamed


def _reviewer(reviewer_id: str = "reviewer:audio:1") -> HumanReviewerAttestationV2:
    return HumanReviewerAttestationV2(
        actor_kind="human",
        reviewer_id=reviewer_id,
        identity_provider="nakama-owner-registry",
        identity_record_sha256=H1,
        attestation_method="authenticated_ui_confirmation",
        attestation_record_sha256=H2,
    )


def _receipt(
    fixture,
    aggregate,
    candidate,
    *,
    reasoning: str,
    reviewer_id: str = "reviewer:audio:1",
    reviewed_at: str = "2026-08-13T01:02:03Z",
):
    return build_human_pronunciation_relation_receipt(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        clip_extraction_policy_hash=H3,
        clip_sha256=H4,
        clip_size_bytes=128,
        clip_start_ms=0,
        clip_end_ms=2_000,
        reasoning_code=reasoning,
        notes_digest=H5,
        reviewer=_reviewer(reviewer_id),
        reviewed_at_utc=reviewed_at,
    )


def _agreeing_receipts(fixture, aggregate, candidate, *, reasoning: str):
    return tuple(
        sorted(
            (
                _receipt(
                    fixture,
                    aggregate,
                    candidate,
                    reasoning=reasoning,
                    reviewer_id=f"reviewer:audio:{index}",
                    reviewed_at=f"2026-08-13T01:02:0{index}Z",
                )
                for index in (1, 2)
            ),
            key=lambda item: item.id,
        )
    )


def _proof(tmp_path: Path, candidate_text: str, *, incomplete: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_id = f"source-{hash_object(candidate_text)[:12]}"
    retriever = LocalReferenceRetriever(
        tmp_path / f"cas-{source_id}",
        (
            _published_book(
                tmp_path / f"{source_id}.md",
                source_id=source_id,
                text=candidate_text,
            ),
        ),
    )
    evidence = retriever.lookup_exact(
        (
            ReferenceExactLookupRequest(
                source_id=source_id,
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(candidate_text),
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
                claimed_text=candidate_text,
                origin="exact_extracted_evidence",
                evidence_id=evidence[0].id,
                claim_start=0,
                claim_end=len(candidate_text),
            ),
        ),
        bounded_retrieval_evidence_ids=() if incomplete else (evidence[0].id,),
    )
    return proof


def _stored_enrollment_snapshot(aggregate, proof=None):
    enrollments = (
        tuple(
            StoredReferenceEnrollmentV3(
                source_id=item.source_id,
                logical_source_id=item.logical_source_id,
                version_id=item.version_id,
                version_status=item.version_status,
                source_artifact_hash=item.artifact_sha256,
                authority_descriptor_hash=item.descriptor_hash,
            )
            for item in proof.coverage.version_coverage
        )
        if proof is not None
        else ()
    )
    payload = {
        "schema_version": 3,
        "generation_id": aggregate.generation_id,
        "full_audit_aggregate_id": aggregate.id,
        "full_audit_aggregate_hash": aggregate.content_hash,
        "generation_manifest_hash": H3,
        "reference_operator_bundle_hash": H4,
        "enrollment_manifest_hash": H5,
        "enrollments": enrollments,
        "enrolled_logical_version_ids": tuple(
            sorted(f"{item.logical_source_id}@{item.version_id}" for item in enrollments)
        ),
        "enrollment_set_hash": hash_object(enrollments),
        "reconstruction": "rebuilt_from_stored_generation_artifacts_not_caller_declaration",
        "authority": "stored_lineage_fact_not_reference_or_audio_verdict",
    }
    return _addressed(StoredReferenceEnrollmentSnapshotV3, payload)


def _lookup(
    aggregate,
    candidate,
    *,
    status: str,
    proof=None,
    enrollment_proof=None,
    failure_artifact_hash: str | None = None,
):
    return build_reference_lookup_state(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        stored_enrollment_snapshot=_stored_enrollment_snapshot(
            aggregate,
            enrollment_proof if enrollment_proof is not None else proof,
        ),
        lookup_receipt_set_hash=H6,
        status=status,
        reference_proof=proof,
        failure_artifact_hash=failure_artifact_hash,
    )


def _verdict(
    fixture,
    aggregate,
    candidate,
    *,
    mode: str,
    lookup,
    proof=None,
    stored_enrollment_proof=None,
    receipts=(),
    adjudication=None,
    policy=None,
):
    snapshot = _stored_enrollment_snapshot(
        aggregate,
        stored_enrollment_proof if stored_enrollment_proof is not None else proof,
    )
    assert lookup.stored_enrollment_snapshot_id == snapshot.id
    return build_correction_authority_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        authority_mode=mode,
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        stored_enrollment_snapshot=snapshot,
        reference_lookup_state=lookup,
        reference_proof=proof,
        human_pronunciation_receipts=receipts,
        human_reference_adjudication=adjudication,
        policy=policy or default_correction_authority_policy(),
    )


@pytest.mark.parametrize(
    ("original", "candidate_text"),
    (("型別", "類型"), ("約會物件", "約會對象")),
)
def test_audibly_distinct_ordinary_lexical_candidate_needs_no_enrolled_reference(
    audited_case,
    original: str,
    candidate_text: str,
) -> None:
    fixture, aggregate, candidate = _named_case(
        audited_case,
        original=original,
        candidate_text=candidate_text,
    )
    receipts = _agreeing_receipts(
        fixture,
        aggregate,
        candidate,
        reasoning="candidate_matches_original_does_not",
    )
    lookup = _lookup(aggregate, candidate, status="not_enrolled")
    verdict = _verdict(
        fixture,
        aggregate,
        candidate,
        mode="audio_discriminable_lexical",
        lookup=lookup,
        receipts=receipts,
    )
    assert verdict.action == "authorize_exact_candidate"
    assert verdict.reasons == ("audio_discriminable_exact_candidate_authorized",)
    assert verdict.selected_reference_claim is None
    assert verdict.authority == "authorization_not_mutation"


def test_homophone_anji_anqi_requires_reference_spelling_authority(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, aggregate, candidate = _named_case(
        audited_case,
        original="安琪",
        candidate_text="安吉",
    )
    receipts = _agreeing_receipts(
        fixture,
        aggregate,
        candidate,
        reasoning="candidate_and_original_both_match_same_pronunciation",
    )
    no_source = _lookup(aggregate, candidate, status="not_enrolled")
    audio_only = _verdict(
        fixture,
        aggregate,
        candidate,
        mode="audio_discriminable_lexical",
        lookup=no_source,
        receipts=receipts,
    )
    assert audio_only.action == "defer"
    assert "audio_pair_not_discriminable" in audio_only.reasons

    proof = _proof(tmp_path, "安吉")
    completed = _lookup(aggregate, candidate, status="completed", proof=proof)
    orthographic = _verdict(
        fixture,
        aggregate,
        candidate,
        mode="orthographic_homophone_or_entity",
        lookup=completed,
        proof=proof,
        receipts=receipts,
    )
    assert orthographic.action == "authorize_exact_candidate"
    assert orthographic.selected_reference_literal == "安吉"
    assert orthographic.reasons == ("orthographic_exact_reference_literal_authorized",)


def test_reference_only_never_authorizes_text_mutation(audited_case) -> None:
    fixture, aggregate, candidate = audited_case
    lookup = _lookup(aggregate, candidate, status="not_enrolled")
    verdict = _verdict(
        fixture,
        aggregate,
        candidate,
        mode="reference_only",
        lookup=lookup,
    )
    assert verdict.action == "defer"
    assert "reference_only_never_authorizes_text_mutation" in verdict.reasons


@pytest.mark.parametrize("status", ("failed", "incomplete"))
def test_failed_or_incomplete_lookup_blocks_audio_lexical_authorization(
    tmp_path: Path,
    audited_case,
    status: str,
) -> None:
    fixture, aggregate, candidate = audited_case
    receipts = _agreeing_receipts(
        fixture,
        aggregate,
        candidate,
        reasoning="candidate_matches_original_does_not",
    )
    proof = _proof(tmp_path / status, candidate.candidate_text, incomplete=status == "incomplete")
    lookup = _lookup(
        aggregate,
        candidate,
        status=status,
        proof=proof if status == "incomplete" else None,
        enrollment_proof=proof,
        failure_artifact_hash=H7 if status == "failed" else None,
    )
    verdict = _verdict(
        fixture,
        aggregate,
        candidate,
        mode="audio_discriminable_lexical",
        lookup=lookup,
        proof=proof if status == "incomplete" else None,
        stored_enrollment_proof=proof,
        receipts=receipts,
    )
    assert verdict.action == "defer"
    assert f"reference_lookup_{status}" in verdict.reasons


def test_authoritative_counterclaim_and_conflict_block_audio_lexical_mode(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, aggregate, candidate = audited_case
    proof = _proof_from_sources(
        tmp_path,
        texts=(candidate.candidate_text, "另一個權威寫法"),
        source_kinds=("book", "book"),
    )
    lookup = _lookup(aggregate, candidate, status="completed", proof=proof)
    receipts = _agreeing_receipts(
        fixture,
        aggregate,
        candidate,
        reasoning="candidate_matches_original_does_not",
    )
    verdict = _verdict(
        fixture,
        aggregate,
        candidate,
        mode="audio_discriminable_lexical",
        lookup=lookup,
        proof=proof,
        receipts=receipts,
    )
    assert verdict.action == "defer"
    assert "reference_counterclaim_blocks_audio_lexical" in verdict.reasons
    assert "reference_conflict_blocks_audio_lexical" in verdict.reasons


def test_audio_incompatible_rejects_even_with_exact_reference(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, aggregate, candidate = audited_case
    proof = _proof(tmp_path, candidate.candidate_text)
    lookup = _lookup(aggregate, candidate, status="completed", proof=proof)
    receipts = _agreeing_receipts(
        fixture,
        aggregate,
        candidate,
        reasoning="original_matches_candidate_does_not",
    )
    verdict = _verdict(
        fixture,
        aggregate,
        candidate,
        mode="orthographic_homophone_or_entity",
        lookup=lookup,
        proof=proof,
        receipts=receipts,
    )
    assert verdict.action == "reject"
    assert verdict.reasons == ("audio_candidate_incompatible",)


def test_duplicate_human_identity_cannot_satisfy_quorum(audited_case) -> None:
    fixture, aggregate, candidate = audited_case
    first = _receipt(
        fixture,
        aggregate,
        candidate,
        reasoning="candidate_matches_original_does_not",
    )
    second = _receipt(
        fixture,
        aggregate,
        candidate,
        reasoning="candidate_matches_original_does_not",
        reviewed_at="2026-08-13T01:02:04Z",
    )
    policy_payload = default_correction_authority_policy().model_dump(
        mode="python",
        exclude={"id", "content_hash"},
    )
    policy_payload["minimum_human_audio_reviewers"] = 2
    policy = _addressed(CorrectionAuthorityPolicyV3, policy_payload)
    lookup = _lookup(aggregate, candidate, status="not_enrolled")
    verdict = _verdict(
        fixture,
        aggregate,
        candidate,
        mode="audio_discriminable_lexical",
        lookup=lookup,
        receipts=tuple(sorted((first, second), key=lambda item: item.id)),
        policy=policy,
    )
    assert verdict.action == "defer"
    assert "duplicate_human_audio_reviewer" in verdict.reasons
    assert "human_audio_quorum_not_met" in verdict.reasons


def test_one_reviewer_blocks_and_policy_cannot_weaken_below_two(audited_case) -> None:
    fixture, aggregate, candidate = audited_case
    receipt = _receipt(
        fixture,
        aggregate,
        candidate,
        reasoning="candidate_matches_original_does_not",
    )
    lookup = _lookup(aggregate, candidate, status="not_enrolled")
    verdict = _verdict(
        fixture,
        aggregate,
        candidate,
        mode="audio_discriminable_lexical",
        lookup=lookup,
        receipts=(receipt,),
    )
    assert verdict.action == "defer"
    assert "human_audio_quorum_not_met" in verdict.reasons
    weak = default_correction_authority_policy().model_dump(mode="python")
    weak["minimum_human_audio_reviewers"] = 1
    with pytest.raises(ValidationError):
        CorrectionAuthorityPolicyV3.model_validate(weak)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("candidate_discovery_id", H7),
        ("full_audit_aggregate_id", H7),
        ("normalized_audio_hash", H7),
        ("clip_lineage_parent_id", H7),
    ),
)
def test_cross_candidate_generation_audio_or_clip_receipt_defers(
    audited_case,
    field: str,
    value: str,
) -> None:
    fixture, aggregate, candidate = audited_case
    receipts = _agreeing_receipts(
        fixture,
        aggregate,
        candidate,
        reasoning="candidate_matches_original_does_not",
    )
    payload = receipts[0].model_dump(mode="python", exclude={"id", "content_hash"})
    payload[field] = value
    crossed = _addressed(HumanPronunciationRelationReceiptV3, payload)
    lookup = _lookup(aggregate, candidate, status="not_enrolled")
    verdict = _verdict(
        fixture,
        aggregate,
        candidate,
        mode="audio_discriminable_lexical",
        lookup=lookup,
        receipts=(crossed,),
    )
    assert verdict.action == "defer"
    assert "human_audio_receipt_lineage_mismatch" in verdict.reasons


def test_reordered_receipts_fail_closed(audited_case) -> None:
    fixture, aggregate, candidate = audited_case
    receipts = tuple(
        sorted(
            (
                _receipt(
                    fixture,
                    aggregate,
                    candidate,
                    reasoning="candidate_matches_original_does_not",
                    reviewer_id=f"reviewer:audio:{index}",
                )
                for index in (1, 2)
            ),
            key=lambda item: item.id,
        )
    )
    lookup = _lookup(aggregate, candidate, status="not_enrolled")
    with pytest.raises(CorrectionAuthorityError, match="reordered"):
        _verdict(
            fixture,
            aggregate,
            candidate,
            mode="audio_discriminable_lexical",
            lookup=lookup,
            receipts=tuple(reversed(receipts)),
        )


def test_receipt_lookup_and_verdict_canonical_rebuilders(audited_case) -> None:
    fixture, aggregate, candidate = audited_case
    receipt = _receipt(
        fixture,
        aggregate,
        candidate,
        reasoning="candidate_matches_original_does_not",
    )
    receipt_bytes = pronunciation_relation_receipt_bytes(receipt)
    assert (
        verify_pronunciation_relation_receipt(
            receipt_bytes,
            candidate=candidate,
            full_audit_aggregate=aggregate,
            recognition_evidence=fixture[7],
        )
        == receipt
    )
    snapshot = _stored_enrollment_snapshot(aggregate)
    lookup = build_reference_lookup_state(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        stored_enrollment_snapshot=snapshot,
        lookup_receipt_set_hash=H6,
        status="not_enrolled",
    )
    assert (
        verify_reference_lookup_state(
            reference_lookup_state_bytes(lookup),
            candidate=candidate,
            full_audit_aggregate=aggregate,
            stored_enrollment_snapshot=snapshot,
            reference_proof=None,
        )
        == lookup
    )
    policy = default_correction_authority_policy()
    receipts = _agreeing_receipts(
        fixture,
        aggregate,
        candidate,
        reasoning="candidate_matches_original_does_not",
    )
    verdict = _verdict(
        fixture,
        aggregate,
        candidate,
        mode="audio_discriminable_lexical",
        lookup=lookup,
        receipts=receipts,
        policy=policy,
    )
    exact = correction_authority_verdict_bytes(verdict)
    assert (
        verify_correction_authority_verdict(
            exact,
            candidate=candidate,
            full_audit_aggregate=aggregate,
            recognition_evidence=fixture[7],
            authority_mode="audio_discriminable_lexical",
            resolution_key="term:exact-candidate",
            reference_scope="literal_terminology",
            stored_enrollment_snapshot=snapshot,
            reference_lookup_state=lookup,
            reference_proof=None,
            human_pronunciation_receipts=receipts,
            human_reference_adjudication=None,
            policy=policy,
        )
        == verdict
    )


def test_tamper_unknown_enum_and_free_text_surface_fail_closed(audited_case) -> None:
    fixture, aggregate, candidate = audited_case
    receipt = _receipt(
        fixture,
        aggregate,
        candidate,
        reasoning="candidate_matches_original_does_not",
    )
    tampered = json.loads(pronunciation_relation_receipt_bytes(receipt))
    tampered["candidate_text"] = "任意自由文字"
    with pytest.raises(CorrectionAuthorityError):
        verify_pronunciation_relation_receipt(
            canonical_json_bytes(tampered),
            candidate=candidate,
            full_audit_aggregate=aggregate,
            recognition_evidence=fixture[7],
        )
    unknown = receipt.model_dump(mode="python")
    unknown["reasoning_code"] = "model_says_so"
    with pytest.raises(ValidationError):
        HumanPronunciationRelationReceiptV3.model_validate(unknown)
    signature = inspect.signature(build_correction_authority_verdict)
    assert "replacement_text" not in signature.parameters
    assert "manual_override" not in signature.parameters
    assert "free_text" not in signature.parameters
