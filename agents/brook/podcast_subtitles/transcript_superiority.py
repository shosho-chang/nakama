"""Predeclared, replayable transcript superiority comparison.

The policy in this module is intentionally fixed before human Gold is opened.
It compares only content-quality metrics emitted by ``transcript_gold`` and
never reads candidate transcript text.  Candidate roles and episode lineage
are bound into the policy instance.  The resulting verdict binds both complete
evaluation artifacts, the shared Gold suite, and the exact lexical evaluator
identity.

Missing coverage or incomparable identities produce a typed
``not_evaluated`` verdict.  Candidate role reversal and canonical/hash tamper
are integrity errors and are rejected instead of being scored.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
import platform
import re
import sys
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import Any, ClassVar, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .hashing import canonical_json_bytes, hash_object, measure_regular_file
from .transcript_gold import (
    MetricNotEvaluated,
    MetricRate,
    TranscriptEvaluationResult,
    TranscriptEvaluationStatus,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

TranscriptSuperiorityMetricName = Literal[
    "lexical_character_error_rate",
    "lexical_deletion_edit_rate",
    "lexical_insertion_edit_rate",
    "entity_recall",
    "code_switch_recall",
    "numeric_recall",
    "critical_omission_rate",
    "needs_review_precision",
    "needs_review_recall",
]
TranscriptSuperiorityScope = Literal[
    "recognition_adapter_shadow_only",
    "full_v2_shadow_cutover_candidate",
]

_POLICY_ID = "podcast-transcript-v2-vs-legacy-v1-superiority-v1"
_REQUIRED_METRICS: tuple[TranscriptSuperiorityMetricName, ...] = (
    "lexical_character_error_rate",
    "lexical_deletion_edit_rate",
    "lexical_insertion_edit_rate",
    "entity_recall",
    "code_switch_recall",
    "numeric_recall",
    "critical_omission_rate",
    "needs_review_precision",
    "needs_review_recall",
)
_NO_REGRESSION_METRICS: tuple[TranscriptSuperiorityMetricName, ...] = (
    "lexical_deletion_edit_rate",
    "lexical_insertion_edit_rate",
    "entity_recall",
    "code_switch_recall",
    "numeric_recall",
    "critical_omission_rate",
    "needs_review_precision",
    "needs_review_recall",
)
_MATCHED_DENOMINATOR_METRICS = frozenset(
    {
        "lexical_character_error_rate",
        "lexical_deletion_edit_rate",
        "lexical_insertion_edit_rate",
        "entity_recall",
        "code_switch_recall",
        "numeric_recall",
        "critical_omission_rate",
        "needs_review_recall",
    }
)
_EXCLUDED_METRICS = (
    "lexical_character_accuracy:algebraically_redundant_with_lexical_character_error_rate",
    "word_token_accuracy:provider_token_boundaries_not_comparable",
    "correction_detection_recall:requires_separate_process_gold",
    "correction_apply_recall:requires_separate_process_gold",
    "false_keep_original_rate:requires_separate_process_gold",
    "harmful_apply_rate:requires_separate_process_gold",
    "source_precision:requires_separate_process_gold",
)
_RATIONALE = (
    "The primary lexical CER must improve materially, not merely differ by rounding noise.",
    "Deletion and insertion rates may not regress because lower aggregate CER can hide "
    "omissions or hallucinations.",
    "Entity, code-switch, numeric, and critical-omission labels require exhaustive Gold "
    "coverage and strict safety floors.",
    "NeedsReview precision and recall must not regress; blanket deferral and silent "
    "confidence are both rejected.",
    "Correction-process and boundary quality remain separate lineage-homogeneous human "
    "studies and cannot be inferred here.",
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_sha256(label: str, value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _artifact_hash(model: BaseModel, *, field: str, kind: str) -> str:
    return hash_object(
        {
            "artifact_kind": kind,
            **model.model_dump(mode="json", exclude={field}),
        }
    )


def _prospective_hash(payload: dict[str, Any], *, kind: str) -> str:
    return hash_object({"artifact_kind": kind, **payload})


class ExactRatioV1(_StrictFrozenModel):
    """A reduced, non-negative rational value with no float ambiguity."""

    numerator: int
    denominator: int

    @model_validator(mode="after")
    def _valid(self) -> ExactRatioV1:
        if self.numerator < 0 or self.denominator <= 0:
            raise ValueError("exact ratio must be non-negative with a positive denominator")
        if math.gcd(self.numerator, self.denominator) != 1:
            raise ValueError("exact ratio must be in reduced canonical form")
        return self

    def fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)


class TranscriptSuperiorityThresholdsV1(_StrictFrozenModel):
    minimum_relative_lexical_cer_reduction: ExactRatioV1
    minimum_absolute_lexical_cer_reduction: ExactRatioV1
    minimum_v2_entity_recall: ExactRatioV1
    minimum_v2_code_switch_recall: ExactRatioV1
    minimum_v2_numeric_recall: ExactRatioV1
    maximum_v2_critical_omission_rate: ExactRatioV1
    minimum_v2_needs_review_precision: ExactRatioV1
    minimum_v2_needs_review_recall: ExactRatioV1


_DEFAULT_THRESHOLDS = TranscriptSuperiorityThresholdsV1(
    minimum_relative_lexical_cer_reduction=ExactRatioV1(numerator=1, denominator=10),
    minimum_absolute_lexical_cer_reduction=ExactRatioV1(numerator=1, denominator=200),
    minimum_v2_entity_recall=ExactRatioV1(numerator=1, denominator=1),
    minimum_v2_code_switch_recall=ExactRatioV1(numerator=1, denominator=1),
    minimum_v2_numeric_recall=ExactRatioV1(numerator=1, denominator=1),
    maximum_v2_critical_omission_rate=ExactRatioV1(numerator=0, denominator=1),
    minimum_v2_needs_review_precision=ExactRatioV1(numerator=4, denominator=5),
    minimum_v2_needs_review_recall=ExactRatioV1(numerator=1, denominator=1),
)


class TranscriptSuperiorityEvaluatorIdentityV1(_StrictFrozenModel):
    """Exact local runtime/code identity for replaying the superiority decision."""

    schema_version: Literal[1] = 1
    implementation: Literal["nakama-transcript-superiority-v1"]
    implementation_version: Literal["1"]
    python_implementation: str
    python_version: str
    python_cache_tag: str
    pydantic_version: str
    evaluator_code_hash: str
    identity_hash: str

    @field_validator(
        "python_implementation",
        "python_version",
        "python_cache_tag",
        "pydantic_version",
    )
    @classmethod
    def _version(cls, value: str, info: Any) -> str:
        if not value:
            raise ValueError(f"{info.field_name} must be non-empty")
        return value

    @field_validator("evaluator_code_hash", "identity_hash")
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @model_validator(mode="after")
    def _closed(self) -> TranscriptSuperiorityEvaluatorIdentityV1:
        if self.identity_hash != _artifact_hash(
            self,
            field="identity_hash",
            kind="transcript_superiority_evaluator_identity",
        ):
            raise ValueError("transcript superiority evaluator identity_hash mismatch")
        return self


def measure_transcript_superiority_evaluator_identity(
) -> TranscriptSuperiorityEvaluatorIdentityV1:
    evaluator_code_hash, _ = measure_regular_file(__file__)
    payload = {
        "schema_version": 1,
        "implementation": "nakama-transcript-superiority-v1",
        "implementation_version": "1",
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_cache_tag": sys.implementation.cache_tag,
        "pydantic_version": importlib.metadata.version("pydantic"),
        "evaluator_code_hash": evaluator_code_hash,
    }
    return TranscriptSuperiorityEvaluatorIdentityV1(
        **payload,
        identity_hash=_prospective_hash(
            payload,
            kind="transcript_superiority_evaluator_identity",
        ),
    )


class TranscriptSuperiorityPolicyV1(_StrictFrozenModel):
    """Fixed thresholds plus exact pre-Gold candidate and lineage bindings."""

    _HASH_KIND: ClassVar[str] = "transcript_superiority_policy"

    schema_version: Literal[1] = 1
    policy_id: Literal["podcast-transcript-v2-vs-legacy-v1-superiority-v1"]
    comparison_scope: TranscriptSuperiorityScope
    challenger_system_id: str
    baseline_system_id: str
    challenger_role: Literal["prebound_challenger"]
    baseline_role: Literal["legacy_v1"]
    v2_candidate_artifact_hash: str
    legacy_v1_candidate_artifact_hash: str
    normalized_audio_hash: str
    annotation_packet_hash: str
    lexical_evaluator_identity_hash: str
    superiority_evaluator_identity: TranscriptSuperiorityEvaluatorIdentityV1
    gold_binding_rule: Literal["same_exact_gold_suite_hash"]
    evaluator_binding_rule: Literal["same_exact_predeclared_lexical_evaluator_identity"]
    claim_limit: Literal[
        "adapter_diagnosis_only_not_full_v2_or_release_superiority",
        "shadow_candidate_only_not_cutover_authorization",
    ]
    post_gold_selection_rule: Literal[
        "forbidden_each_prebound_policy_must_be_reported_independently"
    ]
    decision_rule: Literal["all_required_checks_must_pass"]
    required_metrics: tuple[TranscriptSuperiorityMetricName, ...]
    require_no_regression_metrics: tuple[TranscriptSuperiorityMetricName, ...]
    excluded_metrics: tuple[str, ...]
    thresholds: TranscriptSuperiorityThresholdsV1
    rationale: tuple[str, ...]
    policy_hash: str

    @field_validator(
        "v2_candidate_artifact_hash",
        "legacy_v1_candidate_artifact_hash",
        "normalized_audio_hash",
        "annotation_packet_hash",
        "lexical_evaluator_identity_hash",
        "policy_hash",
    )
    @classmethod
    def _hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(info.field_name, value)

    @field_validator("challenger_system_id", "baseline_system_id")
    @classmethod
    def _system_id(cls, value: str, info: Any) -> str:
        if not value:
            raise ValueError(f"{info.field_name} must be non-empty")
        return value

    @model_validator(mode="after")
    def _closed(self) -> TranscriptSuperiorityPolicyV1:
        if self.v2_candidate_artifact_hash == self.legacy_v1_candidate_artifact_hash:
            raise ValueError("V2 and legacy V1 candidates must have distinct identities")
        expected_claim = (
            "adapter_diagnosis_only_not_full_v2_or_release_superiority"
            if self.comparison_scope == "recognition_adapter_shadow_only"
            else "shadow_candidate_only_not_cutover_authorization"
        )
        if self.claim_limit != expected_claim:
            raise ValueError("policy claim_limit drifts from its declared comparison scope")
        if self.required_metrics != _REQUIRED_METRICS:
            raise ValueError("policy required_metrics differ from the fixed V1 declaration")
        if self.require_no_regression_metrics != _NO_REGRESSION_METRICS:
            raise ValueError("policy no-regression metrics differ from the fixed V1 declaration")
        if self.excluded_metrics != _EXCLUDED_METRICS:
            raise ValueError("policy excluded_metrics differ from the fixed V1 declaration")
        if self.thresholds != _DEFAULT_THRESHOLDS:
            raise ValueError("policy thresholds differ from the fixed V1 declaration")
        if self.rationale != _RATIONALE:
            raise ValueError("policy rationale differs from the fixed V1 declaration")
        if self.policy_hash != _artifact_hash(
            self, field="policy_hash", kind=self._HASH_KIND
        ):
            raise ValueError("transcript superiority policy_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class MetricObservationV1(_StrictFrozenModel):
    status: Literal["evaluated", "not_evaluated"]
    numerator: int | None
    denominator: int | None
    reason_codes: tuple[str, ...]

    @field_validator("reason_codes")
    @classmethod
    def _reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(values))) != values:
            raise ValueError("metric observation reason_codes must be unique and sorted")
        if any(not value for value in values):
            raise ValueError("metric observation reason_codes must be non-empty strings")
        return values

    @model_validator(mode="after")
    def _valid(self) -> MetricObservationV1:
        if self.status == "evaluated":
            if (
                self.numerator is None
                or self.denominator is None
                or self.numerator < 0
                or self.denominator <= 0
                or self.reason_codes
            ):
                raise ValueError("evaluated metric observation requires a positive denominator")
        elif (
            self.numerator is not None
            or self.denominator is not None
            or not self.reason_codes
        ):
            raise ValueError("not_evaluated metric observation requires reasons and no score")
        return self

    def fraction(self) -> Fraction:
        if self.status != "evaluated" or self.numerator is None or self.denominator is None:
            raise ValueError("not_evaluated metric observation has no fraction")
        return Fraction(self.numerator, self.denominator)


class MetricCheckV1(_StrictFrozenModel):
    check_id: str
    passed: bool

    @field_validator("check_id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not value:
            raise ValueError("metric check_id must be non-empty")
        return value


class MetricComparisonV1(_StrictFrozenModel):
    metric_name: TranscriptSuperiorityMetricName
    status: Literal["evaluated", "not_evaluated"]
    v2_observation: MetricObservationV1
    legacy_v1_observation: MetricObservationV1
    checks: tuple[MetricCheckV1, ...]
    reason_codes: tuple[str, ...]

    @field_validator("reason_codes")
    @classmethod
    def _reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(values))) != values:
            raise ValueError("metric comparison reason_codes must be unique and sorted")
        if any(not value for value in values):
            raise ValueError("metric comparison reason_codes must be non-empty strings")
        return values

    @model_validator(mode="after")
    def _valid(self) -> MetricComparisonV1:
        if self.status == "evaluated":
            if (
                self.v2_observation.status != "evaluated"
                or self.legacy_v1_observation.status != "evaluated"
                or not self.checks
            ):
                raise ValueError("evaluated comparison requires both observations and checks")
            expected_reasons = tuple(
                sorted(f"failed_check:{item.check_id}" for item in self.checks if not item.passed)
            )
            if self.reason_codes != expected_reasons:
                raise ValueError("evaluated comparison reasons drift from failed checks")
        elif self.checks or not self.reason_codes:
            raise ValueError("not_evaluated comparison requires reasons and no checks")
        return self


class SuperiorityVerdictStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_EVALUATED = "not_evaluated"


class TranscriptSuperiorityVerdictV1(_StrictFrozenModel):
    """Content-addressed comparison bound to every source evaluation identity."""

    _HASH_KIND: ClassVar[str] = "transcript_superiority_verdict"

    schema_version: Literal[1] = 1
    status: SuperiorityVerdictStatus
    reason_codes: tuple[str, ...]
    policy_hash: str
    v2_evaluation_hash: str
    legacy_v1_evaluation_hash: str
    v2_candidate_artifact_hash: str
    legacy_v1_candidate_artifact_hash: str
    v2_gold_suite_hash: str
    legacy_v1_gold_suite_hash: str
    gold_suite_hash: str | None
    normalized_audio_hash: str
    annotation_packet_hash: str
    expected_lexical_evaluator_identity_hash: str
    superiority_evaluator_identity_hash: str
    v2_lexical_evaluator_identity_hash: str | None
    legacy_v1_lexical_evaluator_identity_hash: str | None
    lexical_evaluator_identity_hash: str | None
    metric_comparisons: tuple[MetricComparisonV1, ...]
    verdict_hash: str

    @field_validator(
        "policy_hash",
        "v2_evaluation_hash",
        "legacy_v1_evaluation_hash",
        "v2_candidate_artifact_hash",
        "legacy_v1_candidate_artifact_hash",
        "v2_gold_suite_hash",
        "legacy_v1_gold_suite_hash",
        "gold_suite_hash",
        "normalized_audio_hash",
        "annotation_packet_hash",
        "expected_lexical_evaluator_identity_hash",
        "superiority_evaluator_identity_hash",
        "v2_lexical_evaluator_identity_hash",
        "legacy_v1_lexical_evaluator_identity_hash",
        "lexical_evaluator_identity_hash",
        "verdict_hash",
    )
    @classmethod
    def _hashes(cls, value: str | None, info: Any) -> str | None:
        if value is not None:
            return _require_sha256(info.field_name, value)
        return value

    @field_validator("reason_codes")
    @classmethod
    def _reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(values))) != values:
            raise ValueError("verdict reason_codes must be unique and sorted")
        if any(not value for value in values):
            raise ValueError("verdict reason_codes must be non-empty strings")
        return values

    @model_validator(mode="after")
    def _valid(self) -> TranscriptSuperiorityVerdictV1:
        if tuple(item.metric_name for item in self.metric_comparisons) != _REQUIRED_METRICS:
            raise ValueError("verdict metric comparisons differ from fixed policy order")
        any_unavailable = any(
            item.status == "not_evaluated" for item in self.metric_comparisons
        )
        any_failed = any(
            not check.passed for item in self.metric_comparisons for check in item.checks
        )
        if self.status is SuperiorityVerdictStatus.PASSED:
            if self.reason_codes or any_unavailable or any_failed:
                raise ValueError("passed verdict requires every fixed comparison to pass")
        elif self.status is SuperiorityVerdictStatus.FAILED:
            if not self.reason_codes or any_unavailable or not any_failed:
                raise ValueError("failed verdict requires complete metrics and a failed check")
        elif not self.reason_codes or not any_unavailable:
            raise ValueError("not_evaluated verdict requires an unavailable comparison")
        if self.gold_suite_hash is not None and (
            self.gold_suite_hash != self.v2_gold_suite_hash
            or self.gold_suite_hash != self.legacy_v1_gold_suite_hash
        ):
            raise ValueError("shared gold_suite_hash does not match source evaluations")
        if (
            self.v2_gold_suite_hash == self.legacy_v1_gold_suite_hash
        ) != (self.gold_suite_hash is not None):
            raise ValueError("shared gold_suite_hash availability drifts from source bindings")
        evaluator_bindings_match = (
            self.v2_lexical_evaluator_identity_hash
            == self.legacy_v1_lexical_evaluator_identity_hash
            == self.expected_lexical_evaluator_identity_hash
        )
        if evaluator_bindings_match != (self.lexical_evaluator_identity_hash is not None):
            raise ValueError("shared evaluator identity availability drifts from source bindings")
        if (
            self.lexical_evaluator_identity_hash is not None
            and self.lexical_evaluator_identity_hash
            != self.expected_lexical_evaluator_identity_hash
        ):
            raise ValueError("shared evaluator identity is not bound to source identities")
        if self.verdict_hash != _artifact_hash(
            self, field="verdict_hash", kind=self._HASH_KIND
        ):
            raise ValueError("transcript superiority verdict_hash mismatch")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


def build_default_transcript_superiority_policy(
    *,
    comparison_scope: TranscriptSuperiorityScope,
    challenger_system_id: str,
    baseline_system_id: str,
    v2_candidate_artifact_hash: str,
    legacy_v1_candidate_artifact_hash: str,
    normalized_audio_hash: str,
    annotation_packet_hash: str,
    lexical_evaluator_identity_hash: str,
) -> TranscriptSuperiorityPolicyV1:
    """Materialize the one fixed V1 policy around pre-Gold identity bindings."""

    superiority_evaluator_identity = measure_transcript_superiority_evaluator_identity()
    payload = {
        "schema_version": 1,
        "policy_id": _POLICY_ID,
        "comparison_scope": comparison_scope,
        "challenger_system_id": challenger_system_id,
        "baseline_system_id": baseline_system_id,
        "challenger_role": "prebound_challenger",
        "baseline_role": "legacy_v1",
        "v2_candidate_artifact_hash": v2_candidate_artifact_hash,
        "legacy_v1_candidate_artifact_hash": legacy_v1_candidate_artifact_hash,
        "normalized_audio_hash": normalized_audio_hash,
        "annotation_packet_hash": annotation_packet_hash,
        "lexical_evaluator_identity_hash": lexical_evaluator_identity_hash,
        "superiority_evaluator_identity": superiority_evaluator_identity,
        "gold_binding_rule": "same_exact_gold_suite_hash",
        "evaluator_binding_rule": "same_exact_predeclared_lexical_evaluator_identity",
        "claim_limit": (
            "adapter_diagnosis_only_not_full_v2_or_release_superiority"
            if comparison_scope == "recognition_adapter_shadow_only"
            else "shadow_candidate_only_not_cutover_authorization"
        ),
        "post_gold_selection_rule": (
            "forbidden_each_prebound_policy_must_be_reported_independently"
        ),
        "decision_rule": "all_required_checks_must_pass",
        "required_metrics": _REQUIRED_METRICS,
        "require_no_regression_metrics": _NO_REGRESSION_METRICS,
        "excluded_metrics": _EXCLUDED_METRICS,
        "thresholds": _DEFAULT_THRESHOLDS,
        "rationale": _RATIONALE,
    }
    return TranscriptSuperiorityPolicyV1(
        **payload,
        policy_hash=_prospective_hash(payload, kind=TranscriptSuperiorityPolicyV1._HASH_KIND),
    )


def _observation(
    value: MetricRate | MetricNotEvaluated | None,
    *,
    role: str,
) -> MetricObservationV1:
    if value is None:
        return MetricObservationV1(
            status="not_evaluated",
            numerator=None,
            denominator=None,
            reason_codes=(f"{role}_evaluation_metrics_unavailable",),
        )
    if isinstance(value, MetricNotEvaluated):
        return MetricObservationV1(
            status="not_evaluated",
            numerator=None,
            denominator=None,
            reason_codes=tuple(
                sorted(f"{role}_metric_not_evaluated:{reason}" for reason in value.reason_codes)
            ),
        )
    if value.denominator == 0 or value.value is None:
        return MetricObservationV1(
            status="not_evaluated",
            numerator=None,
            denominator=None,
            reason_codes=(f"{role}_metric_zero_denominator",),
        )
    return MetricObservationV1(
        status="evaluated",
        numerator=value.numerator,
        denominator=value.denominator,
        reason_codes=(),
    )


def _metric_value(
    evaluation: TranscriptEvaluationResult,
    metric_name: TranscriptSuperiorityMetricName,
) -> MetricRate | MetricNotEvaluated | None:
    if evaluation.metrics is None:
        return None
    value = getattr(evaluation.metrics, metric_name)
    if not isinstance(value, (MetricRate, MetricNotEvaluated)):
        raise ValueError(f"unexpected transcript metric type for {metric_name}")
    return value


def _checks_for_metric(
    metric_name: TranscriptSuperiorityMetricName,
    *,
    v2: Fraction,
    v1: Fraction,
    thresholds: TranscriptSuperiorityThresholdsV1,
) -> tuple[MetricCheckV1, ...]:
    if metric_name == "lexical_character_error_rate":
        absolute_reduction = v1 - v2
        relative_reduction = None if v1 == 0 else absolute_reduction / v1
        return (
            MetricCheckV1(check_id="v2_lexical_cer_strictly_lower", passed=v2 < v1),
            MetricCheckV1(
                check_id="minimum_absolute_lexical_cer_reduction",
                passed=absolute_reduction
                >= thresholds.minimum_absolute_lexical_cer_reduction.fraction(),
            ),
            MetricCheckV1(
                check_id="minimum_relative_lexical_cer_reduction",
                passed=relative_reduction is not None
                and relative_reduction
                >= thresholds.minimum_relative_lexical_cer_reduction.fraction(),
            ),
        )
    if metric_name == "lexical_deletion_edit_rate":
        return (
            MetricCheckV1(check_id="lexical_deletion_no_regression", passed=v2 <= v1),
        )
    if metric_name == "lexical_insertion_edit_rate":
        return (
            MetricCheckV1(check_id="lexical_insertion_no_regression", passed=v2 <= v1),
        )

    floors = {
        "entity_recall": (
            "minimum_v2_entity_recall",
            thresholds.minimum_v2_entity_recall.fraction(),
        ),
        "code_switch_recall": (
            "minimum_v2_code_switch_recall",
            thresholds.minimum_v2_code_switch_recall.fraction(),
        ),
        "numeric_recall": (
            "minimum_v2_numeric_recall",
            thresholds.minimum_v2_numeric_recall.fraction(),
        ),
        "needs_review_precision": (
            "minimum_v2_needs_review_precision",
            thresholds.minimum_v2_needs_review_precision.fraction(),
        ),
        "needs_review_recall": (
            "minimum_v2_needs_review_recall",
            thresholds.minimum_v2_needs_review_recall.fraction(),
        ),
    }
    if metric_name in floors:
        check_id, floor = floors[metric_name]
        return (
            MetricCheckV1(check_id=f"{metric_name}_no_regression", passed=v2 >= v1),
            MetricCheckV1(check_id=check_id, passed=v2 >= floor),
        )
    if metric_name == "critical_omission_rate":
        return (
            MetricCheckV1(check_id="critical_omission_no_regression", passed=v2 <= v1),
            MetricCheckV1(
                check_id="maximum_v2_critical_omission_rate",
                passed=v2 <= thresholds.maximum_v2_critical_omission_rate.fraction(),
            ),
        )
    raise ValueError(f"unsupported fixed transcript superiority metric: {metric_name}")


def _identity_hash(evaluation: TranscriptEvaluationResult) -> str | None:
    if evaluation.metrics is None:
        return None
    return evaluation.metrics.lexical_evaluator_identity.identity_hash


def compare_transcript_evaluations(
    policy: TranscriptSuperiorityPolicyV1,
    *,
    v2_evaluation: TranscriptEvaluationResult,
    v1_evaluation: TranscriptEvaluationResult,
) -> TranscriptSuperiorityVerdictV1:
    """Compare exact evaluation artifacts without inspecting candidate text."""

    verify_transcript_superiority_policy(policy, policy.policy_hash)
    v2_evaluation = TranscriptEvaluationResult.model_validate(
        v2_evaluation.model_dump(mode="python")
    )
    v1_evaluation = TranscriptEvaluationResult.model_validate(
        v1_evaluation.model_dump(mode="python")
    )
    if v2_evaluation.candidate_artifact_hash != policy.v2_candidate_artifact_hash:
        raise ValueError("V2 candidate role binding mismatch")
    if v1_evaluation.candidate_artifact_hash != policy.legacy_v1_candidate_artifact_hash:
        raise ValueError("legacy V1 candidate role binding mismatch")

    global_reasons: set[str] = set()
    if v2_evaluation.status is not TranscriptEvaluationStatus.EVALUATED:
        global_reasons.add("v2_evaluation_not_evaluated")
        global_reasons.update(
            f"v2_evaluation_not_evaluated:{reason}" for reason in v2_evaluation.reason_codes
        )
    if v1_evaluation.status is not TranscriptEvaluationStatus.EVALUATED:
        global_reasons.add("legacy_v1_evaluation_not_evaluated")
        global_reasons.update(
            f"legacy_v1_evaluation_not_evaluated:{reason}"
            for reason in v1_evaluation.reason_codes
        )
    for role, evaluation in (("v2", v2_evaluation), ("legacy_v1", v1_evaluation)):
        if evaluation.normalized_audio_hash != policy.normalized_audio_hash:
            global_reasons.add(f"{role}_normalized_audio_hash_mismatch")
        if evaluation.annotation_packet_hash != policy.annotation_packet_hash:
            global_reasons.add(f"{role}_annotation_packet_hash_mismatch")
    if v2_evaluation.gold_suite_hash != v1_evaluation.gold_suite_hash:
        global_reasons.add("gold_suite_hash_mismatch")
    if tuple(item.clip_id for item in v2_evaluation.clip_results) != tuple(
        item.clip_id for item in v1_evaluation.clip_results
    ):
        global_reasons.add("evaluated_clip_identity_mismatch")
    if (
        v2_evaluation.metrics is not None
        and v1_evaluation.metrics is not None
        and v2_evaluation.metrics.scored_text_clip_count
        != v1_evaluation.metrics.scored_text_clip_count
    ):
        global_reasons.add("scored_text_clip_count_mismatch")

    v2_identity = _identity_hash(v2_evaluation)
    v1_identity = _identity_hash(v1_evaluation)
    if v2_identity is None:
        global_reasons.add("v2_lexical_evaluator_identity_unavailable")
    elif v2_identity != policy.lexical_evaluator_identity_hash:
        global_reasons.add("v2_lexical_evaluator_identity_not_predeclared")
    if v1_identity is None:
        global_reasons.add("legacy_v1_lexical_evaluator_identity_unavailable")
    elif v1_identity != policy.lexical_evaluator_identity_hash:
        global_reasons.add("legacy_v1_lexical_evaluator_identity_not_predeclared")
    if v2_identity is not None and v1_identity is not None and v2_identity != v1_identity:
        global_reasons.add("lexical_evaluator_identity_mismatch")

    comparisons: list[MetricComparisonV1] = []
    for metric_name in policy.required_metrics:
        v2_observation = _observation(_metric_value(v2_evaluation, metric_name), role="v2")
        v1_observation = _observation(
            _metric_value(v1_evaluation, metric_name), role="legacy_v1"
        )
        reasons = set(global_reasons)
        reasons.update(v2_observation.reason_codes)
        reasons.update(v1_observation.reason_codes)
        if (
            not reasons
            and metric_name in _MATCHED_DENOMINATOR_METRICS
            and v2_observation.denominator != v1_observation.denominator
        ):
            reasons.add("metric_denominator_mismatch")
        if reasons:
            comparisons.append(
                MetricComparisonV1(
                    metric_name=metric_name,
                    status="not_evaluated",
                    v2_observation=v2_observation,
                    legacy_v1_observation=v1_observation,
                    checks=(),
                    reason_codes=tuple(sorted(reasons)),
                )
            )
            continue
        checks = _checks_for_metric(
            metric_name,
            v2=v2_observation.fraction(),
            v1=v1_observation.fraction(),
            thresholds=policy.thresholds,
        )
        comparisons.append(
            MetricComparisonV1(
                metric_name=metric_name,
                status="evaluated",
                v2_observation=v2_observation,
                legacy_v1_observation=v1_observation,
                checks=checks,
                reason_codes=tuple(
                    sorted(f"failed_check:{check.check_id}" for check in checks if not check.passed)
                ),
            )
        )

    not_evaluated_reasons = {
        f"metric_not_evaluated:{item.metric_name}:{reason}"
        for item in comparisons
        if item.status == "not_evaluated"
        for reason in item.reason_codes
    }
    failed_reasons = {
        f"metric_failed:{item.metric_name}:{check.check_id}"
        for item in comparisons
        for check in item.checks
        if not check.passed
    }
    if not_evaluated_reasons:
        status = SuperiorityVerdictStatus.NOT_EVALUATED
        reason_codes = tuple(sorted(global_reasons | not_evaluated_reasons))
    elif failed_reasons:
        status = SuperiorityVerdictStatus.FAILED
        reason_codes = tuple(sorted(failed_reasons))
    else:
        status = SuperiorityVerdictStatus.PASSED
        reason_codes = ()

    shared_gold = (
        v2_evaluation.gold_suite_hash
        if v2_evaluation.gold_suite_hash == v1_evaluation.gold_suite_hash
        else None
    )
    shared_evaluator = (
        v2_identity
        if v2_identity is not None
        and v2_identity == v1_identity == policy.lexical_evaluator_identity_hash
        else None
    )
    payload = {
        "schema_version": 1,
        "status": status,
        "reason_codes": reason_codes,
        "policy_hash": policy.policy_hash,
        "v2_evaluation_hash": v2_evaluation.evaluation_hash,
        "legacy_v1_evaluation_hash": v1_evaluation.evaluation_hash,
        "v2_candidate_artifact_hash": v2_evaluation.candidate_artifact_hash,
        "legacy_v1_candidate_artifact_hash": v1_evaluation.candidate_artifact_hash,
        "v2_gold_suite_hash": v2_evaluation.gold_suite_hash,
        "legacy_v1_gold_suite_hash": v1_evaluation.gold_suite_hash,
        "gold_suite_hash": shared_gold,
        "normalized_audio_hash": policy.normalized_audio_hash,
        "annotation_packet_hash": policy.annotation_packet_hash,
        "expected_lexical_evaluator_identity_hash": policy.lexical_evaluator_identity_hash,
        "superiority_evaluator_identity_hash": (
            policy.superiority_evaluator_identity.identity_hash
        ),
        "v2_lexical_evaluator_identity_hash": v2_identity,
        "legacy_v1_lexical_evaluator_identity_hash": v1_identity,
        "lexical_evaluator_identity_hash": shared_evaluator,
        "metric_comparisons": tuple(comparisons),
    }
    return TranscriptSuperiorityVerdictV1(
        **payload,
        verdict_hash=_prospective_hash(
            payload, kind=TranscriptSuperiorityVerdictV1._HASH_KIND
        ),
    )


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _load_exact_canonical(source: bytes | bytearray | Path, model: type[_ModelT]) -> _ModelT:
    raw = Path(source).read_bytes() if isinstance(source, Path) else bytes(source)
    try:
        untyped = json.loads(raw)
        if not isinstance(untyped, dict):
            raise ValueError("top-level artifact must be an object")
        expected_fields = set(model.model_fields)
        if set(untyped) != expected_fields:
            missing = sorted(expected_fields - set(untyped))
            unknown = sorted(set(untyped) - expected_fields)
            raise ValueError(
                f"top-level fields are not exact; missing={missing}, unknown={unknown}"
            )
        value = model.model_validate_json(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid {model.__name__} JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise ValueError(f"{model.__name__} bytes are not exact canonical JSON")
    return value


def load_transcript_superiority_policy(
    source: bytes | bytearray | Path,
) -> TranscriptSuperiorityPolicyV1:
    return _load_exact_canonical(source, TranscriptSuperiorityPolicyV1)


def load_transcript_superiority_verdict(
    source: bytes | bytearray | Path,
) -> TranscriptSuperiorityVerdictV1:
    return _load_exact_canonical(source, TranscriptSuperiorityVerdictV1)


def verify_transcript_superiority_policy(
    policy: TranscriptSuperiorityPolicyV1,
    expected_policy_hash: str,
) -> TranscriptSuperiorityPolicyV1:
    _require_sha256("expected_policy_hash", expected_policy_hash)
    if policy.policy_hash != expected_policy_hash:
        raise ValueError("transcript superiority expected policy hash mismatch")
    TranscriptSuperiorityPolicyV1.model_validate(policy.model_dump(mode="python"))
    current_identity = measure_transcript_superiority_evaluator_identity()
    if canonical_json_bytes(policy.superiority_evaluator_identity) != canonical_json_bytes(
        current_identity
    ):
        raise ValueError("transcript superiority evaluator identity drift")
    return policy


def verify_transcript_superiority_verdict(
    verdict: TranscriptSuperiorityVerdictV1,
    *,
    policy: TranscriptSuperiorityPolicyV1,
    v2_evaluation: TranscriptEvaluationResult,
    v1_evaluation: TranscriptEvaluationResult,
    expected_verdict_hash: str,
) -> TranscriptSuperiorityVerdictV1:
    _require_sha256("expected_verdict_hash", expected_verdict_hash)
    if verdict.verdict_hash != expected_verdict_hash:
        raise ValueError("transcript superiority expected verdict hash mismatch")
    replayed = compare_transcript_evaluations(
        policy,
        v2_evaluation=v2_evaluation,
        v1_evaluation=v1_evaluation,
    )
    if verdict.canonical_bytes() != replayed.canonical_bytes():
        raise ValueError("transcript superiority verdict replay mismatch")
    return verdict


__all__ = [
    "ExactRatioV1",
    "MetricCheckV1",
    "MetricComparisonV1",
    "MetricObservationV1",
    "SuperiorityVerdictStatus",
    "TranscriptSuperiorityMetricName",
    "TranscriptSuperiorityEvaluatorIdentityV1",
    "TranscriptSuperiorityPolicyV1",
    "TranscriptSuperiorityScope",
    "TranscriptSuperiorityThresholdsV1",
    "TranscriptSuperiorityVerdictV1",
    "build_default_transcript_superiority_policy",
    "compare_transcript_evaluations",
    "load_transcript_superiority_policy",
    "load_transcript_superiority_verdict",
    "measure_transcript_superiority_evaluator_identity",
    "verify_transcript_superiority_policy",
    "verify_transcript_superiority_verdict",
]
