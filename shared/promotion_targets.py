"""Single source of truth for ReviewItem → target_kb_path resolution.

Before this module (ADR-034 v2 / PR1), the policy for "where does this
ReviewItem write to" lived in two places:

- ``shared.promotion_acceptance_gate.AcceptanceGate.validate`` — inline
  ``isinstance(item, SourcePageReviewItem)`` branches at lines 115-128
- ``shared.promotion_commit._resolve_target_path`` — helper at lines 501-521

Two copies of the same policy is silent-drift risk. This module
consolidates them. Both gate + commit call :func:`resolve_target_path`.

Dispatch via ``match`` statement (ADR-034 v2 §D3). Default arm raises
``NotImplementedError`` — missing subtype registration is loud, not silent.

PR2 (per ADR-034 v2) extends this with an ``EntityReviewItem`` arm.
"""

from __future__ import annotations

from shared.schemas.promotion_manifest import (
    ConceptReviewItem,
    EntityMetadata,
    EntityReviewItem,
    OrganizationMetadata,
    PersonMetadata,
    SourcePageReviewItem,
)

_ENTITIES_PEOPLE_DIR = "KB/Wiki/Entities/People"
_ENTITIES_ORGANIZATIONS_DIR = "KB/Wiki/Entities/Organizations"


def resolve_target_path(
    item: SourcePageReviewItem | ConceptReviewItem | EntityReviewItem,
) -> str | None:
    """Return the vault-relative target path for ``item``, or ``None``.

    - ``SourcePageReviewItem``: ``item.target_kb_path`` (must be non-empty
      and non-whitespace; ``None`` returned otherwise to let gate surface
      ``target_kb_path_missing``).
    - ``ConceptReviewItem``: ``canonical_match.matched_concept_path`` when
      present; ``None`` when ``canonical_match=None`` or
      ``matched_concept_path`` unset.
    - ``EntityReviewItem``: ``canonical_match.matched_entity_path`` when
      present (update_merge_entity / update_conflict_entity path); else
      derived from ``entity_label`` + ``metadata.entity_type``
      (``KB/Wiki/Entities/People/{label}.md`` or
      ``KB/Wiki/Entities/Organizations/{label}.md``). Obsidian wikilink
      convention — label is used verbatim (spaces allowed).

    ``None`` is the "no eligible target" signal. Gate emits
    ``target_kb_path_missing`` finding; commit skips with reason.

    Raises:
        NotImplementedError: when ``item`` is not a registered
            ``ReviewItem`` subtype, or when ``EntityMetadata`` variant has
            no registered arm. Defensive — `case _: raise` enforces
            register hygiene (ADR-034 v2 §D3).
    """
    match item:
        case SourcePageReviewItem(target_kb_path=p) if p and p.strip():
            return p
        case SourcePageReviewItem():
            return None
        case ConceptReviewItem(canonical_match=cm) if cm and cm.matched_concept_path:
            return cm.matched_concept_path
        case ConceptReviewItem():
            return None
        case EntityReviewItem(canonical_match=cm) if cm and cm.matched_entity_path:
            return cm.matched_entity_path
        case EntityReviewItem(entity_label=label, metadata=meta):
            return _entity_target_path(label, meta)
        case _:
            raise NotImplementedError(
                f"resolve_target_path: no arm for ReviewItem subtype "
                f"{type(item).__name__!r}. Add a `case` per ADR-034 v2 §D3."
            )


def _entity_target_path(label: str, metadata: EntityMetadata) -> str:
    """Derive vault-relative entity page path from label + metadata variant.

    Inner ``match`` dispatch on the ``EntityMetadata`` discriminator
    (ADR-034 v2 §D3 — outer dispatch on ``ReviewItem``, inner on
    ``EntityMetadata``). Adding a new entity_type = add a Metadata class
    + add a `case` arm here.

    Raises:
        NotImplementedError: ``metadata`` is not a registered variant.
    """
    match metadata:
        case PersonMetadata():
            return f"{_ENTITIES_PEOPLE_DIR}/{label}.md"
        case OrganizationMetadata():
            return f"{_ENTITIES_ORGANIZATIONS_DIR}/{label}.md"
        case _:
            raise NotImplementedError(
                f"_entity_target_path: no arm for EntityMetadata variant "
                f"{type(metadata).__name__!r}. Add a `case` per ADR-034 v2 §D3."
            )
