"""Strict decoding boundary for mixed Correction Ledger decision families.

The append-only Ledger stores the exact JSON mapping supplied by its writer.
That mapping is evidence: callers must not choose a model by trial-and-error,
allow Pydantic coercion, or silently fill omitted defaults.  This codec selects
one closed schema from explicit discriminators, validates its canonical typed
representation, and exposes a small immutable view shared by ancestry and
process-trace readers.

Native candidate text is deliberately absent from the common view.  A native
event carries only the discovery artifact identity and exact literal hashes;
the literal itself must be obtained by verifying that separately persisted
discovery artifact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Mapping, TypeAlias

from pydantic import ValidationError

from shared.schemas.podcast_subtitles_v2 import CorrectionDecision

from .hashing import canonical_json_bytes, hash_object, sha256_bytes
from .ledger import GENESIS_HASH, LedgerEntry
from .native_resolution import NativeCorrectionDecisionV2

DecisionFamily: TypeAlias = Literal["legacy_correction_v1", "native_correction_v2"]
DecisionActionFamily: TypeAlias = Literal[
    "text_mutation",
    "confirm_original",
    "reject_candidate",
    "defer",
]
TypedLedgerDecision: TypeAlias = CorrectionDecision | NativeCorrectionDecisionV2

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LedgerDecisionCodecError(ValueError):
    """A Ledger decision is ambiguous, non-canonical, or fails exact identity."""


@dataclass(frozen=True)
class CandidateDiscoveryBinding:
    """Content-addressed native discovery provenance, without unverified text."""

    discovery_id: str
    discovery_hash: str
    literal_sha256: str
    modality: Literal["text", "audio"]


@dataclass(frozen=True)
class LedgerDecisionView:
    """Lossless common fields needed by Ledger lineage and trace readers.

    Family-specific provenance remains explicit: legacy proposal IDs are never
    populated from native discovery IDs, and native selected text is never
    reconstructed from a digest.
    """

    family: DecisionFamily
    schema_version: Literal[1, 2]
    event_id: str
    episode_id: str
    parent_generation_id: str
    reviewed_fingerprint: str
    target_span_ids: tuple[str, ...]
    target_start_ms: int
    target_end_ms: int
    action: str
    action_family: DecisionActionFamily
    mutates_text: bool
    proposal_ids: tuple[str, ...]
    issue_ids: tuple[str, ...]
    candidate_discovery: CandidateDiscoveryBinding | None
    replacement_text: str | None
    selected_candidate: str | None
    original_literal_sha256: str | None
    candidate_literal_sha256: str | None
    authorized_literal_sha256: str | None
    authorization_id: str | None
    authorization_hash: str | None


@dataclass(frozen=True)
class DecodedLedgerDecision:
    """One strictly decoded decision plus identity of its exact raw mapping."""

    family: DecisionFamily
    decision: TypedLedgerDecision
    decision_hash: str
    canonical_bytes: bytes
    view: LedgerDecisionView


def _literal_hash(value: str | None) -> str | None:
    return sha256_bytes(value.encode("utf-8")) if value is not None else None


def _action_family(action: str) -> DecisionActionFamily:
    if action in {"replace", "accept_candidate", "accept_exact_candidate"}:
        return "text_mutation"
    if action == "confirm_original":
        return "confirm_original"
    if action == "reject_candidate":
        return "reject_candidate"
    if action == "defer":
        return "defer"
    raise LedgerDecisionCodecError(f"unsupported decoded decision action: {action!r}")


def _legacy_view(decision: CorrectionDecision) -> LedgerDecisionView:
    candidate_hash = _literal_hash(decision.selected_candidate)
    authorized_text = (
        decision.replacement_text
        if decision.action == "replace"
        else decision.selected_candidate
        if decision.action == "accept_candidate"
        else None
    )
    return LedgerDecisionView(
        family="legacy_correction_v1",
        schema_version=1,
        event_id=decision.event_id,
        episode_id=decision.episode_id,
        parent_generation_id=decision.generation_id,
        reviewed_fingerprint=decision.evidence_fingerprint,
        target_span_ids=decision.target_span_ids,
        target_start_ms=decision.target_start_ms,
        target_end_ms=decision.target_end_ms,
        action=decision.action,
        action_family=_action_family(decision.action),
        mutates_text=decision.action in {"replace", "accept_candidate"},
        proposal_ids=decision.proposal_ids,
        issue_ids=decision.issue_ids,
        candidate_discovery=None,
        replacement_text=decision.replacement_text,
        selected_candidate=decision.selected_candidate,
        original_literal_sha256=None,
        candidate_literal_sha256=candidate_hash,
        authorized_literal_sha256=_literal_hash(authorized_text),
        authorization_id=None,
        authorization_hash=None,
    )


def _native_view(decision: NativeCorrectionDecisionV2) -> LedgerDecisionView:
    return LedgerDecisionView(
        family="native_correction_v2",
        schema_version=2,
        event_id=decision.event_id,
        episode_id=decision.episode_id,
        parent_generation_id=decision.generation_id,
        reviewed_fingerprint=decision.reviewed_fingerprint,
        target_span_ids=decision.target_span_ids,
        target_start_ms=decision.target_start_ms,
        target_end_ms=decision.target_end_ms,
        action=decision.action,
        action_family=_action_family(decision.action),
        mutates_text=decision.mutates_text,
        proposal_ids=(),
        # Native v2 currently has no issue binding.  Keep that absence visible
        # instead of projecting a candidate discovery ID into an issue/proposal.
        issue_ids=(),
        candidate_discovery=CandidateDiscoveryBinding(
            discovery_id=decision.candidate_discovery_id,
            discovery_hash=decision.candidate_discovery_hash,
            literal_sha256=decision.candidate_literal_sha256,
            modality=decision.candidate_modality,
        ),
        replacement_text=None,
        selected_candidate=None,
        original_literal_sha256=decision.original_literal_sha256,
        candidate_literal_sha256=decision.candidate_literal_sha256,
        authorized_literal_sha256=decision.authorized_literal_sha256,
        authorization_id=decision.authorization_id,
        authorization_hash=decision.authorization_hash,
    )


def _strict_typed_decision(
    *, raw_bytes: bytes, family: DecisionFamily
) -> TypedLedgerDecision:
    try:
        if family == "legacy_correction_v1":
            return CorrectionDecision.model_validate_json(raw_bytes, strict=True)
        return NativeCorrectionDecisionV2.model_validate_json(raw_bytes, strict=True)
    except (ValidationError, ValueError) as exc:
        label = "legacy" if family == "legacy_correction_v1" else "native"
        raise LedgerDecisionCodecError(
            f"Ledger decision violates strict {label} schema"
        ) from exc


def decode_ledger_decision(
    raw_decision: Mapping[str, object],
    *,
    expected_decision_hash: str | None = None,
) -> DecodedLedgerDecision:
    """Decode one exact Ledger mapping using a single explicit schema branch."""

    if type(raw_decision) is not dict:
        raise LedgerDecisionCodecError("Ledger decision must be an exact JSON object mapping")
    if any(type(key) is not str for key in raw_decision):
        raise LedgerDecisionCodecError("Ledger decision keys must be exact strings")

    schema_version = raw_decision.get("schema_version")
    if type(schema_version) is not int or schema_version not in {1, 2}:
        raise LedgerDecisionCodecError(
            "Ledger decision schema_version must be exact supported integer 1 or 2"
        )
    if schema_version == 1:
        if "decision_kind" in raw_decision:
            raise LedgerDecisionCodecError(
                "ambiguous legacy Ledger decision carries a native decision_kind"
            )
        family: DecisionFamily = "legacy_correction_v1"
    else:
        if raw_decision.get("decision_kind") != "native_correction_v2":
            raise LedgerDecisionCodecError(
                "native Ledger decision requires exact decision_kind='native_correction_v2'"
            )
        family = "native_correction_v2"

    raw_bytes = canonical_json_bytes(raw_decision)
    decision_hash = sha256_bytes(raw_bytes)
    if expected_decision_hash is not None:
        if (
            type(expected_decision_hash) is not str
            or not _SHA256.fullmatch(expected_decision_hash)
            or expected_decision_hash != decision_hash
        ):
            raise LedgerDecisionCodecError(
                "Ledger decision_hash differs from the exact raw decision mapping"
            )

    decision = _strict_typed_decision(raw_bytes=raw_bytes, family=family)
    if canonical_json_bytes(decision) != raw_bytes:
        raise LedgerDecisionCodecError(
            "Ledger decision raw mapping differs from its canonical typed representation"
        )

    if isinstance(decision, CorrectionDecision):
        view = _legacy_view(decision)
    else:
        view = _native_view(decision)
    return DecodedLedgerDecision(
        family=family,
        decision=decision,
        decision_hash=decision_hash,
        canonical_bytes=raw_bytes,
        view=view,
    )


def decode_ledger_entry(entry: LedgerEntry) -> DecodedLedgerDecision:
    """Verify one complete Ledger entry and decode its exact decision mapping."""

    if not isinstance(entry, LedgerEntry):
        raise LedgerDecisionCodecError("Ledger decoder requires a LedgerEntry")
    if type(entry.sequence) is not int or entry.sequence < 1:
        raise LedgerDecisionCodecError("Ledger entry sequence must be an exact positive integer")
    for label, digest in (
        ("previous_hash", entry.previous_hash),
        ("decision_hash", entry.decision_hash),
        ("entry_hash", entry.entry_hash),
    ):
        if type(digest) is not str or not _SHA256.fullmatch(digest):
            raise LedgerDecisionCodecError(f"Ledger entry {label} must be lowercase SHA-256")

    decoded = decode_ledger_decision(
        entry.decision,
        expected_decision_hash=entry.decision_hash,
    )
    expected_entry_hash = hash_object(
        {
            "sequence": entry.sequence,
            "previous_hash": entry.previous_hash,
            "decision": entry.decision,
            "decision_hash": entry.decision_hash,
        }
    )
    if entry.entry_hash != expected_entry_hash:
        raise LedgerDecisionCodecError("Ledger entry_hash differs from exact entry content")
    return decoded


def decode_ledger_prefix(
    entries: tuple[LedgerEntry, ...] | list[LedgerEntry],
) -> tuple[DecodedLedgerDecision, ...]:
    """Verify and decode one complete Genesis-rooted mixed-family prefix."""

    previous_hash = GENESIS_HASH
    decoded: list[DecodedLedgerDecision] = []
    for expected_sequence, entry in enumerate(entries, start=1):
        if not isinstance(entry, LedgerEntry):
            raise LedgerDecisionCodecError("Ledger prefix contains a non-LedgerEntry value")
        if entry.sequence != expected_sequence:
            raise LedgerDecisionCodecError("Ledger prefix sequence is not contiguous from one")
        if entry.previous_hash != previous_hash:
            raise LedgerDecisionCodecError("Ledger prefix previous_hash chain is broken")
        decoded.append(decode_ledger_entry(entry))
        previous_hash = entry.entry_hash
    return tuple(decoded)


__all__ = [
    "CandidateDiscoveryBinding",
    "DecodedLedgerDecision",
    "DecisionActionFamily",
    "DecisionFamily",
    "LedgerDecisionCodecError",
    "LedgerDecisionView",
    "TypedLedgerDecision",
    "decode_ledger_decision",
    "decode_ledger_entry",
    "decode_ledger_prefix",
]
