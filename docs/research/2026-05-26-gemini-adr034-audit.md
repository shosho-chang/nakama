[2026-05-26 17:33:40,241] nakama.gemini_client WARNING — thinking_budget=2048 超過 max_tokens(6144) // 4，自動縮為 1536 避免餓死 output
# Gemini Audit — ADR-034 Promotion Polymorphism + Entity Review

**Date:** 2026-05-26
**Auditor:** Gemini (via multi-agent panel)
**Artifact under review:** `ADR-034-promotion-polymorphism-and-entity-review.md`
**Panel context:** Step 3 of 5, providing a contrarian lens after Claude's draft and Codex's audit.
**Audit scope:** Focused short (2 axes — 路 A vs 路 B + singledispatch maintainability)

---

### Section 1 — GEMINI'S DIFFERENT PRIOR

My analysis diverges from Claude's proposal and Codex's critique by focusing on a second-order effect: the **interaction** between the schema design (D2) and the polymorphism mechanism (D3). Claude and Codex appear to have evaluated these as two independent choices. My prior, drawn from observing schema evolution in data-intensive systems, is that these two decisions are deeply coupled. A weak schema choice (like `dict[str, Any]`) actively poisons the implementation of any polymorphism pattern, including `singledispatch`.

The core flaw in ADR-034 is that it proposes a "fake" polymorphism. It uses `singledispatch` to dispatch on the coarse-grained type (`EntityReviewItem`), but then immediately requires a *second, manual dispatch* inside every registered function via an `if/elif` ladder on the `entity_type` string. This defeats the entire purpose of a clean dispatch mechanism. It combines the non-local nature of `@register` with the brittleness of string-based switching, achieving the worst of both worlds.

Furthermore, my prior from the data engineering world frames this as a classic "Schema-on-Write vs. Schema-on-Read" dilemma. Pydantic's entire value proposition is enforcing Schema-on-Write: data is validated, structured, and typed at the boundary. ADR-034 §D2 proposes abandoning this principle precisely where the data becomes most heterogeneous and error-prone, reverting to a Schema-on-Read model where every consumer of `entity_metadata` must defensively parse and validate a raw dictionary. For a system at Nakama's scale, this is a significant architectural regression.

### Section 2 — AXIS 1 LENS (路 A vs 路 B)

**Stance: Reject 路 A unequivocally. Mandate 路 B via Pydantic v2 Discriminated Unions.**

I find the justification for 路 A in ADR-034 §D2 to be weak and based on a flawed precedent.

1.  **The `ConceptReviewItem` Precedent is Invalid:** The ADR argues that since `ConceptReviewItem` isn't split by domain (medicine, chemistry), `EntityReviewItem` shouldn't be split by type (person, book). This is a false analogy. The *metadata schema* for a medical concept and a chemistry concept is identical: a label, a definition, relationships. The *metadata schema* for a Person (affiliation, birth_year) and a Book (ISBN, authors) is fundamentally different. The precedent does not transfer.

2.  **"Smearing Complexity":** The `entity_metadata: dict[str, Any]` approach does not eliminate complexity; it smears it. As Codex notes in its Section 2, the logic for handling an ISBN or an affiliation moves from one declarative place (the schema) into dozens of imperative checks scattered across renderers, validators, and API endpoints. This is a direct violation of the Don't Repeat Yourself (DRY) principle, as every consumer must re-implement the knowledge that a "book" has an "isbn" which is a string. This pattern is a primary source of technical debt and rot in systems over an 18-month horizon.

3.  **Pydantic's Core Purpose:** The Nakama codebase chose Pydantic v2 for a reason: type safety, auto-documentation, and robust validation at the boundaries. Using `dict[str, Any]` is a deliberate choice to discard these benefits at the most critical point. Pydantic v2's `Annotated[Union[...], Field(discriminator=...)]` is the idiomatic, purpose-built solution for this exact problem. It provides static analysis, IDE autocompletion, and runtime exhaustiveness checking (e.g., with `match` statements) that a dictionary of strings can never offer.

**Specific Recommendation:**

Mandate the use of a discriminated union for `EntityReviewItem`, mirroring the existing pattern for `ReviewItem` itself. This can be done in two ways, with a strong preference for the first:

*   **Option 1 (Preferred): Separate Classes.** This is the cleanest and most extensible.
    ```python
    class PersonMetadata(BaseModel):
        affiliation: str | None = None
        birth_year: int | None = None

    class BookMetadata(BaseModel):
        isbn: str
        authors: list[str]

    class EntityReviewItem(BaseReviewItem):
        item_kind: Literal["entity"] = "entity"
        entity_metadata: Annotated[
            Union[PersonMetadata, BookMetadata, ...],
            Field(discriminator="entity_type") # Assumes a new 'entity_type' field on metadata models
        ]
    ```
    This approach keeps the top-level `ReviewItem` union small (SourcePage, Concept, Entity) while providing full type safety for the heterogeneous metadata.

*   **Option 2 (Acceptable): Flattened Union.** As suggested by Codex.
    ```python
    class PersonReviewItem(BaseEntityReviewItem):
        entity_type: Literal["person"] = "person"
        affiliation: str | None = None
        ...
    # ... and so on for Book, Organization, etc.
    ```
    This makes the top-level `ReviewItem` union larger but is also a perfectly valid and type-safe solution.

The UI concern cited in the ADR ("five card shapes") is a red herring. Presentation logic should not dictate schema integrity. A single template can easily handle typed variants.

### Section 3 — AXIS 2 LENS (singledispatch)

**Stance: Reject `singledispatch` *in combination with 路 A*. It is only acceptable if 路 B (discriminated union) is adopted.**

My primary objection is the "double-dispatch" problem I outlined in Section 1. If the schema is `EntityReviewItem` with an `entity_type` string, a function like `render_review_item` will look like this:

```python
@render_review_item.register
def _(item: EntityReviewItem, ...) -> str:
    # First dispatch (by singledispatch) gets us here.
    # Now, a second, manual dispatch is required.
    if item.entity_type == "person":
        # ... access item.entity_metadata["affiliation"] with no type safety
    elif item.entity_type == "organization":
        # ... access item.entity_metadata["org_type"]
    ...
```

This pattern is an anti-pattern. It offers no advantage over a simple `isinstance` ladder while adding the cognitive overhead of non-local registration.

However, if **and only if** the schema is changed to a proper discriminated union (路 B), then `singledispatch` becomes a reasonable, though not necessarily superior, choice. It could dispatch on `PersonReviewItem`, `BookReviewItem`, etc., cleanly separating the logic.

Even then, I have reservations that build on Codex's points:

1.  **LLM-Readability and Navigability:** This is a critical point for the Nakama project. An LLM agent tasked with "add logic for Place entities" will find it much harder to trace control flow with `singledispatch`. It must first find the base function (`render_review_item`), then perform a global search for `@render_review_item.register` to find all implementations. This is less efficient and more error-prone than finding the `ReviewItem` base class and seeing an abstract `render()` method, or finding a single visitor class with all `visit_*` methods. An explicit `match` statement over a discriminated union is the most readable pattern for both humans and LLMs.

2.  **Discoverability at Scale:** At year 2, with 5-7 `ReviewItem` types and 4-5 dispatched functions (`resolve_target_path`, `render`, `validate`, `commit_hook`), the matrix of registrations becomes difficult to hold in one's head. The logic for a "Person" is not co-located; it's scattered across `promotion_targets.py`, `promotion_renderer.py`, etc. An object-oriented or visitor approach would group all "Person" logic together, improving conceptual locality.

3.  **"Schema Purity" is a Misplaced Conviction:** The ADR rejects methods on Pydantic models to maintain "schema purity." This is an overly dogmatic interpretation. A schema's responsibility is to define the data *and its fundamental behaviors*. A `render()` or `get_target_path()` method is arguably a fundamental behavior. It does not require importing heavy dependencies; it can be an abstract method implemented by subclasses in a different module, or it can call out to registered functions. The current proposal already violates this purity by making the schema's utility dependent on a web of non-local `singledispatch` registrations.

### Section 4 — BLIND SPOTS CLAUDE+CODEX BOTH MISSED

Claude (in the ADR) and Codex (in its audit) share one critical blind spot: **they analyzed the schema (D2) and polymorphism (D3) decisions in isolation.**

The core issue, which neither identified, is the toxic interaction between the two. Codex correctly identified the weakness of `dict[str, Any]` and the risks of `singledispatch` defaults. But it failed to connect the dots: that the `dict` choice makes the `singledispatch` choice actively harmful by creating the "double-dispatch" anti-pattern. The audit approves `singledispatch` with hardening, but this misses the fact that the chosen schema makes the pattern fundamentally flawed from the start.

A second shared blind spot is the **misapplication of precedent and principles**.
-   Claude misapplied the `ConceptReviewItem` precedent.
-   Codex accepted Claude's rejection of methods-on-models based on a "schema purity" argument that is, in this context, a dogmatic constraint rather than a pragmatic design principle. A better principle would be "group related logic together." The ADR's proposal scatters it.

My contribution is to show that D2 and D3 are a single, combined decision. You cannot choose `dict[str, Any]` *and* get clean polymorphism. The two are mutually exclusive. Therefore, the entire ADR as proposed is built on a contradiction.

### Section 5 — VERDICT

**Reject ADR-034 as written. Approve with the following mandatory modifications.**

The current proposal introduces a "fake polymorphism" anti-pattern and knowingly incurs technical debt by using `dict[str, Any]`. It must be corrected before implementation.

**Top 2 Changes Ranked by P(future-burn) × cost-to-fix:**

1.  **(High Priority) Replace D2 (路 A) with a Typed Discriminated Union (路 B).** This is non-negotiable. The `entity_metadata: dict[str, Any]` is an escape hatch that will cause cascading issues in every part of the system that consumes entities. Use Pydantic's `Annotated[Union[...], Field(discriminator=...)]` to model the different entity metadata structures. This single change fixes the highest-probability source of future bugs and maintenance costs.

2.  **(Medium Priority) Re-evaluate D3 (singledispatch) in light of the new schema.** With a proper discriminated union, the team has better options. I recommend replacing `singledispatch` with a simple `match` statement in the primary functions (`render_review_item`, `resolve_target_path`). This pattern is more explicit, more discoverable for humans and LLMs, and leverages the type safety provided by the corrected schema. It also offers compile-time exhaustiveness checking with the right tooling.
    ```python
    # In promotion_renderer.py
    def render_review_item(item: ReviewItem, ...) -> str:
        match item:
            case SourcePageReviewItem():
                return _render_source_page(item, ...)
            case ConceptReviewItem():
                return _render_concept(item, ...)
            case EntityReviewItem():
                # Further dispatch on typed metadata
                return _render_entity(item, ...) # which can itself use a match
            case _:
                # This can be a static analysis error if all types aren't handled
                raise NotImplementedError(...)
    ```
    This approach is simpler, safer, and more maintainable than `singledispatch` for this specific use case. If the team insists on `singledispatch`, it is only acceptable when dispatching on the final, concrete `PersonReviewItem`, `BookReviewItem` types, not the intermediate `EntityReviewItem`.
