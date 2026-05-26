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
    SourcePageReviewItem,
)


def resolve_target_path(
    item: SourcePageReviewItem | ConceptReviewItem,
) -> str | None:
    """Return the vault-relative target path for ``item``, or ``None``.

    - ``SourcePageReviewItem``: ``item.target_kb_path`` (must be non-empty
      and non-whitespace; ``None`` returned otherwise to let gate surface
      ``target_kb_path_missing``).
    - ``ConceptReviewItem``: ``canonical_match.matched_concept_path`` when
      present; ``None`` when ``canonical_match=None`` or
      ``matched_concept_path`` unset.

    ``None`` is the "no eligible target" signal. Gate emits
    ``target_kb_path_missing`` finding; commit skips with reason.

    Raises:
        NotImplementedError: when ``item`` is not a registered
            ``ReviewItem`` subtype. Defensive — `case _: raise` enforces
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
        case _:
            raise NotImplementedError(
                f"resolve_target_path: no arm for ReviewItem subtype "
                f"{type(item).__name__!r}. Add a `case` per ADR-034 v2 §D3."
            )
