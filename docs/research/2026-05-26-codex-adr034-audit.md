# Codex Audit — ADR-034 Promotion Polymorphism + Entity Review

**Date:** 2026-05-26
**Auditor:** Codex CLI (gpt-5, reasoning_effort=medium)
**Artifact under review:** [`docs/decisions/ADR-034-promotion-polymorphism-and-entity-review.md`](../decisions/ADR-034-promotion-polymorphism-and-entity-review.md)
**Panel context:** [`C:\Users\Shosho\.claude\skills\multi-agent-panel\SKILL.md`](file:///C:/Users/Shosho/.claude/skills/multi-agent-panel/SKILL.md), Step 2 of 5
**Audit scope:** Focused short (2 axes — 路 A vs 路 B + singledispatch maintainability)

---

**Section 1 — CODE GROUNDING**

ADR-034 §Context is materially correct, but with one wording correction: the `_resolve_target_path` “雙寫” is not literally two functions named `_resolve_target_path`. In [promotion_acceptance_gate.py](E:/nakama/shared/promotion_acceptance_gate.py:115), target resolution is inline: SourcePage uses `item.target_kb_path`, Concept uses `item.canonical_match.matched_concept_path` at lines 115-128. In [promotion_commit.py](E:/nakama/shared/promotion_commit.py:501), the same policy is a helper `_resolve_target_path()` at lines 501-520. The silent drift risk is real.

The `isinstance` ladder claim also checks out. Gate branches on `SourcePageReviewItem` / `ConceptReviewItem` at [promotion_acceptance_gate.py](E:/nakama/shared/promotion_acceptance_gate.py:115), [promotion_acceptance_gate.py](E:/nakama/shared/promotion_acceptance_gate.py:116), and the Concept-only G6 invariant at [promotion_acceptance_gate.py](E:/nakama/shared/promotion_acceptance_gate.py:219). Commit branches for attachment migration at [promotion_commit.py](E:/nakama/shared/promotion_commit.py:458), target resolution at [promotion_commit.py](E:/nakama/shared/promotion_commit.py:514), and render dispatch at [promotion_commit.py](E:/nakama/shared/promotion_commit.py:609). Renderer has two separate public functions, [render_source_page](E:/nakama/shared/promotion_renderer.py:70) and [render_concept_page](E:/nakama/shared/promotion_renderer.py:110).

The schema already uses Pydantic discriminated unions: `ItemKind = Literal["source_page", "concept"]` at [promotion_manifest.py](E:/nakama/shared/schemas/promotion_manifest.py:127), `SourcePageReviewItem` at [promotion_manifest.py](E:/nakama/shared/schemas/promotion_manifest.py:239), `ConceptReviewItem` at [promotion_manifest.py](E:/nakama/shared/schemas/promotion_manifest.py:279), and `ReviewItem = Annotated[Union[...], Field(discriminator="item_kind")]` at [promotion_manifest.py](E:/nakama/shared/schemas/promotion_manifest.py:319). That matters: ADR-034 §D2 is not proposing a new typing style; it is choosing to stop using the repo’s strongest existing schema tool right where entity fields become more heterogeneous.

**Section 2 — AXIS 1 PUSH-BACK: 路 A vs 路 B**

My stance: reject raw 路 A as written. Prefer a hybrid closer to 路 B: keep one review-queue concept called “Entity”, but model entity variants with typed Pydantic classes or a discriminated metadata union. The ADR-034 §D2 version with `entity_metadata: dict[str, Any]` is the weak point.

Claude’s “ConceptReviewItem precedent” is not persuasive. `ConceptReviewItem` has one domain shape: label, evidence language, canonical match. Person, Org, Book, and Place do not. Person wants aliases, affiliation, role, birth/death years, credentials; Book wants ISBN, authors, edition, publisher; Org wants org type, jurisdiction, website. Pushing that into `dict[str, Any]` means the schema stops carrying the interface. The complexity does not disappear; it resurfaces as string-key checks in renderer, entity promotion engine, acceptance gate, tests, and eventually UI templates. That fails the deletion test: deleting the typed metadata model would not delete complexity, it would smear it across callers.

The right shape is either:

```python
class PersonEntityReviewItem(EntityReviewItemBase):
    item_kind: Literal["entity"] = "entity"
    entity_type: Literal["person"] = "person"
    affiliation: str | None = None
    role: str | None = None

class OrganizationEntityReviewItem(EntityReviewItemBase):
    item_kind: Literal["entity"] = "entity"
    entity_type: Literal["organization"] = "organization"
    org_type: str | None = None
```

with `ReviewItem = Annotated[Union[SourcePageReviewItem, ConceptReviewItem, PersonEntityReviewItem, OrganizationEntityReviewItem, ...], Field(discriminator=...)]`, or one `EntityReviewItem` whose `entity_metadata` is not a dict but a discriminated union of `PersonMetadata | OrganizationMetadata | BookMetadata | PlaceMetadata`.

The UI concern in ADR-034 §Rejected: Split Person / Org / Book is overstated. “Five schema variants” does not require “five card shapes.” The current card already renders a common shell and only branches for concept label/canonical match in [promotion_review/_item_card.html](E:/nakama/thousand_sunny/templates/promotion_review/_item_card.html:33) and [promotion_review/_item_card.html](E:/nakama/thousand_sunny/templates/promotion_review/_item_card.html:107). Entity variants can share one entity card partial and render a compact metadata row by `entity_type`. UI shape is a presentation concern; it should not justify throwing away schema invariants.

One more concrete correction: ADR-034 §D1 says Book Entity bypasses the gate, but §D2 includes `"book"` in the proposed gated `EntityReviewItem` enum. If Book is not going through promotion, do not add `book` to the gated review item in PR2 unless there is a specific correction/review flow. Otherwise the schema advertises a path the product decision says should not exist.

**Section 3 — AXIS 2 PUSH-BACK: `singledispatch` MAINTAINABILITY**

My stance: `singledispatch` is acceptable for PR1 only if the default branch is loud and test-enforced. I would not approve the ADR’s current “each concern gets its own singledispatch” plan if any default returns an empty list or `None` for unsupported item types.

The good part: moving target resolution into a single `promotion_targets.py` module is clearly better than the current inline/helper duplication. A loud `resolve_target_path()` default that raises `NotImplementedError` is the right module depth: callers get one interface, implementation contains subtype policy.

The risk is register hygiene. With `singledispatch`, adding `EntityReviewItem` means remembering registrations in target resolution, rendering, and invariant validation. If `validate_type_specific_invariants()` defaults to `[]`, missing an Entity registration becomes a silent accept. That is worse than the current `isinstance` ladder, because at least a ladder forces you to stare at the branch site. Defaults must raise for unsupported `ReviewItem` subclasses, except where the function is explicitly named as common/no-op behavior.

Type checker reality: mypy/pyright do not give you the same narrowing ergonomics inside `singledispatch` call sites that you get from explicit `if isinstance(...)` branches or methods. The registered function body is typed, but the generic function’s public signature often degrades to the base signature. Callers generally cannot infer “after `render_review_item(item)` this item was a Concept branch.” That may be fine here because callers do not need post-dispatch narrowing, but ADR-034 §Negative understates it. Also, this repo currently has Ruff and pytest configured, but I found no pyright/mypy config in [pyproject.toml](E:/nakama/pyproject.toml:89). So “mypy / pyright support” is mostly theoretical unless CI adds it.

IDE jumping is mixed. `render_review_item.register` is grep-able, but less direct than `render_concept_page`. This is tolerable if the registered functions are named, not anonymous `_`. Use names like `_render_concept_review_item`, not repeated `def _(...)`. Coverage tools will mark registered function bodies normally, but mutation testing and “which variants are covered?” are clearer if each registered arm has explicit tests.

Claude’s rejection of methods on Pydantic models is rigorous enough. Importing YAML/rendering/path policy into `shared/schemas/` would violate the existing schema boundary. But the visitor-pattern rejection is too convenient. A visitor is ceremony, yes, but it gives exhaustiveness pressure. At Nakama’s current size, `singledispatch` is fine for three subtypes; if this grows past four or five review item variants, I would revisit a visitor-like protocol or a central capability table with startup assertions.

**Section 4 — VERDICT**

Approve with modifications.

Top changes:

1. Replace ADR-034 §D2 raw `entity_metadata: dict[str, Any]` with typed entity variants or a typed discriminated metadata union. Keep one UI “Entity” card if desired, but do not make the schema untyped to protect the template count.

2. Harden ADR-034 §D3: every `singledispatch` default must raise `NotImplementedError` for unsupported `ReviewItem` types. Add tests that every `ReviewItem` union member has registrations for target resolution, rendering, and invariants.

3. Remove `"book"` from the gated `EntityReviewItem` PR2 schema unless PR2 explicitly implements a Book review/correction path. ADR-034 currently says Book bypasses the gate, so the schema should not imply otherwise.
