---
status: accepted
date: 2026-05-11
authors: [shosho-chang, claude]
panel-audit: [codex (gpt-5), gemini-2.5-pro]
panel-trail:
  - docs/research/2026-05-11-codex-adr025-site-cleaners-audit.md
  - docs/research/2026-05-11-gemini-adr025-site-cleaners-audit.md
supersedes: []
superseded-by: []
---

# ADR-025: News Coo per-site DOM cleaners

## Context

News Coo (`extensions/news-coo/`) extracts main page content via [Defuddle](https://github.com/kepano/defuddle) — same engine Obsidian Web Clipper uses internally. For straightforward blog/news pages this works well. For academic publishers (Lancet, NEJM, Nature, JAMA, BMJ, …), inline citation widgets get linearised into the prose.

**Concrete failure** — Lancet article *"Hantavirus in humans: a review of clinical aspects and management"*, captured 2026-05-11. The captured Markdown contains 256 lines matching `^\d+\.$` (citation marker stubs) — each one followed by author/title/journal blocks dropped between two clauses of the same paragraph:

```
Hantaan virus (HTNV) was discovered in 1976,

1.

Lee, HW ∙ Lee, PW ∙ Johnson, KM

**Isolation of the etiologic agent of Korean hemorrhagic fever**

*J Infect Dis.* 1978; **137**:298-308

[Crossref](...) [PubMed](...) [Google Scholar](...)
```

Reading flow destroyed. Same bug reproduces in vanilla Obsidian Web Clipper — not a News Coo bug per se, but a Defuddle-shaped gap.

**Root cause** — Lancet wraps each citation marker `<sup>1</sup>` in a hover/click drop-block:

```html
<div class="dropBlock reference-citations">
  <div class="reference-citations__ctrl">…</div>
  …full reference HTML…
</div>
```

Defuddle does have citation handling — `adoptExternalFootnotes` and `standardizeFootnotes` run during parsing (`extensions/news-coo/node_modules/defuddle/dist/defuddle.js:772-780`), and the README explicitly documents footnote standardization (`extensions/news-coo/node_modules/defuddle/README.md:211-213`). What Defuddle does **not** recognize is Lancet's specific drop-block pattern: the reference content sits as a peer DOM node next to each `<sup>`, not as a marker → footnote pair, so Defuddle treats the whole drop-block as inline body content.

## Decision

Two-stage decision, sequenced:

### Stage 0 — Print-stylesheet spike (1-day timebox)

**Before** committing to a per-site registry, validate a generic heuristic: many academic publishers ship a `<link rel="stylesheet" media="print">` that strips interactive cruft (citation pop-ups, sidebars, share widgets) for printable output. If we apply the print stylesheet to the cloned DOM before Defuddle, a single change may fix a class of sites without per-site code.

**Spike DoD**:

- Build a `applyPrintStylesheets(doc)` helper that finds print stylesheets, switches `media` to `all`, and waits for re-layout
- Test against captured fixtures: Lancet (Hantavirus), one Nature article, one BMJ article
- Compare output quality vs current Markdown (count `^\d+\.$`, sentence-fragment heuristic, manual eyeball)

**Decision branches** at end of spike:

| Spike result | Action |
|--|--|
| ≥2 of 3 fixtures dramatically improve | Print pass becomes default in `extractor`; per-site registry (Stage 1) becomes fallback only, lower priority |
| 1 of 3 improves, others unchanged | Ship print pass as opt-in helper; proceed with Stage 1 as primary |
| 0 of 3 improves | Skip print pass entirely; go straight to Stage 1 |

### Stage 1 — Per-site DOM cleaner registry

Add cleaners that run *after* DOM clone and *before* Defuddle. Layout:

```
extensions/news-coo/src/content/
├── extractor.ts                       ← runs print pass (if Stage 0 wins) → cleaners → defuddle
└── siteCleaners/
    ├── index.ts                       ← registry + dispatch + staleness signal
    ├── types.ts                       ← SiteCleaner interface
    ├── lancet.ts                      ← removes .dropBlock.reference-citations
    └── (future) nejm.ts, nature.ts, …
```

**Cleaner contract** (mandatory, derived from Codex+Gemini audits):

```typescript
interface SiteCleaner {
  name: string;                                          // for staleness logging
  matches: (host: string, url: string) => boolean;       // list of hosts / regex / DOI prefix OK
  clean: (doc: Document, url: string) => CleanReport;    // mutates the cloned doc, NOT live page
}

interface CleanReport {
  matched: boolean;
  removedNodeCount: number;
  warnings: string[];
}
```

**Invariants**:

- **Document ownership**: cleaners run on a `cloneNode(true)` of the target document, never the live page (Defuddle's own clone happens later — we clone earlier).
- **`<head>` is off-limits**: extractor still reads `<head>` for canonical URL, og:image, etc. Cleaners operate on `<body>` subtree only.
- **Per-cleaner exception isolation**: dispatcher wraps each `clean()` in try/catch; a throwing cleaner reports `warnings: [<error>]` and other cleaners + Defuddle still run.
- **Staleness detection**: when a cleaner's `matches()` returns true but `removedNodeCount === 0`, dispatcher logs a `cleaner_stale` event with hostname + cleaner name. Later phase: surface in popup as warning chip.
- **Selection clipping**: cleaners run on full-document path only. Selection-clip path (`extractPage` synthetic doc) skips cleaners — selection content was already user-curated.

**Reuse**: Stage 1 host predicates can crib from Robin's existing publisher-domain table (`agents/robin/url_dispatcher.py:81-96` — 12 academic domains), but we DO NOT import Python from a TS extension. Manually port the list to a shared JSON or duplicate (12 entries — duplication acceptable for now; revisit when ≥3 cleaners exist).

### Initial scope (this ADR ships)

- Stage 0 spike + decision
- Stage 1 infrastructure if Stage 0 doesn't fully solve
- Lancet cleaner (English-first; CJK left to ADR-026 trigger)

### Explicit non-goals

- **CJK publishers** (CNKI, J-STAGE, KISS): real risk, deferred. Architecture is generic enough (list-based `matches`) that adding non-`<sup>` marker support won't require redesign. First CJK encounter triggers ADR-026.
- **Footnote rewriting (`<sup>1</sup>` → `[^1]` + hoisted `[^1]:` reference list)**: deferred to Phase 2. Higher value but per-site reference-list selectors + marker-to-list ID mapping is brittle.
- **Defuddle replacement (Mozilla Readability)**: not pursued. Loses Defuddle's metadata strength on modern web pages; full test-fixture rewrite cost.
- **JSON-LD `ScholarlyArticle` parsing**: future work; orthogonal to this ADR.
- **Defuddle upstream PR**: encouraged but not in this ADR's scope. Long-term, a `pluggable rules` API in Defuddle would centralize this maintenance for the whole community.

## Alternatives considered

**Stage 1 only (skip spike)** — what v1 of this ADR proposed. Rejected because Gemini surfaced print-stylesheet heuristic; cheap to validate, potentially obviates 50% of per-site work.

**Plan B: A + footnote rewriting** — closer to original article reading experience. Each site's reference-list selector differs; marker IDs don't always match list numbering. Estimate 3–5× implementation cost vs Stage 1 (uncalibrated estimate). Phase 2 candidate, not now.

**Plan C: Replace Defuddle with Mozilla Readability** — academic-friendlier, but loses Defuddle's modern-web strength and forces full re-fixture of 139-test suite. Not pursued.

**Defuddle's `removeExactSelectors` / `removePartialSelectors` options** — exist (`defuddle/dist/types.d.ts:65-110`) but expose a flat global selector list, not per-site dispatch. Could be used as the *implementation* of cleaners on simple cases (cleaner returns selector list, dispatcher merges into Defuddle options) — worth considering during Stage 1 implementation.

**Native browser reader API** — Chrome MV3 does not expose a public reader-mode API (verified during audit). Firefox Reader View has internal `dom.distiller` but no extension surface. Not viable.

**Server-side cleanup in Robin (Stage 2 of pipeline)** — News Coo PRD §3 explicitly assigns clean extraction to News Coo (browser), translation/bilingual to Robin. If captured original is structurally polluted, Robin propagates the pollution. Cleanup is correctly placed at News Coo. Robin's URL dispatcher is for direct DOI/PubMed retrieval — different code path.

## Resolved open questions (from v1, after panel)

| v1 open question | Resolution |
|--|--|
| Scope creep risk (10 vs 30+ cleaners) | Acknowledged unbounded; mitigated by print-stylesheet spike + reactive cadence |
| Cleaner contract richness | Mandated — see "Cleaner contract" above |
| Test strategy (fixtures) | Per-cleaner fixture in `tests/content/fixtures/{site}/{slug}.html`; assertion includes negative case (non-matching host untouched) |
| Failure detection | Mandatory staleness signal — not optional |
| Per-site metadata extension | Out of scope for this ADR; cleaners are DOM-only |

## Consequences

**Positive**:
- Solves Lancet today; pattern composes; non-matching sites untouched
- Print-stylesheet spike gives potential broad win cheaply
- Staleness detection turns silent regressions into actionable signal

**Negative**:
- Per-site cleaners = ongoing tail of micro-PRs as new publishers encountered
- Fixtures rot when sites redesign — staleness signal mitigates but doesn't eliminate
- Lossy when Lancet drop-blocks are the *only* reference data on the page (bibliography lives at bottom too, but cleaners must verify before stripping)

**Neutral**:
- Sets precedent that News Coo knows about specific publishers — file a PRD addendum after first 3 cleaners ship
- Long-term path is upstream Defuddle contribution — not blocked

## Slice plan

| Slice | Scope | DoD |
|--|--|--|
| **0** | Print stylesheet spike | `applyPrintStylesheets(doc)` helper; tested against Lancet/Nature/BMJ fixtures; spike report committed to `docs/research/`; decision branch chosen |
| **1** | Cleaner registry infrastructure | `extractor.ts` clones doc → runs print pass (if 0 won) → runs registry → defuddle; per-cleaner try/catch; staleness logging; tests for empty registry, no-match, throwing cleaner, multi-cleaner dispatch |
| **2** | Lancet cleaner + fixture | Hantavirus HTML fixture → 0 inline reference fragments in output; bibliography section preserved if separately present; negative test (NYT page untouched) |
| **3** | Build + bundle integration | `scripts/copy-assets.mjs` and rolldown config pick up `siteCleaners/*.ts`; verify production bundle includes Lancet cleaner |
| **4** | Docs + contribution guide | `docs/news-coo/site-cleaners.md`: contract, "rules of engagement" (no `<head>`, prefer specific selectors, write fixture), "how to add a cleaner" walkthrough |

Slice 5+ = one PR per new site, reactive cadence.

## Audit trail

This ADR was hardened via 3-way panel:

- **Claude Opus 4.7 (1M)** drafted v1
- **Codex (GPT-5)** audit: `docs/research/2026-05-11-codex-adr025-site-cleaners-audit.md` — surfaced Defuddle-is-citation-aware correction, code-grounded insertion point at `extract.ts:36-43`, bibliography-loss risk, `^\d+\.$` proxy-not-proof critique
- **Gemini 2.5 Pro** audit: `docs/research/2026-05-11-gemini-adr025-site-cleaners-audit.md` — surfaced print-stylesheet heuristic (now Stage 0), CJK blind spot (now ADR-026 trigger), JSON-LD alternative (deferred), staleness detection mandate

Universal-agreement items adopted unchanged. Single-source insights evaluated on merit; print-stylesheet (Gemini) and clone-don't-mutate-live (Codex) both ranked as adopt.
