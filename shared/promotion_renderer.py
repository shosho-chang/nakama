"""Promotion renderer (ADR-024 Slice 7 / issue #515).

Deterministic markdown renderers for ``SourcePageReviewItem`` and
``ConceptReviewItem``. Used by ``shared.promotion_commit`` to materialize
KB pages from approved review items.

Determinism contract: identical inputs MUST yield byte-identical outputs.
Frontmatter key order is fixed (explicit list, NOT ``dict`` iteration);
list ordering preserves caller order (no implicit sorting that would mask
upstream non-determinism).

Boundaries (Brief §6 boundary 9): no Jinja, no template-engine dependency.
Pure stdlib f-strings + ``yaml.safe_dump`` (already a project dep via
``shared.promotion_preflight``).

Output shape: YAML frontmatter between ``---`` fences, blank line, then
markdown body. Frontmatter keys explicit per item kind; body sections
labeled with H2 headings.
"""

from __future__ import annotations

import yaml

from shared.schemas.promotion_manifest import (
    ConceptReviewItem,
    EvidenceAnchor,
    PromotionManifest,
    SourcePageReviewItem,
)

# ── Frontmatter key order ──────────────────────────────────────────────────────
# Explicit lists (NOT dict iteration) so two runs produce byte-identical YAML.
# Extension is a behavior change — schema_version bump on the schema module
# may need to coordinate with rendered output if a downstream consumer parses
# these pages.

_SOURCE_PAGE_FRONTMATTER_KEYS: tuple[str, ...] = (
    "type",
    "item_id",
    "source_id",
    "chapter_ref",
    "recommendation",
    "action",
    "confidence",
    "source_importance",
    "reader_salience",
    "promoted_at",
    "promoted_from_manifest",
)

_CONCEPT_PAGE_FRONTMATTER_KEYS: tuple[str, ...] = (
    "type",
    "item_id",
    "source_id",
    "concept_label",
    "evidence_language",
    "recommendation",
    "action",
    "confidence",
    "source_importance",
    "reader_salience",
    "match_basis",
    "matched_concept_path",
    "promoted_at",
    "promoted_from_manifest",
)


def render_source_page(item: SourcePageReviewItem, manifest: PromotionManifest) -> str:
    """Render a ``SourcePageReviewItem`` to markdown.

    Output: frontmatter (fixed key order, ``yaml.safe_dump`` with
    ``sort_keys=False``) + body sections. Body order is fixed:
    Reason → Evidence → Risks. Empty sections are omitted (deterministic
    "include only if list is non-empty"). Two runs with identical input
    yield byte-identical output (T15 idempotency).
    """
    fm: dict[str, object] = {
        "type": "source_page",
        "item_id": item.item_id,
        "source_id": manifest.source_id,
        "chapter_ref": item.chapter_ref,
        "recommendation": item.recommendation,
        "action": item.action,
        "confidence": item.confidence,
        "source_importance": item.source_importance,
        "reader_salience": item.reader_salience,
        "promoted_at": _decided_at_or_none(item),
        "promoted_from_manifest": manifest.manifest_id,
    }
    fm_block = _render_frontmatter(fm, _SOURCE_PAGE_FRONTMATTER_KEYS)

    sections: list[str] = []
    sections.append(f"# {item.chapter_ref or item.item_id}\n")
    sections.append(f"## Reason\n\n{item.reason.strip()}\n")

    if item.evidence:
        sections.append("## Evidence\n")
        sections.append(_render_evidence_list(item.evidence))

    if item.risk:
        sections.append("## Risks\n")
        sections.append(_render_risk_list(item.risk))

    body = "\n".join(sections)
    return f"{fm_block}\n{body}"


def render_concept_page(item: ConceptReviewItem, manifest: PromotionManifest) -> str:
    """Render a ``ConceptReviewItem`` to markdown.

    Output: frontmatter (fixed key order) + body sections. Body order is
    fixed: Aliases (if any) → Reason → Evidence → Cross-source match (if
    any) → Risks. Two runs with identical input yield byte-identical output.
    """
    cm = item.canonical_match
    fm: dict[str, object] = {
        "type": "concept",
        "item_id": item.item_id,
        "source_id": manifest.source_id,
        "concept_label": item.concept_label,
        "evidence_language": item.evidence_language,
        "recommendation": item.recommendation,
        "action": item.action,
        "confidence": item.confidence,
        "source_importance": item.source_importance,
        "reader_salience": item.reader_salience,
        "match_basis": cm.match_basis if cm is not None else None,
        "matched_concept_path": cm.matched_concept_path if cm is not None else None,
        "promoted_at": _decided_at_or_none(item),
        "promoted_from_manifest": manifest.manifest_id,
    }
    fm_block = _render_frontmatter(fm, _CONCEPT_PAGE_FRONTMATTER_KEYS)

    sections: list[str] = []
    sections.append(f"# {item.concept_label}\n")
    sections.append(f"## Reason\n\n{item.reason.strip()}\n")

    if item.evidence:
        sections.append("## Evidence\n")
        sections.append(_render_evidence_list(item.evidence))

    if cm is not None and cm.match_basis != "none" and cm.matched_concept_path:
        sections.append(
            "## Cross-source match\n\n"
            f"- match_basis: {cm.match_basis}\n"
            f"- confidence: {cm.confidence}\n"
            f"- matched_concept_path: {cm.matched_concept_path}\n"
        )

    if item.risk:
        sections.append("## Risks\n")
        sections.append(_render_risk_list(item.risk))

    body = "\n".join(sections)
    return f"{fm_block}\n{body}"


# ── Internal helpers ──────────────────────────────────────────────────────────


def _render_frontmatter(values: dict[str, object], key_order: tuple[str, ...]) -> str:
    """Render frontmatter as YAML between ``---`` fences. Key order is fixed
    (caller-supplied tuple); ``yaml.safe_dump`` is invoked with
    ``sort_keys=False`` and ``allow_unicode=True`` for deterministic output."""
    ordered: dict[str, object] = {}
    for key in key_order:
        ordered[key] = values.get(key)
    yaml_text = yaml.safe_dump(
        ordered,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return f"---\n{yaml_text}---\n"


def _render_evidence_list(anchors: list[EvidenceAnchor]) -> str:
    """Render a list of EvidenceAnchor objects as bullet-list markdown.
    Order matches caller-supplied list (no implicit sort)."""
    parts: list[str] = []
    for anchor in anchors:
        excerpt = anchor.excerpt.strip()
        parts.append(
            f"- **{anchor.kind}** `{anchor.locator}` "
            f"(confidence={anchor.confidence})\n"
            f"  > {excerpt}\n"
            f"  source: `{anchor.source_path}`\n"
        )
    return "\n".join(parts) + "\n"


def _render_risk_list(risks) -> str:
    """Render a list of RiskFlag objects as bullet-list markdown."""
    parts: list[str] = []
    for r in risks:
        parts.append(f"- **{r.code}** ({r.severity}): {r.description}")
    return "\n".join(parts) + "\n"


def _decided_at_or_none(
    item: SourcePageReviewItem | ConceptReviewItem,
) -> str | None:
    """Return ``human_decision.decided_at`` if present, else None."""
    if item.human_decision is None:
        return None
    return item.human_decision.decided_at
