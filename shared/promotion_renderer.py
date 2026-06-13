"""Promotion renderer (ADR-024 Slice 7 / issue #515; Entity arm ADR-034 v2 PR2b).

Deterministic markdown renderers for ``SourcePageReviewItem``,
``ConceptReviewItem`` and ``EntityReviewItem``. Used by
``shared.promotion_commit`` to materialize KB pages from approved review
items.

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

import re

import yaml

from shared.schemas.promotion_manifest import (
    ConceptReviewItem,
    EntityMetadata,
    EntityReviewItem,
    EvidenceAnchor,
    OrganizationMetadata,
    PersonMetadata,
    PromotionManifest,
    SourcePageReviewItem,
)

# ADR-035 §D6 / PR3a-iii — video-source sniffing. Source ids matching this
# prefix get the video body template (## Annotations with mm:ss labels +
# Watch-on-YouTube links) instead of the generic Reason/Evidence/Risks
# layout. The prefix is the same opaque transport string defined by #509
# (``youtube:{video_id}``); we never parse it for identity, only sniff.
_YOUTUBE_SOURCE_ID_PREFIX = "youtube:"

# Matches the timestamp-range locator format defined in ADR-035 §D5
# (``t=<start>[-<end>]``). Kept in sync with the parser in
# ``shared.video_source_map_builder`` and ``thousand_sunny.routers.robin``.
_T_LOCATOR_RE = re.compile(r"t=([0-9]+(?:\.[0-9]+)?)(?:-([0-9]+(?:\.[0-9]+)?))?")

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

# Entity frontmatter shared across Person / Organization variants. Variant-
# specific keys (PersonMetadata fields / OrganizationMetadata fields) are
# appended deterministically by _entity_metadata_fields() — keeps the YAML
# block byte-stable regardless of variant order.
_ENTITY_PAGE_FRONTMATTER_KEYS: tuple[str, ...] = (
    "type",
    "item_id",
    "source_id",
    "entity_kind",
    "entity_label",
    "aliases",
    "evidence_language",
    "recommendation",
    "action",
    "confidence",
    "source_importance",
    "reader_salience",
    "match_basis",
    "matched_entity_path",
    "promoted_at",
    "promoted_from_manifest",
)

# Variant-specific frontmatter key orders (appended after the shared keys
# above). Explicit tuples — NOT dict iteration — for byte-identical output.
_PERSON_METADATA_KEYS: tuple[str, ...] = (
    "affiliation",
    "role",
    "birth_year",
    "death_year",
    "credentials",
)

_ORGANIZATION_METADATA_KEYS: tuple[str, ...] = (
    "org_type",
    "jurisdiction",
    "website",
    "parent_org",
)


def render_review_item(
    item: SourcePageReviewItem | ConceptReviewItem | EntityReviewItem,
    manifest: PromotionManifest,
) -> str:
    """Unified entry point for rendering any ``ReviewItem`` to markdown.

    Dispatch via ``match`` (ADR-034 v2 §D3). Each arm delegates to the
    per-subtype render helper, preserving the byte-identical determinism
    contract documented in :func:`render_source_page` /
    :func:`render_concept_page`.

    Caller-facing API: this is the canonical entry. The per-subtype
    helpers (``render_source_page`` / ``render_concept_page``) remain
    public for backward compatibility but new callers should use this.

    Raises:
        NotImplementedError: ``item`` is not a registered ``ReviewItem``
            subtype. Defensive — see ADR-034 v2 §D3.
    """
    match item:
        case SourcePageReviewItem():
            return render_source_page(item, manifest)
        case ConceptReviewItem():
            return render_concept_page(item, manifest)
        case EntityReviewItem():
            return render_entity_page(item, manifest)
        case _:
            raise NotImplementedError(
                f"render_review_item: no arm for ReviewItem subtype "
                f"{type(item).__name__!r}. Add a `case` per ADR-034 v2 §D3."
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
    sections.append(f"# {item.chapter_title or item.chapter_ref or item.item_id}\n")
    sections.append(f"## Reason\n\n{item.reason.strip()}\n")

    if _is_youtube_source(manifest):
        # ADR-035 §D6 / PR3a-iii — video sources get a dedicated Annotations
        # section with mm:ss labels and per-anchor Watch-on-YouTube deep
        # links. Non-timestamp anchors (defensive — should not appear on
        # video items today) fall back into a generic Evidence section so
        # the page never silently drops evidence.
        timestamp_anchors = [a for a in item.evidence if a.kind == "timestamp_range"]
        other_anchors = [a for a in item.evidence if a.kind != "timestamp_range"]
        video_id = _youtube_video_id_from_source_id(manifest.source_id)
        if timestamp_anchors:
            sections.append("## Annotations\n")
            sections.append(_render_video_annotation_list(timestamp_anchors, video_id))
        if other_anchors:
            sections.append("## Evidence\n")
            sections.append(_render_evidence_list(other_anchors))
    else:
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


def render_entity_page(item: EntityReviewItem, manifest: PromotionManifest) -> str:
    """Render an ``EntityReviewItem`` to markdown.

    Output: frontmatter (fixed key order shared + variant-specific keys) +
    body sections. Body order is fixed: Aliases (if any) → Reason →
    Evidence → Cross-source match (if any) → Metadata details → Risks.
    Two runs with identical input yield byte-identical output.

    ``entity_kind`` is sourced from ``metadata.entity_type`` (the
    discriminator) so frontmatter and on-disk path remain consistent.
    """
    cm = item.canonical_match
    fm: dict[str, object] = {
        "type": "entity",
        "item_id": item.item_id,
        "source_id": manifest.source_id,
        "entity_kind": item.metadata.entity_type,
        "entity_label": item.entity_label,
        "aliases": list(item.aliases),
        "evidence_language": item.evidence_language,
        "recommendation": item.recommendation,
        "action": item.action,
        "confidence": item.confidence,
        "source_importance": item.source_importance,
        "reader_salience": item.reader_salience,
        "match_basis": cm.match_basis if cm is not None else None,
        "matched_entity_path": cm.matched_entity_path if cm is not None else None,
        "promoted_at": _decided_at_or_none(item),
        "promoted_from_manifest": manifest.manifest_id,
    }
    # Append variant-specific keys deterministically.
    fm.update(_entity_metadata_fields(item.metadata))
    fm_block = _render_frontmatter(
        fm,
        _ENTITY_PAGE_FRONTMATTER_KEYS + _entity_metadata_key_order(item.metadata),
    )

    sections: list[str] = []
    sections.append(f"# {item.entity_label}\n")

    if item.aliases:
        bullets = "\n".join(f"- {alias}" for alias in item.aliases)
        sections.append(f"## Aliases\n\n{bullets}\n")

    sections.append(f"## Reason\n\n{item.reason.strip()}\n")

    if item.evidence:
        sections.append("## Evidence\n")
        sections.append(_render_evidence_list(item.evidence))

    if cm is not None and cm.match_basis != "none" and cm.matched_entity_path:
        sections.append(
            "## Cross-source match\n\n"
            f"- match_basis: {cm.match_basis}\n"
            f"- confidence: {cm.confidence}\n"
            f"- matched_entity_path: {cm.matched_entity_path}\n"
        )

    metadata_block = _render_entity_metadata_section(item.metadata)
    if metadata_block:
        sections.append(metadata_block)

    if item.risk:
        sections.append("## Risks\n")
        sections.append(_render_risk_list(item.risk))

    body = "\n".join(sections)
    return f"{fm_block}\n{body}"


# ── Internal helpers ──────────────────────────────────────────────────────────


def _entity_metadata_key_order(metadata: EntityMetadata) -> tuple[str, ...]:
    """Variant-specific frontmatter key order (inner ``match`` on metadata
    discriminator). Adding a new entity_type = add a Metadata class + add
    a `case` arm here + add the variant's key-order tuple at the top of
    this module."""
    match metadata:
        case PersonMetadata():
            return _PERSON_METADATA_KEYS
        case OrganizationMetadata():
            return _ORGANIZATION_METADATA_KEYS
        case _:
            raise NotImplementedError(
                f"_entity_metadata_key_order: no arm for EntityMetadata "
                f"variant {type(metadata).__name__!r}. Add a `case` per "
                "ADR-034 v2 §D3."
            )


def _entity_metadata_fields(metadata: EntityMetadata) -> dict[str, object]:
    """Variant-specific frontmatter values. Returned dict's iteration order
    is irrelevant — ``_render_frontmatter`` orders by ``key_order`` tuple."""
    match metadata:
        case PersonMetadata():
            return {
                "affiliation": metadata.affiliation,
                "role": metadata.role,
                "birth_year": metadata.birth_year,
                "death_year": metadata.death_year,
                "credentials": list(metadata.credentials),
            }
        case OrganizationMetadata():
            return {
                "org_type": metadata.org_type,
                "jurisdiction": metadata.jurisdiction,
                "website": metadata.website,
                "parent_org": metadata.parent_org,
            }
        case _:
            raise NotImplementedError(
                f"_entity_metadata_fields: no arm for EntityMetadata variant "
                f"{type(metadata).__name__!r}. Add a `case` per ADR-034 v2 §D3."
            )


def _render_entity_metadata_section(metadata: EntityMetadata) -> str | None:
    """Render a human-readable Metadata section. Returns ``None`` when the
    variant has no populated fields (avoids empty section in output)."""
    fields = _entity_metadata_fields(metadata)
    lines: list[str] = []
    for key in _entity_metadata_key_order(metadata):
        value = fields.get(key)
        if value is None:
            continue
        if isinstance(value, list) and not value:
            continue
        if isinstance(value, list):
            lines.append(f"- {key}: {', '.join(value)}")
        else:
            lines.append(f"- {key}: {value}")
    if not lines:
        return None
    return "## Metadata\n\n" + "\n".join(lines) + "\n"


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
    item: SourcePageReviewItem | ConceptReviewItem | EntityReviewItem,
) -> str | None:
    """Return ``human_decision.decided_at`` if present, else None."""
    if item.human_decision is None:
        return None
    return item.human_decision.decided_at


# ── Video source helpers (ADR-035 §D6 / PR3a-iii) ─────────────────────────────


def _is_youtube_source(manifest: PromotionManifest) -> bool:
    return manifest.source_id.startswith(_YOUTUBE_SOURCE_ID_PREFIX)


def _youtube_video_id_from_source_id(source_id: str) -> str:
    """Strip the ``youtube:`` prefix to get the canonical 11-char video id.

    Caller must have verified the source via ``_is_youtube_source``;
    returns the raw remainder otherwise (no validation — the prefix
    contract belongs to #509, not the renderer).
    """
    if source_id.startswith(_YOUTUBE_SOURCE_ID_PREFIX):
        return source_id[len(_YOUTUBE_SOURCE_ID_PREFIX) :]
    return source_id


def _parse_timestamp_locator(locator: str) -> float | None:
    """Extract ``start`` seconds from an ADR-035 §D5 ``t=`` locator.

    Returns ``None`` if the locator is not in the timestamp_range shape;
    caller falls back to rendering the row without a seek anchor.
    """
    m = _T_LOCATOR_RE.search(locator)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _format_cue_label(seconds: float) -> str:
    """Format ``seconds`` as ``mm:ss`` (or ``hh:mm:ss`` past one hour).

    Mirrors ``thousand_sunny.routers.robin._format_cue_label`` shape. The
    floor is intentional — seek anchors are integer-second; sub-second
    precision survives in the locator string but not in the human label.
    """
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_watch_url(video_id: str, start_seconds: float) -> str:
    """YouTube deep-link to a specific timestamp.

    ``youtu.be`` short host is preferred over ``youtube.com/watch?v=`` —
    same behavior, half the URL length. Start seconds are floored to int
    because YouTube's ``?t=`` parameter only honors integer seconds.
    """
    start = max(0, int(start_seconds))
    return f"https://youtu.be/{video_id}?t={start}"


def _render_video_annotation_list(anchors: list[EvidenceAnchor], video_id: str) -> str:
    """Render timestamp-range anchors as the video body's ## Annotations
    section. Output order matches caller-supplied list (no implicit sort —
    the builder already orders by cue start).
    """
    parts: list[str] = []
    for anchor in anchors:
        start = _parse_timestamp_locator(anchor.locator)
        excerpt = anchor.excerpt.strip()
        if start is None:
            # Defensive — should not occur once the locator survives the
            # schema's V13 ``timestamp_range`` invariant. Render the
            # anchor without a seek link rather than skip silently.
            parts.append(f"- **[--:--]** `{anchor.locator}`\n  > {excerpt}\n")
            continue
        label = _format_cue_label(start)
        watch_url = _format_watch_url(video_id, start)
        parts.append(
            f"- **[{label}]** `{anchor.locator}`\n"
            f"  > {excerpt}\n"
            f"  [Watch on YouTube]({watch_url})\n"
        )
    return "\n".join(parts) + "\n"
