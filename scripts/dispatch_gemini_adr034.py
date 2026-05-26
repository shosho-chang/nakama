# ruff: noqa: E501
"""Gemini panel audit dispatch for ADR-034 — focused short audit.

Reads ADR-034 + Codex audit, dispatches focused Gemini lens audit on:
  Axis 1: 路 A vs 路 B (Entity schema cardinality)
  Axis 2: functools.singledispatch maintainability at nakama scale
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = REPO_ROOT / "docs/decisions/ADR-034-promotion-polymorphism-and-entity-review.md"
CODEX_AUDIT_PATH = REPO_ROOT / "docs/research/2026-05-26-codex-adr034-audit.md"
TOPIC = "ADR-034 — Promotion polymorphism unification + Entity review (focused short audit)"

sys.path.insert(0, str(REPO_ROOT))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path("E:/nakama/.env"))
    except ImportError:
        pass


def ask_gemini(prompt: str, system: str) -> str:
    from shared.gemini_client import ask_gemini as _ask

    return _ask(
        prompt,
        system=system,
        model="gemini-2.5-pro",
        max_tokens=6144,
        thinking_budget=2048,
        temperature=0.3,
    )


SYSTEM = (
    "You are an independent third-party auditor providing a second opinion on "
    f"{TOPIC}. The owner (修修) has explicitly asked for push-back from your unique "
    "perspective as a Gemini model — do NOT rubber-stamp existing analyses. "
    "Your value is your different reasoning chain than Claude or GPT-5, "
    "broader fact-recall (Python idiomaticity, schema-design literature), "
    "and willingness to disagree on schema design choices. "
    "Refuse 'looks good overall' as audit output — list specific changes if any."
)


def build_prompt(artifact: str, codex_audit: str) -> str:
    return f"""# {TOPIC} — Multi-Agent Panel Step 3 (Gemini Lens)

You are the THIRD reviewer in a multi-agent panel:
- **Step 1**: Claude (Opus 4.7) drafted ADR-034 after 5-round grill with the owner
- **Step 2**: Codex (GPT-5) audited it on TWO focused axes
- **Step 3 (YOU)**: Gemini's different-prior lens

## Background

**Project:** Nakama — multi-agent Python (3.14) system. ~100 `shared/` modules, Pydantic v2 schemas.

**Decision under review:** ADR-034 freezes 3 design choices, of which TWO are in scope for this audit:

- **D2 EntityReviewItem 路 A** — single class + `entity_type: Literal["person", "organization", "book", "place"]` enum + `entity_metadata: dict[str, Any]`. Rejects路 B (Person/Org/Book as separate ReviewItem classes).
- **D3 polymorphism via `functools.singledispatch`** — rejects Pydantic base-class method / visitor / dict registry.

(D1 Hybrid Entity gate is out of scope — product taste decision, already settled.)

Plan: PR1 pure refactor (no schema change yet), PR2 adds EntityReviewItem.

## Scope — FOCUSED SHORT AUDIT (not full ADR-032/033-scale review)

Owner has invested significant grill time. Most decisions are low-risk. Owner wants Gemini push-back on the SAME TWO axes Codex audited, but through Gemini's different lens:

### Axis 1 — 路 A vs 路 B (Entity schema cardinality)

What does Gemini's prior on Pydantic-heavy Python codebases tell you?

- Pydantic v2 discriminated unions are well-supported (`Annotated[Union[...], Field(discriminator="entity_type")]`). They give type-safe access to entity-specific fields without `dict[str, Any]` escape hatch.
- Trade: 5 subtype classes vs 1 + dict + 4-value enum.
- Claude cited ConceptReviewItem as precedent for路 A. But Concept doesn't have entity-like metadata divergence (Book ISBN vs Person affiliation). Does the precedent actually transfer?
- What does literature on schema-design at 18-month horizon say? When does "single class + escape hatch" pattern rot, and when does it stay clean?

### Axis 2 — `functools.singledispatch` maintainability

What's Gemini's prior on Python multimethod patterns?

- Real-world Python codebases that use singledispatch heavily — what does the maintainability curve look like at year 1, year 2, year 3?
- Codex audit Section 3 (parallel to you) will have raised practical engineering concerns. What does Gemini ADD beyond what Codex sees?
- Specific risks Claude may have understated:
  - **Discoverability**: when navigating an unknown codebase, can you find all `@register`s for a given function?
  - **Serialization / API boundary**: if `render_review_item` is called from a router, does the dispatch failure mode propagate cleanly?
  - **LLM-readability**: nakama uses LLM agents extensively. Does singledispatch make code harder for LLMs to navigate (since dispatch is implicit) compared to explicit OO methods?
- Method-on-base-class alternative: Claude rejected it citing "schema purity". Is that conviction misplaced?

## What you should produce

A **focused 800-1500 word Gemini audit**. Structure:

### Section 1 — GEMINI'S DIFFERENT PRIOR
What you see that Claude + Codex likely both miss. Specific to schema design / Python multimethods at this codebase scale. Push back hard if warranted.

### Section 2 — AXIS 1 LENS (路 A vs 路 B)
Your stance. Pydantic v2 discriminated union literature / common patterns. Specific recommendation.

### Section 3 — AXIS 2 LENS (singledispatch)
Your stance. Production maintainability patterns. LLM-codebase navigability angle (this codebase is heavily LLM-edited by Claude / Codex — does dispatch mechanism matter for this?).

### Section 4 — BLIND SPOTS CLAUDE+CODEX BOTH MISSED
Use the parallel Codex audit. Where do Claude and Codex share the same bias? Be specific.

### Section 5 — VERDICT
- Approve / modify / reject
- Top 2-3 changes ranked by P(future-burn) × cost-to-fix

## Required style

- English (matches Codex audit, helps panel comparison)
- Concrete + specific. Cite ADR sections by name. Cite Codex audit by section number.
- Push back where you disagree. Refuse rubber-stamp.
- Owner reads this directly to make the final call.

## Codex audit (verbatim)

{codex_audit}

## ADR-034 content under review (verbatim)

{artifact}

---

Begin your focused 5-section Gemini audit now."""


def main() -> int:
    _load_env()

    if not CODEX_AUDIT_PATH.exists():
        print(
            f"ERROR: Codex audit not yet at {CODEX_AUDIT_PATH}. "
            "Run codex dispatch first.",
            file=sys.stderr,
        )
        return 1

    artifact = ARTIFACT_PATH.read_text(encoding="utf-8")
    codex_audit = CODEX_AUDIT_PATH.read_text(encoding="utf-8")

    prompt = build_prompt(artifact, codex_audit)

    print("=== Gemini panel audit dispatch — ADR-034 ===", file=sys.stderr)
    print(f"Topic: {TOPIC}", file=sys.stderr)
    print(f"Prompt size: {len(prompt)} chars (~{len(prompt) // 4} tokens)", file=sys.stderr)
    print("Model: gemini-2.5-pro", file=sys.stderr)
    print("---", file=sys.stderr)

    response = ask_gemini(prompt, system=SYSTEM)
    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
