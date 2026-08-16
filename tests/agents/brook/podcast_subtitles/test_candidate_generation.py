"""Hostile tests for deterministic, non-authoritative candidate discovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles.audit_plan import (
    build_audit_plan,
    default_correction_audit_policy,
)
from agents.brook.podcast_subtitles.candidate_generation import (
    CandidateGenerationError,
    assert_candidate_group_set,
    assert_candidate_signal_set,
    candidate_group_builder_code_hash,
    candidate_signal_builder_code_hash,
    derive_candidate_group_set,
    derive_candidate_signal_set,
)
from agents.brook.podcast_subtitles.hashing import hash_object
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    CandidateGroupSet,
    CandidateSignal,
    CandidateSourceBinding,
    CanonicalSpan,
    CanonicalTranscript,
    CorrectionAuditPolicySnapshot,
    ReferenceArtifact,
    ReferenceEvidence,
    ReferenceLocator,
    ReferenceLocatorPart,
    ReferenceQueryContext,
    ReferenceQueryContextSlice,
    ReferenceRetrievalHit,
    ReferenceRetrievalPolicySnapshot,
    ReferenceRetrievalReceipt,
    canonical_content_hash,
    reference_evidence_set_hash,
    reference_retrieval_policy_hash,
)
from tests.agents.brook.podcast_subtitles.reference_authority_fixtures import (
    reference_authority_fixture,
)
from tests.agents.brook.podcast_subtitles.test_audit_plan import (
    H1,
    H2,
    H3,
    H4,
    H5,
    H6,
    H7,
    H8,
    H9,
    HA,
    HB,
    HC,
    make_case,
    make_coverage,
    make_seam,
)

CASE_FIXTURE = (
    Path(__file__).parents[3]
    / "fixtures"
    / "podcast_subtitles_v2"
    / "correction_quality_slice1_cases.v1.json"
)


@pytest.fixture(autouse=True)
def isolated_db() -> None:
    """This pure suite performs no application-state access."""


def make_reference_receipt(
    transcript: CanonicalTranscript,
    *,
    candidate_term: str = "數位遊牧",
    support_kind: str = "candidate_term_exact",
) -> tuple[CanonicalTranscript, ReferenceRetrievalReceipt]:
    target = transcript.spans[0]
    query = "".join(
        token.text for token in transcript.tokens if token.id in target.token_ids
    )
    policy = ReferenceRetrievalPolicySnapshot(
        left_unicode_scalar_budget=0,
        right_unicode_scalar_budget=0,
        max_adjacent_spans_per_side=0,
        max_anchor_unicode_scalars=256,
        max_query_unicode_scalars=256,
        stop_at_known_speaker_change=True,
        max_adjacent_gap_ms=2000,
        max_candidate_terms=16,
        max_results=3,
        retrievable_codes=("reference_name_term",),
        vocabulary=(),
    )
    context = ReferenceQueryContext(
        basis_content_hash=transcript.content_hash,
        anchor_span_id=target.id,
        anchor_query_start=0,
        anchor_query_end=len(query),
        slices=(
            ReferenceQueryContextSlice(
                span_id=target.id,
                token_ids=target.token_ids,
                span_text_hash=hashlib.sha256(query.encode("utf-8")).hexdigest(),
                slice_start=0,
                slice_end=len(query),
            ),
        ),
        exact_query=query,
        algorithm="canonical_adjacent_context",
        algorithm_version="unicode-scalar-v1",
        policy_hash=reference_retrieval_policy_hash(policy),
    )
    locator = ReferenceLocator(
        parts=(ReferenceLocatorPart(kind="paragraph", value="1"),)
    )
    artifact = ReferenceArtifact(
        source_id="book-1",
        kind="book",
        source_format="text",
        digest=ArtifactDigest(uri="file:///book.txt", sha256=H1, size_bytes=100),
        extracted_text=ArtifactDigest(
            uri="file:///book.extracted.json", sha256=H2, size_bytes=120
        ),
        extractor_name="fixture-extractor",
        extractor_version="1",
        extractor_config_hash=H3,
        extractor_code_hash=H4,
        extractor_runtime_hash=H5,
        offset_unit="unicode_scalar_v1",
        extraction_block_count=1,
        title="Fixture Book",
        author="Fixture Author",
        publisher="Fixture Publisher",
        version="1",
        trust_tier="authoritative",
        authority=reference_authority_fixture(
            source_id="book-1",
            kind="book",
            title="Fixture Book",
            author="Fixture Author",
            publisher="Fixture Publisher",
            version="1",
            trust_tier="authoritative",
        ),
    )
    excerpt = f"作者使用「{candidate_term}」這個詞。"
    evidence = ReferenceEvidence(
        id="reference-evidence-1",
        artifact=artifact,
        locator=locator,
        extraction_block_index=0,
        extraction_block_hash=H6,
        excerpt_start=0,
        excerpt_end=len(excerpt),
        excerpt=excerpt,
        excerpt_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
    )
    hit = ReferenceRetrievalHit(
        evidence_id=evidence.id,
        rank=1,
        relevance=1.0,
        query_support_start=0,
        query_support_end=len(query),
        support_kind=support_kind,
        candidate_term_index=0 if support_kind == "candidate_term_exact" else None,
    )
    receipt = ReferenceRetrievalReceipt(
        query_id="reference-query-1",
        episode_id=transcript.episode_id,
        invocation_id="reference-run-1",
        audio_span_id=target.id,
        query=query,
        context=context,
        policy=policy,
        candidate_terms=(candidate_term,),
        allowed_source_ids=(artifact.source_id,),
        retriever="fixture-retriever",
        retriever_version="1",
        retriever_config_hash=H7,
        retriever_code_hash=H8,
        retriever_runtime_hash=H9,
        index_hash=HA,
        query_plan_hash=HB,
        candidate_passages_examined=1,
        max_results=3,
        status="completed",
        hits=(hit,),
        evidence=(evidence,),
    )
    revised = CanonicalTranscript.model_validate(
        {
            **transcript.model_dump(),
            "reference_evidence_hash": reference_evidence_set_hash((evidence,)),
            "content_hash": canonical_content_hash(transcript.tokens),
        }
    )
    return revised, receipt


def derive_clean_case() -> tuple[object, object, object, CanonicalTranscript, tuple[object, ...]]:
    transcript, recognitions = make_case()
    coverage = make_coverage(transcript, recognitions)
    seam = make_seam(transcript, recognitions, seam_ms=None)
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
        speech_coverage=coverage,
        seam_evidence=seam,
    )
    signals = derive_candidate_signal_set(
        plan,
        transcript,
        recognitions,
        references_enrolled=False,
        speech_coverage=coverage,
        seam_evidence=seam,
    )
    groups = derive_candidate_group_set(plan, signals, transcript)
    return plan, signals, groups, transcript, recognitions


def test_clean_inputs_can_produce_a_complete_empty_signal_and_group_set() -> None:
    plan, signals, groups, transcript, recognitions = derive_clean_case()

    assert signals.signals == ()
    assert groups.signal_ids == ()
    assert groups.groups == ()
    assert signals.builder_code_hash == candidate_signal_builder_code_hash()
    assert groups.builder_code_hash == candidate_group_builder_code_hash()
    assert assert_candidate_signal_set(
        signals,
        plan,
        transcript,
        recognitions,
        references_enrolled=False,
        speech_coverage=make_coverage(transcript, recognitions),
        seam_evidence=make_seam(transcript, recognitions, seam_ms=None),
    ) == signals
    assert assert_candidate_group_set(groups, plan, signals, transcript) == groups


def test_overlapping_numeric_latin_and_acronym_signals_are_grouped_losslessly() -> None:
    transcript, recognitions = make_case(("AI3",))
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    signal_set = derive_candidate_signal_set(
        plan, transcript, recognitions, references_enrolled=False
    )
    group_set = derive_candidate_group_set(plan, signal_set, transcript)

    relevant = {
        item.code
        for item in signal_set.signals
        if item.code
        in {
            "numeric_arabic_digits_detected",
            "latin_code_switch_detected",
            "acronym_initialism_detected",
        }
    }
    assert relevant == {
        "numeric_arabic_digits_detected",
        "latin_code_switch_detected",
        "acronym_initialism_detected",
    }
    assert tuple(
        sorted(signal_id for group in group_set.groups for signal_id in group.signal_ids)
    ) == group_set.signal_ids
    assert any(len(group.signal_ids) >= 3 for group in group_set.groups)
    assert all(group.discovery_required for group in group_set.groups if not group.options)
    assert group_set.proposal_status == "dormant_no_proposals_created"


def test_recognition_sources_pair_content_evidence_and_raw_digest() -> None:
    transcript, recognitions = make_case(confidences=(None, 0.95))
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    signal_set = derive_candidate_signal_set(
        plan, transcript, recognitions, references_enrolled=False
    )
    signal = next(
        item
        for item in signal_set.signals
        if item.code == "recognition_confidence_missing"
    )
    by_kind = {binding.kind: binding for binding in signal.source_bindings}

    assert by_kind["recognition_evidence"].record_ids == ("recognition-1",)
    assert by_kind["recognition_raw_artifact"].content_hash == recognitions[0].raw_output_hash
    assert by_kind["recognition_raw_artifact"].id.endswith(
        recognitions[0].raw_output_hash
    )


def test_source_kind_cannot_be_forged_even_when_signal_id_is_recomputed() -> None:
    transcript, recognitions = make_case(("AI3",))
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    signal_set = derive_candidate_signal_set(
        plan, transcript, recognitions, references_enrolled=False
    )
    original = next(
        item
        for item in signal_set.signals
        if item.code == "numeric_arabic_digits_detected"
    )
    forged_binding = CandidateSourceBinding(
        kind="reference_source_artifact",
        id="reference-source:forged",
        content_hash=HC,
        record_ids=("forged",),
    )
    payload = original.model_dump()
    payload["source_bindings"] = (forged_binding,)
    payload_without_id = {key: value for key, value in payload.items() if key != "id"}
    payload["id"] = hash_object(payload_without_id)

    with pytest.raises(ValidationError, match="contradicts evidence_kind"):
        CandidateSignal.model_validate(payload)


def test_coverage_signals_include_leading_and_trailing_episode_boundaries() -> None:
    transcript, recognitions = make_case()
    coverage = make_coverage(
        transcript,
        recognitions,
        uncovered=((20, 80), (720, 760)),
    )
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
        speech_coverage=coverage,
    )
    signal_set = derive_candidate_signal_set(
        plan,
        transcript,
        recognitions,
        references_enrolled=False,
        speech_coverage=coverage,
    )
    boundary_signals = [
        item
        for item in signal_set.signals
        if item.code == "speech_coverage_uncovered_at_boundary"
    ]

    assert {item.target_id for item in boundary_signals} == {
        plan.boundary_targets[0].id,
        plan.boundary_targets[-1].id,
    }
    assert all(
        {binding.kind for binding in item.source_bindings}
        == {"speech_coverage_receipt", "speech_coverage_raw_artifact"}
        for item in boundary_signals
    )


def test_conflicted_seam_is_a_trigger_and_never_audio_evidence() -> None:
    transcript, recognitions = make_case()
    seam = make_seam(
        transcript,
        recognitions,
        seam_ms=500,
        observation_status="conflict",
    )
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
        seam_evidence=seam,
    )
    signal_set = derive_candidate_signal_set(
        plan,
        transcript,
        recognitions,
        references_enrolled=False,
        seam_evidence=seam,
    )
    signal = next(item for item in signal_set.signals if item.code == "chunk_seam_conflict")

    assert signal.authority == "review_trigger_only"
    assert signal.is_audio_evidence is False
    assert signal.proposal_created is False
    assert {binding.kind for binding in signal.source_bindings} == {
        "recognition_raw_artifact",
        "seam_evidence",
        "seam_observation",
    }


@pytest.mark.parametrize(
    "support_kind",
    ["candidate_term_exact", "phonetic_exact", "lexical"],
)
def test_reference_support_without_exact_replacement_target_creates_no_option(
    support_kind: str,
) -> None:
    transcript, recognitions = make_case(("數位遊牧",))
    transcript, receipt = make_reference_receipt(
        transcript,
        support_kind=support_kind,
    )
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=True,
        reference_retrievals=(receipt,),
    )
    signal_set = derive_candidate_signal_set(
        plan,
        transcript,
        recognitions,
        references_enrolled=True,
        reference_retrievals=(receipt,),
    )
    group_set = derive_candidate_group_set(plan, signal_set, transcript)
    reference_signal = next(
        item
        for item in signal_set.signals
        if item.category == "reference_name_term"
    )

    assert reference_signal.candidate_text is None
    assert reference_signal.reference_evidence_ids == ("reference-evidence-1",)
    assert all(group.options == () for group in group_set.groups)
    assert all(group.discovery_required for group in group_set.groups)
    assert group_set.proposal_status == "dormant_no_proposals_created"


def test_signal_set_and_group_set_bind_exact_plan_and_source_identities() -> None:
    transcript, recognitions = make_case(("AI3",))
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    signal_set = derive_candidate_signal_set(
        plan, transcript, recognitions, references_enrolled=False
    )
    group_set = derive_candidate_group_set(plan, signal_set, transcript)

    signal_payload = signal_set.model_dump()
    signal_payload["audit_plan_content_hash"] = H1
    signal_without_hash = {
        key: value for key, value in signal_payload.items() if key != "content_hash"
    }
    signal_payload["content_hash"] = hash_object(signal_without_hash)
    forged_signal_set = type(signal_set).model_validate(signal_payload)
    with pytest.raises(CandidateGenerationError, match="AuditPlan content drift"):
        derive_candidate_group_set(plan, forged_signal_set, transcript)

    group_payload = group_set.model_dump()
    group_payload["signal_set_content_hash"] = H2
    group_without_hash = {
        key: value for key, value in group_payload.items() if key != "content_hash"
    }
    group_payload["content_hash"] = hash_object(group_without_hash)
    forged_group_set = type(group_set).model_validate(group_payload)
    with pytest.raises(CandidateGenerationError, match="not reproducible"):
        assert_candidate_group_set(forged_group_set, plan, signal_set, transcript)


def _corpus_cell(
    plan: object, target_kind: str, category: str, ordinal: int
) -> object:
    targets = (
        plan.span_targets if target_kind == "span" else plan.boundary_targets  # type: ignore[attr-defined]
    )
    return next(
        cell
        for cell in plan.cells  # type: ignore[attr-defined]
        if cell.target_kind == target_kind
        and cell.target_id == targets[ordinal].id
        and cell.category == category
    )


def _combine_into_one_span(transcript: CanonicalTranscript) -> CanonicalTranscript:
    combined = CanonicalSpan(
        id="span-combined",
        token_ids=tuple(token.id for token in transcript.tokens),
        start_ms=transcript.spans[0].start_ms,
        end_ms=transcript.spans[-1].end_ms,
    )
    return CanonicalTranscript.model_validate(
        {**transcript.model_dump(), "spans": (combined,)}
    )


def _run_declared_behavior(case: dict[str, object]) -> None:
    case_id = str(case["id"])
    category = str(case["category"])
    target_kind = str(case["target_kind"])
    expected_applicability = str(case["expected_applicability"])
    expected_code = case["expected_signal_code"]
    texts: tuple[str, ...] = ("今天", "訪談")
    confidences: tuple[float | None, ...] | None = None
    speakers: tuple[str | None, ...] | None = None
    coverage_intervals: tuple[tuple[int, int], ...] | None = None
    seam_kind: str | None = None
    references = False

    if case_id == "span-lexical-trigger":
        coverage_intervals = ((150, 250),)
    elif case_id == "span-reference-hit":
        texts = ("數位遊牧",)
        references = True
    elif case_id == "span-numeric-trigger":
        texts = ("3",)
    elif case_id == "span-negation-trigger":
        texts = ("不",)
    elif case_id == "span-latin-trigger":
        texts = ("Ai",)
    elif case_id == "span-acronym-trigger":
        texts = ("AI",)
    elif case_id == "span-disfluency-trigger":
        texts = ("嗯",)
    elif case_id == "span-confidence-low":
        confidences = (0.2,)
        texts = ("今天",)
    elif case_id == "span-speaker-mixed":
        texts = ("甲", "乙")
        speakers = ("speaker-a", "speaker-b")
    elif case_id == "boundary-seam-conflict":
        seam_kind = "conflict"
    elif case_id == "boundary-seam-unchunked":
        seam_kind = "unchunked"
    elif case_id == "boundary-coverage-uncovered":
        coverage_intervals = ((350, 450),)
    elif case_id == "boundary-coverage-clean":
        coverage_intervals = ()
    elif case_id == "boundary-speaker-change":
        speakers = ("speaker-a", "speaker-b")
    elif case_id == "boundary-repetition":
        texts = ("甲", "甲")

    transcript, recognitions = make_case(
        texts, confidences=confidences, speakers=speakers
    )
    if case_id == "span-speaker-mixed":
        transcript = _combine_into_one_span(transcript)

    receipt = None
    if references:
        transcript, receipt = make_reference_receipt(transcript)
    coverage = (
        make_coverage(transcript, recognitions, uncovered=coverage_intervals)
        if coverage_intervals is not None
        else None
    )
    seam = None
    if seam_kind == "conflict":
        seam = make_seam(
            transcript,
            recognitions,
            seam_ms=500,
            observation_status="conflict",
        )
    elif seam_kind == "unchunked":
        seam = make_seam(transcript, recognitions, seam_ms=None)
    reference_receipts = (receipt,) if receipt is not None else ()
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=references,
        reference_retrievals=reference_receipts,
        speech_coverage=coverage,
        seam_evidence=seam,
    )
    ordinal = 0
    if target_kind == "boundary":
        ordinal = (
            0
            if case_id == "boundary-cross-span-endpoint"
            else 1
        )
    actual_cell = _corpus_cell(plan, target_kind, category, ordinal)
    assert actual_cell.applicability == expected_applicability  # type: ignore[attr-defined]

    if case["execution_scope"] == "signal_only":
        signal_set = derive_candidate_signal_set(
            plan,
            transcript,
            recognitions,
            references_enrolled=references,
            reference_retrievals=reference_receipts,
            speech_coverage=coverage,
            seam_evidence=seam,
        )
        matching = [
            signal
            for signal in signal_set.signals
            if signal.category == category and signal.code == expected_code
        ]
        assert matching, f"{case_id} did not execute expected signal {expected_code}"
        assert all(signal.authority == "review_trigger_only" for signal in matching)
        assert all(signal.proposal_created is False for signal in matching)


def _run_closed_category_hostile(_: dict[str, object]) -> None:
    payload = default_correction_audit_policy().model_dump()
    payload["span_categories"] = payload["span_categories"][:-1]
    with pytest.raises(ValidationError, match="closed ordered set"):
        CorrectionAuditPolicySnapshot.model_validate(payload)


def _run_cross_generation_hostile(_: dict[str, object]) -> None:
    transcript, recognitions = make_case()
    first = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    second_transcript = CanonicalTranscript.model_validate(
        {**transcript.model_dump(), "generation_id": "generation-hostile"}
    )
    second = build_audit_plan(
        second_transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    assert first.basis_hash != second.basis_hash
    assert first.span_targets[0].id != second.span_targets[0].id


def _run_forged_provenance_hostile(_: dict[str, object]) -> None:
    transcript, recognitions = make_case(("AI3",))
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    signal_set = derive_candidate_signal_set(
        plan, transcript, recognitions, references_enrolled=False
    )
    original = signal_set.signals[0]
    payload = original.model_dump()
    payload["source_bindings"] = (
        CandidateSourceBinding(
            kind="reference_source_artifact",
            id="reference-source:forged-corpus",
            content_hash=HC,
            record_ids=("forged-corpus",),
        ),
    )
    payload["id"] = hash_object(
        {key: value for key, value in payload.items() if key != "id"}
    )
    with pytest.raises(ValidationError, match="contradicts evidence_kind"):
        CandidateSignal.model_validate(payload)


def _run_lossless_grouping_hostile(_: dict[str, object]) -> None:
    transcript, recognitions = make_case(("AI3",))
    plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
    )
    signal_set = derive_candidate_signal_set(
        plan, transcript, recognitions, references_enrolled=False
    )
    group_set = derive_candidate_group_set(plan, signal_set, transcript)
    payload = group_set.model_dump()
    payload["signal_ids"] = ()
    payload["groups"] = ()
    payload["content_hash"] = hash_object(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )
    forged = CandidateGroupSet.model_validate(payload)
    with pytest.raises(CandidateGenerationError, match="not reproducible"):
        assert_candidate_group_set(forged, plan, signal_set, transcript)


_BEHAVIOR_CASE_IDS = {
    "span-lexical-trigger",
    "span-lexical-clean",
    "span-reference-hit",
    "span-reference-not-enrolled",
    "span-numeric-trigger",
    "span-numeric-clean",
    "span-negation-trigger",
    "span-negation-clean",
    "span-latin-trigger",
    "span-latin-clean",
    "span-acronym-trigger",
    "span-acronym-clean",
    "span-disfluency-trigger",
    "span-disfluency-clean",
    "span-confidence-low",
    "span-confidence-high",
    "span-speaker-mixed",
    "span-speaker-stable",
    "boundary-cross-span-risk",
    "boundary-cross-span-endpoint",
    "boundary-seam-conflict",
    "boundary-seam-unchunked",
    "boundary-coverage-uncovered",
    "boundary-coverage-clean",
    "boundary-speaker-change",
    "boundary-speaker-stable",
    "boundary-repetition",
    "boundary-repetition-clean",
}
_CASE_RUNNERS = {
    **{case_id: _run_declared_behavior for case_id in _BEHAVIOR_CASE_IDS},
    "hostile-closed-category-matrix": _run_closed_category_hostile,
    "hostile-cross-generation-basis": _run_cross_generation_hostile,
    "hostile-forged-provenance": _run_forged_provenance_hostile,
    "hostile-anomaly-signal-drop": _run_lossless_grouping_hostile,
}
_CORPUS_CASES = json.loads(CASE_FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_every_declared_corpus_case_has_one_explicit_executable_runner() -> None:
    assert set(_CASE_RUNNERS) == {case["id"] for case in _CORPUS_CASES}


@pytest.mark.parametrize(
    "case",
    _CORPUS_CASES,
    ids=[case["id"] for case in _CORPUS_CASES],
)
def test_correction_quality_slice1_case_executes(case: dict[str, object]) -> None:
    _CASE_RUNNERS[str(case["id"])](case)
