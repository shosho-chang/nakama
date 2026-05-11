---
status: draft
date: 2026-05-11
authors: [shosho-chang, claude]
supersedes: []
superseded-by: []
---

# ADR-025: News Coo per-site DOM cleaners

## Context

News Coo (`extensions/news-coo/`) extracts main page content via [Defuddle](https://github.com/kepano/defuddle) — same engine Obsidian Web Clipper uses internally. For straightforward blog/news pages this works well. For academic publishers (Lancet, NEJM, Nature, JAMA, BMJ, …), inline citation widgets get linearised into the prose.

**Concrete failure** — Lancet article *"Hantavirus in humans: a review of clinical aspects and management"*, captured 2026-05-11:

```
Hantaan virus (HTNV) was discovered in 1976,

1.

Lee, HW ∙ Lee, PW ∙ Johnson, KM

**Isolation of the etiologic agent of Korean hemorrhagic fever**

*J Infect Dis.* 1978; **137**:298-308

[Crossref](https://doi.org/10.1093/infdis/137.3.298)

[PubMed](https://pubmed.ncbi.nlm.nih.gov/24670/)
```

The whole reference body is dropped between two clauses of the same paragraph. 256 such injections in this single 12.7k-word article. Reading flow destroyed.

**Root cause** — Lancet wraps each citation marker `<sup>1</sup>` in a hover/click drop-block:

```html
<div class="dropBlock reference-citations">
  <div class="reference-citations__ctrl">…</div>
  …full reference HTML…
</div>
```

Defuddle is a *main-content extractor*, not *citation-aware*. The drop-block is part of the main content DOM tree, so it gets pulled. Same bug reproduces in vanilla Obsidian Web Clipper.

## Decision

Add a **per-site DOM cleaner registry** that runs in the content script *before* Defuddle. Each cleaner is a small module:

- exports a hostname predicate (e.g. `host.endsWith("thelancet.com")`)
- exports a `clean(document)` mutator that removes / rewrites known noise

Layout:

```
extensions/news-coo/src/content/
├── extractor.ts                   ← runs cleaners → defuddle → return
└── siteCleaners/
    ├── index.ts                   ← registry + dispatch
    ├── lancet.ts                  ← removes .dropBlock.reference-citations
    └── (future) nejm.ts, nature.ts, …
```

Default behaviour when no cleaner matches: pass DOM through unchanged (current behaviour). Failure mode is graceful — a broken cleaner can return a no-op and the page still extracts.

**Initial scope**: ship infrastructure + Lancet cleaner only. Subsequent sites added one PR at a time as we encounter them ("step-on-it / fix-it").

**Not in scope** (deferred):

- Reformatting `<sup>` citation markers as proper Markdown footnotes (`[^1]`) and hoisting the publisher's reference list to the bottom — semantically nicer, but requires per-site `<section class="bibliography">` selectors and rewriting marker positions. Treat as Phase 2 once we have ≥3 cleaners and understand patterns.
- Switching to Mozilla Readability — different extractor, more academic-friendly tradition, but loses Defuddle's strength on modern blog/SaaS pages and forces a full re-fixture of our 139-test suite.

## Alternatives considered

**Option B: A + footnote rewriting** — find `<sup>` citation anchors, replace with `[^N]`, hoist publisher reference list to bottom as `[^N]: …`. Closer to original article's reading experience. Cost: each site has a different reference-list selector, and `<sup>` numbering doesn't always match list numbering (some sites use IDs like `bib1`). Estimate: 3–5× the implementation cost of A per site, with brittle edge cases.

**Option C: Replace Defuddle with Mozilla Readability** — Firefox Reader View engine, designed for news + long-form, has decades of academic publisher heuristics baked in. Cost: full extraction-engine swap, all unit-test fixtures regenerated, lose Defuddle's per-element metadata that News Coo's frontmatter relies on.

## Open questions for panel

1. **Scope creep risk**: A is "register a cleaner per site." Will this grow to 30+ cleaners and become a maintenance liability? Or does the long tail of academic publishers actually concentrate on ~10 hosts (Lancet, NEJM, Nature, Science, Cell, JAMA, BMJ, Annals of Internal Medicine, PubMed Central, bioRxiv)?
2. **Cleaner contract**: should cleaners be pure DOM mutators, or return a richer `{cleanedHost, removedNodeCount, warnings[]}` so we can surface "site cleaner v2 active" in the popup as a trust signal?
3. **Test strategy**: stash captured HTML fixtures per cleaner under `tests/content/fixtures/{site}.html` and run cleaner against them? How to keep these from going stale when sites redesign?
4. **Failure detection**: when a publisher redesigns and a cleaner stops matching anything, do we silently degrade (back to today's behaviour), or surface a "cleaner stale" warning?
5. **Per-site overrides for slug / frontmatter** — does this registry naturally extend to *also* let a site cleaner contribute custom title extraction, author extraction, DOI capture? Or keep cleaners DOM-only and route metadata through a separate registry?

## Consequences

If we go ahead with A:

- **Positive** — solves the Lancet bug today; pattern composes; per-site work is small (one selector list); 0 risk to non-matching sites.
- **Negative** — opens a "long tail" obligation; each new site I clip from might surface a new bug; slight test-suite growth per cleaner.
- **Neutral** — sets a precedent that News Coo is allowed to know about specific publishers. PRD §3 didn't anticipate this; might warrant a PRD addendum.

## Slice plan (if approved)

| Slice | Scope | DoD |
|---|---|---|
| 1 | Cleaner registry infrastructure | `extractor.ts` runs registry; default no-op; new tests for dispatch logic |
| 2 | Lancet cleaner + fixture | Hantavirus article captured → 0 inline reference fragments in output |
| 3 | Doc + publish pattern | `docs/news-coo/site-cleaners.md` "how to add a cleaner in 15 min" |

Slice 4+ = one PR per new site as user encounters them.
