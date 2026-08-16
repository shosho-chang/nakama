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
    CorrectionAcceptanceError,
    CorrectionAcceptancePolicyV2,
    HumanAudioReviewReceiptV2,
    HumanReferenceAdjudicationReceiptV2,
    HumanReviewerAttestationV2,
    build_correction_acceptance_verdict,
    correction_acceptance_verdict_bytes,
    default_correction_acceptance_policy,
    human_audio_review_receipt_bytes,
    verify_correction_acceptance_verdict,
    verify_human_audio_review_receipt,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object
from agents.brook.podcast_subtitles.reference_claims import (
    ReferenceAuthorityTargetV2,
    ReferenceExactClaimSpecV2,
    build_reference_authority_proof,
)
from shared.schemas.podcast_subtitles_v2_audio_audit import AudioDiscoveredCandidateV2
from tests.agents.brook.podcast_subtitles.test_correction_execution import build_fixture
from tests.agents.brook.podcast_subtitles.test_full_audit_attestation import (
    _build as build_full_audit,
)
from tests.agents.brook.podcast_subtitles.test_full_audit_attestation import (
    _complete_full_audit,
)
from tests.agents.brook.podcast_subtitles.test_reference_claims import (
    _contextual_source,
    _inactive_report,
    _published_book,
)


def _addressed(model_type, payload: dict[str, object]):
    digest = hash_object(payload)
    return model_type(**payload, id=digest, content_hash=digest)


def _authoritative_proof(tmp_path: Path, candidate_text: str):
    retriever = LocalReferenceRetriever(
        tmp_path / "reference-cas",
        (
            _published_book(
                tmp_path / "authority.md",
                source_id="authority",
                text=candidate_text,
            ),
        ),
    )
    evidence = retriever.lookup_exact(
        (
            ReferenceExactLookupRequest(
                source_id="authority",
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
    specs = (
        ReferenceExactClaimSpecV2(
            resolution_key=target.resolution_key,
            scope="literal_terminology",
            claimed_text=candidate_text,
            origin="exact_extracted_evidence",
            evidence_id=evidence[0].id,
            claim_start=0,
            claim_end=len(candidate_text),
        ),
    )
    return build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=specs,
        bounded_retrieval_evidence_ids=(evidence[0].id,),
    )


def _proof_from_sources(
    tmp_path: Path,
    *,
    texts: tuple[str, ...],
    source_kinds: tuple[str, ...],
    scope: str = "literal_terminology",
):
    source_specs = []
    source_ids = []
    proof_key = hash_object({"texts": texts, "source_kinds": source_kinds, "scope": scope})[:12]
    for index, (text, source_kind) in enumerate(zip(texts, source_kinds, strict=True)):
        source_id = f"source-{index}"
        source_ids.append(source_id)
        source_path = tmp_path / f"{proof_key}-{source_id}.md"
        if source_kind == "book":
            source = _published_book(source_path, source_id=source_id, text=text)
        elif source_kind == "contextual":
            source = _contextual_source(source_path, source_id=source_id, text=text)
        elif source_kind in {"active_nonfinal", "draft", "superseded"}:
            version_status = "active" if source_kind == "active_nonfinal" else source_kind
            source = _inactive_report(
                source_path,
                source_id=source_id,
                text=text,
                version_status=version_status,
            )
        else:  # pragma: no cover - test helper guard
            raise AssertionError(f"unknown source kind: {source_kind}")
        source_specs.append(source)
    retriever = LocalReferenceRetriever(tmp_path / f"cas-{proof_key}", tuple(source_specs))
    evidence = retriever.lookup_exact(
        tuple(
            ReferenceExactLookupRequest(
                source_id=source_id,
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(text),
            )
            for source_id, text in zip(source_ids, texts, strict=True)
        )
    )
    target = ReferenceAuthorityTargetV2(
        resolution_key="term:exact-candidate",
        scopes=(scope,),
    )
    specs = tuple(
        ReferenceExactClaimSpecV2(
            resolution_key=target.resolution_key,
            scope=scope,
            claimed_text=item.excerpt,
            origin="exact_extracted_evidence",
            evidence_id=item.id,
            claim_start=0,
            claim_end=len(item.excerpt),
        )
        for item in evidence
    )
    return build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=specs,
        bounded_retrieval_evidence_ids=tuple(item.id for item in evidence),
    )


def _human_audio_receipt(
    candidate,
    aggregate,
    *,
    outcome: str,
    discriminable: bool,
    reviewer_id: str = "reviewer:audio:1",
    timestamp: str = "2026-08-13T01:02:03Z",
    attestation_record_sha256: str = "2" * 64,
):
    reviewer = HumanReviewerAttestationV2(
        actor_kind="human",
        reviewer_id=reviewer_id,
        identity_provider="nakama-owner-registry",
        identity_record_sha256="1" * 64,
        attestation_method="authenticated_ui_confirmation",
        attestation_record_sha256=attestation_record_sha256,
    )
    is_audio_candidate = isinstance(candidate, AudioDiscoveredCandidateV2)
    reasoning_code = {
        "compatible": "audible_pronunciation_matches_exact_candidate",
        "incompatible": "audible_pronunciation_conflicts_exact_candidate",
        "indeterminate": "audio_cannot_discriminate_spelling",
    }[outcome]
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
        "clip_sha256": candidate.clip_hash if is_audio_candidate else "4" * 64,
        "clip_size_bytes": 128,
        "clip_start_ms": 0,
        "clip_end_ms": 2_000,
        "affected_token_ids": candidate.affected_token_ids,
        "cited_span_ids": candidate.cited_span_ids,
        "cited_recognition_evidence_ids": candidate.cited_recognition_evidence_ids,
        "recognized_text": candidate.observed_text,
        "candidate_text": candidate.candidate_text,
        "pronunciation_outcome": outcome,
        "discriminable": discriminable,
        "reasoning_code": reasoning_code,
        "notes_digest": "5" * 64,
        "reviewer": reviewer,
        "reviewed_at_utc": timestamp,
        "authority": "human_pronunciation_review_not_spelling_authority",
    }
    return _addressed(HumanAudioReviewReceiptV2, payload)


def _human_reference_receipt(
    candidate,
    proof,
    *,
    reviewer_id: str = "reviewer:reference:1",
    decision: str = "choose_authoritative_literal",
):
    reviewer = HumanReviewerAttestationV2(
        actor_kind="human",
        reviewer_id=reviewer_id,
        identity_provider="nakama-owner-registry",
        identity_record_sha256="6" * 64,
        attestation_method="detached_signature",
        attestation_record_sha256="7" * 64,
    )
    chosen = next(
        item
        for item in proof.claims
        if item.claimed_text == candidate.candidate_text and item.strength == "authoritative"
    )
    payload = {
        "schema_version": 2,
        "candidate_discovery_id": candidate.id,
        "candidate_discovery_hash": candidate.content_hash,
        "reference_proof_id": proof.id,
        "reference_proof_hash": proof.content_hash,
        "coverage_receipt_id": proof.coverage.id,
        "coverage_receipt_hash": proof.coverage.content_hash,
        "conflict_set_id": proof.conflicts.id,
        "conflict_set_hash": proof.conflicts.content_hash,
        "resolution_key": "term:exact-candidate",
        "scope": "literal_terminology",
        "candidate_text": candidate.candidate_text,
        "conflict_ids": tuple(item.id for item in proof.conflicts.conflicts),
        "decision": decision,
        "chosen_claim_id": chosen.id if decision == "choose_authoritative_literal" else None,
        "chosen_literal": (
            chosen.claimed_text if decision == "choose_authoritative_literal" else None
        ),
        "reasoning_code": (
            "authoritative_literal_selected_after_source_review"
            if decision == "choose_authoritative_literal"
            else "conflicting_authority_requires_defer"
        ),
        "reviewer": reviewer,
        "reviewed_at_utc": "2026-08-13T02:03:04Z",
        "authority": "reference_conflict_adjudication_not_audio_or_text_mutation",
    }
    return _addressed(HumanReferenceAdjudicationReceiptV2, payload)


def _policy_with_quorum(quorum: int) -> CorrectionAcceptancePolicyV2:
    original = default_correction_acceptance_policy()
    payload = original.model_dump(mode="python", exclude={"id", "content_hash"})
    payload["minimum_human_audio_reviewers"] = quorum
    return _addressed(CorrectionAcceptancePolicyV2, payload)


@pytest.fixture(scope="module")
def audited_case(tmp_path_factory: pytest.TempPathFactory):
    tmp_path = tmp_path_factory.mktemp("correction-acceptance-audit")
    fixture = build_fixture(tmp_path)
    complete = _complete_full_audit(tmp_path, findings=True, fixture=fixture)
    aggregate = build_full_audit(complete)
    return fixture, complete, aggregate


def _accepted_inputs(tmp_path: Path, audited_case, *, audio_candidate: bool = False):
    fixture, complete, aggregate = audited_case
    record = complete[5] if audio_candidate else complete[4]
    candidate = record.candidate_discovery_set.candidates[0]
    proof = _authoritative_proof(tmp_path, candidate.candidate_text)
    receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome="compatible",
        discriminable=True,
    )
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
    return candidate, aggregate, proof, receipt, policy, arguments


def test_authoritative_book_and_indiscriminable_audio_defer(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]
    proof = _authoritative_proof(tmp_path, candidate.candidate_text)
    receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome="indeterminate",
        discriminable=False,
    )

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=proof,
        human_audio_receipts=(receipt,),
        human_reference_adjudication=None,
        policy=default_correction_acceptance_policy(),
    )

    assert verdict.action == "defer"
    assert verdict.reasons == ("audio_indiscriminable",)


def test_audio_compatible_without_reference_authority_defers(
    audited_case,
) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]
    receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome="compatible",
        discriminable=True,
    )

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=None,
        human_audio_receipts=(receipt,),
        human_reference_adjudication=None,
        policy=default_correction_acceptance_policy(),
    )

    assert verdict.action == "defer"
    assert verdict.reasons == ("reference_proof_missing",)


def test_authoritative_literal_complete_coverage_and_compatible_audio_accept(
    tmp_path: Path,
    audited_case,
) -> None:
    candidate, aggregate, proof, receipt, policy, arguments = _accepted_inputs(
        tmp_path,
        audited_case,
    )

    verdict = build_correction_acceptance_verdict(**arguments)

    assert verdict.action == "accept_exact_candidate"
    assert verdict.original_text == candidate.observed_text
    assert verdict.candidate_text == candidate.candidate_text
    assert verdict.affected_token_ids == candidate.affected_token_ids
    assert verdict.cited_span_ids == candidate.cited_span_ids
    assert verdict.selected_reference_literal == candidate.candidate_text
    assert verdict.selected_reference_claim_id == proof.claims[0].id
    assert verdict.full_audit_aggregate_hash == aggregate.content_hash
    assert verdict.human_audio_review_receipt_ids == (receipt.id,)
    assert verdict.policy_hash == policy.content_hash
    assert verdict.authority == "authorization_not_mutation"


def test_audio_discovery_is_still_only_discovery_but_exact_human_clip_can_accept(
    tmp_path: Path,
    audited_case,
) -> None:
    candidate, _, _, _, _, arguments = _accepted_inputs(
        tmp_path,
        audited_case,
        audio_candidate=True,
    )

    verdict = build_correction_acceptance_verdict(**arguments)

    assert verdict.action == "accept_exact_candidate"
    assert verdict.candidate_modality == "audio"
    assert arguments["human_audio_receipts"][0].clip_sha256 == candidate.clip_hash


def test_text_only_discovery_always_needs_human_audio_confirmation(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]
    proof = _authoritative_proof(tmp_path, candidate.candidate_text)

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=proof,
        human_audio_receipts=(),
        human_reference_adjudication=None,
        policy=default_correction_acceptance_policy(),
    )

    assert verdict.action == "defer"
    assert verdict.reasons == ("audio_receipt_missing",)


def test_full_audit_completeness_is_not_correction_acceptance(audited_case) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=None,
        human_audio_receipts=(),
        human_reference_adjudication=None,
        policy=default_correction_acceptance_policy(),
    )

    assert aggregate.execution_scope == "all_plan_cells_both_modalities_dispositioned"
    assert verdict.action == "defer"
    assert verdict.reasons == ("audio_receipt_missing", "reference_proof_missing")


def test_two_authorities_conflict_even_when_human_audio_supports_candidate(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]
    proof = _proof_from_sources(
        tmp_path,
        texts=(candidate.candidate_text, f"{candidate.candidate_text}-counterclaim"),
        source_kinds=("book", "book"),
    )
    receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome="compatible",
        discriminable=True,
    )

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=proof,
        human_audio_receipts=(receipt,),
        human_reference_adjudication=None,
        policy=default_correction_acceptance_policy(),
    )

    assert proof.conflicts.conflicts[0].kind == "authoritative_authoritative"
    assert verdict.action == "defer"
    assert verdict.reasons == ("reference_adjudication_missing",)


def test_human_reference_adjudicates_exact_conflict_ids_then_accepts(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]
    proof = _proof_from_sources(
        tmp_path,
        texts=(candidate.candidate_text, f"{candidate.candidate_text}-counterclaim"),
        source_kinds=("book", "book"),
    )
    audio_receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome="compatible",
        discriminable=True,
    )
    reference_receipt = _human_reference_receipt(candidate, proof)

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=proof,
        human_audio_receipts=(audio_receipt,),
        human_reference_adjudication=reference_receipt,
        policy=default_correction_acceptance_policy(),
    )

    assert reference_receipt.conflict_ids == tuple(
        item.id for item in proof.conflicts.conflicts
    )
    assert verdict.action == "accept_exact_candidate"
    assert verdict.human_reference_adjudication_receipt_id == reference_receipt.id


def test_reference_conflict_adjudicator_must_be_independent_from_audio_reviewer(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]
    proof = _proof_from_sources(
        tmp_path,
        texts=(candidate.candidate_text, f"{candidate.candidate_text}-counterclaim"),
        source_kinds=("book", "book"),
    )
    audio_receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome="compatible",
        discriminable=True,
        reviewer_id="reviewer:same",
    )
    reference_receipt = _human_reference_receipt(
        candidate,
        proof,
        reviewer_id="reviewer:same",
    )

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=proof,
        human_audio_receipts=(audio_receipt,),
        human_reference_adjudication=reference_receipt,
        policy=default_correction_acceptance_policy(),
    )

    assert verdict.action == "defer"
    assert verdict.reasons == ("reference_reviewer_not_independent",)


def test_contextual_reference_never_promotes_to_spelling_authority(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]
    proof = _proof_from_sources(
        tmp_path,
        texts=(candidate.candidate_text,),
        source_kinds=("contextual",),
    )
    receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome="compatible",
        discriminable=True,
    )

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=proof,
        human_audio_receipts=(receipt,),
        human_reference_adjudication=None,
        policy=default_correction_acceptance_policy(),
    )

    assert proof.claims[0].strength == "contextual"
    assert verdict.action == "defer"
    assert verdict.reasons == ("reference_authoritative_literal_missing",)


@pytest.mark.parametrize("source_kind", ["active_nonfinal", "draft", "superseded"])
def test_nonfinal_draft_or_superseded_report_cannot_authorize(
    tmp_path: Path,
    audited_case,
    source_kind: str,
) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]
    proof = _proof_from_sources(
        tmp_path,
        texts=(candidate.candidate_text,),
        source_kinds=(source_kind,),
    )
    receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome="compatible",
        discriminable=True,
    )

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=proof,
        human_audio_receipts=(receipt,),
        human_reference_adjudication=None,
        policy=default_correction_acceptance_policy(),
    )

    assert verdict.action == "defer"
    assert verdict.reasons == ("reference_authoritative_literal_missing",)


def test_book_verbatim_scope_cannot_infer_a_whole_spoken_sentence(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]
    proof = _proof_from_sources(
        tmp_path,
        texts=(candidate.candidate_text,),
        source_kinds=("book",),
        scope="verbatim_source_text",
    )
    receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome="compatible",
        discriminable=True,
    )

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="verbatim_source_text",
        reference_proof=proof,
        human_audio_receipts=(receipt,),
        human_reference_adjudication=None,
        policy=default_correction_acceptance_policy(),
    )

    assert verdict.action == "defer"
    assert verdict.reasons == ("reference_scope_not_authorizable",)


def test_human_confirmed_audio_incompatibility_rejects_exact_candidate(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]
    proof = _authoritative_proof(tmp_path, candidate.candidate_text)
    receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome="incompatible",
        discriminable=True,
    )

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=proof,
        human_audio_receipts=(receipt,),
        human_reference_adjudication=None,
        policy=default_correction_acceptance_policy(),
    )

    assert verdict.action == "reject"
    assert verdict.reasons == ("audio_incompatible",)


def test_llm_or_provider_cannot_be_encoded_as_human_reviewer() -> None:
    with pytest.raises(ValidationError, match="actor_kind"):
        HumanReviewerAttestationV2(
            actor_kind="llm",  # type: ignore[arg-type]
            reviewer_id="model:gpt",
            identity_provider="provider",
            identity_record_sha256="1" * 64,
            attestation_method="authenticated_ui_confirmation",
            attestation_record_sha256="2" * 64,
        )


def test_duplicate_human_cannot_satisfy_two_reviewer_quorum(
    tmp_path: Path,
    audited_case,
) -> None:
    fixture, complete, aggregate = audited_case
    candidate = complete[4].candidate_discovery_set.candidates[0]
    proof = _authoritative_proof(tmp_path, candidate.candidate_text)
    receipts = (
        _human_audio_receipt(
            candidate,
            aggregate,
            outcome="compatible",
            discriminable=True,
            reviewer_id="reviewer:duplicate",
        ),
        _human_audio_receipt(
            candidate,
            aggregate,
            outcome="compatible",
            discriminable=True,
            reviewer_id="reviewer:duplicate",
            timestamp="2026-08-13T01:02:04Z",
            attestation_record_sha256="8" * 64,
        ),
    )

    verdict = build_correction_acceptance_verdict(
        candidate=candidate,
        full_audit_aggregate=aggregate,
        recognition_evidence=fixture[7],
        resolution_key="term:exact-candidate",
        reference_scope="literal_terminology",
        reference_proof=proof,
        human_audio_receipts=receipts,
        human_reference_adjudication=None,
        policy=_policy_with_quorum(2),
    )

    assert verdict.action == "defer"
    assert verdict.reasons == (
        "human_audio_quorum_not_met",
        "duplicate_human_audio_reviewer",
    )


def test_free_text_manual_replacement_has_no_api_surface() -> None:
    signature = inspect.signature(build_correction_acceptance_verdict)

    assert "replacement_text" not in signature.parameters
    assert "manual_replacement" not in signature.parameters
    assert "candidate_text" not in signature.parameters


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("candidate_discovery_id",), "9" * 64),
        (("affected_token_ids",), ["forged-token"]),
        (("clip_sha256",), "9" * 64),
        (("reviewer", "reviewer_id"), "reviewer:forged"),
        (("reviewed_at_utc",), "2026-08-13T01:02:04Z"),
    ],
)
def test_human_audio_receipt_tampering_fails_closed(
    tmp_path: Path,
    audited_case,
    path: tuple[str, ...],
    replacement: object,
) -> None:
    *_, receipt, _, _ = _accepted_inputs(tmp_path, audited_case)
    payload = json.loads(human_audio_review_receipt_bytes(receipt))
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    tampered = canonical_json_bytes(payload)

    with pytest.raises(CorrectionAcceptanceError, match="strict v2 schema"):
        verify_human_audio_review_receipt(tampered)


@pytest.mark.parametrize(
    "field",
    [
        "candidate_discovery_hash",
        "full_audit_aggregate_hash",
        "selected_reference_claim_hash",
        "reference_coverage_receipt_hash",
        "reference_conflict_set_hash",
    ],
)
def test_verdict_parent_hash_tampering_fails_closed(
    tmp_path: Path,
    audited_case,
    field: str,
) -> None:
    *_, arguments = _accepted_inputs(tmp_path, audited_case)
    verdict = build_correction_acceptance_verdict(**arguments)
    payload = json.loads(correction_acceptance_verdict_bytes(verdict))
    payload[field] = "9" * 64

    with pytest.raises(CorrectionAcceptanceError, match="strict v2 schema"):
        verify_correction_acceptance_verdict(
            canonical_json_bytes(payload),
            **arguments,
        )


def test_candidate_coordinated_reseal_still_fails_aggregate_membership(
    tmp_path: Path,
    audited_case,
) -> None:
    candidate, _, _, _, _, arguments = _accepted_inputs(tmp_path, audited_case)
    payload = candidate.model_dump(mode="python", exclude={"id", "content_hash"})
    payload["candidate_text"] = f"{candidate.candidate_text}-forged"
    forged = _addressed(type(candidate), payload)
    arguments["candidate"] = forged

    with pytest.raises(CorrectionAcceptanceError, match="not a member"):
        build_correction_acceptance_verdict(**arguments)


@pytest.mark.parametrize(
    "parent",
    ["recognition_token", "aggregate", "reference_claim", "coverage", "conflict_set"],
)
def test_tampered_evidence_parent_objects_fail_before_decision(
    tmp_path: Path,
    audited_case,
    parent: str,
) -> None:
    *_, arguments = _accepted_inputs(tmp_path, audited_case)
    if parent == "recognition_token":
        evidence = arguments["recognition_evidence"][0]
        token = evidence.tokens[0].model_copy(update={"text": "forged-recognition"})
        forged = evidence.model_copy(update={"tokens": (token, *evidence.tokens[1:])})
        arguments["recognition_evidence"] = (forged,)
        match = "Recognition Evidence differs"
    elif parent == "aggregate":
        aggregate = arguments["full_audit_aggregate"]
        arguments["full_audit_aggregate"] = aggregate.model_copy(
            update={"normalized_audio_hash": "9" * 64}
        )
        match = "Full Audit aggregate is not an exact valid artifact"
    else:
        proof = arguments["reference_proof"]
        if parent == "reference_claim":
            claim = proof.claims[0].model_copy(update={"claimed_text": "forged-claim"})
            forged_proof = proof.model_copy(update={"claims": (claim,)})
        elif parent == "coverage":
            coverage = proof.coverage.model_copy(update={"selection_complete": False})
            forged_proof = proof.model_copy(update={"coverage": coverage})
        else:
            conflicts = proof.conflicts.model_copy(update={"blocking": True})
            forged_proof = proof.model_copy(update={"conflicts": conflicts})
        arguments["reference_proof"] = forged_proof
        match = "Reference authority proof is not an exact valid artifact"

    with pytest.raises(CorrectionAcceptanceError, match=match):
        build_correction_acceptance_verdict(**arguments)


def test_verdict_canonical_v2_round_trip_and_fresh_replay(
    tmp_path: Path,
    audited_case,
) -> None:
    candidate, aggregate, _, _, _, arguments = _accepted_inputs(tmp_path, audited_case)
    verdict = build_correction_acceptance_verdict(**arguments)
    exact_bytes = correction_acceptance_verdict_bytes(verdict)

    replayed = verify_correction_acceptance_verdict(exact_bytes, **arguments)

    assert replayed == verdict
    assert correction_acceptance_verdict_bytes(replayed) == exact_bytes

    fresh_receipt = _human_audio_receipt(
        candidate,
        aggregate,
        outcome="compatible",
        discriminable=True,
        timestamp="2026-08-13T01:02:04Z",
        attestation_record_sha256="8" * 64,
    )
    arguments["human_audio_receipts"] = (fresh_receipt,)
    with pytest.raises(CorrectionAcceptanceError, match="differs from frozen parents"):
        verify_correction_acceptance_verdict(exact_bytes, **arguments)


@pytest.mark.parametrize("mutation", ["v1", "unknown", "noncanonical"])
def test_verdict_old_unknown_or_noncanonical_bytes_fail_closed(
    tmp_path: Path,
    audited_case,
    mutation: str,
) -> None:
    *_, arguments = _accepted_inputs(tmp_path, audited_case)
    verdict = build_correction_acceptance_verdict(**arguments)
    payload = json.loads(correction_acceptance_verdict_bytes(verdict))
    if mutation == "v1":
        payload["schema_version"] = 1
        mutated = canonical_json_bytes(payload)
        match = "strict v2 schema"
    elif mutation == "unknown":
        payload["manual_override"] = "replace anything"
        mutated = canonical_json_bytes(payload)
        match = "strict v2 schema"
    else:
        mutated = json.dumps(payload, ensure_ascii=False, indent=2).encode()
        match = "not canonical"

    with pytest.raises(CorrectionAcceptanceError, match=match):
        verify_correction_acceptance_verdict(mutated, **arguments)
