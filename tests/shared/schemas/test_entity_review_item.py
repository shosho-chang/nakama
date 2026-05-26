"""Tests for EntityReviewItem schema (ADR-034 v2 PR2a).

Covers:
- Person / Organization metadata variants validate via discriminated union
- EntityReviewItem accepts both variants under discriminated `item_kind="entity"`
- ReviewItem top-level union round-trips Entity items
- EntityCanonicalMatch V10 mirror (match_basis vs matched_entity_path)
- PromotionManifest V12 — entity items require schema_version >= 2
- Existing v=1 manifests still load (backward compat)
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from shared.schemas.promotion_manifest import (
    EntityCanonicalMatch,
    EntityReviewItem,
    EvidenceAnchor,
    HumanDecision,
    OrganizationMetadata,
    PersonMetadata,
    PromotionManifest,
    RecommenderMetadata,
    ReviewItem,
    SourcePageReviewItem,
)


def _evidence() -> list[EvidenceAnchor]:
    return [
        EvidenceAnchor(
            kind="external_ref",
            source_path="https://youtu.be/abc123",
            locator="12:34",
            excerpt="Huberman discussing dopamine baseline",
            confidence=0.92,
        )
    ]


def _approved_decision() -> HumanDecision:
    return HumanDecision(
        decision="approve",
        decided_at="2026-05-26T00:00:00Z",
        decided_by="tester",
    )


def _make_person_entity() -> EntityReviewItem:
    return EntityReviewItem(
        item_id="ent-huberman",
        recommendation="include",
        action="create_entity",
        reason="recurring authority on neuroscience podcasts",
        evidence=_evidence(),
        risk=[],
        confidence=0.95,
        source_importance=0.9,
        reader_salience=0.85,
        entity_label="Andrew Huberman",
        aliases=["Dr. Huberman", "Andrew D. Huberman"],
        evidence_language="en",
        metadata=PersonMetadata(
            affiliation="Stanford University",
            role="Neuroscience Professor",
            birth_year=1975,
        ),
        canonical_match=EntityCanonicalMatch(
            match_basis="exact_alias",
            matched_entity_path="KB/Wiki/Entities/People/Andrew Huberman.md",
            confidence=0.97,
        ),
        human_decision=_approved_decision(),
    )


def _make_org_entity() -> EntityReviewItem:
    return EntityReviewItem(
        item_id="ent-stanford",
        recommendation="include",
        action="create_entity",
        reason="recurring institution",
        evidence=_evidence(),
        risk=[],
        confidence=0.93,
        source_importance=0.8,
        reader_salience=0.6,
        entity_label="Stanford University",
        metadata=OrganizationMetadata(
            org_type="academic",
            jurisdiction="US",
            website="https://www.stanford.edu",
        ),
    )


def test_person_metadata_validates_with_discriminator() -> None:
    pm = PersonMetadata(affiliation="MIT", role="Professor")
    assert pm.entity_type == "person"
    assert pm.affiliation == "MIT"


def test_organization_metadata_validates_with_discriminator() -> None:
    om = OrganizationMetadata(org_type="academic", jurisdiction="US")
    assert om.entity_type == "organization"
    assert om.org_type == "academic"


def test_entity_review_item_with_person_metadata() -> None:
    item = _make_person_entity()
    assert item.item_kind == "entity"
    assert item.metadata.entity_type == "person"
    assert item.entity_label == "Andrew Huberman"
    assert "Dr. Huberman" in item.aliases


def test_entity_review_item_with_organization_metadata() -> None:
    item = _make_org_entity()
    assert item.metadata.entity_type == "organization"
    assert item.metadata.org_type == "academic"


def test_review_item_union_round_trips_entity_via_discriminator() -> None:
    """Top-level discriminator (`item_kind`) routes Entity correctly."""
    adapter = TypeAdapter(ReviewItem)
    item = _make_person_entity()
    serialized = item.model_dump()
    rebuilt = adapter.validate_python(serialized)
    assert isinstance(rebuilt, EntityReviewItem)
    assert rebuilt.metadata.entity_type == "person"


def test_entity_canonical_match_v10_mirror_basis_none_forbids_path() -> None:
    with pytest.raises(ValidationError, match="match_basis='none'"):
        EntityCanonicalMatch(
            match_basis="none",
            matched_entity_path="KB/Wiki/Entities/People/x.md",
            confidence=0.5,
        )


def test_entity_canonical_match_v10_mirror_non_none_requires_path() -> None:
    with pytest.raises(ValidationError, match="requires matched_entity_path"):
        EntityCanonicalMatch(
            match_basis="exact_alias",
            matched_entity_path=None,
            confidence=0.9,
        )


def test_include_recommendation_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="requires non-empty evidence"):
        EntityReviewItem(
            item_id="ent-no-ev",
            recommendation="include",
            action="create_entity",
            reason="r",
            evidence=[],
            risk=[],
            confidence=0.9,
            source_importance=0.9,
            reader_salience=0.5,
            entity_label="Empty",
            metadata=PersonMetadata(),
        )


def _v1_manifest_kwargs() -> dict:
    return dict(
        manifest_id="m-1",
        source_id="ebook:x",
        created_at="2026-05-26T00:00:00Z",
        status="needs_review",
        recommender=RecommenderMetadata(
            model_name="claude-opus-4-7",
            model_version="2026-04",
            recommended_at="2026-05-26T00:00:00Z",
        ),
        items=[],
        commit_batches=[],
    )


def test_v12_entity_items_require_schema_version_2() -> None:
    """PromotionManifest V12 — entity items in v=1 manifest must raise."""
    with pytest.raises(ValidationError, match="schema_version >= 2"):
        PromotionManifest(
            schema_version=1,
            **{**_v1_manifest_kwargs(), "items": [_make_person_entity()]},
        )


def test_v12_entity_items_accepted_in_schema_version_2() -> None:
    manifest = PromotionManifest(
        schema_version=2,
        **{**_v1_manifest_kwargs(), "items": [_make_person_entity(), _make_org_entity()]},
    )
    assert manifest.schema_version == 2
    assert len(manifest.items) == 2


def test_v12_v1_manifest_without_entity_still_valid() -> None:
    """Backward compat — existing v=1 manifests without entity items load unchanged."""
    src = SourcePageReviewItem(
        item_id="src-1",
        recommendation="include",
        action="create",
        reason="r",
        evidence=_evidence(),
        risk=[],
        confidence=0.9,
        source_importance=0.9,
        reader_salience=0.5,
        target_kb_path="KB/Wiki/Sources/x/ch1.md",
    )
    manifest = PromotionManifest(
        schema_version=1,
        **{**_v1_manifest_kwargs(), "items": [src]},
    )
    assert manifest.schema_version == 1
