from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast

import pytest

from agents.brook.podcast_subtitles.hashing import (
    canonical_json_bytes,
    hash_object,
    sha256_bytes,
)
from agents.brook.podcast_subtitles.ledger import GENESIS_HASH, CorrectionLedger
from agents.brook.podcast_subtitles.ledger_decision_codec import (
    LedgerDecisionCodecError,
    decode_ledger_decision,
    decode_ledger_entry,
    decode_ledger_prefix,
)
from agents.brook.podcast_subtitles.module import PodcastSubtitleV2
from agents.brook.podcast_subtitles.native_resolution import NativeCorrectionDecisionV2
from shared.schemas.podcast_subtitles_v2 import CorrectionDecision


def _legacy_payload() -> dict[str, object]:
    decision = CorrectionDecision(
        event_id="legacy-event-1",
        episode_id="episode-1",
        generation_id="generation-parent-1",
        target_span_ids=("span-1",),
        target_start_ms=100,
        target_end_ms=900,
        evidence_fingerprint="1" * 64,
        proposal_ids=("proposal-1",),
        arbitration_receipt_ids=("arbitration-1",),
        issue_ids=("issue-1",),
        audio_evidence_ids=("audio-evidence-1",),
        action="accept_candidate",
        selected_candidate="correct literal",
        replacement_lexemes=("correct ", "literal"),
        actor_kind="human",
        actor="reviewer-1",
        rationale="exact audio review",
        timestamp=datetime(2026, 8, 13, 1, 2, 3, tzinfo=timezone.utc),
    )
    return decision.model_dump(mode="json", exclude_none=False)


def _native_payload() -> dict[str, object]:
    original_hash = sha256_bytes(b"original literal")
    candidate_hash = sha256_bytes(b"candidate literal")
    payload: dict[str, object] = {
        "schema_version": 2,
        "decision_kind": "native_correction_v2",
        "episode_id": "episode-1",
        "generation_id": "generation-" + "2" * 64,
        "action": "reject_candidate",
        "authorization_kind": "correction_acceptance",
        "authorization_id": "3" * 64,
        "authorization_hash": "3" * 64,
        "authorization_action": "reject",
        "candidate_modality": "text",
        "candidate_discovery_id": "4" * 64,
        "candidate_discovery_hash": "5" * 64,
        "full_audit_aggregate_id": "6" * 64,
        "full_audit_aggregate_hash": "7" * 64,
        "normalized_audio_hash": "8" * 64,
        "reviewed_fingerprint": "9" * 64,
        "target_span_ids": ("span-2",),
        "affected_token_ids": ("token-2",),
        "cited_recognition_evidence_ids": ("recognition-2",),
        "target_start_ms": 1_000,
        "target_end_ms": 1_900,
        "original_literal_sha256": original_hash,
        "candidate_literal_sha256": candidate_hash,
        "authorized_literal_sha256": None,
        "mutates_text": False,
        "authority": "typed_authorization_event_not_free_text",
    }
    digest = hash_object(payload)
    event = NativeCorrectionDecisionV2(
        **payload,
        event_id=f"native-resolution-{digest}",
        content_hash=digest,
    )
    return event.model_dump(mode="json", exclude_none=False)


def test_decodes_legacy_into_exact_common_view() -> None:
    payload = _legacy_payload()

    decoded = decode_ledger_decision(payload)

    assert isinstance(decoded.decision, CorrectionDecision)
    assert decoded.family == "legacy_correction_v1"
    assert decoded.decision_hash == hash_object(payload)
    assert decoded.canonical_bytes == canonical_json_bytes(payload)
    assert decoded.view.parent_generation_id == "generation-parent-1"
    assert decoded.view.action == "accept_candidate"
    assert decoded.view.action_family == "text_mutation"
    assert decoded.view.mutates_text is True
    assert decoded.view.proposal_ids == ("proposal-1",)
    assert decoded.view.candidate_discovery is None
    expected_literal_hash = sha256_bytes(b"correct literal")
    assert decoded.view.candidate_literal_sha256 == expected_literal_hash
    assert decoded.view.authorized_literal_sha256 == expected_literal_hash


def test_decodes_native_without_inventing_legacy_proposal_or_literal() -> None:
    payload = _native_payload()

    decoded = decode_ledger_decision(payload)

    assert isinstance(decoded.decision, NativeCorrectionDecisionV2)
    assert decoded.family == "native_correction_v2"
    assert decoded.view.action == "reject_candidate"
    assert decoded.view.action_family == "reject_candidate"
    assert decoded.view.mutates_text is False
    assert decoded.view.proposal_ids == ()
    assert decoded.view.candidate_discovery is not None
    assert decoded.view.candidate_discovery.discovery_id == "4" * 64
    assert decoded.view.candidate_discovery.discovery_hash == "5" * 64
    assert decoded.view.candidate_discovery.literal_sha256 == sha256_bytes(b"candidate literal")
    assert decoded.view.selected_candidate is None


def test_decodes_a_mixed_legacy_native_hash_chain() -> None:
    legacy = CorrectionLedger._entry_for_payload(
        _legacy_payload(), previous_hash=GENESIS_HASH, sequence=1
    )
    native = CorrectionLedger._entry_for_payload(
        _native_payload(), previous_hash=legacy.entry_hash, sequence=2
    )

    decoded = decode_ledger_prefix((legacy, native))

    assert tuple(item.family for item in decoded) == (
        "legacy_correction_v1",
        "native_correction_v2",
    )


def test_module_ancestry_reader_follows_a_mixed_legacy_native_chain() -> None:
    legacy_payload = _legacy_payload()
    native_payload = _native_payload()
    legacy = CorrectionLedger._entry_for_payload(
        legacy_payload, previous_hash=GENESIS_HASH, sequence=1
    )
    native = CorrectionLedger._entry_for_payload(
        native_payload, previous_hash=legacy.entry_hash, sequence=2
    )
    ledger = SimpleNamespace(entries=lambda: (legacy, native))
    module = cast(PodcastSubtitleV2, SimpleNamespace(ledger=ledger))
    descendant = SimpleNamespace(
        generation_id="generation-child",
        ledger_hash=native.entry_hash,
        episode_id="episode-1",
    )

    assert PodcastSubtitleV2._generation_descends_from(
        module,
        descendant,
        ancestor_generation_id=str(native_payload["generation_id"]),
    )
    assert PodcastSubtitleV2._generation_descends_from(
        module,
        descendant,
        ancestor_generation_id=str(legacy_payload["generation_id"]),
    )
    assert not PodcastSubtitleV2._generation_descends_from(
        module,
        descendant,
        ancestor_generation_id="generation-unrelated",
    )


@pytest.mark.parametrize("schema_version", [True, "1", 0, 3])
def test_unknown_or_coerced_schema_version_fails_closed(schema_version: object) -> None:
    payload = _legacy_payload()
    payload["schema_version"] = schema_version

    with pytest.raises(LedgerDecisionCodecError, match="schema_version"):
        decode_ledger_decision(payload)


def test_legacy_payload_with_native_discriminator_is_rejected_as_ambiguous() -> None:
    payload = _legacy_payload()
    payload["decision_kind"] = "native_correction_v2"

    with pytest.raises(LedgerDecisionCodecError, match="ambiguous"):
        decode_ledger_decision(payload)


def test_native_payload_with_wrong_or_missing_discriminator_fails_closed() -> None:
    wrong = _native_payload()
    wrong["decision_kind"] = "legacy_correction_v1"
    missing = _native_payload()
    del missing["decision_kind"]

    for payload in (wrong, missing):
        with pytest.raises(LedgerDecisionCodecError, match="decision_kind"):
            decode_ledger_decision(payload)


def test_field_coercion_is_rejected() -> None:
    payload = _legacy_payload()
    payload["target_start_ms"] = "100"

    with pytest.raises(LedgerDecisionCodecError, match="strict legacy"):
        decode_ledger_decision(payload)


def test_default_injection_is_not_accepted_as_exact_raw_mapping() -> None:
    payload = _legacy_payload()
    del payload["reference_evidence_ids"]

    with pytest.raises(LedgerDecisionCodecError, match="canonical typed representation"):
        decode_ledger_decision(payload)


def test_ledger_decision_hash_is_checked_against_exact_raw_mapping() -> None:
    entry = CorrectionLedger._entry_for_payload(
        _legacy_payload(), previous_hash=GENESIS_HASH, sequence=1
    )
    tampered_payload = {**entry.decision, "actor": "different-reviewer"}
    tampered = replace(entry, decision=tampered_payload)

    with pytest.raises(LedgerDecisionCodecError, match="decision_hash"):
        decode_ledger_entry(tampered)


def test_native_internal_content_identity_is_checked_even_if_ledger_hash_is_resealed() -> None:
    payload = _native_payload()
    payload["normalized_audio_hash"] = "a" * 64
    resealed_ledger_entry = CorrectionLedger._entry_for_payload(
        payload, previous_hash=GENESIS_HASH, sequence=1
    )

    with pytest.raises(LedgerDecisionCodecError, match="strict native"):
        decode_ledger_entry(resealed_ledger_entry)


def test_prefix_chain_metadata_tamper_is_rejected() -> None:
    entry = CorrectionLedger._entry_for_payload(
        _legacy_payload(), previous_hash=GENESIS_HASH, sequence=1
    )

    with pytest.raises(LedgerDecisionCodecError, match="entry_hash"):
        decode_ledger_prefix((replace(entry, entry_hash="f" * 64),))
