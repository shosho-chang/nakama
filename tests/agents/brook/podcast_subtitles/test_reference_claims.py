from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.adapters.reference import (
    LocalReferenceRetriever,
    ReferenceExactLookupRequest,
    ReferenceSourceSpec,
)
from agents.brook.podcast_subtitles.hashing import hash_object
from agents.brook.podcast_subtitles.ports import AdapterInputError, ReferenceRetrievalRequest
from agents.brook.podcast_subtitles.reference_claims import (
    ReferenceAuthorityTargetV2,
    ReferenceEvidenceOmissionSpecV2,
    ReferenceExactClaimSpecV2,
    build_reference_authority_proof,
    reference_authority_proof_bytes,
    verify_reference_authority_proof,
)
from shared.schemas.podcast_subtitles_v2 import (
    ReferenceAuthorityAttestation,
    ReferenceAuthorityDescriptor,
    ReferenceAuthorityPrincipal,
    ReferenceAuthorityVersionRef,
    ReferenceQueryContext,
    ReferenceQueryContextSlice,
    ReferenceRetrievalPolicySnapshot,
    reference_retrieval_policy_hash,
)


def _canonical_payload_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _reseal(payload: dict[str, object]) -> None:
    digest = hash_object(
        {key: value for key, value in payload.items() if key not in {"id", "content_hash"}}
    )
    payload["id"] = digest
    payload["content_hash"] = digest


def _published_book(path: Path, *, source_id: str, text: str) -> ReferenceSourceSpec:
    path.write_text(text, encoding="utf-8")
    title = f"Book {source_id}"
    author = f"Author {source_id}"
    publisher = f"Publisher {source_id}"
    authority = ReferenceAuthorityDescriptor(
        logical_source_id=f"logical:{source_id}",
        version_id="edition-1",
        version_status="active",
        release_status="published",
        source_kind="book",
        trust_tier="authoritative",
        role="published_author_book",
        subject=ReferenceAuthorityPrincipal(
            kind="publication",
            stable_id=f"publication:{source_id}",
            display_name=title,
        ),
        owner=ReferenceAuthorityPrincipal(
            kind="person",
            stable_id=f"author:{source_id}",
            display_name=author,
        ),
        allowed_scopes=(
            "source_title",
            "source_author",
            "literal_terminology",
            "verbatim_source_text",
        ),
        attestation=ReferenceAuthorityAttestation(
            confirmed=True,
            provenance="publisher_record",
            attestor=ReferenceAuthorityPrincipal(
                kind="organization",
                stable_id=f"publisher:{source_id}",
                display_name=publisher,
            ),
            record_sha256=hashlib.sha256(f"record:{source_id}".encode()).hexdigest(),
        ),
    )
    return ReferenceSourceSpec(
        path=path,
        source_id=source_id,
        kind="book",
        title=title,
        author=author,
        publisher=publisher,
        version="edition-1",
        trust_tier="authoritative",
        authority=authority,
    )


def _contextual_source(path: Path, *, source_id: str, text: str) -> ReferenceSourceSpec:
    path.write_text(text, encoding="utf-8")
    title = f"Notes {source_id}"
    authority = ReferenceAuthorityDescriptor(
        logical_source_id=f"logical:{source_id}",
        version_id="snapshot-1",
        version_status="active",
        release_status="not_applicable",
        source_kind="knowledge_base",
        trust_tier="contextual",
        role="contextual_reference",
        subject=ReferenceAuthorityPrincipal(
            kind="other",
            stable_id=f"notes:{source_id}",
            display_name=title,
        ),
        owner=None,
        allowed_scopes=(),
        attestation=ReferenceAuthorityAttestation(
            confirmed=False,
            provenance="none",
            attestor=None,
            record_sha256=None,
        ),
    )
    return ReferenceSourceSpec(
        path=path,
        source_id=source_id,
        kind="knowledge_base",
        title=title,
        author=None,
        publisher=None,
        version="snapshot-1",
        trust_tier="contextual",
        authority=authority,
    )


def _final_report(
    path: Path,
    *,
    source_id: str,
    text: str,
    logical_source_id: str | None = None,
    version_id: str = "final-1",
    supersedes: tuple[ReferenceAuthorityVersionRef, ...] = (),
) -> ReferenceSourceSpec:
    path.write_text(text, encoding="utf-8")
    title = f"Report {source_id}"
    owner = ReferenceAuthorityPrincipal(
        kind="person",
        stable_id=f"owner:{source_id}",
        display_name=f"Owner {source_id}",
    )
    authority = ReferenceAuthorityDescriptor(
        logical_source_id=logical_source_id or f"logical:{source_id}",
        version_id=version_id,
        version_status="active",
        release_status="final",
        supersedes=supersedes,
        source_kind="research_report",
        trust_tier="authoritative",
        role="owner_final_report",
        subject=ReferenceAuthorityPrincipal(
            kind="report",
            stable_id=f"report:{source_id}",
            display_name=title,
        ),
        owner=owner,
        allowed_scopes=(
            "source_title",
            "literal_terminology",
            "verbatim_source_text",
        ),
        attestation=ReferenceAuthorityAttestation(
            confirmed=True,
            provenance="owner_record",
            attestor=owner,
            record_sha256=hashlib.sha256(f"report:{source_id}".encode()).hexdigest(),
        ),
    )
    return ReferenceSourceSpec(
        path=path,
        source_id=source_id,
        kind="research_report",
        title=title,
        author=owner.display_name,
        publisher=None,
        version=version_id,
        trust_tier="authoritative",
        authority=authority,
    )


def _inactive_report(
    path: Path,
    *,
    source_id: str,
    text: str,
    version_status: str,
    logical_source_id: str | None = None,
    version_id: str = "historical-1",
) -> ReferenceSourceSpec:
    path.write_text(text, encoding="utf-8")
    title = f"Historical report {source_id}"
    authority = ReferenceAuthorityDescriptor(
        logical_source_id=logical_source_id or f"logical:{source_id}",
        version_id=version_id,
        version_status=version_status,  # type: ignore[arg-type]
        release_status="not_applicable",
        source_kind="research_report",
        trust_tier="contextual",
        role="contextual_reference",
        subject=ReferenceAuthorityPrincipal(
            kind="report",
            stable_id=f"report:{source_id}",
            display_name=title,
        ),
        owner=None,
        allowed_scopes=(),
        attestation=ReferenceAuthorityAttestation(
            confirmed=False,
            provenance="none",
            attestor=None,
            record_sha256=None,
        ),
    )
    return ReferenceSourceSpec(
        path=path,
        source_id=source_id,
        kind="research_report",
        title=title,
        author=None,
        publisher=None,
        version=version_id,
        trust_tier="contextual",
        authority=authority,
    )


def _retrieval_request(query: str, *, max_results: int) -> ReferenceRetrievalRequest:
    policy = ReferenceRetrievalPolicySnapshot(
        left_unicode_scalar_budget=0,
        right_unicode_scalar_budget=0,
        max_adjacent_spans_per_side=0,
        max_anchor_unicode_scalars=256,
        max_query_unicode_scalars=256,
        stop_at_known_speaker_change=True,
        max_adjacent_gap_ms=2_000,
        max_candidate_terms=16,
        max_results=max_results,
        retrievable_codes=("suspicious_token",),
        vocabulary=(),
    )
    context = ReferenceQueryContext(
        basis_content_hash="a" * 64,
        anchor_span_id="span-1",
        anchor_query_start=0,
        anchor_query_end=len(query),
        slices=(
            ReferenceQueryContextSlice(
                span_id="span-1",
                token_ids=("token-1",),
                span_text_hash=hashlib.sha256(query.encode()).hexdigest(),
                slice_start=0,
                slice_end=len(query),
            ),
        ),
        exact_query=query,
        algorithm="canonical_adjacent_context",
        algorithm_version="unicode-scalar-v1",
        policy_hash=reference_retrieval_policy_hash(policy),
    )
    return ReferenceRetrievalRequest(
        episode_id="episode-1",
        invocation_id="retrieval-1",
        context=context,
        policy=policy,
        candidate_terms=("Traveling Village", "Travelling Village"),
    )


def _conflicting_authority_proof(tmp_path: Path) -> tuple[object, ...]:
    support = "Traveling Village"
    counter = "Travelling Village"
    retriever = LocalReferenceRetriever(
        tmp_path / "cas",
        (
            _published_book(tmp_path / "a.md", source_id="a-support", text=support),
            _published_book(tmp_path / "z.md", source_id="z-counter", text=counter),
        ),
    )
    bounded = retriever.retrieve(_retrieval_request(support, max_results=1))
    assert len(bounded.evidence) == 1

    complete = retriever.lookup_exact(
        (
            ReferenceExactLookupRequest(
                source_id="a-support",
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(support),
            ),
            ReferenceExactLookupRequest(
                source_id="z-counter",
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(counter),
            ),
        )
    )
    specs = tuple(
        ReferenceExactClaimSpecV2(
            resolution_key="term:traveling-village",
            scope="literal_terminology",
            claimed_text=item.excerpt,
            origin="exact_extracted_evidence",
            evidence_id=item.id,
            claim_start=0,
            claim_end=len(item.excerpt),
        )
        for item in complete
    )
    targets = (
        ReferenceAuthorityTargetV2(
            resolution_key="term:traveling-village",
            scopes=("literal_terminology",),
        ),
    )
    bounded_ids = tuple(item.id for item in bounded.evidence)
    proof = build_reference_authority_proof(
        retriever=retriever,
        targets=targets,
        evidence=complete,
        claim_specs=specs,
        bounded_retrieval_evidence_ids=bounded_ids,
    )
    return retriever, targets, complete, specs, bounded_ids, proof


def test_complete_lookup_finds_authority_counterclaim_hidden_by_top_k(
    tmp_path: Path,
) -> None:
    support = "Traveling Village"
    counter = "Travelling Village"
    *_, proof = _conflicting_authority_proof(tmp_path)

    assert proof.coverage.selection_complete is True
    assert proof.coverage.bounded_retrieval_complete is False
    assert len(proof.coverage.bounded_retrieval_omitted_claim_ids) == 1
    assert {item.code for item in proof.conflicts.unresolved_conditions} == {
        "bounded_retrieval_not_authority_complete"
    }
    assert len(proof.conflicts.conflicts) == 1
    assert proof.conflicts.conflicts[0].kind == "authoritative_authoritative"
    assert proof.conflicts.blocking is True
    assert {claim.claimed_text for claim in proof.claims} == {support, counter}


def test_reference_authority_proof_round_trips_from_exact_frozen_parents(
    tmp_path: Path,
) -> None:
    retriever, targets, evidence, specs, bounded_ids, proof = _conflicting_authority_proof(tmp_path)
    exact_bytes = reference_authority_proof_bytes(proof)

    replayed = verify_reference_authority_proof(
        exact_bytes,
        retriever=retriever,
        targets=targets,
        evidence=evidence,
        claim_specs=specs,
        bounded_retrieval_evidence_ids=bounded_ids,
    )

    assert replayed == proof
    assert reference_authority_proof_bytes(replayed) == exact_bytes


def test_reference_authority_proof_v1_fails_closed(tmp_path: Path) -> None:
    retriever, targets, evidence, specs, bounded_ids, proof = _conflicting_authority_proof(tmp_path)
    payload = json.loads(reference_authority_proof_bytes(proof))
    payload["schema_version"] = 1
    old_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    with pytest.raises(ValueError, match="strict v2 schema"):
        verify_reference_authority_proof(
            old_bytes,
            retriever=retriever,
            targets=targets,
            evidence=evidence,
            claim_specs=specs,
            bounded_retrieval_evidence_ids=bounded_ids,
        )


def test_authoritative_claim_and_explicit_contextual_counterclaim_block(
    tmp_path: Path,
) -> None:
    texts = ("mTORC1", "mTOR complex 1")
    retriever = LocalReferenceRetriever(
        tmp_path / "cas-contextual",
        (
            _published_book(tmp_path / "book.md", source_id="book", text=texts[0]),
            _contextual_source(tmp_path / "notes.md", source_id="notes", text=texts[1]),
        ),
    )
    evidence = retriever.lookup_exact(
        tuple(
            ReferenceExactLookupRequest(
                source_id=source_id,
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(text),
            )
            for source_id, text in zip(("book", "notes"), texts, strict=True)
        )
    )
    target = ReferenceAuthorityTargetV2(
        resolution_key="term:mtorc1",
        scopes=("literal_terminology",),
    )
    proof = build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=tuple(
            ReferenceExactClaimSpecV2(
                resolution_key=target.resolution_key,
                scope="literal_terminology",
                claimed_text=item.excerpt,
                origin="exact_extracted_evidence",
                evidence_id=item.id,
                claim_start=0,
                claim_end=len(item.excerpt),
            )
            for item in evidence
        ),
    )

    assert {claim.strength for claim in proof.claims} == {"authoritative", "contextual"}
    assert proof.conflicts.conflicts[0].kind == "authoritative_contextual_counterclaim"
    assert proof.conflicts.blocking is True


def test_equivalent_contextual_claims_do_not_conflict_or_gain_authority(
    tmp_path: Path,
) -> None:
    texts = ("mTORC1", "ｍＴＯＲＣ１")
    retriever = LocalReferenceRetriever(
        tmp_path / "cas-equivalent",
        tuple(
            _contextual_source(
                tmp_path / f"notes-{index}.md",
                source_id=f"notes-{index}",
                text=text,
            )
            for index, text in enumerate(texts)
        ),
    )
    evidence = retriever.lookup_exact(
        tuple(
            ReferenceExactLookupRequest(
                source_id=f"notes-{index}",
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(text),
            )
            for index, text in enumerate(texts)
        )
    )
    target = ReferenceAuthorityTargetV2(
        resolution_key="term:mtorc1",
        scopes=("literal_terminology",),
    )
    proof = build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=tuple(
            ReferenceExactClaimSpecV2(
                resolution_key=target.resolution_key,
                scope="literal_terminology",
                claimed_text=item.excerpt,
                origin="exact_extracted_evidence",
                evidence_id=item.id,
                claim_start=0,
                claim_end=len(item.excerpt),
            )
            for item in evidence
        ),
    )

    assert {claim.normalized_claimed_text for claim in proof.claims} == {"mtorc1"}
    assert {claim.strength for claim in proof.claims} == {"contextual"}
    assert proof.conflicts.conflicts == ()
    assert proof.conflicts.blocking is False


def test_non_overlapping_claim_scopes_do_not_form_a_conflict(tmp_path: Path) -> None:
    texts = ("Omega-3", "Omega Three")
    retriever = LocalReferenceRetriever(
        tmp_path / "cas-scopes",
        tuple(
            _published_book(
                tmp_path / f"book-{index}.md",
                source_id=f"book-{index}",
                text=text,
            )
            for index, text in enumerate(texts)
        ),
    )
    evidence = retriever.lookup_exact(
        tuple(
            ReferenceExactLookupRequest(
                source_id=f"book-{index}",
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(text),
            )
            for index, text in enumerate(texts)
        )
    )
    target = ReferenceAuthorityTargetV2(
        resolution_key="entity:omega-3",
        scopes=("literal_terminology", "source_title"),
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
        for item, scope in zip(
            evidence,
            ("literal_terminology", "source_title"),
            strict=True,
        )
    )

    proof = build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=specs,
        evidence_omissions=(
            ReferenceEvidenceOmissionSpecV2(
                evidence_id=evidence[0].id,
                resolution_key=target.resolution_key,
                scopes=("source_title",),
                reason="no_exact_claim_for_target",
            ),
            ReferenceEvidenceOmissionSpecV2(
                evidence_id=evidence[1].id,
                resolution_key=target.resolution_key,
                scopes=("literal_terminology",),
                reason="no_exact_claim_for_target",
            ),
        ),
    )

    assert {claim.scope for claim in proof.claims} == {
        "literal_terminology",
        "source_title",
    }
    assert proof.conflicts.conflicts == ()


def test_published_author_book_and_owner_final_report_counterclaims_block(
    tmp_path: Path,
) -> None:
    texts = ("β-hydroxybutyrate", "beta hydroxybutyrate")
    retriever = LocalReferenceRetriever(
        tmp_path / "cas-book-report",
        (
            _published_book(tmp_path / "book.md", source_id="book", text=texts[0]),
            _final_report(tmp_path / "report.md", source_id="report", text=texts[1]),
        ),
    )
    evidence = retriever.lookup_exact(
        tuple(
            ReferenceExactLookupRequest(
                source_id=source_id,
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(text),
            )
            for source_id, text in zip(("book", "report"), texts, strict=True)
        )
    )
    target = ReferenceAuthorityTargetV2(
        resolution_key="term:beta-hydroxybutyrate",
        scopes=("literal_terminology",),
    )
    proof = build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=tuple(
            ReferenceExactClaimSpecV2(
                resolution_key=target.resolution_key,
                scope="literal_terminology",
                claimed_text=item.excerpt,
                origin="exact_extracted_evidence",
                evidence_id=item.id,
                claim_start=0,
                claim_end=len(item.excerpt),
            )
            for item in evidence
        ),
    )

    assert {claim.role for claim in proof.claims} == {
        "published_author_book",
        "owner_final_report",
    }
    assert proof.conflicts.conflicts[0].kind == "authoritative_authoritative"
    assert proof.conflicts.blocking is True


@pytest.mark.parametrize("version_status", ["draft", "superseded"])
def test_inactive_reference_version_is_excluded_but_covered(
    tmp_path: Path,
    version_status: str,
) -> None:
    text = "outdated spelling"
    source_id = f"report-{version_status}"
    retriever = LocalReferenceRetriever(
        tmp_path / f"cas-{version_status}",
        (
            _inactive_report(
                tmp_path / f"{version_status}.md",
                source_id=source_id,
                text=text,
                version_status=version_status,
            ),
        ),
    )
    evidence = retriever.lookup_exact(
        (
            ReferenceExactLookupRequest(
                source_id=source_id,
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(text),
            ),
        )
    )
    target = ReferenceAuthorityTargetV2(
        resolution_key="term:historical",
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
                claimed_text=text,
                origin="exact_extracted_evidence",
                evidence_id=evidence[0].id,
                claim_start=0,
                claim_end=len(text),
            ),
        ),
    )

    assert proof.claims == ()
    assert proof.coverage.selection_complete is True
    assert proof.coverage.version_coverage[0].disposition == f"excluded_{version_status}"
    assert proof.coverage.omissions[0].reason == f"{version_status}_version"
    assert proof.conflicts.blocking is False


def test_explicit_supersedes_lineage_excludes_old_version_without_false_conflict(
    tmp_path: Path,
) -> None:
    logical_source_id = "logical:report-series"
    old_text = "old spelling"
    new_text = "new spelling"
    retriever = LocalReferenceRetriever(
        tmp_path / "cas-supersedes",
        (
            _inactive_report(
                tmp_path / "old.md",
                source_id="report-old",
                text=old_text,
                version_status="superseded",
                logical_source_id=logical_source_id,
                version_id="report-v1",
            ),
            _final_report(
                tmp_path / "new.md",
                source_id="report-new",
                text=new_text,
                logical_source_id=logical_source_id,
                version_id="report-v2",
                supersedes=(
                    ReferenceAuthorityVersionRef(
                        logical_source_id=logical_source_id,
                        version_id="report-v1",
                    ),
                ),
            ),
        ),
    )
    evidence = retriever.lookup_exact(
        (
            ReferenceExactLookupRequest(
                source_id="report-new",
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(new_text),
            ),
            ReferenceExactLookupRequest(
                source_id="report-old",
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(old_text),
            ),
        )
    )
    target = ReferenceAuthorityTargetV2(
        resolution_key="term:report-series",
        scopes=("literal_terminology",),
    )
    proof = build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=tuple(
            ReferenceExactClaimSpecV2(
                resolution_key=target.resolution_key,
                scope="literal_terminology",
                claimed_text=item.excerpt,
                origin="exact_extracted_evidence",
                evidence_id=item.id,
                claim_start=0,
                claim_end=len(item.excerpt),
            )
            for item in evidence
        ),
    )

    assert len(proof.claims) == 1
    assert proof.claims[0].version_id == "report-v2"
    assert proof.claims[0].supersedes[0].version_id == "report-v1"
    assert proof.coverage.selection_complete is True
    assert {item.disposition for item in proof.coverage.version_coverage} == {
        "eligible_active",
        "excluded_superseded",
    }
    assert proof.conflicts.conflicts == ()


def test_active_evidence_requires_typed_claim_or_explicit_omission(
    tmp_path: Path,
) -> None:
    text = "background without this target"
    retriever = LocalReferenceRetriever(
        tmp_path / "cas-omission",
        (
            _contextual_source(
                tmp_path / "background.md",
                source_id="background",
                text=text,
            ),
        ),
    )
    evidence = retriever.lookup_exact(
        (
            ReferenceExactLookupRequest(
                source_id="background",
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(text),
            ),
        )
    )
    target = ReferenceAuthorityTargetV2(
        resolution_key="term:unrelated",
        scopes=("literal_terminology",),
    )
    incomplete = build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=(),
    )
    complete = build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=(),
        evidence_omissions=(
            ReferenceEvidenceOmissionSpecV2(
                evidence_id=evidence[0].id,
                resolution_key=target.resolution_key,
                scopes=target.scopes,
                reason="no_exact_claim_for_target",
            ),
        ),
    )

    assert incomplete.coverage.selection_complete is False
    assert incomplete.conflicts.unresolved_conditions[0].code == "coverage_incomplete"
    assert complete.coverage.selection_complete is True
    assert complete.coverage.omissions[0].reason == "no_exact_claim_for_target"
    assert complete.conflicts.blocking is False


def test_llm_only_claim_origin_is_rejected() -> None:
    with pytest.raises(ValueError, match="exact_extracted_evidence"):
        ReferenceExactClaimSpecV2.model_validate(
            {
                "schema_version": 2,
                "resolution_key": "term:invented",
                "scope": "literal_terminology",
                "claimed_text": "invented by a model",
                "origin": "llm_generated",
                "evidence_id": "reference-invented",
                "claim_start": 0,
                "claim_end": 19,
            }
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("excerpt_hash", "0" * 64),
        ("authority_descriptor_hash", "1" * 64),
        ("locator_hash", "2" * 64),
    ],
)
def test_claim_locator_excerpt_and_authority_tamper_fail_closed(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    retriever, targets, evidence, specs, bounded_ids, proof = _conflicting_authority_proof(tmp_path)
    payload = json.loads(reference_authority_proof_bytes(proof))
    payload["claims"][0][field] = replacement

    with pytest.raises(ValueError):
        verify_reference_authority_proof(
            _canonical_payload_bytes(payload),
            retriever=retriever,
            targets=targets,
            evidence=evidence,
            claim_specs=specs,
            bounded_retrieval_evidence_ids=bounded_ids,
        )


def test_coordinated_index_and_outer_hash_tamper_fails_parent_rebuild(
    tmp_path: Path,
) -> None:
    retriever, targets, evidence, specs, bounded_ids, proof = _conflicting_authority_proof(tmp_path)
    payload = json.loads(reference_authority_proof_bytes(proof))
    coverage = payload["coverage"]
    conflicts = payload["conflicts"]
    assert isinstance(coverage, dict) and isinstance(conflicts, dict)
    coverage["index_hash"] = "0" * 64
    _reseal(coverage)
    conflicts["coverage_receipt_id"] = coverage["id"]
    conflicts["coverage_receipt_hash"] = coverage["content_hash"]
    _reseal(conflicts)
    _reseal(payload)

    with pytest.raises(ValueError, match="frozen parents"):
        verify_reference_authority_proof(
            _canonical_payload_bytes(payload),
            retriever=retriever,
            targets=targets,
            evidence=evidence,
            claim_specs=specs,
            bounded_retrieval_evidence_ids=bounded_ids,
        )


@pytest.mark.parametrize(
    "mutation",
    ["missing", "duplicate", "reorder", "selection_complete_lie", "claim_reorder"],
)
def test_missing_duplicate_reordered_or_lying_coverage_fails_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    retriever, targets, evidence, specs, bounded_ids, proof = _conflicting_authority_proof(tmp_path)
    payload = json.loads(reference_authority_proof_bytes(proof))
    coverage = payload["coverage"]
    conflicts = payload["conflicts"]
    assert isinstance(coverage, dict) and isinstance(conflicts, dict)
    claim_ids = coverage["examined_claim_ids"]
    assert isinstance(claim_ids, list) and len(claim_ids) == 2
    if mutation == "missing":
        coverage["examined_claim_ids"] = claim_ids[:-1]
    elif mutation == "duplicate":
        coverage["examined_claim_ids"] = [claim_ids[0], claim_ids[0]]
    elif mutation == "reorder":
        coverage["examined_claim_ids"] = list(reversed(claim_ids))
    elif mutation == "selection_complete_lie":
        coverage["selection_complete"] = False
    else:
        payload["claims"] = list(reversed(payload["claims"]))
    _reseal(coverage)
    conflicts["coverage_receipt_id"] = coverage["id"]
    conflicts["coverage_receipt_hash"] = coverage["content_hash"]
    _reseal(conflicts)
    _reseal(payload)

    with pytest.raises(ValueError):
        verify_reference_authority_proof(
            _canonical_payload_bytes(payload),
            retriever=retriever,
            targets=targets,
            evidence=evidence,
            claim_specs=specs,
            bounded_retrieval_evidence_ids=bounded_ids,
        )


def test_coverage_omission_without_typed_reason_fails_closed(tmp_path: Path) -> None:
    retriever, targets, evidence, specs, bounded_ids, proof = _conflicting_authority_proof(tmp_path)
    payload = json.loads(reference_authority_proof_bytes(proof))
    coverage = payload["coverage"]
    conflicts = payload["conflicts"]
    assert isinstance(coverage, dict) and isinstance(conflicts, dict)
    coverage["omissions"] = [
        {
            "schema_version": 2,
            "kind": "evidence",
            "omitted_id": "reference-untyped-omission",
            "blocking": False,
        }
    ]
    _reseal(coverage)
    conflicts["coverage_receipt_id"] = coverage["id"]
    conflicts["coverage_receipt_hash"] = coverage["content_hash"]
    _reseal(conflicts)
    _reseal(payload)

    with pytest.raises(ValueError, match="strict v2 schema"):
        verify_reference_authority_proof(
            _canonical_payload_bytes(payload),
            retriever=retriever,
            targets=targets,
            evidence=evidence,
            claim_specs=specs,
            bounded_retrieval_evidence_ids=bounded_ids,
        )


def test_same_source_equivalent_passages_do_not_create_a_false_conflict(
    tmp_path: Path,
) -> None:
    texts = ("mTORC1", "ｍＴＯＲＣ１")
    retriever = LocalReferenceRetriever(
        tmp_path / "cas-same-source",
        (
            _published_book(
                tmp_path / "book.md",
                source_id="book",
                text="\n\n".join(texts),
            ),
        ),
    )
    evidence = retriever.lookup_exact(
        tuple(
            ReferenceExactLookupRequest(
                source_id="book",
                extraction_block_index=index,
                excerpt_start=0,
                excerpt_end=len(text),
            )
            for index, text in enumerate(texts)
        )
    )
    target = ReferenceAuthorityTargetV2(
        resolution_key="term:mtorc1",
        scopes=("literal_terminology",),
    )
    proof = build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=tuple(
            ReferenceExactClaimSpecV2(
                resolution_key=target.resolution_key,
                scope="literal_terminology",
                claimed_text=item.excerpt,
                origin="exact_extracted_evidence",
                evidence_id=item.id,
                claim_start=0,
                claim_end=len(item.excerpt),
            )
            for item in evidence
        ),
    )

    assert len(proof.claims) == 2
    assert {claim.normalized_claimed_text for claim in proof.claims} == {"mtorc1"}
    assert proof.conflicts.conflicts == ()


def test_complete_lookup_rejects_duplicate_exact_ranges(tmp_path: Path) -> None:
    text = "NRF2"
    retriever = LocalReferenceRetriever(
        tmp_path / "cas-duplicate-lookup",
        (_published_book(tmp_path / "book.md", source_id="book", text=text),),
    )
    request = ReferenceExactLookupRequest(
        source_id="book",
        extraction_block_index=0,
        excerpt_start=0,
        excerpt_end=len(text),
    )

    with pytest.raises(AdapterInputError, match="must be unique"):
        retriever.lookup_exact((request, request))


def test_complete_claim_registry_rejects_duplicate_specs(tmp_path: Path) -> None:
    retriever, targets, evidence, specs, bounded_ids, _ = _conflicting_authority_proof(tmp_path)

    with pytest.raises(ValueError, match="claim specs must be unique"):
        build_reference_authority_proof(
            retriever=retriever,
            targets=targets,
            evidence=evidence,
            claim_specs=(*specs, specs[0]),
            bounded_retrieval_evidence_ids=bounded_ids,
        )


def test_claimed_text_must_match_exact_evidence_range(tmp_path: Path) -> None:
    retriever, targets, evidence, specs, bounded_ids, _ = _conflicting_authority_proof(tmp_path)
    forged = specs[0].model_copy(update={"claimed_text": "LLM paraphrase"})

    with pytest.raises(ValueError, match="not a member"):
        build_reference_authority_proof(
            retriever=retriever,
            targets=targets,
            evidence=evidence,
            claim_specs=(forged, specs[1]),
            bounded_retrieval_evidence_ids=bounded_ids,
        )


def test_complete_proof_recomputes_and_rejects_tampered_index_identity(
    tmp_path: Path,
) -> None:
    retriever, targets, evidence, specs, bounded_ids, _ = _conflicting_authority_proof(tmp_path)
    object.__setattr__(
        retriever,
        "_index",
        replace(retriever.index, index_hash="0" * 64),
    )

    with pytest.raises(ValueError, match="index integrity failed"):
        build_reference_authority_proof(
            retriever=retriever,
            targets=targets,
            evidence=evidence,
            claim_specs=specs,
            bounded_retrieval_evidence_ids=bounded_ids,
        )


def test_non_equivalent_contextual_claims_in_same_scope_remain_unresolved(
    tmp_path: Path,
) -> None:
    texts = ("Nrf2", "NRF-2")
    retriever = LocalReferenceRetriever(
        tmp_path / "cas-contextual-conflict",
        tuple(
            _contextual_source(
                tmp_path / f"notes-{index}.md",
                source_id=f"notes-{index}",
                text=text,
            )
            for index, text in enumerate(texts)
        ),
    )
    evidence = retriever.lookup_exact(
        tuple(
            ReferenceExactLookupRequest(
                source_id=f"notes-{index}",
                extraction_block_index=0,
                excerpt_start=0,
                excerpt_end=len(text),
            )
            for index, text in enumerate(texts)
        )
    )
    target = ReferenceAuthorityTargetV2(
        resolution_key="term:nrf2",
        scopes=("literal_terminology",),
    )
    proof = build_reference_authority_proof(
        retriever=retriever,
        targets=(target,),
        evidence=evidence,
        claim_specs=tuple(
            ReferenceExactClaimSpecV2(
                resolution_key=target.resolution_key,
                scope="literal_terminology",
                claimed_text=item.excerpt,
                origin="exact_extracted_evidence",
                evidence_id=item.id,
                claim_start=0,
                claim_end=len(item.excerpt),
            )
            for item in evidence
        ),
    )

    assert proof.conflicts.conflicts[0].kind == "contextual_counterclaim"
    assert proof.conflicts.conflicts[0].severity == "unresolved"
    assert proof.conflicts.blocking is False
