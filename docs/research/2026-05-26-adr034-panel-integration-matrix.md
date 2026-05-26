# ADR-034 Panel Integration Matrix

**Date:** 2026-05-26
**Panel:** Claude (Opus 4.7) v1 → Codex (GPT-5 medium) audit → Gemini 2.5 Pro audit
**Audit scope:** Focused short (Axis 1 路 A vs 路 B + Axis 2 singledispatch maintainability)
**Audits:**
- [Codex audit](2026-05-26-codex-adr034-audit.md)
- [Gemini audit](2026-05-26-gemini-adr034-audit.md)

---

## Integration matrix

| # | Topic | Claude v1 | Codex | Gemini | 3-way pattern | Resolution |
|---|---|---|---|---|---|---|
| 1 | **D2: 路 A `entity_type` enum + `dict[str, Any]`** | Adopt 路 A | **Reject** — `dict[str, Any]` fails deletion test; complexity smears across renderer/gate/engine/tests/UI | **Reject unequivocally** — Schema-on-Read regression; ConceptReviewItem precedent invalid (Concept has uniform metadata shape, Entity does not) | **Universal reject 路 A** | **Adopt: discriminated metadata union** (Pydantic v2 `Annotated[Union[...], Field(discriminator="entity_type")]`) matching existing `ReviewItem` discriminator pattern at `promotion_manifest.py:321` |
| 2 | **D3: `functools.singledispatch` primary mechanism** | Adopt | Conditional approve — only with raise-default + named registered funcs | **Reject in combination with 路 A** (fake polymorphism via double-dispatch); prefer `match` statement even after route correction; LLM-codebase navigability worse than `match` | **2-of-2 reject D3-as-written** | **Modify: use `match` statement on outer `ReviewItem` dispatch**; keep `singledispatch` deprioritized (consider only if subtypes >5 later) |
| 3 | **D2/D1 internal contradiction: `book` in gated `entity_type` enum** | Both, no flag | Caught — Book bypasses gate per §D1 but appears in §D2 enum | (didn't focus) | Codex single-source | **Adopt: remove `book` from gated `EntityReviewItem.metadata` union** in PR2; keep `kb_writer.write_book_entity()` as Book's sole path |
| 4 | **Dispatch default behavior (silent vs raise)** | Implicit silent (default `NotImplementedError` in example) | Mandate raise everywhere — silent default worse than `isinstance` ladder | (covered by `match` `case _: raise`) | Codex single-source + Gemini parallel | **Adopt: every dispatch default raises `NotImplementedError`** with the unhandled type name; `match` `case _: raise` is the idiom |
| 5 | **D2+D3 coupling** (toxic interaction) | Treated separately | Treated separately | **NEW insight** — `dict[str, Any]` + singledispatch = double-dispatch anti-pattern; combines non-local `@register` with brittle string-switching | Gemini single-source novel | **Adopt framing**: ADR v2 must address D2 + D3 together, not as independent axes |
| 6 | **`ReviewItem` discriminated union already exists** | Treated as new design | **Caught** — `promotion_manifest.py:321` already uses `Annotated[Union[...], Field(discriminator="item_kind")]` | Same observation | Universal | **Adopt: extend existing pattern, don't invent new one** |
| 7 | **`schema purity` rejection of methods-on-models** | Strong rejection | Endorsed Claude | **Pushed back** — "schema purity" is dogmatic; abstract methods or thin behavior on schema is legitimate | Single-source dissent (Gemini) | **Note in v2 §Considered Options** — schema-purity not absolute principle; v2 still avoids methods-on-models because dispatch is now `match`-based not OO-based, but doctrine softened |
| 8 | **Type-checker (mypy/pyright) reality** | Cited as positive for singledispatch | **Caught** — no pyright/mypy in `pyproject.toml`; "narrowing support" is theoretical for nakama today | (covered by `match` having own static-analysis story) | Codex single-source factual | **Adopt: drop the mypy claim from §Negative**; note `match` exhaustiveness depends on static analyzer config (future work) |
| 9 | **LLM-codebase navigability** | Not addressed | Not addressed | **NEW** — nakama is heavily LLM-edited; singledispatch dispatch is implicit (global `@register` search), `match` is explicit and easier to trace | Gemini single-source novel | **Adopt: `match` over singledispatch additionally justified by LLM-readability**; add to v2 §Decision rationale |
| 10 | **UI "5 card shapes" concern** | Cited as 路 A rationale | **Refute** — `_item_card.html` already common shell + per-type branches; UI can take 5 typed variants with no template explosion | Same refute | Universal refute | **Adopt: drop "UI complexity" as path-A rationale** — actual UI structure refutes it |
| 11 | **`_resolve_target_path` 雙寫 friction** | Real bug risk | Verified — confirmed at `gate.py:115-128` + `commit.py:501-520` | (didn't focus) | Codex verified Claude | **No change** — PR1 still solves this via `promotion_targets.py` extraction |
| 12 | **Code grounding (file paths / line numbers)** | Asserted | **All verified** — gate lines, commit lines, renderer functions, schema locations all check out | (didn't focus) | Codex verified | **No change** — v1 grounding correct |

---

## Adoption summary

**Universal adoptions (3-way or 2-of-2 agreement):**
- #1 — Discriminated metadata union (extends existing repo pattern)
- #2 — `match` over `singledispatch`
- #4 — Defaults raise, no silent
- #6 — Reuse existing `Field(discriminator=...)` pattern
- #10 — Drop UI rationale

**Single-source adoptions (kept because evidence is solid):**
- #3 — Remove `book` from gated enum (Codex factual)
- #5 — Recognize D2+D3 coupling in v2 framing (Gemini novel)
- #8 — Drop mypy claim (Codex factual)
- #9 — `match` better for LLM navigability (Gemini novel)

**Single-source notes (acknowledged, not adopted):**
- #7 — Schema-purity dogmatic — note in §Considered Options, v2 still avoids methods-on-models for other reasons

**v1 retentions (unchanged):**
- D1 Hybrid Entity gate (Book auto / Person+Org gate / confidence fast-track) — out of audit scope
- PR1 / PR2 sequencing
- `promotion_targets.py` new file as single source of truth (refactor structure)
- Renderer determinism contract (byte-identical)

---

## v1 → v2 engineering-impact deltas

| Delta | v1 effort | v2 effort | Why |
|---|---|---|---|
| PR1 — refactor scope | abstract `singledispatch` everywhere | abstract `match` everywhere | Net same complexity; `match` is stdlib syntax, no new dep |
| PR2 — EntityReviewItem schema | 1 class + enum + `dict[str, Any]` | 1 class + 2-3 typed `Metadata` subclasses + discriminated union | +0.5d schema design; saves ~2-3d debugging dict-key drift later |
| PR2 — entity_type scope | Person/Org/Book/Place 4 types | Person/Org 2 types (defer Book + Place) | -1d scope; Book stays in kb_writer; Place added when needed |
| Risk reduction | medium silent-bug risk in renderer / engine | low (types enforce shape at boundary) | Schema-on-Write at validation boundary |

Net: v2 is similar effort, lower risk, and more idiomatic to existing nakama Pydantic-discriminated-union convention.
