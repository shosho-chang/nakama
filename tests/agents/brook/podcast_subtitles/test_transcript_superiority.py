from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object
from agents.brook.podcast_subtitles.transcript_gold import (
    LexicalEvaluatorIdentityV1,
    MetricNotEvaluated,
    MetricRate,
    TranscriptEvaluationResult,
    TranscriptEvaluationStatus,
    TranscriptMetrics,
    measure_lexical_evaluator_identity,
)
from agents.brook.podcast_subtitles.transcript_superiority import (
    SuperiorityVerdictStatus,
    build_default_transcript_superiority_policy,
    compare_transcript_evaluations,
    load_transcript_superiority_policy,
    load_transcript_superiority_verdict,
    verify_transcript_superiority_policy,
    verify_transcript_superiority_verdict,
)

_H = {
    "gold": "1" * 64,
    "gold_other": "2" * 64,
    "packet": "3" * 64,
    "audio": "4" * 64,
    "v2": "5" * 64,
    "v1": "6" * 64,
}


@pytest.fixture(scope="module")
def evaluator_identity() -> LexicalEvaluatorIdentityV1:
    return measure_lexical_evaluator_identity()


def _metric(value: tuple[int, int]) -> MetricRate:
    return MetricRate.of(*value)


def _metrics(
    identity: LexicalEvaluatorIdentityV1,
    *,
    substitutions: int,
    deletions: int,
    insertions: int,
    denominator: int = 1_000,
    entity: MetricRate | MetricNotEvaluated | None = None,
    code_switch: MetricRate | MetricNotEvaluated | None = None,
    numeric: MetricRate | MetricNotEvaluated | None = None,
    critical_omission: MetricRate | MetricNotEvaluated | None = None,
    needs_review_precision: tuple[int, int] = (9, 10),
    needs_review_recall: tuple[int, int] = (10, 10),
) -> TranscriptMetrics:
    errors = substitutions + deletions + insertions
    return TranscriptMetrics(
        lexical_normalization_profile=identity.normalization_profile,
        lexical_evaluator_identity=identity,
        scored_text_clip_count=20,
        lexical_character_substitutions=substitutions,
        lexical_character_deletions=deletions,
        lexical_character_insertions=insertions,
        lexical_character_error_rate=MetricRate.of(errors, denominator),
        lexical_character_accuracy=MetricRate.of(max(denominator - errors, 0), denominator),
        word_token_accuracy=MetricNotEvaluated(
            reason_codes=("provider_token_boundaries_not_comparable",)
        ),
        entity_recall=entity or MetricRate.of(10, 10),
        code_switch_recall=code_switch or MetricRate.of(5, 5),
        numeric_recall=numeric or MetricRate.of(4, 4),
        lexical_deletion_edit_rate=MetricRate.of(deletions, denominator),
        critical_omission_rate=critical_omission or MetricRate.of(0, 6),
        lexical_insertion_edit_rate=MetricRate.of(insertions, denominator),
        needs_review_precision=_metric(needs_review_precision),
        needs_review_recall=_metric(needs_review_recall),
        correction_detection_recall=MetricRate.of(0, 0),
        correction_apply_recall=MetricRate.of(0, 0),
        false_keep_original_rate=MetricRate.of(0, 0),
        harmful_apply_rate=MetricRate.of(0, 0),
        source_precision=MetricRate.of(0, 0),
    )


def _evaluation(
    *,
    candidate_hash: str,
    identity: LexicalEvaluatorIdentityV1,
    gold_hash: str = _H["gold"],
    substitutions: int,
    deletions: int,
    insertions: int,
    entity: MetricRate | MetricNotEvaluated | None = None,
    needs_review_precision: tuple[int, int] = (9, 10),
    needs_review_recall: tuple[int, int] = (10, 10),
) -> TranscriptEvaluationResult:
    metrics = _metrics(
        identity,
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        entity=entity,
        needs_review_precision=needs_review_precision,
        needs_review_recall=needs_review_recall,
    )
    return TranscriptEvaluationResult.build(
        status=TranscriptEvaluationStatus.EVALUATED,
        reason_codes=(),
        gold_suite_hash=gold_hash,
        candidate_artifact_hash=candidate_hash,
        normalized_audio_hash=_H["audio"],
        annotation_packet_hash=_H["packet"],
        metrics=metrics,
        correction_metrics_status=TranscriptEvaluationStatus.NOT_EVALUATED,
        correction_metrics_reason_codes=("separate_process_gold_required",),
        clip_results=(
            # The superiority layer binds the complete evaluation artifact; it does not
            # inspect candidate transcript text or reproduce per-clip scoring.
            {
                "clip_id": "clip-001",
                "text_scored": True,
                "gold_text_hash": "7" * 64,
                "candidate_text_hash": "8" * 64,
                "lexical_character_substitutions": substitutions,
                "lexical_character_deletions": deletions,
                "lexical_character_insertions": insertions,
                "gold_lexical_character_count": 1_000,
                "entity_correct": 10,
                "entity_total": 10,
                "code_switch_correct": 5,
                "code_switch_total": 5,
                "numeric_correct": 4,
                "numeric_total": 4,
                "critical_omissions": 0,
                "critical_omission_total": 6,
                "gold_needs_review": True,
                "candidate_needs_review": True,
            },
        ),
    )


def _policy(identity: LexicalEvaluatorIdentityV1):
    return build_default_transcript_superiority_policy(
        comparison_scope="recognition_adapter_shadow_only",
        challenger_system_id="qwen-primary",
        baseline_system_id="legacy-v1-whisperx",
        v2_candidate_artifact_hash=_H["v2"],
        legacy_v1_candidate_artifact_hash=_H["v1"],
        normalized_audio_hash=_H["audio"],
        annotation_packet_hash=_H["packet"],
        lexical_evaluator_identity_hash=identity.identity_hash,
    )


def _passing_pair(identity: LexicalEvaluatorIdentityV1):
    v2 = _evaluation(
        candidate_hash=_H["v2"],
        identity=identity,
        substitutions=74,
        deletions=8,
        insertions=8,
        needs_review_precision=(9, 10),
        needs_review_recall=(10, 10),
    )
    v1 = _evaluation(
        candidate_hash=_H["v1"],
        identity=identity,
        substitutions=100,
        deletions=10,
        insertions=10,
        needs_review_precision=(8, 10),
        needs_review_recall=(9, 10),
    )
    return v2, v1


def test_fixed_policy_is_deterministic_content_addressed_and_rejects_tamper(
    evaluator_identity: LexicalEvaluatorIdentityV1,
) -> None:
    policy = _policy(evaluator_identity)

    assert policy == _policy(evaluator_identity)
    assert policy.comparison_scope == "recognition_adapter_shadow_only"
    assert policy.claim_limit == "adapter_diagnosis_only_not_full_v2_or_release_superiority"
    assert verify_transcript_superiority_policy(policy, policy.policy_hash) == policy
    assert load_transcript_superiority_policy(policy.canonical_bytes()) == policy

    policy_payload = policy.model_dump(mode="python", exclude={"policy_hash"})
    identity_payload = policy.superiority_evaluator_identity.model_dump(
        mode="python", exclude={"identity_hash"}
    )
    identity_payload["python_version"] = "drifted-comparator-runtime"
    identity_payload["identity_hash"] = hash_object(
        {
            "artifact_kind": "transcript_superiority_evaluator_identity",
            **identity_payload,
        }
    )
    policy_payload["superiority_evaluator_identity"] = identity_payload
    drifted_policy = type(policy)(
        **policy_payload,
        policy_hash=hash_object(
            {"artifact_kind": "transcript_superiority_policy", **policy_payload}
        ),
    )
    with pytest.raises(ValueError, match="evaluator identity drift"):
        verify_transcript_superiority_policy(drifted_policy, drifted_policy.policy_hash)

    payload = json.loads(policy.canonical_bytes())
    payload["thresholds"]["minimum_relative_lexical_cer_reduction"]["numerator"] = 0
    with pytest.raises(ValueError, match="invalid TranscriptSuperiorityPolicyV1 JSON"):
        load_transcript_superiority_policy(canonical_json_bytes(payload))

    pretty = json.dumps(json.loads(policy.canonical_bytes()), indent=2).encode()
    with pytest.raises(ValueError, match="exact canonical JSON"):
        load_transcript_superiority_policy(pretty)


def test_v2_must_show_clear_improvement_and_all_safety_checks_to_pass(
    evaluator_identity: LexicalEvaluatorIdentityV1,
) -> None:
    policy = _policy(evaluator_identity)
    v2, v1 = _passing_pair(evaluator_identity)

    verdict = compare_transcript_evaluations(policy, v2_evaluation=v2, v1_evaluation=v1)

    assert verdict.status is SuperiorityVerdictStatus.PASSED
    assert verdict.reason_codes == ()
    assert verdict.gold_suite_hash == _H["gold"]
    assert verdict.v2_candidate_artifact_hash == _H["v2"]
    assert verdict.legacy_v1_candidate_artifact_hash == _H["v1"]
    assert all(item.status == "evaluated" for item in verdict.metric_comparisons)
    assert all(check.passed for item in verdict.metric_comparisons for check in item.checks)
    assert (
        verify_transcript_superiority_verdict(
            verdict,
            policy=policy,
            v2_evaluation=v2,
            v1_evaluation=v1,
            expected_verdict_hash=verdict.verdict_hash,
        )
        == verdict
    )


@pytest.mark.parametrize(
    ("v2_substitutions", "v2_deletions", "v2_insertions", "failed_check"),
    [
        (95, 8, 8, "minimum_relative_lexical_cer_reduction"),
        (73, 11, 6, "lexical_deletion_no_regression"),
    ],
)
def test_no_material_improvement_or_any_safety_regression_fails(
    evaluator_identity: LexicalEvaluatorIdentityV1,
    v2_substitutions: int,
    v2_deletions: int,
    v2_insertions: int,
    failed_check: str,
) -> None:
    v1 = _evaluation(
        candidate_hash=_H["v1"],
        identity=evaluator_identity,
        substitutions=100,
        deletions=10,
        insertions=10,
        needs_review_precision=(8, 10),
        needs_review_recall=(9, 10),
    )
    v2 = _evaluation(
        candidate_hash=_H["v2"],
        identity=evaluator_identity,
        substitutions=v2_substitutions,
        deletions=v2_deletions,
        insertions=v2_insertions,
    )

    verdict = compare_transcript_evaluations(
        _policy(evaluator_identity), v2_evaluation=v2, v1_evaluation=v1
    )

    assert verdict.status is SuperiorityVerdictStatus.FAILED
    assert any(failed_check in reason for reason in verdict.reason_codes)


def test_required_metric_not_evaluated_makes_whole_verdict_not_evaluated(
    evaluator_identity: LexicalEvaluatorIdentityV1,
) -> None:
    v2, v1 = _passing_pair(evaluator_identity)
    unavailable = MetricNotEvaluated(reason_codes=("entity_labels_not_exhaustive",))
    v2 = _evaluation(
        candidate_hash=_H["v2"],
        identity=evaluator_identity,
        substitutions=74,
        deletions=8,
        insertions=8,
        entity=unavailable,
    )

    verdict = compare_transcript_evaluations(
        _policy(evaluator_identity), v2_evaluation=v2, v1_evaluation=v1
    )

    assert verdict.status is SuperiorityVerdictStatus.NOT_EVALUATED
    entity = next(
        item for item in verdict.metric_comparisons if item.metric_name == "entity_recall"
    )
    assert entity.status == "not_evaluated"
    assert "v2_metric_not_evaluated:entity_labels_not_exhaustive" in entity.reason_codes


def test_zero_or_incomparable_required_coverage_cannot_be_silently_skipped(
    evaluator_identity: LexicalEvaluatorIdentityV1,
) -> None:
    v2, v1 = _passing_pair(evaluator_identity)
    v2 = _evaluation(
        candidate_hash=_H["v2"],
        identity=evaluator_identity,
        substitutions=74,
        deletions=8,
        insertions=8,
        entity=MetricRate.of(5, 5),
    )
    denominator_mismatch = compare_transcript_evaluations(
        _policy(evaluator_identity), v2_evaluation=v2, v1_evaluation=v1
    )
    entity = next(
        item
        for item in denominator_mismatch.metric_comparisons
        if item.metric_name == "entity_recall"
    )
    assert denominator_mismatch.status is SuperiorityVerdictStatus.NOT_EVALUATED
    assert entity.reason_codes == ("metric_denominator_mismatch",)

    unevaluated_v2 = TranscriptEvaluationResult.build(
        status=TranscriptEvaluationStatus.NOT_EVALUATED,
        reason_codes=("human_gold_incomplete",),
        gold_suite_hash=_H["gold"],
        candidate_artifact_hash=_H["v2"],
        normalized_audio_hash=_H["audio"],
        annotation_packet_hash=_H["packet"],
        metrics=None,
        correction_metrics_status=TranscriptEvaluationStatus.NOT_EVALUATED,
        correction_metrics_reason_codes=("episode_transcript_not_evaluated",),
        clip_results=(),
    )
    missing_gold = compare_transcript_evaluations(
        _policy(evaluator_identity),
        v2_evaluation=unevaluated_v2,
        v1_evaluation=v1,
    )
    assert missing_gold.status is SuperiorityVerdictStatus.NOT_EVALUATED
    assert "v2_evaluation_not_evaluated" in missing_gold.reason_codes


def test_gold_or_evaluator_identity_mismatch_is_typed_not_evaluated(
    evaluator_identity: LexicalEvaluatorIdentityV1,
) -> None:
    policy = _policy(evaluator_identity)
    v2, v1 = _passing_pair(evaluator_identity)
    other_gold = _evaluation(
        candidate_hash=_H["v1"],
        identity=evaluator_identity,
        gold_hash=_H["gold_other"],
        substitutions=100,
        deletions=10,
        insertions=10,
        needs_review_precision=(8, 10),
        needs_review_recall=(9, 10),
    )
    gold_mismatch = compare_transcript_evaluations(
        policy, v2_evaluation=v2, v1_evaluation=other_gold
    )
    assert gold_mismatch.status is SuperiorityVerdictStatus.NOT_EVALUATED
    assert "gold_suite_hash_mismatch" in gold_mismatch.reason_codes

    identity_payload = evaluator_identity.model_dump(mode="python", exclude={"identity_hash"})
    identity_payload["python_version"] = "incomparable-test-runtime"
    other_identity = LexicalEvaluatorIdentityV1(
        **identity_payload,
        identity_hash=hash_object(
            {"artifact_kind": "lexical_evaluator_identity", **identity_payload}
        ),
    )
    other_evaluator = _evaluation(
        candidate_hash=_H["v1"],
        identity=other_identity,
        substitutions=100,
        deletions=10,
        insertions=10,
        needs_review_precision=(8, 10),
        needs_review_recall=(9, 10),
    )
    evaluator_mismatch = compare_transcript_evaluations(
        policy, v2_evaluation=v2, v1_evaluation=other_evaluator
    )
    assert evaluator_mismatch.status is SuperiorityVerdictStatus.NOT_EVALUATED
    assert "lexical_evaluator_identity_mismatch" in evaluator_mismatch.reason_codes


def test_candidate_roles_cannot_be_reversed(
    evaluator_identity: LexicalEvaluatorIdentityV1,
) -> None:
    v2, v1 = _passing_pair(evaluator_identity)
    with pytest.raises(ValueError, match="V2 candidate role binding mismatch"):
        compare_transcript_evaluations(
            _policy(evaluator_identity), v2_evaluation=v1, v1_evaluation=v2
        )


def test_verdict_canonical_hash_tamper_is_rejected(
    evaluator_identity: LexicalEvaluatorIdentityV1,
) -> None:
    policy = _policy(evaluator_identity)
    v2, v1 = _passing_pair(evaluator_identity)
    verdict = compare_transcript_evaluations(policy, v2_evaluation=v2, v1_evaluation=v1)
    payload = json.loads(verdict.canonical_bytes())
    payload["normalized_audio_hash"] = "9" * 64

    with pytest.raises(ValueError, match="invalid TranscriptSuperiorityVerdictV1 JSON"):
        load_transcript_superiority_verdict(canonical_json_bytes(payload))


def test_fresh_process_cli_replays_identical_policy_and_verdict(
    tmp_path: Path,
    evaluator_identity: LexicalEvaluatorIdentityV1,
) -> None:
    policy = _policy(evaluator_identity)
    v2, v1 = _passing_pair(evaluator_identity)
    policy_path = tmp_path / "policy.json"
    v2_path = tmp_path / "v2.json"
    v1_path = tmp_path / "v1.json"
    policy_path.write_bytes(policy.canonical_bytes())
    v2_path.write_bytes(v2.canonical_bytes())
    v1_path.write_bytes(v1.canonical_bytes())
    script = Path("scripts/podcast_subtitle_transcript_superiority.py").resolve()

    materialized = subprocess.run(
        [
            sys.executable,
            str(script),
            "materialize-policy",
            "--comparison-scope",
            "recognition_adapter_shadow_only",
            "--challenger-system-id",
            "qwen-primary",
            "--baseline-system-id",
            "legacy-v1-whisperx",
            "--v2-candidate-hash",
            _H["v2"],
            "--legacy-v1-candidate-hash",
            _H["v1"],
            "--normalized-audio-hash",
            _H["audio"],
            "--annotation-packet-hash",
            _H["packet"],
            "--lexical-evaluator-identity-hash",
            evaluator_identity.identity_hash,
        ],
        check=True,
        capture_output=True,
    ).stdout.rstrip(b"\n")
    assert materialized == policy.canonical_bytes()

    command = [
        sys.executable,
        str(script),
        "compare",
        "--policy",
        str(policy_path),
        "--expected-policy-hash",
        policy.policy_hash,
        "--v2-evaluation",
        str(v2_path),
        "--legacy-v1-evaluation",
        str(v1_path),
    ]
    first = subprocess.run(command, check=True, capture_output=True).stdout.rstrip(b"\n")
    second = subprocess.run(command, check=True, capture_output=True).stdout.rstrip(b"\n")
    assert first == second
    assert load_transcript_superiority_verdict(first).status is SuperiorityVerdictStatus.PASSED
