# ADR-025 Stage 0 — Print stylesheet spike report

**Date**: 2026-05-11
**Author**: Claude Opus 4.7 (1M)
**Decision**: ❌ **Skip print pass entirely. Proceed to Stage 1 (per-site cleaner registry).**

## Hypothesis

Per Gemini panel suggestion: many academic publishers ship a `<link rel="stylesheet" media="print">` that strips interactive cruft. If we apply the print stylesheet to the cloned DOM before Defuddle, a single change may obviate per-site cleaners for a class of sites.

## Method

Live-page test against two target publishers using Playwright + in-browser `evaluate`:

1. Load article in headless Chrome
2. Count problem markers (drop-blocks / reference markers) before
3. Apply print pass: switch `link[media~=print]` and `style[media~=print]` to `media="all"`; rewrite inline `@media print { … }` to `@media all { … }`
4. Force layout (`document.body.offsetHeight`)
5. Recount problem markers + total hidden elements after

Implementation: `extensions/news-coo/src/content/applyPrintStylesheets.ts` (7 unit tests passing in happy-dom).

## Results

### Lancet (target site — Hantavirus article)

```json
{
  "switchTouched": 1,           // 1 link[media=print]
  "inlineBlocks": 1,            // 1 inline @media print rewritten
  "before": { "dropBlocks": 145, "visibleDropBlocks": 145 },
  "after":  { "visibleDropBlocks": 145, "totalHidden": 311 }
}
```

**`.dropBlock.reference-citations` count visible: 145 → 145.** The print stylesheet does not hide the reference drop-blocks. Lancet's print CSS targets navigation chrome, share widgets, and ads — not the citation widgets that are our actual problem.

### Nature (Aging article — `s43587-024-00692-2`)

```json
{
  "printSheets": 2, "printStyles": 1,
  "switchTouched": 2, "inlineBlocks": 1,
  "refMarkers": 89, "refSections": 2,
  "totalHiddenAfter": 83
}
```

Nature has 89 reference link anchors and 2 reference sections — these survive the print pass. Without a baseline counter run we can't compute Δ visible refs precisely on Nature, but the structural pattern is the same as Lancet: reference content is not gated on `@media screen`, it's part of the page's main content tree.

### BMJ (planned)

Skipped — Lancet result is decisive for the target site. BMJ would not change the Stage 1 conclusion.

## Why print pass doesn't help these sites

- Academic publishers want references **printable** (it's an article — references are part of the citation record). They do NOT mark reference widgets as `@media screen` only.
- Print stylesheets at Lancet/Nature target peripheral chrome (nav, share buttons, ads), which Defuddle already strips effectively as non-content nodes.
- The reference drop-block IS the content from the publisher's standpoint, just rendered as collapsible UX. A generic print-pass heuristic cannot distinguish "fold-out reference widget" from "real article body".

## Decision

Per ADR-025 Stage 0 decision branch:

> **0 of (tested) fixtures improve** → Skip print pass entirely; go straight to Stage 1.

Lancet (the actual user-blocking case) shows zero improvement. Nature evidence is consistent. The print-pass helper code (`applyPrintStylesheets.ts` + tests) will **not** be called from `extractor.ts`. The module stays in the repo as documented dead code with this report linked, in case a different class of sites (news/blog with `media=print` cruft) surfaces later — at which point we can revisit cheaply.

## Stage 1 implications

Proceed to per-site DOM cleaner registry as primary path. No fallback complexity from a partially-effective print pass to design around.

## Artifacts

- Helper: `extensions/news-coo/src/content/applyPrintStylesheets.ts`
- Tests: `extensions/news-coo/tests/content/applyPrintStylesheets.test.ts` (7/7 pass)
- Spike script (unused after this conclusion): `extensions/news-coo/scripts/spike-print-pass.mts`
