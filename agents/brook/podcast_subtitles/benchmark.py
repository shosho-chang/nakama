"""Deterministic golden-corpus comparison for Podcast Subtitle V2.

This module deliberately separates three kinds of evidence:

* adjudicated text and boundary gold, which can be scored exactly;
* safety cases whose only correct outcome is ``needs_review``; and
* release metrics that require a complete blinded human evaluation; and
* proposal/decision process metrics bound to immutable Generation artifacts.

An absent or incomplete blind study is reported as ``not_evaluated``.  It is
never inferred from regression fixtures and never treated as a passing gate.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from .hashing import hash_object


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_EVALUATED = "not_evaluated"


class ObservationOutcome(str, Enum):
    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


def _coerce_outcome(value: ObservationOutcome | str) -> ObservationOutcome:
    if isinstance(value, ObservationOutcome):
        return value
    try:
        return ObservationOutcome(value)
    except ValueError as exc:
        raise ValueError(f"unsupported observation outcome: {value!r}") from exc


@dataclass(frozen=True)
class TextObservation:
    """Candidate canonical text for one decision-target span."""

    case_id: str
    text: str | None
    outcome: ObservationOutcome | str = ObservationOutcome.ACCEPTED

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("text observation case_id must be non-empty")
        object.__setattr__(self, "outcome", _coerce_outcome(self.outcome))
        if self.outcome is ObservationOutcome.ACCEPTED and self.text is None:
            raise ValueError("accepted text observation must include text")


@dataclass(frozen=True)
class BoundaryObservation:
    """Internal token boundaries selected for one gold phrase.

    A boundary position ``n`` means a cue/line break between lexemes ``n-1``
    and ``n``.  Positions zero and ``len(lexemes)`` are external phrase edges,
    not internal boundaries, and therefore invalid observations.
    """

    case_id: str
    break_positions: tuple[int, ...]
    outcome: ObservationOutcome | str = ObservationOutcome.ACCEPTED

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("boundary observation case_id must be non-empty")
        positions = tuple(self.break_positions)
        if any(
            isinstance(position, bool) or not isinstance(position, int) for position in positions
        ):
            raise TypeError("boundary positions must be integers")
        if tuple(sorted(set(positions))) != positions:
            raise ValueError("boundary positions must be sorted and unique")
        object.__setattr__(self, "break_positions", positions)
        object.__setattr__(self, "outcome", _coerce_outcome(self.outcome))


@dataclass(frozen=True)
class ReviewObservation:
    """Resolution state for an ambiguity whose gold truth is intentionally open."""

    case_id: str
    outcome: ObservationOutcome | str

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("review observation case_id must be non-empty")
        object.__setattr__(self, "outcome", _coerce_outcome(self.outcome))


@dataclass(frozen=True)
class BenchmarkCandidate:
    candidate_id: str
    generation_id: str
    canonical_content_hash: str
    normalized_audio_hash: str
    artifact_hash: str
    text_observations: tuple[TextObservation, ...] = ()
    boundary_observations: tuple[BoundaryObservation, ...] = ()
    review_observations: tuple[ReviewObservation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise ValueError("candidate_id must be non-empty")
        if not isinstance(self.generation_id, str) or not self.generation_id:
            raise ValueError("generation_id must be non-empty")
        for label, value in (
            ("canonical_content_hash", self.canonical_content_hash),
            ("normalized_audio_hash", self.normalized_audio_hash),
            ("artifact_hash", self.artifact_hash),
        ):
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        object.__setattr__(self, "text_observations", tuple(self.text_observations))
        object.__setattr__(self, "boundary_observations", tuple(self.boundary_observations))
        object.__setattr__(self, "review_observations", tuple(self.review_observations))
        _index_unique(self.text_observations, label="text observations")
        _index_unique(self.boundary_observations, label="boundary observations")
        _index_unique(self.review_observations, label="review observations")


ProcessExpectedAction = Literal["accept_correction", "keep_original"]
ProcessAdjudicationStatus = Literal["in_progress", "completed"]
ProcessArtifactRole = Literal["proposal_set", "candidate_discovery", "ledger_prefix"]


@dataclass(frozen=True)
class ProcessGoldSource:
    """One immutable source used by humans to adjudicate process gold."""

    artifact_id: str
    role: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, str) or not self.artifact_id:
            raise ValueError("process gold source artifact_id must be non-empty")
        if not isinstance(self.role, str) or not self.role:
            raise ValueError("process gold source role must be non-empty")
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("process gold source sha256 must be lowercase SHA-256")

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "role": self.role,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ProcessAdjudication:
    """Human-labelling protocol metadata; never inferred from candidate output."""

    protocol_id: str
    adjudicator_ids: tuple[str, ...]
    status: ProcessAdjudicationStatus
    blinded_to_candidate: bool

    def __post_init__(self) -> None:
        if not isinstance(self.protocol_id, str) or not self.protocol_id:
            raise ValueError("process adjudication protocol_id must be non-empty")
        object.__setattr__(self, "adjudicator_ids", tuple(self.adjudicator_ids))
        if not self.adjudicator_ids or any(
            not isinstance(item, str) or not item for item in self.adjudicator_ids
        ):
            raise ValueError("process adjudication requires non-empty adjudicator_ids")
        if len(set(self.adjudicator_ids)) != len(self.adjudicator_ids):
            raise ValueError("process adjudicator_ids must be unique")
        if self.status not in {"in_progress", "completed"}:
            raise ValueError("unsupported process adjudication status")
        if not isinstance(self.blinded_to_candidate, bool):
            raise TypeError("process adjudication blinded_to_candidate must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "adjudicator_ids": list(self.adjudicator_ids),
            "status": self.status,
            "blinded_to_candidate": self.blinded_to_candidate,
        }


@dataclass(frozen=True)
class ProcessGoldCase:
    """One stable correction target with an independently adjudicated action."""

    case_id: str
    lineage_id: str
    normalized_audio_hash: str
    audio_span_ids: tuple[str, ...]
    evidence_token_ids: tuple[str, ...]
    start_ms: int
    end_ms: int
    observed_text: str
    expected_action: ProcessExpectedAction
    expected_replacement: str | None
    source_artifact_ids: tuple[str, ...]
    adjudication_locator: str

    def __post_init__(self) -> None:
        for label, value in (
            ("case_id", self.case_id),
            ("lineage_id", self.lineage_id),
            ("observed_text", self.observed_text),
            ("adjudication_locator", self.adjudication_locator),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"process gold {label} must be non-empty")
        if not isinstance(self.normalized_audio_hash, str) or not _SHA256_RE.fullmatch(
            self.normalized_audio_hash
        ):
            raise ValueError("process gold normalized_audio_hash must be lowercase SHA-256")
        for label in ("audio_span_ids", "evidence_token_ids", "source_artifact_ids"):
            values = tuple(getattr(self, label))
            object.__setattr__(self, label, values)
            if not values or any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"process gold {label} must contain non-empty IDs")
            if len(set(values)) != len(values):
                raise ValueError(f"process gold {label} must be unique")
        if (
            isinstance(self.start_ms, bool)
            or not isinstance(self.start_ms, int)
            or isinstance(self.end_ms, bool)
            or not isinstance(self.end_ms, int)
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
        ):
            raise ValueError("process gold case has invalid time bounds")
        if self.expected_action == "accept_correction":
            if (
                not isinstance(self.expected_replacement, str)
                or not self.expected_replacement
                or self.expected_replacement == self.observed_text
            ):
                raise ValueError(
                    "accept_correction gold requires a non-empty changed expected_replacement"
                )
        elif self.expected_action == "keep_original":
            if self.expected_replacement is not None:
                raise ValueError("keep_original gold must not carry expected_replacement")
        else:
            raise ValueError(f"unsupported process expected_action: {self.expected_action!r}")

    def target_key(self) -> tuple[tuple[str, ...], tuple[str, ...], int, int]:
        return (self.audio_span_ids, self.evidence_token_ids, self.start_ms, self.end_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "lineage_id": self.lineage_id,
            "normalized_audio_hash": self.normalized_audio_hash,
            "audio_span_ids": list(self.audio_span_ids),
            "evidence_token_ids": list(self.evidence_token_ids),
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "observed_text": self.observed_text,
            "expected_action": self.expected_action,
            "expected_replacement": self.expected_replacement,
            "source_artifact_ids": list(self.source_artifact_ids),
            "adjudication_locator": self.adjudication_locator,
        }


@dataclass(frozen=True)
class ProcessGoldCorpus:
    """Closed, versioned human gold for proposal/decision process metrics."""

    schema_version: int
    corpus_id: str
    episode_id: str
    lineage_id: str
    normalized_audio_hash: str
    gold_corpus_hash: str
    complete: bool
    expected_case_count: int
    adjudication: ProcessAdjudication
    sources: tuple[ProcessGoldSource, ...]
    cases: tuple[ProcessGoldCase, ...]

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise ValueError("process gold schema_version must be 1")
        for label, value in (
            ("corpus_id", self.corpus_id),
            ("episode_id", self.episode_id),
            ("lineage_id", self.lineage_id),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"process gold {label} must be non-empty")
        if not isinstance(self.normalized_audio_hash, str) or not _SHA256_RE.fullmatch(
            self.normalized_audio_hash
        ):
            raise ValueError("process gold normalized_audio_hash must be lowercase SHA-256")
        if not isinstance(self.gold_corpus_hash, str) or not _SHA256_RE.fullmatch(
            self.gold_corpus_hash
        ):
            raise ValueError("process gold gold_corpus_hash must be lowercase SHA-256")
        if not isinstance(self.complete, bool):
            raise TypeError("process gold complete must be boolean")
        if (
            isinstance(self.expected_case_count, bool)
            or not isinstance(self.expected_case_count, int)
            or self.expected_case_count < 1
        ):
            raise ValueError("process gold expected_case_count must be positive")
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "cases", tuple(self.cases))
        if not self.sources or not self.cases:
            raise ValueError("process gold requires sources and per-case labels")
        _index_unique(self.sources, label="process gold sources", id_field="artifact_id")
        _index_unique(self.cases, label="process gold cases")
        if len(self.cases) > self.expected_case_count:
            raise ValueError("process gold cases exceed expected_case_count")
        if self.complete and len(self.cases) != self.expected_case_count:
            raise ValueError("complete process gold must cover expected_case_count exactly")
        if self.complete and self.adjudication.status != "completed":
            raise ValueError("complete process gold requires completed human adjudication")
        source_ids = {source.artifact_id for source in self.sources}
        targets: set[tuple[tuple[str, ...], tuple[str, ...], int, int]] = set()
        ordered_cases = sorted(self.cases, key=lambda case: (case.start_ms, case.end_ms))
        for case in self.cases:
            if (
                case.lineage_id != self.lineage_id
                or case.normalized_audio_hash != self.normalized_audio_hash
            ):
                raise ValueError("process gold case lineage/hash mismatch")
            unknown_sources = set(case.source_artifact_ids) - source_ids
            if unknown_sources:
                raise ValueError(
                    f"process gold case {case.case_id} cites unknown source artifacts: "
                    f"{sorted(unknown_sources)}"
                )
            if case.target_key() in targets:
                raise ValueError("duplicate process gold target")
            targets.add(case.target_key())
        for left, right in zip(ordered_cases, ordered_cases[1:]):
            if left.end_ms > right.start_ms:
                raise ValueError("process gold target time ranges must not overlap")
        for index, left in enumerate(self.cases):
            for right in self.cases[index + 1 :]:
                if set(left.audio_span_ids) & set(right.audio_span_ids):
                    raise ValueError("process gold targets must not share AudioSpan IDs")
                if set(left.evidence_token_ids) & set(right.evidence_token_ids):
                    raise ValueError("process gold targets must not share Evidence token IDs")
        if self.gold_corpus_hash != _process_gold_hash(self):
            raise ValueError("process gold gold_corpus_hash mismatch")

    def hash_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluation_kind": "correction_process_gold",
            "corpus_id": self.corpus_id,
            "episode_id": self.episode_id,
            "lineage_id": self.lineage_id,
            "normalized_audio_hash": self.normalized_audio_hash,
            "complete": self.complete,
            "expected_case_count": self.expected_case_count,
            "adjudication": self.adjudication.to_dict(),
            "sources": [source.to_dict() for source in self.sources],
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(frozen=True)
class ProcessEvidenceArtifact:
    artifact_id: str
    role: ProcessArtifactRole
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, str) or not self.artifact_id:
            raise ValueError("process evidence artifact_id must be non-empty")
        if self.role not in {"proposal_set", "candidate_discovery", "ledger_prefix"}:
            raise ValueError("unsupported process evidence artifact role")
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("process evidence artifact sha256 must be lowercase SHA-256")

    def to_dict(self) -> dict[str, str]:
        return {"artifact_id": self.artifact_id, "role": self.role, "sha256": self.sha256}


@dataclass(frozen=True)
class ProcessProposalTrace:
    proposal_id: str
    generation_id: str
    audio_span_ids: tuple[str, ...]
    evidence_token_ids: tuple[str, ...]
    start_ms: int
    end_ms: int
    observed_text: str
    candidate_text: str
    source: str
    proposal_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("proposal_id", self.proposal_id),
            ("generation_id", self.generation_id),
            ("observed_text", self.observed_text),
            ("candidate_text", self.candidate_text),
            ("source", self.source),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"process proposal {label} must be non-empty")
        for label in ("audio_span_ids", "evidence_token_ids"):
            values = tuple(getattr(self, label))
            object.__setattr__(self, label, values)
            if not values or any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"process proposal {label} must contain non-empty IDs")
            if len(set(values)) != len(values):
                raise ValueError(f"process proposal {label} must be unique")
        if (
            isinstance(self.start_ms, bool)
            or not isinstance(self.start_ms, int)
            or isinstance(self.end_ms, bool)
            or not isinstance(self.end_ms, int)
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
        ):
            raise ValueError("process proposal has invalid time bounds")
        if not isinstance(self.proposal_hash, str) or not _SHA256_RE.fullmatch(self.proposal_hash):
            raise ValueError("process proposal proposal_hash must be lowercase SHA-256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "generation_id": self.generation_id,
            "audio_span_ids": list(self.audio_span_ids),
            "evidence_token_ids": list(self.evidence_token_ids),
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "observed_text": self.observed_text,
            "candidate_text": self.candidate_text,
            "source": self.source,
            "proposal_hash": self.proposal_hash,
        }


@dataclass(frozen=True)
class ProcessDecisionTrace:
    sequence: int
    event_id: str
    parent_generation_id: str
    resulting_generation_id: str
    ledger_entry_hash: str
    decision_hash: str
    target_span_ids: tuple[str, ...]
    target_start_ms: int
    target_end_ms: int
    proposal_ids: tuple[str, ...]
    action: str
    replacement_text: str | None
    selected_candidate: str | None
    decision_family: str = "legacy_correction_v1"
    candidate_discovery_id: str | None = None
    candidate_discovery_hash: str | None = None
    candidate_literal_sha256: str | None = None
    authorized_literal_sha256: str | None = None
    authorization_id: str | None = None
    authorization_hash: str | None = None
    trace_schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("process decision sequence must be positive")
        for label, value in (
            ("event_id", self.event_id),
            ("parent_generation_id", self.parent_generation_id),
            ("resulting_generation_id", self.resulting_generation_id),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"process decision {label} must be non-empty")
        for label, value in (
            ("ledger_entry_hash", self.ledger_entry_hash),
            ("decision_hash", self.decision_hash),
        ):
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                raise ValueError(f"process decision {label} must be lowercase SHA-256")
        object.__setattr__(self, "target_span_ids", tuple(self.target_span_ids))
        object.__setattr__(self, "proposal_ids", tuple(self.proposal_ids))
        if not self.target_span_ids or len(set(self.target_span_ids)) != len(self.target_span_ids):
            raise ValueError("process decision target_span_ids must be non-empty and unique")
        if len(set(self.proposal_ids)) != len(self.proposal_ids):
            raise ValueError("process decision proposal_ids must be unique")
        if (
            isinstance(self.target_start_ms, bool)
            or not isinstance(self.target_start_ms, int)
            or isinstance(self.target_end_ms, bool)
            or not isinstance(self.target_end_ms, int)
            or self.target_start_ms < 0
            or self.target_end_ms <= self.target_start_ms
        ):
            raise ValueError("process decision has invalid time bounds")
        allowed_actions = {
            "confirm_original",
            "replace",
            "accept_candidate",
            "reject_candidate",
            "defer",
            "accept_exact_candidate",
        }
        if self.action not in allowed_actions:
            raise ValueError(f"unsupported process decision action: {self.action!r}")
        if self.decision_family not in {
            "legacy_correction_v1",
            "native_correction_v2",
        }:
            raise ValueError("unsupported process decision family")
        if (
            isinstance(self.trace_schema_version, bool)
            or self.trace_schema_version not in {1, 2}
        ):
            raise ValueError("process decision trace_schema_version must be exact 1 or 2")
        native_fields = (
            self.candidate_discovery_id,
            self.candidate_discovery_hash,
            self.candidate_literal_sha256,
            self.authorization_id,
            self.authorization_hash,
        )
        for label, value in (
            ("candidate_discovery_id", self.candidate_discovery_id),
            ("candidate_discovery_hash", self.candidate_discovery_hash),
            ("candidate_literal_sha256", self.candidate_literal_sha256),
            ("authorized_literal_sha256", self.authorized_literal_sha256),
            ("authorization_id", self.authorization_id),
            ("authorization_hash", self.authorization_hash),
        ):
            if value is not None and (
                not isinstance(value, str) or not _SHA256_RE.fullmatch(value)
            ):
                raise ValueError(f"process decision {label} must be lowercase SHA-256")
        if self.decision_family == "legacy_correction_v1":
            if self.action == "replace" and not self.replacement_text:
                raise ValueError("replace process decision requires replacement_text")
            if self.action != "replace" and self.replacement_text is not None:
                raise ValueError(f"{self.action} process decision cannot carry replacement_text")
            if self.action in {"accept_candidate", "reject_candidate"}:
                if not self.proposal_ids or not self.selected_candidate:
                    raise ValueError(
                        f"{self.action} process decision requires proposal_ids "
                        "and selected_candidate"
                    )
            elif self.selected_candidate is not None:
                raise ValueError(
                    f"{self.action} process decision cannot carry selected_candidate"
                )
            if any(value is not None for value in (*native_fields, self.authorized_literal_sha256)):
                raise ValueError("legacy process decision cannot carry native discovery provenance")
            if self.action == "accept_exact_candidate":
                raise ValueError("legacy process decision cannot use native action")
        else:
            if self.trace_schema_version != 2:
                raise ValueError("native process decision requires trace schema_version 2")
            if any(value is None for value in native_fields):
                raise ValueError(
                    "native process decision requires exact discovery and authorization"
                )
            if self.proposal_ids:
                raise ValueError("native process decision cannot carry legacy proposal_ids")
            if self.replacement_text is not None or self.selected_candidate is not None:
                raise ValueError("native process decision cannot embed unverified literal text")
            if self.action not in {
                "accept_exact_candidate",
                "confirm_original",
                "reject_candidate",
                "defer",
            }:
                raise ValueError("native process decision has a legacy-only action")
            if self.action == "accept_exact_candidate" and (
                self.authorized_literal_sha256 != self.candidate_literal_sha256
            ):
                raise ValueError("native accepted literal hash differs from discovery literal hash")
            if self.action in {"reject_candidate", "defer"} and (
                self.authorized_literal_sha256 is not None
            ):
                raise ValueError("native reject/defer cannot carry authorized literal hash")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "sequence": self.sequence,
            "event_id": self.event_id,
            "parent_generation_id": self.parent_generation_id,
            "resulting_generation_id": self.resulting_generation_id,
            "ledger_entry_hash": self.ledger_entry_hash,
            "decision_hash": self.decision_hash,
            "target_span_ids": list(self.target_span_ids),
            "target_start_ms": self.target_start_ms,
            "target_end_ms": self.target_end_ms,
            "action": self.action,
        }
        if self.decision_family == "legacy_correction_v1":
            payload.update(
                {
                    "proposal_ids": list(self.proposal_ids),
                    "replacement_text": self.replacement_text,
                    "selected_candidate": self.selected_candidate,
                }
            )
            if self.trace_schema_version == 2:
                payload["decision_family"] = self.decision_family
        else:
            payload.update(
                {
                    "decision_family": self.decision_family,
                    "candidate_discovery_id": self.candidate_discovery_id,
                    "candidate_discovery_hash": self.candidate_discovery_hash,
                    "candidate_literal_sha256": self.candidate_literal_sha256,
                    "authorized_literal_sha256": self.authorized_literal_sha256,
                    "authorization_id": self.authorization_id,
                    "authorization_hash": self.authorization_hash,
                }
            )
        return payload


@dataclass(frozen=True)
class CorrectionProcessEvaluation:
    """Complete, stored-artifact-backed proposal and decision process trace."""

    candidate_generation_id: str
    candidate_content_hash: str
    normalized_audio_hash: str
    candidate_artifact_id: str
    candidate_artifact_hash: str
    benchmark_suite_hash: str
    schema_version: int
    process_gold_hash: str
    trace_hash: str
    complete: bool
    expected_proposal_count: int
    expected_decision_count: int
    covered_case_ids: tuple[str, ...]
    source_artifacts: tuple[ProcessEvidenceArtifact, ...]
    proposals: tuple[ProcessProposalTrace, ...]
    decisions: tuple[ProcessDecisionTrace, ...]

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.candidate_generation_id, self.candidate_artifact_id)
        ):
            raise ValueError("correction process candidate IDs must be non-empty")
        for label, value in (
            ("candidate_content_hash", self.candidate_content_hash),
            ("normalized_audio_hash", self.normalized_audio_hash),
            ("candidate_artifact_hash", self.candidate_artifact_hash),
            ("benchmark_suite_hash", self.benchmark_suite_hash),
        ):
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                raise ValueError(f"correction process evidence {label} must be lowercase SHA-256")
        if isinstance(self.schema_version, bool) or self.schema_version not in {1, 2}:
            raise ValueError("correction process evidence schema_version must be exact 1 or 2")
        for label, value in (
            ("process_gold_hash", self.process_gold_hash),
            ("trace_hash", self.trace_hash),
        ):
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                raise ValueError(f"correction process evidence {label} must be lowercase SHA-256")
        if not isinstance(self.complete, bool):
            raise TypeError("correction process evidence complete must be boolean")
        for label in ("expected_proposal_count", "expected_decision_count"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"correction process evidence {label} cannot be negative")
        for label in ("covered_case_ids", "source_artifacts", "proposals", "decisions"):
            object.__setattr__(self, label, tuple(getattr(self, label)))
        if len(set(self.covered_case_ids)) != len(self.covered_case_ids) or any(
            not isinstance(item, str) or not item for item in self.covered_case_ids
        ):
            raise ValueError("correction process covered_case_ids must be non-empty unique IDs")
        if not self.source_artifacts:
            raise ValueError("correction process evidence requires source artifacts")
        _index_unique(
            self.source_artifacts,
            label="correction process source artifacts",
            id_field="artifact_id",
        )
        if tuple(sorted(self.source_artifacts, key=lambda item: item.artifact_id)) != (
            self.source_artifacts
        ):
            raise ValueError("correction process source artifacts must be sorted by artifact_id")
        proposal_keys = [(item.generation_id, item.proposal_id) for item in self.proposals]
        if len(set(proposal_keys)) != len(proposal_keys):
            raise ValueError("duplicate correction process proposal provenance")
        if len({item.proposal_id for item in self.proposals}) != len(self.proposals):
            raise ValueError("correction process proposal_ids must be globally unique")
        if (
            tuple(
                sorted(
                    self.proposals,
                    key=lambda item: (
                        item.generation_id,
                        item.start_ms,
                        item.end_ms,
                        item.proposal_id,
                    ),
                )
            )
            != self.proposals
        ):
            raise ValueError("correction process proposals must use deterministic order")
        if len({item.sequence for item in self.decisions}) != len(self.decisions):
            raise ValueError("duplicate correction process decision sequence")
        if len({item.event_id for item in self.decisions}) != len(self.decisions):
            raise ValueError("duplicate correction process decision event_id")
        if len(self.proposals) > self.expected_proposal_count:
            raise ValueError("process proposals exceed expected_proposal_count")
        if len(self.decisions) > self.expected_decision_count:
            raise ValueError("process decisions exceed expected_decision_count")
        if self.complete and (
            len(self.proposals) != self.expected_proposal_count
            or len(self.decisions) != self.expected_decision_count
        ):
            raise ValueError("complete process evidence must cover declared trace counts exactly")
        if tuple(sorted(self.decisions, key=lambda item: item.sequence)) != self.decisions:
            raise ValueError("correction process decisions must follow ledger sequence")
        if self.schema_version == 1 and any(
            decision.decision_family != "legacy_correction_v1"
            or decision.trace_schema_version != 1
            for decision in self.decisions
        ):
            raise ValueError("correction process schema v1 accepts only legacy decisions")
        if self.schema_version == 2 and any(
            decision.trace_schema_version != 2 for decision in self.decisions
        ):
            raise ValueError("correction process schema v2 requires family-aware decisions")
        proposal_ids = {item.proposal_id for item in self.proposals}
        unknown_proposals = {
            proposal_id
            for decision in self.decisions
            for proposal_id in decision.proposal_ids
            if proposal_id not in proposal_ids
        }
        if unknown_proposals:
            raise ValueError(
                f"correction process decisions cite unknown proposals: {sorted(unknown_proposals)}"
            )
        proposal_by_id = {item.proposal_id: item for item in self.proposals}
        for decision in self.decisions:
            for proposal_id in decision.proposal_ids:
                proposal = proposal_by_id[proposal_id]
                if (
                    proposal.generation_id != decision.parent_generation_id
                    or proposal.audio_span_ids != decision.target_span_ids
                    or proposal.start_ms != decision.target_start_ms
                    or proposal.end_ms != decision.target_end_ms
                ):
                    raise ValueError(
                        "correction process decision proposal provenance crosses "
                        "generation or target"
                    )
            if decision.action in {"accept_candidate", "reject_candidate"} and not any(
                proposal_by_id[proposal_id].candidate_text == decision.selected_candidate
                for proposal_id in decision.proposal_ids
            ):
                raise ValueError(
                    "correction process decision selected_candidate does not match a cited proposal"
                )
        if self.trace_hash != _process_evidence_trace_hash(self):
            raise ValueError("correction process evidence trace_hash mismatch")

    def trace_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluation_kind": "correction_process",
            "process_gold_hash": self.process_gold_hash,
            "complete": self.complete,
            "expected_proposal_count": self.expected_proposal_count,
            "expected_decision_count": self.expected_decision_count,
            "covered_case_ids": list(self.covered_case_ids),
            "source_artifacts": [item.to_dict() for item in self.source_artifacts],
            "proposals": [item.to_dict() for item in self.proposals],
            "decisions": [item.to_dict() for item in self.decisions],
            **{field: getattr(self, field) for field in sorted(_LINEAGE_FIELDS)},
        }


@dataclass(frozen=True)
class BlindBoundaryClip:
    """One frozen audio interval used by the blinded boundary study."""

    clip_id: str
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.clip_id, str) or not self.clip_id:
            raise ValueError("blind boundary clip_id must be non-empty")
        if (
            isinstance(self.start_ms, bool)
            or not isinstance(self.start_ms, int)
            or isinstance(self.end_ms, bool)
            or not isinstance(self.end_ms, int)
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
        ):
            raise ValueError("blind boundary clip has invalid time bounds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }


@dataclass(frozen=True)
class BlindBoundaryCase:
    """One displayed candidate boundary and its blinded human judgement."""

    case_id: str
    clip_id: str
    boundary_after_token_id: str
    accepted: bool

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.case_id, self.clip_id, self.boundary_after_token_id)
        ):
            raise ValueError("blind boundary case IDs must be non-empty")
        if not isinstance(self.accepted, bool):
            raise TypeError("blind boundary accepted must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "clip_id": self.clip_id,
            "boundary_after_token_id": self.boundary_after_token_id,
            "accepted": self.accepted,
        }


@dataclass(frozen=True)
class TermCodeSwitchCase:
    """One gold text span and the exact candidate text observed for it."""

    case_id: str
    start_ms: int
    end_ms: int
    expected_text: str
    observed_text: str

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise ValueError("term/code-switch case_id must be non-empty")
        if (
            isinstance(self.start_ms, bool)
            or not isinstance(self.start_ms, int)
            or isinstance(self.end_ms, bool)
            or not isinstance(self.end_ms, int)
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
        ):
            raise ValueError("term/code-switch case has invalid time bounds")
        if (
            not isinstance(self.expected_text, str)
            or not self.expected_text
            or not isinstance(self.observed_text, str)
        ):
            raise ValueError("term/code-switch expected and observed text are required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "expected_text": self.expected_text,
            "observed_text": self.observed_text,
        }


@dataclass(frozen=True)
class _EvaluationLineage:
    candidate_generation_id: str
    candidate_content_hash: str
    normalized_audio_hash: str
    candidate_artifact_id: str
    candidate_artifact_hash: str
    benchmark_suite_hash: str

    def _validate_lineage(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.candidate_generation_id, self.candidate_artifact_id)
        ):
            raise ValueError("evaluation candidate IDs must be non-empty")
        for label, value in (
            ("candidate_content_hash", self.candidate_content_hash),
            ("normalized_audio_hash", self.normalized_audio_hash),
            ("candidate_artifact_hash", self.candidate_artifact_hash),
            ("benchmark_suite_hash", self.benchmark_suite_hash),
        ):
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                raise ValueError(f"evaluation {label} must be lowercase SHA-256")


@dataclass(frozen=True)
class BlindBoundaryEvaluation(_EvaluationLineage):
    """Legacy single-candidate diagnostic; never evidence of V2 superiority."""

    schema_version: int
    corpus_id: str
    gold_corpus_hash: str
    blinded: bool
    complete: bool
    clips: tuple[BlindBoundaryClip, ...]
    cases: tuple[BlindBoundaryCase, ...]

    def __post_init__(self) -> None:
        self._validate_lineage()
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise ValueError("blind boundary evidence schema_version must be 1")
        if not isinstance(self.corpus_id, str) or not self.corpus_id:
            raise ValueError("blind boundary corpus_id must be non-empty")
        if not isinstance(self.blinded, bool) or not isinstance(self.complete, bool):
            raise TypeError("blind boundary flags must be booleans")
        object.__setattr__(self, "clips", tuple(self.clips))
        object.__setattr__(self, "cases", tuple(self.cases))
        if not self.clips or not self.cases:
            raise ValueError("blind boundary evidence requires clips and per-case labels")
        clips_by_id: dict[str, BlindBoundaryClip] = {}
        for clip in self.clips:
            if clip.clip_id in clips_by_id:
                raise ValueError(f"duplicate blind boundary clip_id: {clip.clip_id}")
            clips_by_id[clip.clip_id] = clip
        ordered_clips = sorted(self.clips, key=lambda clip: (clip.start_ms, clip.end_ms))
        for left, right in zip(ordered_clips, ordered_clips[1:]):
            if left.end_ms > right.start_ms:
                raise ValueError("blind boundary clip ranges must not overlap")
        _index_unique(self.cases, label="blind boundary evidence cases")
        unknown_clips = {case.clip_id for case in self.cases} - set(clips_by_id)
        if unknown_clips:
            raise ValueError(
                f"blind boundary cases reference unknown clips: {sorted(unknown_clips)}"
            )
        if len({case.boundary_after_token_id for case in self.cases}) != len(self.cases):
            raise ValueError("each displayed candidate boundary may be labelled only once")
        empty_clips = set(clips_by_id) - {case.clip_id for case in self.cases}
        if empty_clips:
            raise ValueError(f"blind boundary clips lack per-case labels: {sorted(empty_clips)}")
        expected_hash = _blind_gold_hash(self.corpus_id, self.clips)
        if self.gold_corpus_hash != expected_hash:
            raise ValueError("blind boundary gold_corpus_hash mismatch")

    @property
    def clip_count(self) -> int:
        return len(self.clips)


class PairedBoundaryOutcome(str, Enum):
    """A human preference over the two opaque, complete clip renderings."""

    A_BETTER = "a_better"
    B_BETTER = "b_better"
    TIE_BOTH_GOOD = "tie_both_good"
    TIE_BOTH_BAD = "tie_both_bad"


def _require_sha256(label: str, value: object) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _require_nonempty_string(label: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be non-empty")
    return value


def _parse_utc_timestamp(label: str, value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid RFC3339 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} must be UTC")
    return parsed


@dataclass(frozen=True)
class PairedStudyClip:
    """One predeclared, non-overlapping study unit from exact normalized audio."""

    clip_id: str
    start_ms: int
    end_ms: int
    normalized_audio_hash: str
    audio_clip_hash: str
    selection_stratum: str

    def __post_init__(self) -> None:
        _require_nonempty_string("paired clip_id", self.clip_id)
        _require_nonempty_string("paired clip selection_stratum", self.selection_stratum)
        _require_sha256("paired clip normalized_audio_hash", self.normalized_audio_hash)
        _require_sha256("paired clip audio_clip_hash", self.audio_clip_hash)
        if (
            isinstance(self.start_ms, bool)
            or not isinstance(self.start_ms, int)
            or isinstance(self.end_ms, bool)
            or not isinstance(self.end_ms, int)
            or self.start_ms < 0
            or self.end_ms <= self.start_ms
        ):
            raise ValueError("paired clip has invalid time bounds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "normalized_audio_hash": self.normalized_audio_hash,
            "audio_clip_hash": self.audio_clip_hash,
            "selection_stratum": self.selection_stratum,
        }


@dataclass(frozen=True)
class PairedBoundaryPredeclaration:
    """Selection and decision thresholds frozen before either candidate exists."""

    schema_version: int
    study_id: str
    frozen_at_utc: str
    selection_method: str
    selection_independent_of_candidates: bool
    sampling_frame_hash: str
    selection_seed_commitment: str
    minimum_clip_count: int
    minimum_decisive_count: int
    one_sided_alpha: float
    minimum_v2_decisive_win_rate: float
    maximum_v2_unacceptable_rate: float
    clips: tuple[PairedStudyClip, ...]
    predeclaration_hash: str

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise ValueError("paired predeclaration schema_version must be 1")
        _require_nonempty_string("paired predeclaration study_id", self.study_id)
        _parse_utc_timestamp("paired predeclaration frozen_at_utc", self.frozen_at_utc)
        if self.selection_method != "predeclared_nonoverlapping_episode_windows_v1":
            raise ValueError("unsupported paired predeclaration selection_method")
        if not isinstance(self.selection_independent_of_candidates, bool):
            raise TypeError("selection_independent_of_candidates must be boolean")
        _require_sha256("paired predeclaration sampling_frame_hash", self.sampling_frame_hash)
        _require_sha256(
            "paired predeclaration selection_seed_commitment",
            self.selection_seed_commitment,
        )
        for label in ("minimum_clip_count", "minimum_decisive_count"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"paired predeclaration {label} must be positive")
        if self.minimum_clip_count < 20:
            raise ValueError("paired predeclaration minimum_clip_count cannot be below 20")
        if self.minimum_decisive_count < 12:
            raise ValueError("paired predeclaration minimum_decisive_count cannot be below 12")
        if self.minimum_decisive_count > self.minimum_clip_count:
            raise ValueError("minimum_decisive_count cannot exceed minimum_clip_count")
        for label in (
            "one_sided_alpha",
            "minimum_v2_decisive_win_rate",
            "maximum_v2_unacceptable_rate",
        ):
            value = getattr(self, label)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"paired predeclaration {label} must be finite")
        if not 0 < self.one_sided_alpha <= 0.05:
            raise ValueError("paired predeclaration one_sided_alpha must be in (0, 0.05]")
        if not 0.65 <= self.minimum_v2_decisive_win_rate <= 1:
            raise ValueError(
                "paired predeclaration minimum_v2_decisive_win_rate must be in [0.65, 1]"
            )
        if not 0 <= self.maximum_v2_unacceptable_rate <= 0.05:
            raise ValueError(
                "paired predeclaration maximum_v2_unacceptable_rate must be in [0, 0.05]"
            )
        object.__setattr__(self, "clips", tuple(self.clips))
        if not self.clips:
            raise ValueError("paired predeclaration requires clips")
        clip_ids = [clip.clip_id for clip in self.clips]
        if len(set(clip_ids)) != len(clip_ids):
            raise ValueError("paired predeclaration clip_ids must be unique")
        expected_order = tuple(
            sorted(self.clips, key=lambda clip: (clip.start_ms, clip.end_ms, clip.clip_id))
        )
        if expected_order != self.clips:
            raise ValueError("paired predeclaration clips must use deterministic time order")
        for left, right in zip(self.clips, self.clips[1:]):
            if left.end_ms > right.start_ms:
                raise ValueError("paired predeclaration clip ranges must not overlap")
        _require_sha256("paired predeclaration hash", self.predeclaration_hash)
        if self.predeclaration_hash != paired_boundary_predeclaration_hash(self):
            raise ValueError("paired boundary predeclaration_hash mismatch")

    def hash_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "frozen_at_utc": self.frozen_at_utc,
            "selection_method": self.selection_method,
            "selection_independent_of_candidates": self.selection_independent_of_candidates,
            "sampling_frame_hash": self.sampling_frame_hash,
            "selection_seed_commitment": self.selection_seed_commitment,
            "minimum_clip_count": self.minimum_clip_count,
            "minimum_decisive_count": self.minimum_decisive_count,
            "one_sided_alpha": self.one_sided_alpha,
            "minimum_v2_decisive_win_rate": self.minimum_v2_decisive_win_rate,
            "maximum_v2_unacceptable_rate": self.maximum_v2_unacceptable_rate,
            "clips": [clip.to_dict() for clip in self.clips],
        }


@dataclass(frozen=True)
class PairedClipPresentation:
    """Exact all-cue rendering shown for one candidate over one frozen clip."""

    clip_id: str
    presentation_artifact_hash: str
    cue_set_hash: str
    cue_count: int

    def __post_init__(self) -> None:
        _require_nonempty_string("paired presentation clip_id", self.clip_id)
        _require_sha256(
            "paired presentation presentation_artifact_hash",
            self.presentation_artifact_hash,
        )
        _require_sha256("paired presentation cue_set_hash", self.cue_set_hash)
        if (
            isinstance(self.cue_count, bool)
            or not isinstance(self.cue_count, int)
            or self.cue_count <= 0
        ):
            raise ValueError("paired presentation cue_count must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "presentation_artifact_hash": self.presentation_artifact_hash,
            "cue_set_hash": self.cue_set_hash,
            "cue_count": self.cue_count,
        }


@dataclass(frozen=True)
class PairedBoundaryCandidate:
    """One content-addressed V1 or V2 candidate and every complete clip view."""

    system: Literal["v1", "v2"]
    candidate_id: str
    generation_id: str
    canonical_content_hash: str
    token_sequence_hash: str
    candidate_artifact_hash: str
    subtitle_bytes_hash: str
    renderer_identity_hash: str
    normalized_audio_hash: str
    predeclaration_hash: str
    generated_at_utc: str
    clip_presentations: tuple[PairedClipPresentation, ...]
    candidate_record_hash: str

    def __post_init__(self) -> None:
        if self.system not in {"v1", "v2"}:
            raise ValueError("paired candidate system must be v1 or v2")
        _require_nonempty_string("paired candidate_id", self.candidate_id)
        _require_nonempty_string("paired candidate generation_id", self.generation_id)
        for label in (
            "canonical_content_hash",
            "token_sequence_hash",
            "candidate_artifact_hash",
            "subtitle_bytes_hash",
            "renderer_identity_hash",
            "normalized_audio_hash",
            "predeclaration_hash",
            "candidate_record_hash",
        ):
            _require_sha256(f"paired candidate {label}", getattr(self, label))
        _parse_utc_timestamp("paired candidate generated_at_utc", self.generated_at_utc)
        object.__setattr__(self, "clip_presentations", tuple(self.clip_presentations))
        if not self.clip_presentations:
            raise ValueError("paired candidate requires clip presentations")
        ids = [item.clip_id for item in self.clip_presentations]
        if len(ids) != len(set(ids)):
            raise ValueError("paired candidate clip presentations must be unique")
        if self.candidate_record_hash != paired_boundary_candidate_record_hash(self):
            raise ValueError("paired boundary candidate_record_hash mismatch")

    def hash_payload(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "candidate_id": self.candidate_id,
            "generation_id": self.generation_id,
            "canonical_content_hash": self.canonical_content_hash,
            "token_sequence_hash": self.token_sequence_hash,
            "candidate_artifact_hash": self.candidate_artifact_hash,
            "subtitle_bytes_hash": self.subtitle_bytes_hash,
            "renderer_identity_hash": self.renderer_identity_hash,
            "normalized_audio_hash": self.normalized_audio_hash,
            "predeclaration_hash": self.predeclaration_hash,
            "generated_at_utc": self.generated_at_utc,
            "clip_presentations": [item.to_dict() for item in self.clip_presentations],
        }


@dataclass(frozen=True)
class PairedBoundaryMappingEntry:
    clip_id: str
    a_candidate_record_hash: str
    b_candidate_record_hash: str
    nonce: str

    def __post_init__(self) -> None:
        _require_nonempty_string("paired mapping clip_id", self.clip_id)
        _require_sha256("paired mapping A candidate", self.a_candidate_record_hash)
        _require_sha256("paired mapping B candidate", self.b_candidate_record_hash)
        _require_nonempty_string("paired mapping nonce", self.nonce)
        if self.a_candidate_record_hash == self.b_candidate_record_hash:
            raise ValueError("paired mapping A and B must reference different candidates")

    def to_dict(self) -> dict[str, str]:
        return {
            "clip_id": self.clip_id,
            "a_candidate_record_hash": self.a_candidate_record_hash,
            "b_candidate_record_hash": self.b_candidate_record_hash,
            "nonce": self.nonce,
        }


@dataclass(frozen=True)
class PairedBoundaryMappingReveal:
    revealed_at_utc: str
    labels_completed_at_utc: str
    entries: tuple[PairedBoundaryMappingEntry, ...]

    def __post_init__(self) -> None:
        _parse_utc_timestamp("paired mapping revealed_at_utc", self.revealed_at_utc)
        _parse_utc_timestamp(
            "paired mapping labels_completed_at_utc",
            self.labels_completed_at_utc,
        )
        object.__setattr__(self, "entries", tuple(self.entries))
        if not self.entries:
            raise ValueError("paired mapping reveal requires entries")
        ids = [entry.clip_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("paired mapping reveal clip_ids must be unique")


@dataclass(frozen=True)
class PairedBoundaryMapping:
    commitment_hash: str
    committed_at_utc: str
    randomization_method: str
    reveal: PairedBoundaryMappingReveal | None

    def __post_init__(self) -> None:
        _require_sha256("paired mapping commitment_hash", self.commitment_hash)
        _parse_utc_timestamp("paired mapping committed_at_utc", self.committed_at_utc)
        if self.randomization_method != "opaque_balanced_per_clip_v1":
            raise ValueError("unsupported paired mapping randomization_method")
        if self.reveal is not None and not isinstance(self.reveal, PairedBoundaryMappingReveal):
            raise TypeError("paired mapping reveal has invalid type")


@dataclass(frozen=True)
class PairedBoundaryJudgement:
    clip_id: str
    evaluator_id: str
    outcome: PairedBoundaryOutcome | str
    a_unacceptable: bool
    b_unacceptable: bool
    a_presentation_artifact_hash: str
    b_presentation_artifact_hash: str
    mapping_commitment_hash: str
    submitted_at_utc: str

    def __post_init__(self) -> None:
        _require_nonempty_string("paired judgement clip_id", self.clip_id)
        _require_nonempty_string("paired judgement evaluator_id", self.evaluator_id)
        try:
            outcome = (
                self.outcome
                if isinstance(self.outcome, PairedBoundaryOutcome)
                else PairedBoundaryOutcome(self.outcome)
            )
        except ValueError as exc:
            raise ValueError(f"unsupported paired boundary outcome: {self.outcome!r}") from exc
        object.__setattr__(self, "outcome", outcome)
        if not isinstance(self.a_unacceptable, bool) or not isinstance(self.b_unacceptable, bool):
            raise TypeError("paired judgement unacceptable flags must be booleans")
        if outcome is PairedBoundaryOutcome.TIE_BOTH_GOOD and (
            self.a_unacceptable or self.b_unacceptable
        ):
            raise ValueError("tie_both_good cannot mark a candidate unacceptable")
        if outcome is PairedBoundaryOutcome.TIE_BOTH_BAD and not (
            self.a_unacceptable and self.b_unacceptable
        ):
            raise ValueError("tie_both_bad must mark both candidates unacceptable")
        for label in (
            "a_presentation_artifact_hash",
            "b_presentation_artifact_hash",
            "mapping_commitment_hash",
        ):
            _require_sha256(f"paired judgement {label}", getattr(self, label))
        _parse_utc_timestamp("paired judgement submitted_at_utc", self.submitted_at_utc)


@dataclass(frozen=True)
class PairedBoundaryStudy:
    """Complete paired study artifact; aggregate-only evidence is not representable."""

    schema_version: int
    evaluation_kind: str
    study_id: str
    protocol_id: str
    episode_id: str
    lineage_id: str
    normalized_audio_hash: str
    benchmark_suite_hash: str
    complete: bool
    candidate_identity_hidden_during_labelling: bool
    labels_created_by_humans: bool
    predeclaration: PairedBoundaryPredeclaration
    candidates: tuple[PairedBoundaryCandidate, ...]
    mapping: PairedBoundaryMapping
    judgements: tuple[PairedBoundaryJudgement, ...]
    study_hash: str

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise ValueError("paired boundary study schema_version must be 1")
        if self.evaluation_kind != "paired_boundary_superiority":
            raise ValueError(
                "paired boundary evaluation_kind must be 'paired_boundary_superiority'"
            )
        for label in ("study_id", "episode_id", "lineage_id"):
            _require_nonempty_string(f"paired boundary {label}", getattr(self, label))
        if self.protocol_id != "podcast-subtitle-v2-paired-blind-v1":
            raise ValueError("unsupported paired boundary protocol_id")
        _require_sha256("paired boundary normalized_audio_hash", self.normalized_audio_hash)
        _require_sha256("paired boundary benchmark_suite_hash", self.benchmark_suite_hash)
        _require_sha256("paired boundary study_hash", self.study_hash)
        for label in (
            "complete",
            "candidate_identity_hidden_during_labelling",
            "labels_created_by_humans",
        ):
            if not isinstance(getattr(self, label), bool):
                raise TypeError(f"paired boundary {label} must be boolean")
        if self.study_id != self.predeclaration.study_id:
            raise ValueError("paired boundary study_id does not match predeclaration")
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "judgements", tuple(self.judgements))
        candidate_by_system = {candidate.system: candidate for candidate in self.candidates}
        if len(self.candidates) != 2 or set(candidate_by_system) != {"v1", "v2"}:
            raise ValueError("paired boundary study requires exactly one V1 and one V2 candidate")
        if len({item.candidate_record_hash for item in self.candidates}) != 2:
            raise ValueError("paired boundary candidates must have distinct record hashes")
        for field in (
            "canonical_content_hash",
            "token_sequence_hash",
            "renderer_identity_hash",
        ):
            if len({getattr(item, field) for item in self.candidates}) != 1:
                raise ValueError(
                    f"paired boundary candidates must share exact {field} to isolate segmentation"
                )
        frozen_at = _parse_utc_timestamp(
            "paired predeclaration frozen_at_utc",
            self.predeclaration.frozen_at_utc,
        )
        expected_clip_ids = tuple(clip.clip_id for clip in self.predeclaration.clips)
        for clip in self.predeclaration.clips:
            if clip.normalized_audio_hash != self.normalized_audio_hash:
                raise ValueError("paired boundary clip crosses normalized-audio lineage")
        for candidate in self.candidates:
            if candidate.normalized_audio_hash != self.normalized_audio_hash:
                raise ValueError("paired boundary candidate crosses normalized-audio lineage")
            if candidate.predeclaration_hash != self.predeclaration.predeclaration_hash:
                raise ValueError("paired boundary candidate does not bind the predeclaration")
            if (
                _parse_utc_timestamp(
                    "paired candidate generated_at_utc", candidate.generated_at_utc
                )
                <= frozen_at
            ):
                raise ValueError(
                    "paired boundary candidates must be generated after selection freeze"
                )
            if tuple(item.clip_id for item in candidate.clip_presentations) != expected_clip_ids:
                raise ValueError(
                    "paired boundary candidate must present every predeclared clip in exact order"
                )
        committed_at = _parse_utc_timestamp(
            "paired mapping committed_at_utc", self.mapping.committed_at_utc
        )
        if any(
            committed_at
            <= _parse_utc_timestamp("paired candidate generated_at_utc", item.generated_at_utc)
            for item in self.candidates
        ):
            raise ValueError("paired mapping commitment must follow both candidate artifacts")
        judgement_by_clip = {item.clip_id: item for item in self.judgements}
        if len(judgement_by_clip) != len(self.judgements):
            raise ValueError("paired boundary study permits exactly one judgement per clip")
        unknown_judgements = set(judgement_by_clip) - set(expected_clip_ids)
        if unknown_judgements:
            raise ValueError(
                f"paired judgements reference unknown clips: {sorted(unknown_judgements)}"
            )
        for judgement in self.judgements:
            if judgement.mapping_commitment_hash != self.mapping.commitment_hash:
                raise ValueError("paired judgement mapping commitment mismatch")
            if (
                _parse_utc_timestamp(
                    "paired judgement submitted_at_utc", judgement.submitted_at_utc
                )
                <= committed_at
            ):
                raise ValueError("paired judgements must follow the opaque mapping commitment")
        if self.mapping.reveal is not None:
            reveal = self.mapping.reveal
            if tuple(entry.clip_id for entry in reveal.entries) != expected_clip_ids:
                raise ValueError("paired mapping reveal must cover every clip in exact order")
            expected_candidate_hashes = {item.candidate_record_hash for item in self.candidates}
            v2_record_hash = candidate_by_system["v2"].candidate_record_hash
            v2_in_a = 0
            for entry in reveal.entries:
                if {
                    entry.a_candidate_record_hash,
                    entry.b_candidate_record_hash,
                } != expected_candidate_hashes:
                    raise ValueError(
                        "paired mapping entry must map exact V1 and V2 candidate records"
                    )
                v2_in_a += int(entry.a_candidate_record_hash == v2_record_hash)
            if abs(v2_in_a - (len(reveal.entries) - v2_in_a)) > 1:
                raise ValueError("paired mapping must balance V2 across A/B presentation positions")
            expected_commitment = paired_boundary_mapping_commitment_hash(
                study_id=self.study_id,
                predeclaration_hash=self.predeclaration.predeclaration_hash,
                entries=reveal.entries,
            )
            if self.mapping.commitment_hash != expected_commitment:
                raise ValueError("paired boundary mapping commitment mismatch")
            labels_completed_at = _parse_utc_timestamp(
                "paired mapping labels_completed_at_utc", reveal.labels_completed_at_utc
            )
            revealed_at = _parse_utc_timestamp(
                "paired mapping revealed_at_utc", reveal.revealed_at_utc
            )
            if revealed_at <= labels_completed_at:
                raise ValueError("paired mapping may be revealed only after labels are sealed")
            entries_by_clip = {entry.clip_id: entry for entry in reveal.entries}
            candidates_by_hash = {item.candidate_record_hash: item for item in self.candidates}
            for judgement in self.judgements:
                submitted_at = _parse_utc_timestamp(
                    "paired judgement submitted_at_utc", judgement.submitted_at_utc
                )
                if submitted_at > labels_completed_at:
                    raise ValueError("paired judgement was submitted after labels were sealed")
                entry = entries_by_clip[judgement.clip_id]
                a_candidate = candidates_by_hash[entry.a_candidate_record_hash]
                b_candidate = candidates_by_hash[entry.b_candidate_record_hash]
                a_presentation = next(
                    item
                    for item in a_candidate.clip_presentations
                    if item.clip_id == judgement.clip_id
                )
                b_presentation = next(
                    item
                    for item in b_candidate.clip_presentations
                    if item.clip_id == judgement.clip_id
                )
                if (
                    judgement.a_presentation_artifact_hash
                    != a_presentation.presentation_artifact_hash
                    or judgement.b_presentation_artifact_hash
                    != b_presentation.presentation_artifact_hash
                ):
                    raise ValueError("paired judgement presentation hash/mapping mismatch")
        if self.complete and self.mapping.reveal is None:
            raise ValueError("complete paired boundary study requires mapping reveal")
        if self.complete and set(judgement_by_clip) != set(expected_clip_ids):
            raise ValueError("complete paired boundary study requires exactly one label per clip")
        if self.study_hash != paired_boundary_study_hash(self):
            raise ValueError("paired boundary study_hash mismatch")

    def hash_payload(self) -> dict[str, Any]:
        reveal = self.mapping.reveal
        return {
            "schema_version": self.schema_version,
            "evaluation_kind": self.evaluation_kind,
            "study_id": self.study_id,
            "protocol_id": self.protocol_id,
            "episode_id": self.episode_id,
            "lineage_id": self.lineage_id,
            "normalized_audio_hash": self.normalized_audio_hash,
            "benchmark_suite_hash": self.benchmark_suite_hash,
            "complete": self.complete,
            "candidate_identity_hidden_during_labelling": (
                self.candidate_identity_hidden_during_labelling
            ),
            "labels_created_by_humans": self.labels_created_by_humans,
            "predeclaration": {
                **self.predeclaration.hash_payload(),
                "predeclaration_hash": self.predeclaration.predeclaration_hash,
            },
            "candidates": [
                {
                    **candidate.hash_payload(),
                    "candidate_record_hash": candidate.candidate_record_hash,
                }
                for candidate in self.candidates
            ],
            "mapping": {
                "commitment_hash": self.mapping.commitment_hash,
                "committed_at_utc": self.mapping.committed_at_utc,
                "randomization_method": self.mapping.randomization_method,
                "reveal": (
                    None
                    if reveal is None
                    else {
                        "revealed_at_utc": reveal.revealed_at_utc,
                        "labels_completed_at_utc": reveal.labels_completed_at_utc,
                        "entries": [entry.to_dict() for entry in reveal.entries],
                    }
                ),
            },
            "judgements": [
                {
                    "clip_id": item.clip_id,
                    "evaluator_id": item.evaluator_id,
                    "outcome": item.outcome.value,
                    "a_unacceptable": item.a_unacceptable,
                    "b_unacceptable": item.b_unacceptable,
                    "a_presentation_artifact_hash": item.a_presentation_artifact_hash,
                    "b_presentation_artifact_hash": item.b_presentation_artifact_hash,
                    "mapping_commitment_hash": item.mapping_commitment_hash,
                    "submitted_at_utc": item.submitted_at_utc,
                }
                for item in self.judgements
            ],
        }


@dataclass(frozen=True)
class PairedBoundaryComparisonReport:
    """Fail-closed superiority result scoped to this exact episode and clip sample."""

    study_id: str
    study_hash: str
    status: GateStatus
    evidence_scope: str
    labelled_pair_count: int
    decisive_pair_count: int
    v2_wins: int
    v1_wins: int
    tie_both_good: int
    tie_both_bad: int
    v2_unacceptable_count: int
    v2_decisive_win_rate: float | None
    v2_unacceptable_rate: float | None
    one_sided_p_value: float | None
    reason: str | None
    cases: tuple[CaseEvaluation, ...]
    statistical_method: str
    predeclared_thresholds: tuple[tuple[str, int | float], ...]

    def to_gate_result(self) -> GateResult:
        return GateResult(
            gate_id="paired_boundary_v2_superiority",
            status=self.status,
            requirement=(
                "A complete predeclared same-lineage paired human study must show V2 "
                "superiority by its frozen effect, exact sign-test, and unacceptable-rate gates."
            ),
            evidence_scope=self.evidence_scope,
            sample_count=self.labelled_pair_count,
            passed_count=sum(case.passed for case in self.cases),
            cases=self.cases,
            metric_name="v2_decisive_win_rate",
            metric_value=self.v2_decisive_win_rate,
            baseline_value=None,
            target="predeclared effect + exact one-sided sign test + V2 guardrail",
            reason=self.reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.study_id,
            "study_hash": self.study_hash,
            "status": self.status.value,
            "evidence_scope": self.evidence_scope,
            "labelled_pair_count": self.labelled_pair_count,
            "decisive_pair_count": self.decisive_pair_count,
            "v2_wins": self.v2_wins,
            "v1_wins": self.v1_wins,
            "tie_both_good": self.tie_both_good,
            "tie_both_bad": self.tie_both_bad,
            "v2_unacceptable_count": self.v2_unacceptable_count,
            "v2_decisive_win_rate": self.v2_decisive_win_rate,
            "v2_unacceptable_rate": self.v2_unacceptable_rate,
            "one_sided_p_value": self.one_sided_p_value,
            "statistical_method": self.statistical_method,
            "predeclared_thresholds": dict(self.predeclared_thresholds),
            "reason": self.reason,
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(frozen=True)
class TermCodeSwitchEvaluation(_EvaluationLineage):
    """Strict, per-span gold evidence for term/code-switch accuracy."""

    schema_version: int
    corpus_id: str
    gold_corpus_hash: str
    complete: bool
    cases: tuple[TermCodeSwitchCase, ...]

    def __post_init__(self) -> None:
        self._validate_lineage()
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise ValueError("term/code-switch evidence schema_version must be 1")
        if not isinstance(self.corpus_id, str) or not self.corpus_id:
            raise ValueError("term/code-switch corpus_id must be non-empty")
        if not isinstance(self.complete, bool):
            raise TypeError("term/code-switch complete must be a boolean")
        object.__setattr__(self, "cases", tuple(self.cases))
        if not self.cases:
            raise ValueError("term/code-switch evidence requires per-case labels")
        _index_unique(self.cases, label="term/code-switch evidence cases")
        expected_hash = _term_gold_hash(self.corpus_id, self.cases)
        if self.gold_corpus_hash != expected_hash:
            raise ValueError("term/code-switch gold_corpus_hash mismatch")


@dataclass(frozen=True)
class CaseEvaluation:
    case_id: str
    passed: bool
    expected: Mapping[str, Any]
    observed: Mapping[str, Any]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "expected": dict(self.expected),
            "observed": dict(self.observed),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    status: GateStatus
    requirement: str
    evidence_scope: str
    sample_count: int
    passed_count: int
    cases: tuple[CaseEvaluation, ...] = ()
    metric_name: str | None = None
    metric_value: float | None = None
    baseline_value: float | None = None
    target: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.gate_id:
            raise ValueError("gate_id must be non-empty")
        if self.sample_count < 0 or self.passed_count < 0:
            raise ValueError("gate counts cannot be negative")
        if self.passed_count > self.sample_count:
            raise ValueError("passed_count cannot exceed sample_count")
        object.__setattr__(self, "cases", tuple(self.cases))

    @property
    def failed_case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases if not case.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status.value,
            "requirement": self.requirement,
            "evidence_scope": self.evidence_scope,
            "sample_count": self.sample_count,
            "passed_count": self.passed_count,
            "failed_count": self.sample_count - self.passed_count,
            "failed_case_ids": list(self.failed_case_ids),
            "cases": [case.to_dict() for case in self.cases],
            "metric": (
                None
                if self.metric_name is None
                else {
                    "name": self.metric_name,
                    "value": self.metric_value,
                    "baseline_value": self.baseline_value,
                    "target": self.target,
                }
            ),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BenchmarkReport:
    schema_version: int
    episode_id: str
    candidate_id: str
    candidate_generation_id: str
    candidate_content_hash: str
    normalized_audio_hash: str
    benchmark_suite_hash: str
    corpus_hashes: tuple[tuple[str, str], ...]
    evaluation_hashes: tuple[tuple[str, str], ...]
    gates: tuple[GateResult, ...]
    scored_case_count: int
    scored_correct_count: int
    safety_case_count: int
    safety_passed_count: int
    known_gold_passed: bool
    release_ready: bool

    def gate(self, gate_id: str) -> GateResult:
        for result in self.gates:
            if result.gate_id == gate_id:
                return result
        raise KeyError(gate_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "candidate_id": self.candidate_id,
            "candidate_generation_id": self.candidate_generation_id,
            "candidate_content_hash": self.candidate_content_hash,
            "normalized_audio_hash": self.normalized_audio_hash,
            "benchmark_suite_hash": self.benchmark_suite_hash,
            "corpus_hashes": dict(self.corpus_hashes),
            "evaluation_hashes": dict(self.evaluation_hashes),
            "summary": {
                "scored_case_count": self.scored_case_count,
                "scored_correct_count": self.scored_correct_count,
                "safety_case_count": self.safety_case_count,
                "safety_passed_count": self.safety_passed_count,
                "known_gold_passed": self.known_gold_passed,
                "release_ready": self.release_ready,
            },
            "gates": [gate.to_dict() for gate in self.gates],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
        )


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CORRECTION_CATEGORIES = {
    "accepted_correction",
    "keep_original",
    "post_seal_freeze",
}
_SCORED_GATE_IDS = {
    "correction_recurrence_zero",
    "keep_original_non_regression",
    "post_seal_text_freeze",
    "semantic_boundary_constraints",
}
_KNOWN_GATE_IDS = _SCORED_GATE_IDS | {
    "candidate_input_integrity",
    "needs_review_safety",
}


def benchmark_suite_hash(
    correction_gold: Mapping[str, Any],
    boundary_gold: Mapping[str, Any],
    review_gold: Mapping[str, Any],
) -> str:
    """Content identity for the exact three-corpus release suite."""

    return hash_object(
        {
            "schema_version": 1,
            "correction_gold_hash": hash_object(correction_gold),
            "boundary_gold_hash": hash_object(boundary_gold),
            "review_gold_hash": hash_object(review_gold),
        }
    )


def process_gold_corpus_hash(value: ProcessGoldCorpus | Mapping[str, Any]) -> str:
    """Identity of exact human process labels and every cited source hash."""

    if isinstance(value, ProcessGoldCorpus):
        payload = value.hash_payload()
    else:
        payload = dict(value)
        payload.pop("gold_corpus_hash", None)
    return hash_object(payload)


def correction_process_trace_hash(
    value: CorrectionProcessEvaluation | Mapping[str, Any],
) -> str:
    """Identity of exact candidate proposal/decision trace plus source hashes."""

    if isinstance(value, CorrectionProcessEvaluation):
        payload = value.trace_payload()
    else:
        payload = dict(value)
        payload.pop("trace_hash", None)
    return hash_object(payload)


def _process_gold_hash(value: ProcessGoldCorpus) -> str:
    return process_gold_corpus_hash(value)


def _process_evidence_trace_hash(value: CorrectionProcessEvaluation) -> str:
    return correction_process_trace_hash(value)


def _blind_gold_hash(corpus_id: str, clips: Sequence[BlindBoundaryClip]) -> str:
    return hash_object(
        {
            "schema_version": 1,
            "evaluation_kind": "blind_boundary",
            "corpus_id": corpus_id,
            "clips": [clip.to_dict() for clip in clips],
        }
    )


def paired_boundary_predeclaration_hash(
    value: PairedBoundaryPredeclaration | Mapping[str, Any],
) -> str:
    """Content identity frozen before either paired candidate is generated."""

    if isinstance(value, PairedBoundaryPredeclaration):
        payload = value.hash_payload()
    else:
        payload = dict(value)
        payload.pop("predeclaration_hash", None)
    return hash_object(payload)


def paired_boundary_candidate_record_hash(
    value: PairedBoundaryCandidate | Mapping[str, Any],
) -> str:
    """Bind candidate bytes, renderer identity, lineage, and every complete clip view."""

    if isinstance(value, PairedBoundaryCandidate):
        payload = value.hash_payload()
    else:
        payload = dict(value)
        payload.pop("candidate_record_hash", None)
    return hash_object(payload)


def paired_boundary_mapping_commitment_hash(
    *,
    study_id: str,
    predeclaration_hash: str,
    entries: Sequence[PairedBoundaryMappingEntry | Mapping[str, Any]],
) -> str:
    """Commit to opaque per-clip A/B mappings; nonces keep two-way mappings hidden."""

    serialised = [
        entry.to_dict() if isinstance(entry, PairedBoundaryMappingEntry) else dict(entry)
        for entry in entries
    ]
    return hash_object(
        {
            "schema_version": 1,
            "evaluation_kind": "paired_boundary_mapping",
            "study_id": study_id,
            "predeclaration_hash": predeclaration_hash,
            "entries": serialised,
        }
    )


def paired_boundary_study_hash(value: PairedBoundaryStudy | Mapping[str, Any]) -> str:
    """Identity of the full predeclaration, candidates, mapping reveal, and human labels."""

    if isinstance(value, PairedBoundaryStudy):
        payload = value.hash_payload()
    else:
        payload = dict(value)
        payload.pop("study_hash", None)
    return hash_object(payload)


def _term_gold_hash(corpus_id: str, cases: Sequence[TermCodeSwitchCase]) -> str:
    return hash_object(
        {
            "schema_version": 1,
            "evaluation_kind": "term_code_switch",
            "corpus_id": corpus_id,
            "cases": [
                {
                    "case_id": case.case_id,
                    "start_ms": case.start_ms,
                    "end_ms": case.end_ms,
                    "expected_text": case.expected_text,
                }
                for case in cases
            ],
        }
    )


_LINEAGE_FIELDS = {
    "candidate_generation_id",
    "candidate_content_hash",
    "normalized_audio_hash",
    "candidate_artifact_id",
    "candidate_artifact_hash",
    "benchmark_suite_hash",
}


def _strict_object(
    value: object,
    *,
    required: set[str],
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    actual = set(value)
    if actual != required:
        missing = sorted(required - actual)
        unknown = sorted(actual - required)
        raise ValueError(f"{label} fields mismatch; missing={missing}, unknown={unknown}")
    return value


def _load_evaluation_payload(value: Mapping[str, Any] | str | Path) -> Mapping[str, Any]:
    return load_json_fixture(value) if isinstance(value, (str, Path)) else value


def load_blind_boundary_evaluation(
    value: Mapping[str, Any] | str | Path,
) -> BlindBoundaryEvaluation:
    """Strictly parse per-case blinded evidence; aggregate-only JSON is invalid."""

    payload = _strict_object(
        _load_evaluation_payload(value),
        required=_LINEAGE_FIELDS
        | {
            "schema_version",
            "evaluation_kind",
            "corpus_id",
            "gold_corpus_hash",
            "blinded",
            "complete",
            "clips",
            "cases",
        },
        label="blind boundary evidence",
    )
    if payload["evaluation_kind"] != "blind_boundary":
        raise ValueError("blind boundary evaluation_kind must be 'blind_boundary'")
    raw_clips = payload["clips"]
    if not isinstance(raw_clips, list) or not raw_clips:
        raise ValueError("blind boundary evidence requires a non-empty clips array")
    clips = tuple(
        BlindBoundaryClip(
            **_strict_object(
                item,
                required={"clip_id", "start_ms", "end_ms"},
                label="blind boundary clip",
            )
        )
        for item in raw_clips
    )
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("blind boundary evidence requires a non-empty cases array")
    cases = tuple(
        BlindBoundaryCase(
            **_strict_object(
                item,
                required={"case_id", "clip_id", "boundary_after_token_id", "accepted"},
                label="blind boundary case",
            )
        )
        for item in raw_cases
    )
    return BlindBoundaryEvaluation(
        schema_version=payload["schema_version"],
        corpus_id=payload["corpus_id"],
        gold_corpus_hash=payload["gold_corpus_hash"],
        blinded=payload["blinded"],
        complete=payload["complete"],
        clips=clips,
        cases=cases,
        **{field: payload[field] for field in _LINEAGE_FIELDS},
    )


def _strict_array(value: object, *, label: str, allow_empty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "an array" if allow_empty else "a non-empty array"
        raise ValueError(f"{label} must be {qualifier}")
    return value


def load_paired_boundary_study(
    value: Mapping[str, Any] | str | Path,
) -> PairedBoundaryStudy:
    """Strictly parse an auditable paired study; totals without labels are rejected."""

    payload = _strict_object(
        _load_evaluation_payload(value),
        required={
            "schema_version",
            "evaluation_kind",
            "study_id",
            "protocol_id",
            "episode_id",
            "lineage_id",
            "normalized_audio_hash",
            "benchmark_suite_hash",
            "complete",
            "candidate_identity_hidden_during_labelling",
            "labels_created_by_humans",
            "predeclaration",
            "candidates",
            "mapping",
            "judgements",
            "study_hash",
        },
        label="paired boundary study",
    )
    raw_predeclaration = _strict_object(
        payload["predeclaration"],
        required={
            "schema_version",
            "study_id",
            "frozen_at_utc",
            "selection_method",
            "selection_independent_of_candidates",
            "sampling_frame_hash",
            "selection_seed_commitment",
            "minimum_clip_count",
            "minimum_decisive_count",
            "one_sided_alpha",
            "minimum_v2_decisive_win_rate",
            "maximum_v2_unacceptable_rate",
            "clips",
            "predeclaration_hash",
        },
        label="paired boundary predeclaration",
    )
    clips = tuple(
        PairedStudyClip(
            **_strict_object(
                item,
                required={
                    "clip_id",
                    "start_ms",
                    "end_ms",
                    "normalized_audio_hash",
                    "audio_clip_hash",
                    "selection_stratum",
                },
                label="paired boundary clip",
            )
        )
        for item in _strict_array(
            raw_predeclaration["clips"], label="paired boundary predeclaration clips"
        )
    )
    predeclaration = PairedBoundaryPredeclaration(
        **{key: raw_predeclaration[key] for key in raw_predeclaration if key != "clips"},
        clips=clips,
    )

    candidates: list[PairedBoundaryCandidate] = []
    for raw_candidate_value in _strict_array(
        payload["candidates"], label="paired boundary candidates"
    ):
        raw_candidate = _strict_object(
            raw_candidate_value,
            required={
                "system",
                "candidate_id",
                "generation_id",
                "canonical_content_hash",
                "token_sequence_hash",
                "candidate_artifact_hash",
                "subtitle_bytes_hash",
                "renderer_identity_hash",
                "normalized_audio_hash",
                "predeclaration_hash",
                "generated_at_utc",
                "clip_presentations",
                "candidate_record_hash",
            },
            label="paired boundary candidate",
        )
        presentations = tuple(
            PairedClipPresentation(
                **_strict_object(
                    item,
                    required={
                        "clip_id",
                        "presentation_artifact_hash",
                        "cue_set_hash",
                        "cue_count",
                    },
                    label="paired boundary clip presentation",
                )
            )
            for item in _strict_array(
                raw_candidate["clip_presentations"],
                label="paired boundary candidate clip_presentations",
            )
        )
        candidates.append(
            PairedBoundaryCandidate(
                **{key: raw_candidate[key] for key in raw_candidate if key != "clip_presentations"},
                clip_presentations=presentations,
            )
        )

    raw_mapping = _strict_object(
        payload["mapping"],
        required={"commitment_hash", "committed_at_utc", "randomization_method", "reveal"},
        label="paired boundary mapping",
    )
    reveal: PairedBoundaryMappingReveal | None = None
    if raw_mapping["reveal"] is not None:
        raw_reveal = _strict_object(
            raw_mapping["reveal"],
            required={"revealed_at_utc", "labels_completed_at_utc", "entries"},
            label="paired boundary mapping reveal",
        )
        entries = tuple(
            PairedBoundaryMappingEntry(
                **_strict_object(
                    item,
                    required={
                        "clip_id",
                        "a_candidate_record_hash",
                        "b_candidate_record_hash",
                        "nonce",
                    },
                    label="paired boundary mapping entry",
                )
            )
            for item in _strict_array(
                raw_reveal["entries"], label="paired boundary mapping reveal entries"
            )
        )
        reveal = PairedBoundaryMappingReveal(
            revealed_at_utc=raw_reveal["revealed_at_utc"],
            labels_completed_at_utc=raw_reveal["labels_completed_at_utc"],
            entries=entries,
        )
    mapping = PairedBoundaryMapping(
        commitment_hash=raw_mapping["commitment_hash"],
        committed_at_utc=raw_mapping["committed_at_utc"],
        randomization_method=raw_mapping["randomization_method"],
        reveal=reveal,
    )
    judgements = tuple(
        PairedBoundaryJudgement(
            **_strict_object(
                item,
                required={
                    "clip_id",
                    "evaluator_id",
                    "outcome",
                    "a_unacceptable",
                    "b_unacceptable",
                    "a_presentation_artifact_hash",
                    "b_presentation_artifact_hash",
                    "mapping_commitment_hash",
                    "submitted_at_utc",
                },
                label="paired boundary judgement",
            )
        )
        for item in _strict_array(
            payload["judgements"], label="paired boundary judgements", allow_empty=True
        )
    )
    return PairedBoundaryStudy(
        schema_version=payload["schema_version"],
        evaluation_kind=payload["evaluation_kind"],
        study_id=payload["study_id"],
        protocol_id=payload["protocol_id"],
        episode_id=payload["episode_id"],
        lineage_id=payload["lineage_id"],
        normalized_audio_hash=payload["normalized_audio_hash"],
        benchmark_suite_hash=payload["benchmark_suite_hash"],
        complete=payload["complete"],
        candidate_identity_hidden_during_labelling=payload[
            "candidate_identity_hidden_during_labelling"
        ],
        labels_created_by_humans=payload["labels_created_by_humans"],
        predeclaration=predeclaration,
        candidates=tuple(candidates),
        mapping=mapping,
        judgements=judgements,
        study_hash=payload["study_hash"],
    )


def load_term_code_switch_evaluation(
    value: Mapping[str, Any] | str | Path,
) -> TermCodeSwitchEvaluation:
    """Strictly parse per-case term evidence; caller-supplied totals are forbidden."""

    payload = _strict_object(
        _load_evaluation_payload(value),
        required=_LINEAGE_FIELDS
        | {
            "schema_version",
            "evaluation_kind",
            "corpus_id",
            "gold_corpus_hash",
            "complete",
            "cases",
        },
        label="term/code-switch evidence",
    )
    if payload["evaluation_kind"] != "term_code_switch":
        raise ValueError("term/code-switch evaluation_kind must be 'term_code_switch'")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("term/code-switch evidence requires a non-empty cases array")
    cases = tuple(
        TermCodeSwitchCase(
            **_strict_object(
                item,
                required={
                    "case_id",
                    "start_ms",
                    "end_ms",
                    "expected_text",
                    "observed_text",
                },
                label="term/code-switch case",
            )
        )
        for item in raw_cases
    )
    return TermCodeSwitchEvaluation(
        schema_version=payload["schema_version"],
        corpus_id=payload["corpus_id"],
        gold_corpus_hash=payload["gold_corpus_hash"],
        complete=payload["complete"],
        cases=cases,
        **{field: payload[field] for field in _LINEAGE_FIELDS},
    )


def load_process_gold(
    value: Mapping[str, Any] | str | Path,
) -> ProcessGoldCorpus:
    """Strictly parse human correction-process gold; unknown fields are invalid."""

    payload = _strict_object(
        _load_evaluation_payload(value),
        required={
            "schema_version",
            "evaluation_kind",
            "corpus_id",
            "episode_id",
            "lineage_id",
            "normalized_audio_hash",
            "gold_corpus_hash",
            "complete",
            "expected_case_count",
            "adjudication",
            "sources",
            "cases",
        },
        label="correction process gold",
    )
    if payload["evaluation_kind"] != "correction_process_gold":
        raise ValueError(
            "correction process gold evaluation_kind must be 'correction_process_gold'"
        )
    adjudication_payload = _strict_object(
        payload["adjudication"],
        required={"protocol_id", "adjudicator_ids", "status", "blinded_to_candidate"},
        label="correction process adjudication",
    )
    raw_sources = payload["sources"]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("correction process gold requires a non-empty sources array")
    sources = tuple(
        ProcessGoldSource(
            **_strict_object(
                item,
                required={"artifact_id", "role", "sha256"},
                label="correction process gold source",
            )
        )
        for item in raw_sources
    )
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("correction process gold requires a non-empty cases array")
    cases = tuple(
        ProcessGoldCase(
            **_strict_object(
                item,
                required={
                    "case_id",
                    "lineage_id",
                    "normalized_audio_hash",
                    "audio_span_ids",
                    "evidence_token_ids",
                    "start_ms",
                    "end_ms",
                    "observed_text",
                    "expected_action",
                    "expected_replacement",
                    "source_artifact_ids",
                    "adjudication_locator",
                },
                label="correction process gold case",
            )
        )
        for item in raw_cases
    )
    return ProcessGoldCorpus(
        schema_version=payload["schema_version"],
        corpus_id=payload["corpus_id"],
        episode_id=payload["episode_id"],
        lineage_id=payload["lineage_id"],
        normalized_audio_hash=payload["normalized_audio_hash"],
        gold_corpus_hash=payload["gold_corpus_hash"],
        complete=payload["complete"],
        expected_case_count=payload["expected_case_count"],
        adjudication=ProcessAdjudication(**adjudication_payload),
        sources=sources,
        cases=cases,
    )


def load_correction_process_evaluation(
    value: Mapping[str, Any] | str | Path,
) -> CorrectionProcessEvaluation:
    """Strictly parse a candidate trace; aggregate process counts are forbidden."""

    payload = _strict_object(
        _load_evaluation_payload(value),
        required=_LINEAGE_FIELDS
        | {
            "schema_version",
            "evaluation_kind",
            "process_gold_hash",
            "trace_hash",
            "complete",
            "expected_proposal_count",
            "expected_decision_count",
            "covered_case_ids",
            "source_artifacts",
            "proposals",
            "decisions",
        },
        label="correction process evidence",
    )
    if payload["evaluation_kind"] != "correction_process":
        raise ValueError("correction process evidence evaluation_kind must be 'correction_process'")
    raw_source_artifacts = payload["source_artifacts"]
    if not isinstance(raw_source_artifacts, list) or not raw_source_artifacts:
        raise ValueError("correction process evidence requires source_artifacts")
    source_artifacts = tuple(
        ProcessEvidenceArtifact(
            **_strict_object(
                item,
                required={"artifact_id", "role", "sha256"},
                label="correction process source artifact",
            )
        )
        for item in raw_source_artifacts
    )
    raw_proposals = payload["proposals"]
    if not isinstance(raw_proposals, list):
        raise ValueError("correction process proposals must be an array")
    proposals = tuple(
        ProcessProposalTrace(
            **_strict_object(
                item,
                required={
                    "proposal_id",
                    "generation_id",
                    "audio_span_ids",
                    "evidence_token_ids",
                    "start_ms",
                    "end_ms",
                    "observed_text",
                    "candidate_text",
                    "source",
                    "proposal_hash",
                },
                label="correction process proposal",
            )
        )
        for item in raw_proposals
    )
    raw_decisions = payload["decisions"]
    if not isinstance(raw_decisions, list):
        raise ValueError("correction process decisions must be an array")
    if isinstance(payload["schema_version"], bool) or payload["schema_version"] not in {
        1,
        2,
    }:
        raise ValueError("correction process evidence schema_version must be exact 1 or 2")
    legacy_fields = {
        "sequence",
        "event_id",
        "parent_generation_id",
        "resulting_generation_id",
        "ledger_entry_hash",
        "decision_hash",
        "target_span_ids",
        "target_start_ms",
        "target_end_ms",
        "proposal_ids",
        "action",
        "replacement_text",
        "selected_candidate",
    }
    common_fields = legacy_fields - {
        "proposal_ids",
        "replacement_text",
        "selected_candidate",
    }
    native_fields = common_fields | {
        "decision_family",
        "candidate_discovery_id",
        "candidate_discovery_hash",
        "candidate_literal_sha256",
        "authorized_literal_sha256",
        "authorization_id",
        "authorization_hash",
    }
    decisions_list: list[ProcessDecisionTrace] = []
    for raw_item in raw_decisions:
        if payload["schema_version"] == 1:
            item = _strict_object(
                raw_item,
                required=legacy_fields,
                label="correction process decision",
            )
            family = "legacy_correction_v1"
        else:
            if not isinstance(raw_item, Mapping):
                raise ValueError("correction process decision must be a JSON object")
            family = raw_item.get("decision_family")
            if family == "native_correction_v2" and "proposal_ids" in raw_item:
                raise ValueError(
                    "native correction process decision cannot carry legacy proposal_ids"
                )
            required = (
                legacy_fields | {"decision_family"}
                if family == "legacy_correction_v1"
                else native_fields
                if family == "native_correction_v2"
                else set()
            )
            if not required:
                raise ValueError("correction process decision has unknown decision_family")
            item = _strict_object(
                raw_item,
                required=required,
                label="correction process decision",
            )
        decision_values = dict(item)
        decision_values.pop("decision_family", None)
        if family == "native_correction_v2":
            decision_values.update(
                proposal_ids=(),
                replacement_text=None,
                selected_candidate=None,
            )
        decisions_list.append(
            ProcessDecisionTrace(
                **decision_values,
                decision_family=family,
                trace_schema_version=payload["schema_version"],
            )
        )
    decisions = tuple(decisions_list)
    covered_case_ids = payload["covered_case_ids"]
    if not isinstance(covered_case_ids, list):
        raise ValueError("correction process covered_case_ids must be an array")
    return CorrectionProcessEvaluation(
        schema_version=payload["schema_version"],
        process_gold_hash=payload["process_gold_hash"],
        trace_hash=payload["trace_hash"],
        complete=payload["complete"],
        expected_proposal_count=payload["expected_proposal_count"],
        expected_decision_count=payload["expected_decision_count"],
        covered_case_ids=tuple(covered_case_ids),
        source_artifacts=source_artifacts,
        proposals=proposals,
        decisions=decisions,
        **{field: payload[field] for field in _LINEAGE_FIELDS},
    )


def load_json_fixture(path: str | Path) -> dict[str, Any]:
    """Load one fixture without interpreting it or accessing external sources."""

    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"fixture {path!s} contains duplicate JSON key: {key!r}")
            result[key] = item
        return result

    with Path(path).open("r", encoding="utf-8") as stream:
        value = json.load(stream, object_pairs_hook=strict_object)
    if not isinstance(value, dict):
        raise ValueError(f"fixture {path!s} must contain a JSON object")
    return value


def _index_unique(
    items: Iterable[Any],
    *,
    label: str,
    id_field: str = "case_id",
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        case_id = getattr(item, id_field) if hasattr(item, id_field) else item.get(id_field)
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"{label} must have non-empty {id_field} values")
        if case_id in result:
            raise ValueError(f"duplicate {label} {id_field}: {case_id}")
        result[case_id] = item
    return result


def _require_common_corpus(corpus: Mapping[str, Any], *, label: str) -> list[Mapping[str, Any]]:
    if corpus.get("schema_version") != 1:
        raise ValueError(f"{label} schema_version must be 1")
    if not isinstance(corpus.get("episode_id"), str) or not corpus["episode_id"]:
        raise ValueError(f"{label} episode_id must be non-empty")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{label} cases must be a non-empty list")
    if any(not isinstance(case, Mapping) for case in cases):
        raise ValueError(f"{label} cases must be objects")
    _index_unique(cases, label=f"{label} cases")
    return cases


def _validate_correction_corpus(corpus: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    cases = _require_common_corpus(corpus, label="correction corpus")
    if not isinstance(corpus.get("corpus_id"), str) or not corpus["corpus_id"]:
        raise ValueError("correction corpus corpus_id must be non-empty")
    sources = corpus.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("correction corpus sources must be a non-empty list")
    source_by_id: dict[str, Mapping[str, Any]] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            raise ValueError("correction corpus sources must be objects")
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("correction source_id must be non-empty")
        if source_id in source_by_id:
            raise ValueError(f"duplicate correction source_id: {source_id}")
        digest = source.get("sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ValueError(f"correction source {source_id} must have lowercase sha256")
        if not isinstance(source.get("path"), str) or not source["path"]:
            raise ValueError(f"correction source {source_id} must have a path")
        source_by_id[source_id] = source

    for case in cases:
        case_id = case["case_id"]
        if case.get("category") not in _CORRECTION_CATEGORIES:
            raise ValueError(f"correction case {case_id} has unsupported category")
        start_ms = case.get("start_ms")
        end_ms = case.get("end_ms")
        if (
            isinstance(start_ms, bool)
            or not isinstance(start_ms, int)
            or isinstance(end_ms, bool)
            or not isinstance(end_ms, int)
            or start_ms < 0
            or end_ms <= start_ms
        ):
            raise ValueError(f"correction case {case_id} has invalid time bounds")
        expected = case.get("expected_text")
        if not isinstance(expected, str) or not expected:
            raise ValueError(f"correction case {case_id} expected_text must be non-empty")
        forbidden = case.get("forbidden_texts")
        if not isinstance(forbidden, list) or any(not isinstance(text, str) for text in forbidden):
            raise ValueError(f"correction case {case_id} forbidden_texts must be strings")
        if expected in forbidden:
            raise ValueError(f"correction case {case_id} forbids its expected text")
        verification = case.get("verification")
        if not isinstance(verification, Mapping):
            raise ValueError(f"correction case {case_id} lacks verification provenance")
        if not isinstance(verification.get("level"), str) or not verification["level"]:
            raise ValueError(f"correction case {case_id} lacks verification level")
        if not isinstance(verification.get("locator"), str) or not verification["locator"]:
            raise ValueError(f"correction case {case_id} lacks verification locator")
        source_ids = verification.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            raise ValueError(f"correction case {case_id} lacks verification source_ids")
        unknown_sources = set(source_ids) - set(source_by_id)
        if unknown_sources:
            raise ValueError(
                f"correction case {case_id} references unknown sources: {sorted(unknown_sources)}"
            )
    return cases


def _validate_boundary_corpus(corpus: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    cases = _require_common_corpus(corpus, label="boundary corpus")
    for case in cases:
        case_id = case["case_id"]
        lexemes = case.get("lexemes")
        if (
            not isinstance(lexemes, list)
            or not lexemes
            or any(not isinstance(lexeme, str) or not lexeme for lexeme in lexemes)
        ):
            raise ValueError(f"boundary case {case_id} lexemes must be non-empty strings")
        if "".join(lexemes) != case.get("canonical_text"):
            raise ValueError(f"boundary case {case_id} lexemes do not reconstruct canonical_text")
        forbidden = case.get("forbidden_breaks")
        if not isinstance(forbidden, list) or any(
            isinstance(position, bool) or not isinstance(position, int) for position in forbidden
        ):
            raise ValueError(f"boundary case {case_id} forbidden_breaks must be integers")
        if len(set(forbidden)) != len(forbidden) or any(
            position <= 0 or position >= len(lexemes) for position in forbidden
        ):
            raise ValueError(f"boundary case {case_id} has invalid forbidden_breaks")
    return cases


def _validate_review_corpus(corpus: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    cases = _require_common_corpus(corpus, label="review corpus")
    for case in cases:
        case_id = case["case_id"]
        if case.get("expected_outcome") != ObservationOutcome.NEEDS_REVIEW.value:
            raise ValueError(f"review case {case_id} must expect needs_review")
    return cases


def _case_gate(
    *,
    gate_id: str,
    requirement: str,
    cases: Sequence[CaseEvaluation],
) -> GateResult:
    typed = tuple(cases)
    passed_count = sum(case.passed for case in typed)
    if not typed:
        return GateResult(
            gate_id=gate_id,
            status=GateStatus.NOT_EVALUATED,
            requirement=requirement,
            evidence_scope="missing_adjudicated_gold",
            sample_count=0,
            passed_count=0,
            reason="the mandatory gold category contains no adjudicated cases",
        )
    return GateResult(
        gate_id=gate_id,
        status=GateStatus.PASSED if passed_count == len(typed) else GateStatus.FAILED,
        requirement=requirement,
        evidence_scope="adjudicated_gold",
        sample_count=len(typed),
        passed_count=passed_count,
        cases=typed,
    )


def _text_case_evaluation(
    gold: Mapping[str, Any],
    observation: TextObservation | None,
) -> CaseEvaluation:
    expected_text = gold["expected_text"]
    forbidden = tuple(gold["forbidden_texts"])
    expected = {
        "outcome": ObservationOutcome.ACCEPTED.value,
        "text": expected_text,
        "forbidden_texts": list(forbidden),
    }
    if observation is None:
        return CaseEvaluation(
            case_id=gold["case_id"],
            passed=False,
            expected=expected,
            observed={"outcome": "missing", "text": None},
            reason="required gold case was not observed",
        )
    observed = {"outcome": observation.outcome.value, "text": observation.text}
    if observation.outcome is not ObservationOutcome.ACCEPTED:
        return CaseEvaluation(
            case_id=gold["case_id"],
            passed=False,
            expected=expected,
            observed=observed,
            reason=(
                "needs_review is safety-preserving but is not correct for an "
                "already adjudicated case"
                if observation.outcome is ObservationOutcome.NEEDS_REVIEW
                else "candidate rejected an already adjudicated case"
            ),
        )
    if observation.text in forbidden:
        return CaseEvaluation(
            case_id=gold["case_id"],
            passed=False,
            expected=expected,
            observed=observed,
            reason="candidate reproduced a forbidden V1 value",
        )
    if observation.text != expected_text:
        return CaseEvaluation(
            case_id=gold["case_id"],
            passed=False,
            expected=expected,
            observed=observed,
            reason="candidate text is not an exact match for the adjudicated target span",
        )
    return CaseEvaluation(
        case_id=gold["case_id"],
        passed=True,
        expected=expected,
        observed=observed,
        reason="exact adjudicated text preserved",
    )


def _blind_boundary_gate(result: BlindBoundaryEvaluation | None) -> GateResult:
    requirement = (
        "Legacy single-candidate boundary rejection diagnostic; this gate cannot establish "
        "V2 superiority or authorize release."
    )
    unavailable_reason = None
    if result is None:
        unavailable_reason = "no blinded human boundary evaluation was supplied"
    elif not result.blinded:
        unavailable_reason = "evaluation was not blinded"
    elif not result.complete:
        unavailable_reason = "human boundary labelling is incomplete"
    elif result.clip_count != 3:
        unavailable_reason = "ADR-056 requires exactly the three frozen problem clips"
    if unavailable_reason is not None:
        cases = ()
        sample_count = 0
        passed_count = 0
        if result is not None:
            cases = tuple(
                CaseEvaluation(
                    case_id=case.case_id,
                    passed=False,
                    expected={"complete_blinded_study": True},
                    observed={
                        "blinded": result.blinded,
                        "complete": result.complete,
                        "human_accepted": case.accepted,
                    },
                    reason=unavailable_reason,
                )
                for case in result.cases
            )
            sample_count = len(cases)
        return GateResult(
            gate_id="blind_boundary_rejection_rate_lt_5_percent",
            status=GateStatus.NOT_EVALUATED,
            requirement=requirement,
            evidence_scope="missing_or_incomplete_blind_labels",
            sample_count=sample_count,
            passed_count=passed_count,
            cases=cases,
            metric_name="boundary_rejection_rate",
            metric_value=None,
            baseline_value=None,
            target="<0.05",
            reason=unavailable_reason,
        )
    labelled_boundaries = len(result.cases)
    rejected_boundaries = sum(not case.accepted for case in result.cases)
    rate = rejected_boundaries / labelled_boundaries
    passed = rate < 0.05
    cases = tuple(
        CaseEvaluation(
            case_id=case.case_id,
            passed=case.accepted,
            expected={"human_accepted": True, "clip_id": case.clip_id},
            observed={
                "human_accepted": case.accepted,
                "boundary_after_token_id": case.boundary_after_token_id,
            },
            reason=(
                "blinded evaluator accepted the displayed boundary"
                if case.accepted
                else "blinded evaluator rejected the displayed boundary"
            ),
        )
        for case in result.cases
    )
    return GateResult(
        gate_id="blind_boundary_rejection_rate_lt_5_percent",
        status=GateStatus.PASSED if passed else GateStatus.FAILED,
        requirement=requirement,
        evidence_scope=f"complete_blind_human_labels:{result.corpus_id}",
        sample_count=labelled_boundaries,
        passed_count=labelled_boundaries - rejected_boundaries,
        cases=cases,
        metric_name="boundary_rejection_rate",
        metric_value=rate,
        baseline_value=None,
        target="<0.05",
        reason=None if passed else "measured rejection rate did not meet the strict target",
    )


def _one_sided_sign_test_p_value(*, wins: int, losses: int) -> float:
    """Exact P[X >= wins] for X~Binomial(wins+losses, 0.5)."""

    total = wins + losses
    if total <= 0:
        raise ValueError("sign test requires at least one decisive pair")
    return sum(math.comb(total, count) for count in range(wins, total + 1)) / (2**total)


_PAIRED_BOUNDARY_STATISTICAL_METHOD = (
    "exact_one_sided_paired_sign_test_binomial_p0_0.5_ties_excluded"
)


def _paired_thresholds(
    study: PairedBoundaryStudy,
) -> tuple[tuple[str, int | float], ...]:
    declaration = study.predeclaration
    return (
        ("minimum_clip_count", declaration.minimum_clip_count),
        ("minimum_decisive_count", declaration.minimum_decisive_count),
        ("one_sided_alpha", declaration.one_sided_alpha),
        ("minimum_v2_decisive_win_rate", declaration.minimum_v2_decisive_win_rate),
        ("maximum_v2_unacceptable_rate", declaration.maximum_v2_unacceptable_rate),
    )


def _paired_not_evaluated(
    study: PairedBoundaryStudy,
    *,
    reason: str,
) -> PairedBoundaryComparisonReport:
    return PairedBoundaryComparisonReport(
        study_id=study.study_id,
        study_hash=study.study_hash,
        status=GateStatus.NOT_EVALUATED,
        evidence_scope="one_episode_predeclared_nonoverlapping_clips",
        labelled_pair_count=len(study.judgements),
        decisive_pair_count=0,
        v2_wins=0,
        v1_wins=0,
        tie_both_good=0,
        tie_both_bad=0,
        v2_unacceptable_count=0,
        v2_decisive_win_rate=None,
        v2_unacceptable_rate=None,
        one_sided_p_value=None,
        reason=reason,
        cases=(),
        statistical_method=_PAIRED_BOUNDARY_STATISTICAL_METHOD,
        predeclared_thresholds=_paired_thresholds(study),
    )


def evaluate_paired_boundary_study(
    study: PairedBoundaryStudy | Mapping[str, Any] | str | Path,
) -> PairedBoundaryComparisonReport:
    """Compare exact V1/V2 clip views without a fixed or self-reported baseline."""

    if not isinstance(study, PairedBoundaryStudy):
        study = load_paired_boundary_study(study)
    if not study.complete:
        return _paired_not_evaluated(study, reason="paired human labels are incomplete")
    if not study.predeclaration.selection_independent_of_candidates:
        return _paired_not_evaluated(
            study,
            reason="clip selection was not declared independent of candidate outputs",
        )
    if not study.candidate_identity_hidden_during_labelling:
        return _paired_not_evaluated(
            study,
            reason="candidate identity was visible during human labelling",
        )
    if not study.labels_created_by_humans:
        return _paired_not_evaluated(
            study,
            reason="labels are not attested as human judgements",
        )
    if study.mapping.reveal is None:
        return _paired_not_evaluated(study, reason="opaque A/B mapping has not been revealed")
    if len(study.predeclaration.clips) < study.predeclaration.minimum_clip_count:
        return _paired_not_evaluated(
            study,
            reason=(
                "predeclared clip sample is below the frozen minimum: "
                f"{len(study.predeclaration.clips)} < {study.predeclaration.minimum_clip_count}"
            ),
        )

    candidate_by_hash = {item.candidate_record_hash: item for item in study.candidates}
    entry_by_clip = {item.clip_id: item for item in study.mapping.reveal.entries}
    v2_wins = 0
    v1_wins = 0
    tie_both_good = 0
    tie_both_bad = 0
    v2_unacceptable_count = 0
    cases: list[CaseEvaluation] = []
    for judgement in study.judgements:
        entry = entry_by_clip[judgement.clip_id]
        a_system = candidate_by_hash[entry.a_candidate_record_hash].system
        b_system = candidate_by_hash[entry.b_candidate_record_hash].system
        if judgement.outcome is PairedBoundaryOutcome.A_BETTER:
            winner = a_system
        elif judgement.outcome is PairedBoundaryOutcome.B_BETTER:
            winner = b_system
        else:
            winner = None
        if winner == "v2":
            v2_wins += 1
            comparison = "v2_better"
        elif winner == "v1":
            v1_wins += 1
            comparison = "v1_better"
        elif judgement.outcome is PairedBoundaryOutcome.TIE_BOTH_GOOD:
            tie_both_good += 1
            comparison = PairedBoundaryOutcome.TIE_BOTH_GOOD.value
        else:
            tie_both_bad += 1
            comparison = PairedBoundaryOutcome.TIE_BOTH_BAD.value
        v2_unacceptable = judgement.a_unacceptable if a_system == "v2" else judgement.b_unacceptable
        v2_unacceptable_count += int(v2_unacceptable)
        case_passed = comparison in {"v2_better", "tie_both_good"} and not v2_unacceptable
        cases.append(
            CaseEvaluation(
                case_id=judgement.clip_id,
                passed=case_passed,
                expected={
                    "comparison": "v2_better_or_tie_both_good",
                    "v2_unacceptable": False,
                },
                observed={
                    "comparison": comparison,
                    "v2_unacceptable": v2_unacceptable,
                    "evaluator_id": judgement.evaluator_id,
                },
                reason=(
                    "human preferred V2 and V2 passed the unacceptable guardrail"
                    if comparison == "v2_better" and not v2_unacceptable
                    else (
                        "both complete cue sets were judged good"
                        if comparison == "tie_both_good"
                        else (
                            "V2 was marked unacceptable"
                            if v2_unacceptable
                            else "human did not prefer V2 for this paired clip"
                        )
                    )
                ),
            )
        )

    labelled = len(study.judgements)
    decisive = v2_wins + v1_wins
    unacceptable_rate = v2_unacceptable_count / labelled
    guardrail_passed = unacceptable_rate <= study.predeclaration.maximum_v2_unacceptable_rate
    if not guardrail_passed:
        return PairedBoundaryComparisonReport(
            study_id=study.study_id,
            study_hash=study.study_hash,
            status=GateStatus.FAILED,
            evidence_scope="one_episode_predeclared_nonoverlapping_clips",
            labelled_pair_count=labelled,
            decisive_pair_count=decisive,
            v2_wins=v2_wins,
            v1_wins=v1_wins,
            tie_both_good=tie_both_good,
            tie_both_bad=tie_both_bad,
            v2_unacceptable_count=v2_unacceptable_count,
            v2_decisive_win_rate=(v2_wins / decisive if decisive else None),
            v2_unacceptable_rate=unacceptable_rate,
            one_sided_p_value=(
                _one_sided_sign_test_p_value(wins=v2_wins, losses=v1_wins) if decisive else None
            ),
            reason=(
                "V2 unacceptable rate exceeded the frozen guardrail: "
                f"{unacceptable_rate:.6f} > "
                f"{study.predeclaration.maximum_v2_unacceptable_rate:.6f}"
            ),
            cases=tuple(cases),
            statistical_method=_PAIRED_BOUNDARY_STATISTICAL_METHOD,
            predeclared_thresholds=_paired_thresholds(study),
        )
    if decisive < study.predeclaration.minimum_decisive_count:
        return PairedBoundaryComparisonReport(
            study_id=study.study_id,
            study_hash=study.study_hash,
            status=GateStatus.NOT_EVALUATED,
            evidence_scope="one_episode_predeclared_nonoverlapping_clips",
            labelled_pair_count=labelled,
            decisive_pair_count=decisive,
            v2_wins=v2_wins,
            v1_wins=v1_wins,
            tie_both_good=tie_both_good,
            tie_both_bad=tie_both_bad,
            v2_unacceptable_count=v2_unacceptable_count,
            v2_decisive_win_rate=(v2_wins / decisive if decisive else None),
            v2_unacceptable_rate=unacceptable_rate,
            one_sided_p_value=None,
            reason=(
                "decisive paired labels are below the frozen minimum: "
                f"{decisive} < {study.predeclaration.minimum_decisive_count}"
            ),
            cases=tuple(cases),
            statistical_method=_PAIRED_BOUNDARY_STATISTICAL_METHOD,
            predeclared_thresholds=_paired_thresholds(study),
        )
    win_rate = v2_wins / decisive
    p_value = _one_sided_sign_test_p_value(wins=v2_wins, losses=v1_wins)
    superiority_passed = (
        win_rate >= study.predeclaration.minimum_v2_decisive_win_rate
        and p_value <= study.predeclaration.one_sided_alpha
    )
    passed = guardrail_passed and superiority_passed
    if not superiority_passed:
        reason = (
            "V2 did not meet both frozen superiority thresholds: "
            f"win_rate={win_rate:.6f}, exact_one_sided_p={p_value:.6f}"
        )
    else:
        reason = None
    return PairedBoundaryComparisonReport(
        study_id=study.study_id,
        study_hash=study.study_hash,
        status=GateStatus.PASSED if passed else GateStatus.FAILED,
        evidence_scope="one_episode_predeclared_nonoverlapping_clips",
        labelled_pair_count=labelled,
        decisive_pair_count=decisive,
        v2_wins=v2_wins,
        v1_wins=v1_wins,
        tie_both_good=tie_both_good,
        tie_both_bad=tie_both_bad,
        v2_unacceptable_count=v2_unacceptable_count,
        v2_decisive_win_rate=win_rate,
        v2_unacceptable_rate=unacceptable_rate,
        one_sided_p_value=p_value,
        reason=reason,
        cases=tuple(cases),
        statistical_method=_PAIRED_BOUNDARY_STATISTICAL_METHOD,
        predeclared_thresholds=_paired_thresholds(study),
    )


def _term_code_switch_gate(result: TermCodeSwitchEvaluation | None) -> GateResult:
    requirement = "Complete gold corpus term and code-switch accuracy must be 100%."
    if result is None or not result.complete:
        reason = (
            "no labelled term/code-switch corpus was supplied"
            if result is None
            else "term/code-switch labels are incomplete or empty"
        )
        cases = ()
        sample_count = 0
        if result is not None:
            cases = tuple(
                CaseEvaluation(
                    case_id=case.case_id,
                    passed=False,
                    expected={"complete_gold_study": True, "text": case.expected_text},
                    observed={"complete": result.complete, "text": case.observed_text},
                    reason=reason,
                )
                for case in result.cases
            )
            sample_count = len(cases)
        return GateResult(
            gate_id="gold_term_code_switch_accuracy_100_percent",
            status=GateStatus.NOT_EVALUATED,
            requirement=requirement,
            evidence_scope="missing_or_incomplete_gold_labels",
            sample_count=sample_count,
            passed_count=0,
            cases=cases,
            metric_name="accuracy",
            metric_value=None,
            target="1.0",
            reason=reason,
        )
    cases = tuple(
        CaseEvaluation(
            case_id=case.case_id,
            passed=case.observed_text == case.expected_text,
            expected={
                "text": case.expected_text,
                "start_ms": case.start_ms,
                "end_ms": case.end_ms,
            },
            observed={"text": case.observed_text},
            reason=(
                "candidate exactly preserved the labelled term/code-switch"
                if case.observed_text == case.expected_text
                else "candidate did not exactly match the labelled term/code-switch"
            ),
        )
        for case in result.cases
    )
    correct_count = sum(case.passed for case in cases)
    total_count = len(cases)
    accuracy = correct_count / total_count
    passed = correct_count == total_count
    return GateResult(
        gate_id="gold_term_code_switch_accuracy_100_percent",
        status=GateStatus.PASSED if passed else GateStatus.FAILED,
        requirement=requirement,
        evidence_scope=f"complete_labelled_gold:{result.corpus_id}",
        sample_count=total_count,
        passed_count=correct_count,
        cases=cases,
        metric_name="accuracy",
        metric_value=accuracy,
        target="1.0",
        reason=None if passed else "one or more labelled terms/code-switches were incorrect",
    )


def _process_metric_not_evaluated(gate_id: str, requirement: str, reason: str) -> GateResult:
    return GateResult(
        gate_id=gate_id,
        status=GateStatus.NOT_EVALUATED,
        requirement=requirement,
        evidence_scope="missing_proposal_and_decision_trace_gold",
        sample_count=0,
        passed_count=0,
        reason=reason,
    )


_PROCESS_GATE_REQUIREMENTS = {
    "accepted_correction_detection_recall": (
        "Every accepted-correction gold case must emit a changed correction proposal."
    ),
    "accepted_correction_false_negative_rate_zero": (
        "Every accepted-correction gold case must apply its exact adjudicated replacement."
    ),
    "keep_original_false_proposal_rate_zero": (
        "Keep-original cases must receive no speculative changed proposal."
    ),
    "harmful_apply_rate_zero": (
        "No decision may apply text that contradicts the adjudicated process gold."
    ),
}


def _process_not_evaluated_gates(reason: str) -> tuple[GateResult, ...]:
    return tuple(
        _process_metric_not_evaluated(gate_id, requirement, reason)
        for gate_id, requirement in _PROCESS_GATE_REQUIREMENTS.items()
    )


def _ranges_overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return left_start < right_end and right_start < left_end


def _proposal_matches_process_case(
    proposal: ProcessProposalTrace,
    case: ProcessGoldCase,
) -> bool:
    return (
        proposal.audio_span_ids == case.audio_span_ids
        and proposal.evidence_token_ids == case.evidence_token_ids
        and proposal.start_ms == case.start_ms
        and proposal.end_ms == case.end_ms
        and proposal.observed_text == case.observed_text
    )


def _decision_matches_process_case(
    decision: ProcessDecisionTrace,
    case: ProcessGoldCase,
) -> bool:
    return (
        decision.target_span_ids == case.audio_span_ids
        and decision.target_start_ms == case.start_ms
        and decision.target_end_ms == case.end_ms
    )


def _applied_text(decision: ProcessDecisionTrace) -> str | None:
    if decision.action == "replace":
        return decision.replacement_text
    if decision.action == "accept_candidate":
        return decision.selected_candidate
    return None


def _metric_gate(
    *,
    gate_id: str,
    cases: Sequence[CaseEvaluation],
    metric_name: str,
    failure_rate: bool,
) -> GateResult:
    typed = tuple(cases)
    requirement = _PROCESS_GATE_REQUIREMENTS[gate_id]
    if not typed:
        return GateResult(
            gate_id=gate_id,
            status=GateStatus.NOT_EVALUATED,
            requirement=requirement,
            evidence_scope="complete_process_gold_missing_required_label_class",
            sample_count=0,
            passed_count=0,
            metric_name=metric_name,
            metric_value=None,
            target="0.0" if failure_rate else "1.0",
            reason="complete process gold has no cases for this mandatory metric",
        )
    passed_count = sum(item.passed for item in typed)
    failures = len(typed) - passed_count
    metric_value = failures / len(typed) if failure_rate else passed_count / len(typed)
    passed = failures == 0
    return GateResult(
        gate_id=gate_id,
        status=GateStatus.PASSED if passed else GateStatus.FAILED,
        requirement=requirement,
        evidence_scope="complete_blinded_human_process_gold_and_stored_trace",
        sample_count=len(typed),
        passed_count=passed_count,
        cases=typed,
        metric_name=metric_name,
        metric_value=metric_value,
        target="0.0" if failure_rate else "1.0",
        reason=None if passed else "one or more correction-process cases failed",
    )


def _correction_process_gates(
    gold: ProcessGoldCorpus | None,
    evidence: CorrectionProcessEvaluation | None,
) -> tuple[GateResult, ...]:
    if gold is None:
        return _process_not_evaluated_gates(
            "no complete human correction-process gold was supplied"
        )
    if evidence is None:
        return _process_not_evaluated_gates(
            "no candidate proposal/decision process evidence was supplied"
        )
    if not gold.complete:
        return _process_not_evaluated_gates(
            "human correction-process gold does not cover its expected case count"
        )
    if gold.adjudication.status != "completed":
        return _process_not_evaluated_gates(
            "human correction-process adjudication is not completed"
        )
    if not gold.adjudication.blinded_to_candidate:
        return _process_not_evaluated_gates(
            "human correction-process labels were not blinded to the candidate"
        )
    if not evidence.complete:
        return _process_not_evaluated_gates(
            "candidate proposal/decision process trace is incomplete"
        )
    expected_case_ids = {case.case_id for case in gold.cases}
    supplied_case_ids = set(evidence.covered_case_ids)
    if supplied_case_ids != expected_case_ids:
        raise ValueError(
            "complete correction process evidence case coverage mismatch; "
            f"missing={sorted(expected_case_ids - supplied_case_ids)}, "
            f"unknown={sorted(supplied_case_ids - expected_case_ids)}"
        )
    roles = [artifact.role for artifact in evidence.source_artifacts]
    if roles.count("ledger_prefix") != 1 or not {
        "proposal_set",
        "candidate_discovery",
    }.intersection(roles):
        raise ValueError(
            "complete correction process evidence requires exactly one ledger_prefix "
            "and at least one proposal or native discovery source artifact"
        )

    proposal_by_id = {proposal.proposal_id: proposal for proposal in evidence.proposals}
    proposals_by_case: dict[str, list[ProcessProposalTrace]] = {
        case.case_id: [] for case in gold.cases
    }
    decisions_by_case: dict[str, list[ProcessDecisionTrace]] = {
        case.case_id: [] for case in gold.cases
    }
    for proposal in evidence.proposals:
        matches = [case for case in gold.cases if _proposal_matches_process_case(proposal, case)]
        overlaps = [
            case
            for case in gold.cases
            if _ranges_overlap(proposal.start_ms, proposal.end_ms, case.start_ms, case.end_ms)
        ]
        if overlaps and len(matches) != 1:
            raise ValueError(
                f"candidate proposal {proposal.proposal_id} overlaps process gold without "
                "one exact stable target"
            )
        if matches:
            proposals_by_case[matches[0].case_id].append(proposal)
    for decision in evidence.decisions:
        matches = [case for case in gold.cases if _decision_matches_process_case(decision, case)]
        overlaps = [
            case
            for case in gold.cases
            if _ranges_overlap(
                decision.target_start_ms,
                decision.target_end_ms,
                case.start_ms,
                case.end_ms,
            )
        ]
        if overlaps and len(matches) != 1:
            raise ValueError(
                f"candidate decision {decision.event_id} overlaps process gold without "
                "one exact stable target"
            )
        if not matches:
            continue
        case = matches[0]
        for proposal_id in decision.proposal_ids:
            proposal = proposal_by_id[proposal_id]
            if (
                proposal.generation_id != decision.parent_generation_id
                or not _proposal_matches_process_case(proposal, case)
            ):
                raise ValueError(
                    f"candidate decision {decision.event_id} cites a proposal outside its "
                    "exact generation/target"
                )
        decisions_by_case[case.case_id].append(decision)

    detection_cases: list[CaseEvaluation] = []
    false_negative_cases: list[CaseEvaluation] = []
    false_proposal_cases: list[CaseEvaluation] = []
    harmful_apply_cases: list[CaseEvaluation] = []
    for case in gold.cases:
        proposals = proposals_by_case[case.case_id]
        decisions = decisions_by_case[case.case_id]
        changed_proposals = [
            proposal for proposal in proposals if proposal.candidate_text != case.observed_text
        ]
        applied = [
            (decision, text)
            for decision in decisions
            if (text := _applied_text(decision)) is not None
        ]
        observed_trace = {
            "proposal_ids": [item.proposal_id for item in proposals],
            "changed_candidate_texts": [item.candidate_text for item in changed_proposals],
            "decision_event_ids": [item.event_id for item in decisions],
            "applied_texts": [text for _, text in applied],
        }
        if case.expected_action == "accept_correction":
            detected = bool(changed_proposals)
            detection_cases.append(
                CaseEvaluation(
                    case_id=case.case_id,
                    passed=detected,
                    expected={"changed_proposal_detected": True},
                    observed=observed_trace,
                    reason=(
                        "correction audit emitted a changed proposal for the exact gold target"
                        if detected
                        else "correction audit missed the accepted-correction gold target"
                    ),
                )
            )
            exact_applied = any(text == case.expected_replacement for _, text in applied)
            false_negative_cases.append(
                CaseEvaluation(
                    case_id=case.case_id,
                    passed=exact_applied,
                    expected={"applied_text": case.expected_replacement},
                    observed=observed_trace,
                    reason=(
                        "the exact adjudicated correction was applied"
                        if exact_applied
                        else "the accepted correction was not applied exactly"
                    ),
                )
            )
            harmful = any(text != case.expected_replacement for _, text in applied)
            harmful_expected = {"only_safe_applied_text": case.expected_replacement}
        else:
            no_false_proposal = not changed_proposals
            false_proposal_cases.append(
                CaseEvaluation(
                    case_id=case.case_id,
                    passed=no_false_proposal,
                    expected={"changed_proposal_count": 0},
                    observed=observed_trace,
                    reason=(
                        "no speculative changed proposal was emitted"
                        if no_false_proposal
                        else "keep-original gold received a speculative changed proposal"
                    ),
                )
            )
            harmful = any(text != case.observed_text for _, text in applied)
            harmful_expected = {"only_safe_applied_text": case.observed_text}
        harmful_apply_cases.append(
            CaseEvaluation(
                case_id=case.case_id,
                passed=not harmful,
                expected=harmful_expected,
                observed=observed_trace,
                reason=(
                    "no harmful text-changing decision was applied"
                    if not harmful
                    else "a decision applied text that contradicts process gold"
                ),
            )
        )

    return (
        _metric_gate(
            gate_id="accepted_correction_detection_recall",
            cases=detection_cases,
            metric_name="detection_recall",
            failure_rate=False,
        ),
        _metric_gate(
            gate_id="accepted_correction_false_negative_rate_zero",
            cases=false_negative_cases,
            metric_name="false_negative_rate",
            failure_rate=True,
        ),
        _metric_gate(
            gate_id="keep_original_false_proposal_rate_zero",
            cases=false_proposal_cases,
            metric_name="false_proposal_rate",
            failure_rate=True,
        ),
        _metric_gate(
            gate_id="harmful_apply_rate_zero",
            cases=harmful_apply_cases,
            metric_name="harmful_apply_rate",
            failure_rate=True,
        ),
    )


def evaluate_benchmark(
    *,
    correction_gold: Mapping[str, Any],
    boundary_gold: Mapping[str, Any],
    review_gold: Mapping[str, Any],
    candidate: BenchmarkCandidate,
    blind_boundary: BlindBoundaryEvaluation | None = None,
    paired_boundary: PairedBoundaryStudy | Mapping[str, Any] | str | Path | None = None,
    term_code_switch: TermCodeSwitchEvaluation | None = None,
    process_gold: ProcessGoldCorpus | Mapping[str, Any] | None = None,
    correction_process: CorrectionProcessEvaluation | Mapping[str, Any] | None = None,
) -> BenchmarkReport:
    """Evaluate a candidate without mutating artifacts or consulting a model/API."""

    correction_cases = _validate_correction_corpus(correction_gold)
    boundary_cases = _validate_boundary_corpus(boundary_gold)
    review_cases = _validate_review_corpus(review_gold)
    if process_gold is not None and not isinstance(process_gold, ProcessGoldCorpus):
        process_gold = load_process_gold(process_gold)
    if correction_process is not None and not isinstance(
        correction_process, CorrectionProcessEvaluation
    ):
        correction_process = load_correction_process_evaluation(correction_process)
    if paired_boundary is not None and not isinstance(paired_boundary, PairedBoundaryStudy):
        paired_boundary = load_paired_boundary_study(paired_boundary)
    if correction_process is not None and process_gold is None:
        raise ValueError("correction process evidence requires matching process gold")
    episode_ids = {
        correction_gold["episode_id"],
        boundary_gold["episode_id"],
        review_gold["episode_id"],
    }
    if len(episode_ids) != 1:
        raise ValueError("all benchmark corpora must describe the same episode")
    episode_id = next(iter(episode_ids))
    lineages = {
        corpus.get("lineage_id") for corpus in (correction_gold, boundary_gold, review_gold)
    }
    normalized_hashes = {
        corpus.get("normalized_audio_hash")
        for corpus in (correction_gold, boundary_gold, review_gold)
    }
    if len(lineages) != 1 or not all(isinstance(lineage, str) and lineage for lineage in lineages):
        raise ValueError("benchmark corpora must be one lineage-homogeneous suite")
    if len(normalized_hashes) != 1 or not all(
        isinstance(digest, str) and _SHA256_RE.fullmatch(digest) for digest in normalized_hashes
    ):
        raise ValueError("benchmark corpora require one normalized audio hash")
    expected_audio_hash = next(iter(normalized_hashes))
    if candidate.normalized_audio_hash != expected_audio_hash:
        raise ValueError("benchmark normalized audio lineage does not match candidate generation")
    suite_hash = benchmark_suite_hash(correction_gold, boundary_gold, review_gold)
    expected_lineage = next(iter(lineages))
    if process_gold is not None:
        if process_gold.episode_id != episode_id:
            raise ValueError("process gold episode does not match benchmark suite")
        if (
            process_gold.lineage_id != expected_lineage
            or process_gold.normalized_audio_hash != expected_audio_hash
        ):
            raise ValueError("process gold lineage/hash does not match benchmark suite")

    def validate_evaluation_lineage(
        label: str,
        evaluation: _EvaluationLineage | CorrectionProcessEvaluation | None,
    ) -> None:
        if evaluation is None:
            return
        expected = {
            "candidate_generation_id": candidate.generation_id,
            "candidate_content_hash": candidate.canonical_content_hash,
            "normalized_audio_hash": candidate.normalized_audio_hash,
            "candidate_artifact_id": candidate.candidate_id,
            "candidate_artifact_hash": candidate.artifact_hash,
            "benchmark_suite_hash": suite_hash,
        }
        mismatches = {
            field: (getattr(evaluation, field), value)
            for field, value in expected.items()
            if getattr(evaluation, field) != value
        }
        if mismatches:
            raise ValueError(f"{label} evaluation lineage/hash mismatch: {mismatches}")

    validate_evaluation_lineage("blind boundary", blind_boundary)
    validate_evaluation_lineage("term/code-switch", term_code_switch)
    validate_evaluation_lineage("correction process", correction_process)
    if correction_process is not None and process_gold is not None:
        if correction_process.process_gold_hash != process_gold.gold_corpus_hash:
            raise ValueError("correction process evidence process_gold_hash mismatch")
    if paired_boundary is not None:
        if paired_boundary.episode_id != episode_id:
            raise ValueError("paired boundary study episode does not match benchmark suite")
        if (
            paired_boundary.lineage_id != expected_lineage
            or paired_boundary.normalized_audio_hash != expected_audio_hash
            or paired_boundary.benchmark_suite_hash != suite_hash
        ):
            raise ValueError("paired boundary study lineage/hash does not match benchmark suite")
        v2_candidate = next(item for item in paired_boundary.candidates if item.system == "v2")
        if (
            v2_candidate.candidate_id != candidate.candidate_id
            or v2_candidate.generation_id != candidate.generation_id
            or v2_candidate.canonical_content_hash != candidate.canonical_content_hash
            or v2_candidate.candidate_artifact_hash != candidate.artifact_hash
        ):
            raise ValueError("paired boundary V2 candidate does not match benchmark candidate")

    text_by_id = _index_unique(candidate.text_observations, label="text observations")
    boundary_by_id = _index_unique(candidate.boundary_observations, label="boundary observations")
    review_by_id = _index_unique(candidate.review_observations, label="review observations")
    correction_by_id = _index_unique(correction_cases, label="correction cases")
    boundary_gold_by_id = _index_unique(boundary_cases, label="boundary cases")
    review_gold_by_id = _index_unique(review_cases, label="review cases")

    unknown = sorted(
        (set(text_by_id) - set(correction_by_id))
        | (set(boundary_by_id) - set(boundary_gold_by_id))
        | (set(review_by_id) - set(review_gold_by_id))
    )
    unknown_cases = tuple(
        CaseEvaluation(
            case_id=case_id,
            passed=False,
            expected={"known_case_id": True},
            observed={"known_case_id": False},
            reason="candidate supplied an observation outside the versioned corpus",
        )
        for case_id in unknown
    )
    integrity_cases = unknown_cases or (
        CaseEvaluation(
            case_id="candidate-lineage-contract",
            passed=True,
            expected={
                "known_case_ids_only": True,
                "normalized_audio_hash": expected_audio_hash,
            },
            observed={
                "known_case_ids_only": True,
                "normalized_audio_hash": candidate.normalized_audio_hash,
            },
            reason="candidate identity and observations match the versioned suite",
        ),
    )
    integrity_gate = GateResult(
        gate_id="candidate_input_integrity",
        status=GateStatus.PASSED if not unknown else GateStatus.FAILED,
        requirement="Candidate observations must refer only to versioned gold case IDs.",
        evidence_scope="versioned_corpus_identity",
        sample_count=len(integrity_cases),
        passed_count=0 if unknown else 1,
        cases=integrity_cases,
        reason=None if not unknown else "unknown case IDs cannot inflate or alter the score",
    )

    text_gates: list[GateResult] = []
    category_specs = (
        (
            "accepted_correction",
            "correction_recurrence_zero",
            "All previously accepted corrections must resolve to their exact adjudicated text.",
        ),
        (
            "keep_original",
            "keep_original_non_regression",
            "All keep-original decisions must replay without speculative rewriting.",
        ),
        (
            "post_seal_freeze",
            "post_seal_text_freeze",
            "Post-resolution stages must preserve exact sealed text and never "
            "reintroduce forbidden substitutions.",
        ),
    )
    for category, gate_id, requirement in category_specs:
        selected = [case for case in correction_cases if case["category"] == category]
        text_gates.append(
            _case_gate(
                gate_id=gate_id,
                requirement=requirement,
                cases=[
                    _text_case_evaluation(case, text_by_id.get(case["case_id"]))
                    for case in selected
                ],
            )
        )

    boundary_results: list[CaseEvaluation] = []
    for gold in boundary_cases:
        case_id = gold["case_id"]
        observation = boundary_by_id.get(case_id)
        forbidden = set(gold["forbidden_breaks"])
        lexeme_count = len(gold["lexemes"])
        allowed = sorted(set(range(1, lexeme_count)) - forbidden)
        expected = {
            "outcome": ObservationOutcome.ACCEPTED.value,
            "allowed_breaks": allowed,
            "forbidden_breaks": sorted(forbidden),
        }
        if observation is None:
            boundary_results.append(
                CaseEvaluation(
                    case_id=case_id,
                    passed=False,
                    expected=expected,
                    observed={"outcome": "missing", "break_positions": None},
                    reason="required boundary gold case was not observed",
                )
            )
            continue
        observed = {
            "outcome": observation.outcome.value,
            "break_positions": list(observation.break_positions),
        }
        invalid = {
            position
            for position in observation.break_positions
            if position <= 0 or position >= lexeme_count
        }
        forbidden_used = set(observation.break_positions) & forbidden
        if observation.outcome is not ObservationOutcome.ACCEPTED:
            reason = "needs_review/rejected boundary output is not correct for adjudicated gold"
            passed = False
        elif invalid:
            reason = f"out-of-range internal boundary positions: {sorted(invalid)}"
            passed = False
        elif forbidden_used:
            reason = f"candidate used forbidden semantic boundaries: {sorted(forbidden_used)}"
            passed = False
        else:
            reason = "all selected boundaries are accepted by the versioned gold case"
            passed = True
        boundary_results.append(
            CaseEvaluation(
                case_id=case_id,
                passed=passed,
                expected=expected,
                observed=observed,
                reason=reason,
            )
        )
    boundary_gate = _case_gate(
        gate_id="semantic_boundary_constraints",
        requirement=(
            "Every selected internal boundary must be accepted; forbidden semantic breaks are zero."
        ),
        cases=boundary_results,
    )

    review_results: list[CaseEvaluation] = []
    for gold in review_cases:
        case_id = gold["case_id"]
        observation = review_by_id.get(case_id)
        expected = {"outcome": ObservationOutcome.NEEDS_REVIEW.value}
        observed = {"outcome": "missing" if observation is None else observation.outcome.value}
        passed = observation is not None and observation.outcome is ObservationOutcome.NEEDS_REVIEW
        review_results.append(
            CaseEvaluation(
                case_id=case_id,
                passed=passed,
                expected=expected,
                observed=observed,
                reason=(
                    "ambiguity correctly remained unresolved; excluded from accuracy scoring"
                    if passed
                    else (
                        "candidate promoted or omitted an ambiguity whose only safe gold "
                        "outcome is needs_review"
                    )
                ),
            )
        )
    review_gate = _case_gate(
        gate_id="needs_review_safety",
        requirement=(
            "Audio-ambiguous cases must remain needs_review and are never counted as correct text."
        ),
        cases=review_results,
    )

    blind_gate = _blind_boundary_gate(blind_boundary)
    paired_gate = (
        GateResult(
            gate_id="paired_boundary_v2_superiority",
            status=GateStatus.NOT_EVALUATED,
            requirement=(
                "A complete predeclared same-lineage paired human study must show V2 "
                "superiority by its frozen effect, exact sign-test, and unacceptable-rate gates."
            ),
            evidence_scope="missing_paired_human_study",
            sample_count=0,
            passed_count=0,
            metric_name="v2_decisive_win_rate",
            metric_value=None,
            baseline_value=None,
            target="predeclared effect + exact one-sided sign test + V2 guardrail",
            reason="no paired blinded V1/V2 human boundary study was supplied",
        )
        if paired_boundary is None
        else evaluate_paired_boundary_study(paired_boundary).to_gate_result()
    )
    term_gate = _term_code_switch_gate(term_code_switch)
    process_gates = _correction_process_gates(process_gold, correction_process)
    gates = (
        integrity_gate,
        *text_gates,
        boundary_gate,
        review_gate,
        blind_gate,
        paired_gate,
        term_gate,
        *process_gates,
    )
    scored_gates = [gate for gate in gates if gate.gate_id in _SCORED_GATE_IDS]
    scored_case_count = sum(gate.sample_count for gate in scored_gates)
    scored_correct_count = sum(gate.passed_count for gate in scored_gates)
    known_gates = [gate for gate in gates if gate.gate_id in _KNOWN_GATE_IDS]
    known_gold_passed = all(gate.status is GateStatus.PASSED for gate in known_gates)
    release_ready = all(
        gate.status is GateStatus.PASSED and gate.sample_count > 0
        for gate in gates
        if gate.gate_id != "blind_boundary_rejection_rate_lt_5_percent"
    )
    return BenchmarkReport(
        schema_version=3,
        episode_id=episode_id,
        candidate_id=candidate.candidate_id,
        candidate_generation_id=candidate.generation_id,
        candidate_content_hash=candidate.canonical_content_hash,
        normalized_audio_hash=candidate.normalized_audio_hash,
        benchmark_suite_hash=suite_hash,
        corpus_hashes=(
            ("correction_gold", hash_object(correction_gold)),
            ("boundary_gold", hash_object(boundary_gold)),
            ("review_gold", hash_object(review_gold)),
            *(
                (("blind_boundary_gold", blind_boundary.gold_corpus_hash),)
                if blind_boundary is not None
                else ()
            ),
            *(
                (("paired_boundary_study", paired_boundary.study_hash),)
                if paired_boundary is not None
                else ()
            ),
            *(
                (("term_code_switch_gold", term_code_switch.gold_corpus_hash),)
                if term_code_switch is not None
                else ()
            ),
            *(
                (("correction_process_gold", process_gold.gold_corpus_hash),)
                if process_gold is not None
                else ()
            ),
        ),
        evaluation_hashes=(
            *(
                (("blind_boundary", hash_object(blind_boundary)),)
                if blind_boundary is not None
                else ()
            ),
            *(
                (("paired_boundary", paired_boundary.study_hash),)
                if paired_boundary is not None
                else ()
            ),
            *(
                (("term_code_switch", hash_object(term_code_switch)),)
                if term_code_switch is not None
                else ()
            ),
            *(
                (("correction_process", correction_process.trace_hash),)
                if correction_process is not None
                else ()
            ),
        ),
        gates=gates,
        scored_case_count=scored_case_count,
        scored_correct_count=scored_correct_count,
        safety_case_count=review_gate.sample_count,
        safety_passed_count=review_gate.passed_count,
        known_gold_passed=known_gold_passed,
        release_ready=release_ready,
    )


__all__ = [
    "BenchmarkCandidate",
    "BenchmarkReport",
    "BlindBoundaryCase",
    "BlindBoundaryClip",
    "BlindBoundaryEvaluation",
    "BoundaryObservation",
    "CaseEvaluation",
    "CorrectionProcessEvaluation",
    "GateResult",
    "GateStatus",
    "ObservationOutcome",
    "PairedBoundaryCandidate",
    "PairedBoundaryComparisonReport",
    "PairedBoundaryJudgement",
    "PairedBoundaryMapping",
    "PairedBoundaryMappingEntry",
    "PairedBoundaryMappingReveal",
    "PairedBoundaryOutcome",
    "PairedBoundaryPredeclaration",
    "PairedBoundaryStudy",
    "PairedClipPresentation",
    "PairedStudyClip",
    "ProcessAdjudication",
    "ProcessDecisionTrace",
    "ProcessEvidenceArtifact",
    "ProcessGoldCase",
    "ProcessGoldCorpus",
    "ProcessGoldSource",
    "ProcessProposalTrace",
    "ReviewObservation",
    "TermCodeSwitchCase",
    "TermCodeSwitchEvaluation",
    "TextObservation",
    "benchmark_suite_hash",
    "correction_process_trace_hash",
    "evaluate_benchmark",
    "evaluate_paired_boundary_study",
    "load_blind_boundary_evaluation",
    "load_correction_process_evaluation",
    "load_json_fixture",
    "load_paired_boundary_study",
    "load_process_gold",
    "load_term_code_switch_evaluation",
    "process_gold_corpus_hash",
    "paired_boundary_candidate_record_hash",
    "paired_boundary_mapping_commitment_hash",
    "paired_boundary_predeclaration_hash",
    "paired_boundary_study_hash",
]
