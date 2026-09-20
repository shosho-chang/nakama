from __future__ import annotations

import copy
import json
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.benchmark import (
    BenchmarkCandidate,
    BlindBoundaryCase,
    BlindBoundaryClip,
    BlindBoundaryEvaluation,
    BoundaryObservation,
    GateStatus,
    ObservationOutcome,
    ReviewObservation,
    TermCodeSwitchCase,
    TermCodeSwitchEvaluation,
    TextObservation,
    benchmark_suite_hash,
    correction_process_trace_hash,
    evaluate_benchmark,
    load_blind_boundary_evaluation,
    load_correction_process_evaluation,
    load_json_fixture,
    load_process_gold,
    load_term_code_switch_evaluation,
    process_gold_corpus_hash,
)
from agents.brook.podcast_subtitles.hashing import hash_object

FIXTURES = Path(__file__).parents[3] / "fixtures" / "podcast_subtitles_v2"


def _corpora() -> tuple[dict, dict, dict]:
    return (
        load_json_fixture(FIXTURES / "anji_correction_gold.v1.json"),
        load_json_fixture(FIXTURES / "anji_boundary_gold.v1.json"),
        load_json_fixture(FIXTURES / "anji_review_gold.v1.json"),
    )


def _passing_candidate(
    *,
    text_overrides: dict[str, tuple[str | None, ObservationOutcome | str]] | None = None,
    boundary_overrides: dict[str, tuple[int, ...]] | None = None,
    review_outcome: ObservationOutcome | str = ObservationOutcome.NEEDS_REVIEW,
) -> BenchmarkCandidate:
    correction, boundary, review = _corpora()
    text_overrides = text_overrides or {}
    boundary_overrides = boundary_overrides or {}
    text_observations = []
    for case in correction["cases"]:
        text, outcome = text_overrides.get(
            case["case_id"],
            (case["expected_text"], ObservationOutcome.ACCEPTED),
        )
        text_observations.append(
            TextObservation(case_id=case["case_id"], text=text, outcome=outcome)
        )
    boundary_observations = []
    for case in boundary["cases"]:
        allowed = sorted(set(range(1, len(case["lexemes"]))) - set(case["forbidden_breaks"]))
        default_breaks = (allowed[0],) if allowed else ()
        boundary_observations.append(
            BoundaryObservation(
                case_id=case["case_id"],
                break_positions=boundary_overrides.get(case["case_id"], default_breaks),
            )
        )
    canonical_hash = hash_object(
        {
            "text": [item.text for item in text_observations],
            "boundaries": [item.break_positions for item in boundary_observations],
            "review": review_outcome.value
            if isinstance(review_outcome, ObservationOutcome)
            else review_outcome,
        }
    )
    return BenchmarkCandidate(
        candidate_id="v2-shadow-generation",
        generation_id="v2-shadow-generation",
        canonical_content_hash=canonical_hash,
        normalized_audio_hash=correction["normalized_audio_hash"],
        artifact_hash=hash_object(
            {"candidate_id": "v2-shadow-generation", "content_hash": canonical_hash}
        ),
        text_observations=tuple(text_observations),
        boundary_observations=tuple(boundary_observations),
        review_observations=tuple(
            ReviewObservation(case_id=case["case_id"], outcome=review_outcome)
            for case in review["cases"]
        ),
    )


def _evaluate(candidate: BenchmarkCandidate, **kwargs: object):
    correction, boundary, review = _corpora()
    return evaluate_benchmark(
        correction_gold=correction,
        boundary_gold=boundary,
        review_gold=review,
        candidate=candidate,
        **kwargs,
    )


def _lineage(candidate: BenchmarkCandidate) -> dict[str, str]:
    correction, boundary, review = _corpora()
    return {
        "candidate_generation_id": candidate.generation_id,
        "candidate_content_hash": candidate.canonical_content_hash,
        "normalized_audio_hash": candidate.normalized_audio_hash,
        "candidate_artifact_id": candidate.candidate_id,
        "candidate_artifact_hash": candidate.artifact_hash,
        "benchmark_suite_hash": benchmark_suite_hash(correction, boundary, review),
    }


def _blind_evaluation(
    candidate: BenchmarkCandidate,
    *,
    accepted: tuple[bool, ...] = (True,) * 100,
    complete: bool = True,
    blinded: bool = True,
) -> BlindBoundaryEvaluation:
    clips = tuple(
        BlindBoundaryClip(
            clip_id=f"clip-{index}",
            start_ms=index * 1000,
            end_ms=(index + 1) * 1000,
        )
        for index in range(3)
    )
    cases = tuple(
        BlindBoundaryCase(
            case_id=f"boundary-{index}",
            clip_id=f"clip-{index % 3}",
            boundary_after_token_id=f"token-{index}",
            accepted=value,
        )
        for index, value in enumerate(accepted)
    )
    gold_hash = hash_object(
        {
            "schema_version": 1,
            "evaluation_kind": "blind_boundary",
            "corpus_id": "three-clips-blind-v1",
            "clips": [clip.to_dict() for clip in clips],
        }
    )
    return BlindBoundaryEvaluation(
        schema_version=1,
        corpus_id="three-clips-blind-v1",
        gold_corpus_hash=gold_hash,
        blinded=blinded,
        complete=complete,
        clips=clips,
        cases=cases,
        **_lineage(candidate),
    )


def _term_evaluation(
    candidate: BenchmarkCandidate,
    *,
    observed: str = "Omega-3",
    complete: bool = True,
) -> TermCodeSwitchEvaluation:
    cases = (
        TermCodeSwitchCase(
            case_id="omega-3",
            start_ms=100,
            end_ms=500,
            expected_text="Omega-3",
            observed_text=observed,
        ),
    )
    gold_hash = hash_object(
        {
            "schema_version": 1,
            "evaluation_kind": "term_code_switch",
            "corpus_id": "terms-v1",
            "cases": [
                {
                    "case_id": "omega-3",
                    "start_ms": 100,
                    "end_ms": 500,
                    "expected_text": "Omega-3",
                }
            ],
        }
    )
    return TermCodeSwitchEvaluation(
        schema_version=1,
        corpus_id="terms-v1",
        gold_corpus_hash=gold_hash,
        complete=complete,
        cases=cases,
        **_lineage(candidate),
    )


def test_versioned_correction_fixture_has_only_existing_adjudications_and_provenance() -> None:
    correction, _, _ = _corpora()

    assert correction["schema_version"] == 1
    assert correction["corpus_id"] == "anji-program-correction-gold-v1"
    assert correction["lineage_id"] == "anji-program-v2-bc652157"
    assert Counter(case["category"] for case in correction["cases"]) == {
        "post_seal_freeze": 4,
    }
    expected_texts = {case["expected_text"] for case in correction["cases"]}
    assert {
        "類型",
        "約會對象",
        "競爭的對象",
    }.issubset(expected_texts)
    source_ids = {source["source_id"] for source in correction["sources"]}
    assert all(len(source["sha256"]) == 64 for source in correction["sources"])
    assert all(
        case["verification"]["source_ids"]
        and set(case["verification"]["source_ids"]) <= source_ids
        and case["verification"]["level"]
        and case["verification"]["locator"]
        for case in correction["cases"]
    )
    words_source = next(
        source for source in correction["sources"] if source["source_id"] == "anji-program-words-v1"
    )
    assert words_source["sha256"] == (
        "0de841c1e84772eea58b8746943c82dfbab4906f9128380685fe4537cd079200"
    )
    assert {
        case["case_id"]: (case["start_ms"], case["end_ms"]) for case in correction["cases"]
    } == {
        "anji-freeze-type": (1_718_928, 1_719_269),
        "anji-freeze-date-object-one": (2_636_285, 2_637_185),
        "anji-freeze-date-object-two": (3_236_877, 3_237_318),
        "anji-freeze-competition-object": (3_598_811, 3_599_671),
    }

    legacy = load_json_fixture(FIXTURES / "anji_legacy_correction_gold.v1.json")
    assert legacy["evaluation_scope"] == "correction_only_not_release_suite"
    assert legacy["lineage_id"] == "anji-legacy-normalized-0fabc786"
    assert Counter(case["category"] for case in legacy["cases"]) == {
        "accepted_correction": 4,
        "keep_original": 10,
    }


def test_known_gold_passes_but_unlabelled_release_gates_stay_not_evaluated() -> None:
    report = _evaluate(_passing_candidate())

    assert report.scored_case_count == 7  # 4 program text decisions + 3 boundary cases
    assert report.scored_correct_count == 7
    assert report.safety_case_count == 1
    assert report.safety_passed_count == 1
    assert not report.known_gold_passed
    assert not report.release_ready
    assert (
        report.gate("blind_boundary_rejection_rate_lt_5_percent").status is GateStatus.NOT_EVALUATED
    )
    assert (
        report.gate("gold_term_code_switch_accuracy_100_percent").status is GateStatus.NOT_EVALUATED
    )
    assert report.gate("correction_recurrence_zero").status is GateStatus.NOT_EVALUATED
    assert report.gate("keep_original_non_regression").status is GateStatus.NOT_EVALUATED
    assert {
        gate_id: report.gate(gate_id).status
        for gate_id in (
            "accepted_correction_detection_recall",
            "accepted_correction_false_negative_rate_zero",
            "keep_original_false_proposal_rate_zero",
            "harmful_apply_rate_zero",
        )
    } == {
        "accepted_correction_detection_recall": GateStatus.NOT_EVALUATED,
        "accepted_correction_false_negative_rate_zero": GateStatus.NOT_EVALUATED,
        "keep_original_false_proposal_rate_zero": GateStatus.NOT_EVALUATED,
        "harmful_apply_rate_zero": GateStatus.NOT_EVALUATED,
    }

    plain = report.to_dict()
    encoded = report.to_json()
    assert json.loads(encoded) == plain
    assert plain["summary"]["release_ready"] is False
    assert all(isinstance(gate["status"], str) for gate in plain["gates"])


def test_recurrence_keep_original_and_opencc_regressions_fail_independently() -> None:
    candidate = _passing_candidate(
        text_overrides={
            "anji-freeze-type": ("型別", "accepted"),
        }
    )

    report = _evaluate(candidate)

    assert report.gate("correction_recurrence_zero").sample_count == 0
    assert report.gate("keep_original_non_regression").sample_count == 0
    assert report.gate("correction_recurrence_zero").status is GateStatus.NOT_EVALUATED
    assert report.gate("keep_original_non_regression").status is GateStatus.NOT_EVALUATED
    assert report.gate("post_seal_text_freeze").failed_case_ids == ("anji-freeze-type",)
    assert report.scored_correct_count == 6
    assert not report.known_gold_passed


def test_all_required_accepted_corrections_and_forbidden_opencc_substitutions_are_scored() -> None:
    correction, _, _ = _corpora()
    freeze = {
        case["case_id"]: case
        for case in correction["cases"]
        if case["category"] == "post_seal_freeze"
    }

    assert all(case["lineage_id"] == correction["lineage_id"] for case in freeze.values())
    assert [case["expected_text"] for case in freeze.values()].count("約會對象") == 2
    assert {text for case in freeze.values() for text in case["forbidden_texts"]} == {
        "型別",
        "約會物件",
        "競爭的物件",
    }


def test_legal_boundary_is_accepted_and_forbidden_boundary_is_rejected() -> None:
    passing = _evaluate(_passing_candidate())
    boundary_gate = passing.gate("semantic_boundary_constraints")
    real_shape = next(
        case for case in boundary_gate.cases if case.case_id == "anji-real-shape-of-life"
    )
    assert real_shape.passed
    assert real_shape.observed["break_positions"] == [6]

    failing = _evaluate(_passing_candidate(boundary_overrides={"anji-academic-background": (2,)}))
    gate = failing.gate("semantic_boundary_constraints")
    assert gate.status is GateStatus.FAILED
    assert gate.failed_case_ids == ("anji-academic-background",)
    assert "forbidden" in gate.cases[0].reason


def test_ambiguous_mental_case_must_be_needs_review_and_never_in_accuracy_score() -> None:
    safe_report = _evaluate(_passing_candidate())
    unsafe_report = _evaluate(_passing_candidate(review_outcome=ObservationOutcome.ACCEPTED))

    assert safe_report.scored_case_count == unsafe_report.scored_case_count == 7
    assert safe_report.scored_correct_count == unsafe_report.scored_correct_count == 7
    assert safe_report.gate("needs_review_safety").status is GateStatus.PASSED
    assert unsafe_report.gate("needs_review_safety").status is GateStatus.FAILED
    assert not unsafe_report.known_gold_passed

    adjudicated_as_review = _evaluate(
        _passing_candidate(
            text_overrides={"anji-freeze-type": (None, ObservationOutcome.NEEDS_REVIEW)}
        )
    )
    assert adjudicated_as_review.scored_correct_count == 6
    assert adjudicated_as_review.gate("post_seal_text_freeze").status is GateStatus.FAILED


def test_blind_and_term_release_metrics_require_complete_labels_and_strict_thresholds() -> None:
    candidate = _passing_candidate()
    incomplete = _evaluate(
        candidate,
        blind_boundary=_blind_evaluation(candidate, accepted=(True,) * 10, complete=False),
    )
    assert (
        incomplete.gate("blind_boundary_rejection_rate_lt_5_percent").status
        is GateStatus.NOT_EVALUATED
    )

    passing = _evaluate(
        candidate,
        blind_boundary=_blind_evaluation(candidate, accepted=(False,) * 4 + (True,) * 96),
        term_code_switch=_term_evaluation(candidate),
    )
    legacy_boundary_gate = passing.gate("blind_boundary_rejection_rate_lt_5_percent")
    assert legacy_boundary_gate.status is GateStatus.PASSED
    assert legacy_boundary_gate.baseline_value is None
    assert "cannot establish V2 superiority" in legacy_boundary_gate.requirement
    paired_gate = passing.gate("paired_boundary_v2_superiority")
    assert paired_gate.status is GateStatus.NOT_EVALUATED
    assert paired_gate.baseline_value is None
    assert passing.gate("gold_term_code_switch_accuracy_100_percent").status is GateStatus.PASSED
    assert dict(passing.evaluation_hashes).keys() == {
        "blind_boundary",
        "term_code_switch",
    }
    # Proposal/decision-level safety gold is still absent and must remain an
    # explicit release blocker rather than being inferred from final text.
    assert not passing.release_ready
    assert passing.gate("harmful_apply_rate_zero").status is GateStatus.NOT_EVALUATED

    exactly_five_percent = _evaluate(
        candidate,
        blind_boundary=_blind_evaluation(candidate, accepted=(False,) * 5 + (True,) * 95),
        term_code_switch=_term_evaluation(candidate),
    )
    assert (
        exactly_five_percent.gate("blind_boundary_rejection_rate_lt_5_percent").status
        is GateStatus.FAILED
    )
    assert not exactly_five_percent.release_ready


def test_aggregate_only_release_evidence_is_rejected() -> None:
    candidate = _passing_candidate()
    aggregate = {
        "schema_version": 1,
        "evaluation_kind": "blind_boundary",
        "corpus_id": "fabricated",
        "gold_corpus_hash": "a" * 64,
        "blinded": True,
        "complete": True,
        "clip_count": 3,
        "labelled_boundaries": 100,
        "rejected_boundaries": 0,
        **_lineage(candidate),
    }
    with pytest.raises(ValueError, match="fields mismatch"):
        load_blind_boundary_evaluation(aggregate)

    aggregate_terms = {
        "schema_version": 1,
        "evaluation_kind": "term_code_switch",
        "corpus_id": "fabricated",
        "gold_corpus_hash": "b" * 64,
        "complete": True,
        "correct_count": 100,
        "total_count": 100,
        **_lineage(candidate),
    }
    with pytest.raises(ValueError, match="fields mismatch"):
        load_term_code_switch_evaluation(aggregate_terms)


def test_strict_per_case_evidence_json_round_trips() -> None:
    candidate = _passing_candidate()
    blind = _blind_evaluation(candidate)
    blind_payload = json.loads(json.dumps({**asdict(blind), "evaluation_kind": "blind_boundary"}))
    terms = _term_evaluation(candidate)
    term_payload = json.loads(json.dumps({**asdict(terms), "evaluation_kind": "term_code_switch"}))

    assert load_blind_boundary_evaluation(blind_payload) == blind
    assert load_term_code_switch_evaluation(term_payload) == terms


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_generation_id", "another-generation"),
        ("candidate_content_hash", "f" * 64),
        ("normalized_audio_hash", "e" * 64),
        ("candidate_artifact_hash", "d" * 64),
        ("benchmark_suite_hash", "c" * 64),
    ],
)
def test_release_evidence_lineage_or_hash_mismatch_is_rejected(
    field: str,
    value: str,
) -> None:
    candidate = _passing_candidate()
    evidence = replace(_term_evaluation(candidate), **{field: value})

    with pytest.raises(ValueError, match="lineage/hash mismatch"):
        _evaluate(candidate, term_code_switch=evidence)


def test_fixture_provenance_and_unknown_candidate_ids_fail_closed() -> None:
    correction, boundary, review = _corpora()
    missing_locator = copy.deepcopy(correction)
    del missing_locator["cases"][0]["verification"]["locator"]
    with pytest.raises(ValueError, match="locator"):
        evaluate_benchmark(
            correction_gold=missing_locator,
            boundary_gold=boundary,
            review_gold=review,
            candidate=_passing_candidate(),
        )

    candidate = _passing_candidate()
    candidate = replace(
        candidate,
        text_observations=candidate.text_observations
        + (TextObservation(case_id="invented-easy-case", text="pass"),),
    )
    report = evaluate_benchmark(
        correction_gold=correction,
        boundary_gold=boundary,
        review_gold=review,
        candidate=candidate,
    )
    assert report.gate("candidate_input_integrity").status is GateStatus.FAILED
    assert report.gate("candidate_input_integrity").failed_case_ids == ("invented-easy-case",)
    assert not report.known_gold_passed


def test_duplicate_observations_are_rejected_instead_of_double_counted() -> None:
    duplicate = TextObservation(case_id="same", text="x")
    with pytest.raises(ValueError, match="duplicate"):
        BenchmarkCandidate(
            candidate_id="inflated",
            generation_id="generation",
            canonical_content_hash="a" * 64,
            normalized_audio_hash="b" * 64,
            artifact_hash="c" * 64,
            text_observations=(duplicate, duplicate),
        )


def _process_gold_payload(
    candidate: BenchmarkCandidate,
    *,
    complete: bool = True,
) -> dict:
    correction, _, _ = _corpora()
    payload = {
        "schema_version": 1,
        "evaluation_kind": "correction_process_gold",
        "corpus_id": "synthetic-process-gold-v1",
        "episode_id": correction["episode_id"],
        "lineage_id": correction["lineage_id"],
        "normalized_audio_hash": candidate.normalized_audio_hash,
        "complete": complete,
        "expected_case_count": 2 if complete else 3,
        "adjudication": {
            "protocol_id": "synthetic-blind-human-v1",
            "adjudicator_ids": ["reviewer-a", "reviewer-b"],
            "status": "completed",
            "blinded_to_candidate": True,
        },
        "sources": [
            {
                "artifact_id": "synthetic-audio-relisten-log",
                "role": "human_audio_adjudication",
                "sha256": "7" * 64,
            }
        ],
        "cases": [
            {
                "case_id": "positive",
                "lineage_id": correction["lineage_id"],
                "normalized_audio_hash": candidate.normalized_audio_hash,
                "audio_span_ids": ["span-positive"],
                "evidence_token_ids": ["token-positive"],
                "start_ms": 100,
                "end_ms": 200,
                "observed_text": "wrong",
                "expected_action": "accept_correction",
                "expected_replacement": "right",
                "source_artifact_ids": ["synthetic-audio-relisten-log"],
                "adjudication_locator": "row:1",
            },
            {
                "case_id": "negative",
                "lineage_id": correction["lineage_id"],
                "normalized_audio_hash": candidate.normalized_audio_hash,
                "audio_span_ids": ["span-negative"],
                "evidence_token_ids": ["token-negative"],
                "start_ms": 300,
                "end_ms": 400,
                "observed_text": "original",
                "expected_action": "keep_original",
                "expected_replacement": None,
                "source_artifact_ids": ["synthetic-audio-relisten-log"],
                "adjudication_locator": "row:2",
            },
        ],
    }
    payload["gold_corpus_hash"] = process_gold_corpus_hash(payload)
    return payload


def _process_evidence_payload(
    candidate: BenchmarkCandidate,
    process_gold: dict,
    *,
    positive_proposal: bool = True,
    positive_apply: bool = True,
    negative_proposal: bool = False,
    negative_harmful_apply: bool = False,
    complete: bool = True,
) -> dict:
    proposals = []
    decisions = []
    if positive_proposal:
        proposals.append(
            {
                "proposal_id": "proposal-positive",
                "generation_id": "generation-parent-positive",
                "audio_span_ids": ["span-positive"],
                "evidence_token_ids": ["token-positive"],
                "start_ms": 100,
                "end_ms": 200,
                "observed_text": "wrong",
                "candidate_text": "right",
                "source": "synthetic-corrector",
                "proposal_hash": "8" * 64,
            }
        )
    if positive_apply:
        decisions.append(
            {
                "sequence": 1,
                "event_id": "decision-positive",
                "parent_generation_id": "generation-parent-positive",
                "resulting_generation_id": "generation-positive-child",
                "ledger_entry_hash": "9" * 64,
                "decision_hash": "a" * 64,
                "target_span_ids": ["span-positive"],
                "target_start_ms": 100,
                "target_end_ms": 200,
                "proposal_ids": ["proposal-positive"],
                "action": "accept_candidate",
                "replacement_text": None,
                "selected_candidate": "right",
            }
        )
    if negative_proposal:
        proposals.append(
            {
                "proposal_id": "proposal-negative",
                "generation_id": "generation-parent-negative",
                "audio_span_ids": ["span-negative"],
                "evidence_token_ids": ["token-negative"],
                "start_ms": 300,
                "end_ms": 400,
                "observed_text": "original",
                "candidate_text": "speculation",
                "source": "synthetic-corrector",
                "proposal_hash": "b" * 64,
            }
        )
    if negative_harmful_apply:
        decisions.append(
            {
                "sequence": len(decisions) + 1,
                "event_id": "decision-negative-harmful",
                "parent_generation_id": "generation-parent-negative",
                "resulting_generation_id": candidate.generation_id,
                "ledger_entry_hash": "c" * 64,
                "decision_hash": "d" * 64,
                "target_span_ids": ["span-negative"],
                "target_start_ms": 300,
                "target_end_ms": 400,
                "proposal_ids": [],
                "action": "replace",
                "replacement_text": "harmful",
                "selected_candidate": None,
            }
        )
    proposals.sort(
        key=lambda item: (
            item["generation_id"],
            item["start_ms"],
            item["end_ms"],
            item["proposal_id"],
        )
    )
    payload = {
        "schema_version": 1,
        "evaluation_kind": "correction_process",
        "process_gold_hash": process_gold["gold_corpus_hash"],
        "complete": complete,
        "expected_proposal_count": len(proposals) if complete else len(proposals) + 1,
        "expected_decision_count": len(decisions) if complete else len(decisions) + 1,
        "covered_case_ids": ["positive", "negative"],
        "source_artifacts": [
            {
                "artifact_id": "generation:all:correction_proposals.json",
                "role": "proposal_set",
                "sha256": "e" * 64,
            },
            {
                "artifact_id": "ledger-prefix:synthetic",
                "role": "ledger_prefix",
                "sha256": "f" * 64,
            },
        ],
        "proposals": proposals,
        "decisions": decisions,
        **_lineage(candidate),
    }
    payload["trace_hash"] = correction_process_trace_hash(payload)
    return payload


def _evaluate_process(
    candidate: BenchmarkCandidate,
    gold_payload: dict,
    evidence_payload: dict,
):
    return _evaluate(
        candidate,
        process_gold=load_process_gold(gold_payload),
        correction_process=load_correction_process_evaluation(evidence_payload),
    )


def test_process_trace_schema_v2_preserves_native_discovery_provenance() -> None:
    candidate = _passing_candidate()
    gold = _process_gold_payload(candidate)
    payload = _process_evidence_payload(candidate, gold)
    payload["schema_version"] = 2
    payload["decisions"] = [
        {
            "decision_family": "native_correction_v2",
            "sequence": 1,
            "event_id": "native-resolution-" + "1" * 64,
            "parent_generation_id": "generation-" + "2" * 64,
            "resulting_generation_id": candidate.generation_id,
            "ledger_entry_hash": "3" * 64,
            "decision_hash": "4" * 64,
            "target_span_ids": ["span-positive"],
            "target_start_ms": 100,
            "target_end_ms": 200,
            "action": "accept_exact_candidate",
            "candidate_discovery_id": "5" * 64,
            "candidate_discovery_hash": "6" * 64,
            "candidate_literal_sha256": "7" * 64,
            "authorized_literal_sha256": "7" * 64,
            "authorization_id": "8" * 64,
            "authorization_hash": "9" * 64,
        }
    ]
    payload["expected_decision_count"] = 1
    payload["trace_hash"] = correction_process_trace_hash(payload)

    loaded = load_correction_process_evaluation(payload)

    decision = loaded.decisions[0]
    assert decision.decision_family == "native_correction_v2"
    assert decision.proposal_ids == ()
    assert decision.candidate_discovery_id == "5" * 64
    assert decision.candidate_discovery_hash == "6" * 64
    assert decision.candidate_literal_sha256 == "7" * 64
    assert decision.selected_candidate is None


def test_process_trace_schema_v2_rejects_native_discovery_as_legacy_proposal() -> None:
    candidate = _passing_candidate()
    gold = _process_gold_payload(candidate)
    payload = _process_evidence_payload(candidate, gold)
    payload["schema_version"] = 2
    payload["decisions"] = [
        {
            "decision_family": "native_correction_v2",
            "sequence": 1,
            "event_id": "native-resolution-" + "1" * 64,
            "parent_generation_id": "generation-" + "2" * 64,
            "resulting_generation_id": candidate.generation_id,
            "ledger_entry_hash": "3" * 64,
            "decision_hash": "4" * 64,
            "target_span_ids": ["span-positive"],
            "target_start_ms": 100,
            "target_end_ms": 200,
            "proposal_ids": ["5" * 64],
            "action": "reject_candidate",
            "candidate_discovery_id": "5" * 64,
            "candidate_discovery_hash": "6" * 64,
            "candidate_literal_sha256": "7" * 64,
            "authorized_literal_sha256": None,
            "authorization_id": "8" * 64,
            "authorization_hash": "9" * 64,
        }
    ]
    payload["expected_decision_count"] = 1
    payload["trace_hash"] = correction_process_trace_hash(payload)

    with pytest.raises(ValueError, match="native.*proposal_ids"):
        load_correction_process_evaluation(payload)


def test_legacy_process_trace_schema_v1_replays_without_new_fields() -> None:
    candidate = _passing_candidate()
    gold = _process_gold_payload(candidate)
    payload = _process_evidence_payload(candidate, gold)

    loaded = load_correction_process_evaluation(payload)

    assert loaded.schema_version == 1
    assert all(decision.decision_family == "legacy_correction_v1" for decision in loaded.decisions)
    assert all(decision.candidate_discovery_id is None for decision in loaded.decisions)


def test_mixed_family_schema_v2_keeps_legacy_decision_discriminated() -> None:
    candidate = _passing_candidate()
    gold = _process_gold_payload(candidate)
    payload = _process_evidence_payload(candidate, gold)
    payload["schema_version"] = 2
    for decision in payload["decisions"]:
        decision["decision_family"] = "legacy_correction_v1"
    payload["trace_hash"] = correction_process_trace_hash(payload)

    loaded = load_correction_process_evaluation(payload)

    assert loaded.schema_version == 2
    assert all(decision.trace_schema_version == 2 for decision in loaded.decisions)
    assert [decision.to_dict() for decision in loaded.decisions] == payload["decisions"]


def test_complete_process_gold_and_trace_mechanically_pass_all_four_gates() -> None:
    candidate = _passing_candidate()
    gold = _process_gold_payload(candidate)
    evidence = _process_evidence_payload(candidate, gold)

    report = _evaluate_process(candidate, gold, evidence)

    for gate_id in (
        "accepted_correction_detection_recall",
        "accepted_correction_false_negative_rate_zero",
        "keep_original_false_proposal_rate_zero",
        "harmful_apply_rate_zero",
    ):
        gate = report.gate(gate_id)
        assert gate.status is GateStatus.PASSED
        assert gate.sample_count > 0
    assert dict(report.corpus_hashes)["correction_process_gold"] == gold["gold_corpus_hash"]
    assert dict(report.evaluation_hashes)["correction_process"] == evidence["trace_hash"]


def test_process_missed_detection_and_accepted_false_negative_are_measured() -> None:
    candidate = _passing_candidate()
    gold = _process_gold_payload(candidate)
    missed = _process_evidence_payload(
        candidate,
        gold,
        positive_proposal=False,
        positive_apply=False,
    )
    missed_report = _evaluate_process(candidate, gold, missed)
    assert missed_report.gate("accepted_correction_detection_recall").status is GateStatus.FAILED

    not_applied = _process_evidence_payload(candidate, gold, positive_apply=False)
    not_applied_report = _evaluate_process(candidate, gold, not_applied)
    assert (
        not_applied_report.gate("accepted_correction_detection_recall").status is GateStatus.PASSED
    )
    assert (
        not_applied_report.gate("accepted_correction_false_negative_rate_zero").status
        is GateStatus.FAILED
    )


def test_process_false_proposal_and_harmful_apply_fail_independently() -> None:
    candidate = _passing_candidate()
    gold = _process_gold_payload(candidate)
    false_proposal = _process_evidence_payload(candidate, gold, negative_proposal=True)
    proposal_report = _evaluate_process(candidate, gold, false_proposal)
    assert (
        proposal_report.gate("keep_original_false_proposal_rate_zero").status is GateStatus.FAILED
    )
    assert proposal_report.gate("harmful_apply_rate_zero").status is GateStatus.PASSED

    harmful = _process_evidence_payload(candidate, gold, negative_harmful_apply=True)
    harmful_report = _evaluate_process(candidate, gold, harmful)
    assert harmful_report.gate("keep_original_false_proposal_rate_zero").status is GateStatus.PASSED
    assert harmful_report.gate("harmful_apply_rate_zero").status is GateStatus.FAILED


def test_incomplete_process_inputs_remain_not_evaluated() -> None:
    candidate = _passing_candidate()
    incomplete_gold = _process_gold_payload(candidate, complete=False)
    evidence = _process_evidence_payload(candidate, incomplete_gold)
    report = _evaluate_process(candidate, incomplete_gold, evidence)
    assert all(
        report.gate(gate_id).status is GateStatus.NOT_EVALUATED
        for gate_id in (
            "accepted_correction_detection_recall",
            "accepted_correction_false_negative_rate_zero",
            "keep_original_false_proposal_rate_zero",
            "harmful_apply_rate_zero",
        )
    )

    complete_gold = _process_gold_payload(candidate)
    incomplete_evidence = _process_evidence_payload(
        candidate,
        complete_gold,
        complete=False,
    )
    report = _evaluate_process(candidate, complete_gold, incomplete_evidence)
    assert report.gate("harmful_apply_rate_zero").status is GateStatus.NOT_EVALUATED


def test_process_schema_rejects_hash_drift_duplicate_overlap_and_ambiguous_labels() -> None:
    candidate = _passing_candidate()
    gold = _process_gold_payload(candidate)

    drifted = copy.deepcopy(gold)
    drifted["sources"][0]["sha256"] = "1" * 64
    with pytest.raises(ValueError, match="gold_corpus_hash mismatch"):
        load_process_gold(drifted)

    duplicate = copy.deepcopy(gold)
    copied_case = copy.deepcopy(duplicate["cases"][0])
    copied_case["case_id"] = "duplicate-target"
    duplicate["cases"].append(copied_case)
    duplicate["expected_case_count"] = 3
    duplicate["gold_corpus_hash"] = process_gold_corpus_hash(duplicate)
    with pytest.raises(ValueError, match="duplicate process gold target"):
        load_process_gold(duplicate)

    overlap = copy.deepcopy(gold)
    overlap["cases"][1].update(
        {
            "audio_span_ids": ["span-overlap"],
            "evidence_token_ids": ["token-overlap"],
            "start_ms": 150,
            "end_ms": 250,
        }
    )
    overlap["gold_corpus_hash"] = process_gold_corpus_hash(overlap)
    with pytest.raises(ValueError, match="time ranges must not overlap"):
        load_process_gold(overlap)

    ambiguous = copy.deepcopy(gold)
    ambiguous["cases"][1]["expected_replacement"] = "should-not-exist"
    ambiguous["gold_corpus_hash"] = process_gold_corpus_hash(ambiguous)
    with pytest.raises(ValueError, match="keep_original"):
        load_process_gold(ambiguous)


def test_process_trace_rejects_hash_drift_and_non_exact_overlapping_target() -> None:
    candidate = _passing_candidate()
    gold = _process_gold_payload(candidate)
    evidence = _process_evidence_payload(candidate, gold)

    drifted = copy.deepcopy(evidence)
    drifted["source_artifacts"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="trace_hash mismatch"):
        load_correction_process_evaluation(drifted)

    overlapping = copy.deepcopy(evidence)
    overlapping["proposals"].append(
        {
            "proposal_id": "ambiguous-overlap",
            "generation_id": "generation-parent-overlap",
            "audio_span_ids": ["different-span"],
            "evidence_token_ids": ["different-token"],
            "start_ms": 150,
            "end_ms": 250,
            "observed_text": "wrong",
            "candidate_text": "guess",
            "source": "synthetic-corrector",
            "proposal_hash": "1" * 64,
        }
    )
    overlapping["proposals"].sort(
        key=lambda item: (
            item["generation_id"],
            item["start_ms"],
            item["end_ms"],
            item["proposal_id"],
        )
    )
    overlapping["expected_proposal_count"] += 1
    overlapping["trace_hash"] = correction_process_trace_hash(overlapping)
    with pytest.raises(ValueError, match="overlaps process gold"):
        _evaluate_process(candidate, gold, overlapping)


def test_process_evidence_cannot_be_inferred_from_final_candidate_only() -> None:
    candidate = _passing_candidate()
    gold = load_process_gold(_process_gold_payload(candidate))
    report = _evaluate(candidate, process_gold=gold)

    assert not report.release_ready
    assert report.gate("accepted_correction_detection_recall").status is GateStatus.NOT_EVALUATED
    assert "proposal/decision" in report.gate("harmful_apply_rate_zero").reason


def test_process_gold_and_candidate_trace_cannot_cross_lineage() -> None:
    candidate = _passing_candidate()
    gold = _process_gold_payload(candidate)
    legacy_gold = copy.deepcopy(gold)
    legacy_gold["lineage_id"] = "anji-legacy-normalized-0fabc786"
    for case in legacy_gold["cases"]:
        case["lineage_id"] = legacy_gold["lineage_id"]
    legacy_gold["gold_corpus_hash"] = process_gold_corpus_hash(legacy_gold)
    evidence = _process_evidence_payload(candidate, legacy_gold)
    with pytest.raises(ValueError, match="process gold lineage/hash"):
        _evaluate_process(candidate, legacy_gold, evidence)

    valid_gold = _process_gold_payload(candidate)
    wrong_candidate = _process_evidence_payload(candidate, valid_gold)
    wrong_candidate["candidate_generation_id"] = "another-generation"
    wrong_candidate["trace_hash"] = correction_process_trace_hash(wrong_candidate)
    with pytest.raises(ValueError, match="lineage/hash mismatch"):
        _evaluate_process(candidate, valid_gold, wrong_candidate)


def test_versioned_fixture_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_json_fixture(duplicate)
