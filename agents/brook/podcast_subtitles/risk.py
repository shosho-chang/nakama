"""Deterministic risk discovery for Evidence-to-Canonical reconciliation.

Risk discovery proposes candidates and records provenance; it never chooses a
different text merely because it looks linguistically plausible.  Audio
Evidence remains primary and material ambiguity is fail-closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from shared.schemas.podcast_subtitles_v2 import ReferenceEvidence, ReviewIssue

from .editorial import inspect_editorial_text
from .hashing import hash_object


@dataclass(frozen=True, slots=True)
class ReconciliationCandidate:
    text: str
    evidence_ids: tuple[str, ...] = ()
    reference_ids: tuple[str, ...] = ()
    confidence: float | None = None
    speaker_labels: tuple[str | None, ...] = ()
    adapter_id: str = ""


@dataclass(frozen=True, slots=True)
class SpanObservation:
    audio_span_ids: tuple[str, ...]
    candidates: tuple[ReconciliationCandidate, ...]
    expected_adapter_ids: tuple[str, ...] = ()
    coverage_code: str | None = None

    def __post_init__(self) -> None:
        if not self.audio_span_ids:
            raise ValueError("SpanObservation requires stable AudioSpanIds")
        if len(set(self.audio_span_ids)) != len(self.audio_span_ids):
            raise ValueError("SpanObservation AudioSpanIds must be unique")
        if len(set(self.expected_adapter_ids)) != len(self.expected_adapter_ids):
            raise ValueError("SpanObservation expected Adapter IDs must be unique")

    @property
    def audio_span_id(self) -> str:
        """Compatibility convenience for callers that require one span."""

        if len(self.audio_span_ids) != 1:
            raise ValueError("observation covers multiple AudioSpanIds")
        return self.audio_span_ids[0]


@dataclass(frozen=True, slots=True)
class RiskRecord:
    """A schema ReviewIssue plus provenance not yet present in shared schema v1."""

    issue: ReviewIssue
    audio_span_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    supporting_reference_ids: tuple[str, ...] = ()
    conflicting_reference_ids: tuple[str, ...] = ()
    resolution_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class TermHint:
    id: str
    canonical_text: str
    aliases: tuple[str, ...]
    reference_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.canonical_text.strip():
            raise ValueError("TermHint requires non-blank identity and canonical text")
        if not self.aliases or any(not alias.strip() for alias in self.aliases):
            raise ValueError("TermHint requires non-blank aliases")
        if not self.reference_ids or len(set(self.reference_ids)) != len(self.reference_ids):
            raise ValueError("TermHint requires unique Reference Evidence IDs")


def _issue_id(*, code: str, span_ids: Sequence[str], candidates: Sequence[str]) -> str:
    payload = {
        "code": code,
        "span_ids": list(span_ids),
        "candidates": sorted(set(candidates)),
    }
    return f"issue-{hash_object(payload)}"


def discover_recognition_risks(observation: SpanObservation) -> tuple[RiskRecord, ...]:
    """Flag conflicting recognizer readings without selecting a winner."""

    by_text: dict[str, list[ReconciliationCandidate]] = {}
    for candidate in observation.candidates:
        by_text.setdefault(candidate.text, []).append(candidate)
    observed_adapters = {
        candidate.adapter_id for candidate in observation.candidates if candidate.adapter_id
    }
    missing_adapters = set(observation.expected_adapter_ids) - observed_adapters
    if observation.coverage_code is not None:
        code = observation.coverage_code
    elif missing_adapters:
        code = "recognition_coverage_disagreement"
    elif len(by_text) > 1:
        code = "recognition_disagreement"
    else:
        return ()
    texts = tuple(sorted(by_text))
    evidence_ids = tuple(
        sorted(
            {
                evidence_id
                for candidates in by_text.values()
                for candidate in candidates
                for evidence_id in candidate.evidence_ids
            }
        )
    )
    issue = ReviewIssue(
        id=_issue_id(
            code=code,
            span_ids=observation.audio_span_ids,
            candidates=texts,
        ),
        risk="text",
        severity="high",
        code=code,
        span_ids=observation.audio_span_ids,
        audio_evidence_ids=evidence_ids,
        candidates=texts,
        status="unresolved",
    )
    return (
        RiskRecord(
            issue=issue,
            audio_span_ids=observation.audio_span_ids,
            evidence_ids=evidence_ids,
        ),
    )


def discover_intrinsic_risks(observation: SpanObservation) -> tuple[RiskRecord, ...]:
    """Find risks visible in the primary audio hypothesis itself."""

    if not observation.candidates:
        return ()
    primary = observation.candidates[0]
    risks: list[RiskRecord] = []

    def add(code: str, severity: str, *, risk_kind: str = "text") -> None:
        evidence_ids = tuple(sorted(primary.evidence_ids))
        issue = ReviewIssue(
            id=_issue_id(
                code=code,
                span_ids=observation.audio_span_ids,
                candidates=(primary.text,),
            ),
            risk=risk_kind,
            severity=severity,
            code=code,
            span_ids=observation.audio_span_ids,
            audio_evidence_ids=evidence_ids,
            candidates=(primary.text,),
            status="unresolved",
        )
        risks.append(
            RiskRecord(
                issue=issue,
                audio_span_ids=observation.audio_span_ids,
                evidence_ids=evidence_ids,
            )
        )

    # A recognizer that does not expose confidence is a generation-wide Adapter
    # capability limitation, not thousands of independent token defects.  The
    # canonical layer aggregates it into one provenance warning and requires a
    # complete full-audit receipt set before acceptance.
    if primary.confidence is not None and primary.confidence < 0.75:
        add("low_confidence", "high" if primary.confidence < 0.5 else "medium")
    if not primary.speaker_labels or any(
        label is None or not label.strip() for label in primary.speaker_labels
    ):
        add("speaker_unresolved", "medium", risk_kind="speaker")
    if re.search(r"[A-Za-z]", primary.text):
        add(
            "code_switch",
            "low" if (primary.confidence or 0.0) >= 0.9 else "medium",
        )

    compact = "".join(primary.text.split())
    has_repetition = any(
        compact[start : start + width] == compact[start + width : start + width * 2]
        for width in range(2, len(compact) // 2 + 1)
        for start in range(0, len(compact) - width * 2 + 1)
    )
    if has_repetition:
        add("adjacent_repetition", "medium")
    if re.search(r"(?i)\[unk\]|<unk>|\ufffd|@{2,}|\?{3,}", primary.text):
        add("suspicious_token", "high")
    return tuple(risks)


def discover_editorial_risks(
    observations: Sequence[SpanObservation],
) -> tuple[RiskRecord, ...]:
    """Inspect continuous canonical text without rewriting or token-local bias.

    House delimiters may legitimately open in one Recognition token and close
    in another, so this check runs once over the ordered transcript and maps
    every finding back to the exact stable AudioSpan IDs that supplied it.
    """

    typed = tuple(observations)
    if not typed:
        return ()
    parts: list[str] = []
    scalar_to_observation: list[int] = []
    for index, observation in enumerate(typed):
        if not observation.candidates:
            raise ValueError("editorial risk discovery requires a primary candidate per span")
        text = observation.candidates[0].text
        parts.append(text)
        scalar_to_observation.extend([index] * len(text))
    continuous_text = "".join(parts)
    if not continuous_text:
        return ()

    risks: list[RiskRecord] = []
    emitted_issue_ids: set[str] = set()
    for finding in inspect_editorial_text(continuous_text):
        observation_indexes = tuple(
            dict.fromkeys(scalar_to_observation[position] for position in finding.positions)
        )
        span_ids = tuple(
            span_id for index in observation_indexes for span_id in typed[index].audio_span_ids
        )
        evidence_ids = tuple(
            sorted(
                {
                    evidence_id
                    for index in observation_indexes
                    for candidate in typed[index].candidates[:1]
                    for evidence_id in candidate.evidence_ids
                }
            )
        )
        candidates = tuple(
            dict.fromkeys(typed[index].candidates[0].text for index in observation_indexes)
        )
        issue_id = _issue_id(
            code=finding.code,
            span_ids=span_ids,
            candidates=candidates,
        )
        # Multiple punctuation characters in one Recognition span describe the
        # same stable review target.  Collapse them into one Issue instead of
        # emitting duplicate IDs that would make the transcript unsealable.
        if issue_id in emitted_issue_ids:
            continue
        emitted_issue_ids.add(issue_id)
        issue = ReviewIssue(
            id=issue_id,
            risk="text",
            severity="medium",
            code=finding.code,
            span_ids=span_ids,
            audio_evidence_ids=evidence_ids,
            candidates=candidates,
            status="unresolved",
        )
        risks.append(
            RiskRecord(
                issue=issue,
                audio_span_ids=span_ids,
                evidence_ids=evidence_ids,
            )
        )
    return tuple(risks)


def _replace_case_insensitive(text: str, old: str, new: str) -> str | None:
    match = re.search(re.escape(old), text, flags=re.IGNORECASE)
    if match is None:
        return None
    return f"{text[: match.start()]}{new}{text[match.end() :]}"


def apply_reference_hints(
    observation: SpanObservation,
    references: Sequence[ReferenceEvidence],
    term_hints: Sequence[TermHint],
) -> tuple[SpanObservation, tuple[RiskRecord, ...]]:
    """Add documentary candidates without promoting them to audio truth."""

    reference_by_id = {reference.id: reference for reference in references}
    if len(reference_by_id) != len(references):
        raise ValueError("Reference Evidence IDs must be unique")
    audio_candidates = tuple(observation.candidates)
    audio_texts = {candidate.text for candidate in audio_candidates}
    candidates = list(audio_candidates)
    risks: list[RiskRecord] = []
    for hint in sorted(term_hints, key=lambda item: item.id):
        try:
            hint_references = tuple(reference_by_id[item] for item in hint.reference_ids)
        except KeyError as exc:
            raise ValueError(
                f"TermHint {hint.id!r} references unknown Reference Evidence {exc.args[0]!r}"
            ) from exc
        proposed: str | None = None
        for alias in hint.aliases:
            proposed = _replace_case_insensitive(
                audio_candidates[0].text,
                alias,
                hint.canonical_text,
            )
            if proposed is not None and proposed != audio_candidates[0].text:
                break
        if proposed is None or proposed == audio_candidates[0].text:
            continue

        reference_ids = tuple(sorted(reference.id for reference in hint_references))
        if proposed not in {candidate.text for candidate in candidates}:
            candidates.append(
                ReconciliationCandidate(
                    text=proposed,
                    reference_ids=reference_ids,
                )
            )
        located = tuple(sorted(reference.id for reference in hint_references))
        contextual = tuple(
            sorted(
                reference.id
                for reference in hint_references
                if reference.artifact.trust_tier == "contextual"
            )
        )
        if contextual:
            code = "reference_contextual_only"
            severity = "medium"
            supporting = contextual
            conflicting = ()
        elif len(audio_candidates) > 1 and len(audio_texts) == 1 and proposed not in audio_texts:
            code = "reference_audio_conflict"
            severity = "high"
            supporting = ()
            conflicting = located
        else:
            code = "reference_term_candidate"
            severity = "medium"
            supporting = located
            conflicting = ()
        issue_candidates = tuple(sorted(audio_texts | {proposed}))
        issue = ReviewIssue(
            id=_issue_id(
                code=code,
                span_ids=observation.audio_span_ids,
                candidates=issue_candidates,
            ),
            risk="text",
            severity=severity,
            code=code,
            span_ids=observation.audio_span_ids,
            audio_evidence_ids=tuple(
                sorted(
                    {
                        evidence_id
                        for candidate in audio_candidates
                        for evidence_id in candidate.evidence_ids
                    }
                )
            ),
            reference_evidence_ids=reference_ids,
            candidates=issue_candidates,
            status="unresolved",
        )
        risks.append(
            RiskRecord(
                issue=issue,
                audio_span_ids=observation.audio_span_ids,
                evidence_ids=tuple(
                    sorted(
                        {
                            evidence_id
                            for candidate in audio_candidates
                            for evidence_id in candidate.evidence_ids
                        }
                    )
                ),
                supporting_reference_ids=supporting,
                conflicting_reference_ids=conflicting,
            )
        )
    return (
        SpanObservation(
            audio_span_ids=observation.audio_span_ids,
            candidates=tuple(candidates),
            expected_adapter_ids=observation.expected_adapter_ids,
            coverage_code=observation.coverage_code,
        ),
        tuple(risks),
    )


__all__ = [
    "ReconciliationCandidate",
    "RiskRecord",
    "SpanObservation",
    "TermHint",
    "apply_reference_hints",
    "discover_editorial_risks",
    "discover_intrinsic_risks",
    "discover_recognition_risks",
]
