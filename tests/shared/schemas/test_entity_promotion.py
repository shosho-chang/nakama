"""Tests for shared.schemas.entity_promotion (ADR-034 v2 PR2b-ii).

Covers:
- EntityCandidate E4 invariant (entity_type ↔ metadata.entity_type)
- EntityPromotionResult E7 invariant (error → items=[])
- KBEntityEntry / EntityMatchOutcome frozen + extra-forbid
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.schemas.entity_promotion import (
    EntityCandidate,
    EntityMatchOutcome,
    EntityPromotionResult,
    KBEntityEntry,
)
from shared.schemas.promotion_manifest import (
    EntityCanonicalMatch,
    EntityReviewItem,
    EvidenceAnchor,
    OrganizationMetadata,
    PersonMetadata,
)


def _person_meta() -> PersonMetadata:
    return PersonMetadata(affiliation="Stanford")


def _evidence() -> list[EvidenceAnchor]:
    return [
        EvidenceAnchor(
            kind="external_ref",
            source_path="00:12:34",
            locator="00:12:34",
            excerpt="x",
            confidence=0.85,
        )
    ]


def test_entity_candidate_e4_rejects_metadata_mismatch() -> None:
    with pytest.raises(ValidationError, match="differs from metadata.entity_type"):
        EntityCandidate(
            candidate_id="c1",
            entity_type="organization",
            label="X",
            evidence_language="en",
            source_refs=[],
            raw_quotes=[],
            metadata=_person_meta(),  # type mismatch
        )


def test_entity_candidate_consistent_variant_validates() -> None:
    cand = EntityCandidate(
        candidate_id="c1",
        entity_type="person",
        label="Andrew Huberman",
        evidence_language="en",
        source_refs=["00:12:34"],
        raw_quotes=["q"],
        metadata=_person_meta(),
    )
    assert cand.entity_type == "person"
    assert cand.metadata.entity_type == "person"


def test_entity_candidate_frozen() -> None:
    cand = EntityCandidate(
        candidate_id="c1",
        entity_type="organization",
        label="Stanford",
        evidence_language="en",
        source_refs=[],
        raw_quotes=[],
        metadata=OrganizationMetadata(),
    )
    with pytest.raises(ValidationError):
        cand.label = "MIT"  # type: ignore[misc]


def test_kb_entity_entry_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        KBEntityEntry(
            entity_path="KB/Wiki/Entities/People/X.md",
            entity_type="person",
            canonical_label="X",
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_entity_match_outcome_carries_canonical_and_signals() -> None:
    outcome = EntityMatchOutcome(
        canonical_match=EntityCanonicalMatch(
            match_basis="exact_alias",
            matched_entity_path="KB/Wiki/Entities/People/X.md",
            confidence=0.95,
        ),
        conflict_signals=["affiliation diverges"],
    )
    assert outcome.canonical_match.confidence == 0.95
    assert outcome.conflict_signals == ["affiliation diverges"]


def test_entity_promotion_result_error_requires_empty_items() -> None:
    item = EntityReviewItem(
        item_id="x",
        recommendation="include",
        action="create_entity",
        reason="r",
        evidence=_evidence(),
        risk=[],
        confidence=0.9,
        source_importance=0.9,
        reader_salience=0.5,
        entity_label="X",
        metadata=_person_meta(),
    )
    with pytest.raises(ValidationError, match="error is not None requires items"):
        EntityPromotionResult(
            source_id="podcast:ep1",
            primary_lang="en",
            candidates_received=1,
            items=[item],
            risks=[],
            error="matcher_failed: ValueError(boom)",
        )


def test_entity_promotion_result_error_with_empty_items_validates() -> None:
    result = EntityPromotionResult(
        source_id="podcast:ep1",
        primary_lang="en",
        candidates_received=3,
        items=[],
        risks=[],
        error="matcher_failed: ValueError(boom)",
    )
    assert result.error is not None
    assert result.items == []


def test_entity_promotion_result_success_path() -> None:
    item = EntityReviewItem(
        item_id="x",
        recommendation="include",
        action="create_entity",
        reason="r",
        evidence=_evidence(),
        risk=[],
        confidence=0.9,
        source_importance=0.9,
        reader_salience=0.5,
        entity_label="X",
        metadata=_person_meta(),
    )
    result = EntityPromotionResult(
        source_id="podcast:ep1",
        primary_lang="en",
        candidates_received=1,
        items=[item],
        risks=[],
        error=None,
    )
    assert len(result.items) == 1
    assert result.error is None
