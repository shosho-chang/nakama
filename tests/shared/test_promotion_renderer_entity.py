"""Tests for ``render_entity_page`` (ADR-034 v2 PR2b).

Covers:
- Byte-deterministic rendering (two runs → identical bytes)
- Frontmatter key order (shared + variant-specific)
- Body section order: heading → Aliases → Reason → Evidence →
  Cross-source match → Metadata → Risks
- Person variant: affiliation / role / birth_year / death_year / credentials
- Organization variant: org_type / jurisdiction / website / parent_org
- Empty optional sections omitted (no empty Aliases / Metadata / Risks block)
- canonical_match cross-source section only when basis != "none"
"""

from __future__ import annotations

import hashlib

from shared.promotion_renderer import render_entity_page
from shared.schemas.promotion_manifest import (
    EntityCanonicalMatch,
    EntityReviewItem,
    EvidenceAnchor,
    HumanDecision,
    OrganizationMetadata,
    PersonMetadata,
    PromotionManifest,
    RecommenderMetadata,
    RiskFlag,
)


def _evidence() -> list[EvidenceAnchor]:
    return [
        EvidenceAnchor(
            kind="external_ref",
            source_path="https://youtu.be/abc123",
            locator="12:34",
            excerpt="Huberman discussing dopamine baseline.",
            confidence=0.92,
        )
    ]


def _manifest() -> PromotionManifest:
    return PromotionManifest(
        schema_version=2,
        manifest_id="m-ent-1",
        source_id="podcast:hubermanlab-ep-123",
        created_at="2026-05-27T00:00:00Z",
        status="needs_review",
        recommender=RecommenderMetadata(
            model_name="claude-opus-4-7",
            model_version="2026-04",
            recommended_at="2026-05-27T00:00:00Z",
        ),
        items=[],
        commit_batches=[],
    )


def _approved() -> HumanDecision:
    return HumanDecision(
        decision="approve",
        decided_at="2026-05-27T00:10:00Z",
        decided_by="tester",
    )


def _person_full() -> EntityReviewItem:
    return EntityReviewItem(
        item_id="ent-huberman",
        recommendation="include",
        action="create_entity",
        reason="recurring authority across neuroscience podcasts",
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
            credentials=["PhD"],
        ),
        canonical_match=EntityCanonicalMatch(
            match_basis="exact_alias",
            matched_entity_path="KB/Wiki/Entities/People/Andrew Huberman.md",
            confidence=0.97,
        ),
        human_decision=_approved(),
    )


def _org_minimal() -> EntityReviewItem:
    return EntityReviewItem(
        item_id="ent-stanford",
        recommendation="include",
        action="create_entity",
        reason="recurring institution",
        evidence=_evidence(),
        risk=[],
        confidence=0.9,
        source_importance=0.8,
        reader_salience=0.5,
        entity_label="Stanford University",
        metadata=OrganizationMetadata(),
    )


def test_render_entity_page_is_byte_deterministic() -> None:
    item = _person_full()
    manifest = _manifest()
    first = render_entity_page(item, manifest)
    second = render_entity_page(item, manifest)
    assert first == second
    assert (
        hashlib.sha256(first.encode("utf-8")).hexdigest()
        == hashlib.sha256(second.encode("utf-8")).hexdigest()
    )


def test_render_person_entity_frontmatter_contains_variant_keys() -> None:
    out = render_entity_page(_person_full(), _manifest())
    # Frontmatter region — between the two `---` fences.
    fm_block = out.split("---\n", 2)[1]
    # Shared keys
    assert "type: entity" in fm_block
    assert "entity_kind: person" in fm_block
    assert "entity_label: Andrew Huberman" in fm_block
    # Person-specific
    assert "affiliation: Stanford University" in fm_block
    assert "role: Neuroscience Professor" in fm_block
    assert "birth_year: 1975" in fm_block
    assert "credentials:" in fm_block
    # Org-specific keys must NOT leak
    assert "org_type" not in fm_block
    assert "jurisdiction" not in fm_block


def test_render_organization_entity_frontmatter_contains_variant_keys() -> None:
    item = EntityReviewItem(
        item_id="ent-org-1",
        recommendation="include",
        action="create_entity",
        reason="r",
        evidence=_evidence(),
        risk=[],
        confidence=0.9,
        source_importance=0.8,
        reader_salience=0.5,
        entity_label="Stanford University",
        metadata=OrganizationMetadata(
            org_type="academic",
            jurisdiction="US",
            website="https://www.stanford.edu",
        ),
    )
    out = render_entity_page(item, _manifest())
    fm_block = out.split("---\n", 2)[1]
    assert "entity_kind: organization" in fm_block
    assert "org_type: academic" in fm_block
    assert "jurisdiction: US" in fm_block
    assert "website: https://www.stanford.edu" in fm_block
    # Person-specific keys must NOT leak
    assert "affiliation" not in fm_block
    assert "birth_year" not in fm_block


def test_render_entity_body_section_order() -> None:
    out = render_entity_page(_person_full(), _manifest())
    body = out.split("---\n", 2)[2]
    aliases_idx = body.index("## Aliases")
    reason_idx = body.index("## Reason")
    evidence_idx = body.index("## Evidence")
    crossmatch_idx = body.index("## Cross-source match")
    metadata_idx = body.index("## Metadata")
    assert aliases_idx < reason_idx < evidence_idx < crossmatch_idx < metadata_idx


def test_render_entity_omits_empty_optional_sections() -> None:
    """Org with no metadata fields → no Metadata section. No aliases → no
    Aliases section. No risks → no Risks section."""
    out = render_entity_page(_org_minimal(), _manifest())
    assert "## Aliases" not in out
    assert "## Metadata" not in out
    assert "## Risks" not in out
    assert "## Cross-source match" not in out  # canonical_match=None
    # Required sections still present
    assert "## Reason" in out
    assert "## Evidence" in out


def test_render_entity_cross_source_match_only_when_path_set() -> None:
    """canonical_match with match_basis='none' → no Cross-source section."""
    item = EntityReviewItem(
        item_id="ent-no-cm",
        recommendation="include",
        action="create_entity",
        reason="r",
        evidence=_evidence(),
        risk=[],
        confidence=0.9,
        source_importance=0.9,
        reader_salience=0.5,
        entity_label="Someone New",
        metadata=PersonMetadata(),
        canonical_match=EntityCanonicalMatch(
            match_basis="none",
            matched_entity_path=None,
            confidence=0.0,
        ),
    )
    out = render_entity_page(item, _manifest())
    assert "## Cross-source match" not in out


def test_render_entity_risks_section_rendered_when_present() -> None:
    item = EntityReviewItem(
        item_id="ent-risky",
        recommendation="include",
        action="create_entity",
        reason="r",
        evidence=_evidence(),
        risk=[
            RiskFlag(
                code="cross_lingual_uncertain",
                severity="medium",
                description="zh-Hant alias not yet linked",
            )
        ],
        confidence=0.9,
        source_importance=0.9,
        reader_salience=0.5,
        entity_label="安德魯·胡伯曼",
        metadata=PersonMetadata(),
    )
    out = render_entity_page(item, _manifest())
    assert "## Risks" in out
    assert "cross_lingual_uncertain" in out


def test_render_entity_unicode_label_preserved() -> None:
    """Non-ASCII labels render verbatim (no slugification / escaping)."""
    item = EntityReviewItem(
        item_id="ent-cjk",
        recommendation="include",
        action="create_entity",
        reason="r",
        evidence=_evidence(),
        risk=[],
        confidence=0.9,
        source_importance=0.9,
        reader_salience=0.5,
        entity_label="安德魯·胡伯曼",
        aliases=["Andrew Huberman"],
        metadata=PersonMetadata(),
    )
    out = render_entity_page(item, _manifest())
    assert "# 安德魯·胡伯曼" in out
    assert "entity_label: 安德魯·胡伯曼" in out
