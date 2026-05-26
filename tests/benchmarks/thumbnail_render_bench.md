# Thumbnail Render Benchmark — ADR-033 D10 PR4 Gate

**Status:** TO RUN (gate not yet satisfied — fill in numbers + decision before PR4-A merge)
**Owner:** 修修
**Created:** 2026-05-26 (PR4-A worktree scaffolding)
**Related:** ADR-033 D10 amendment §"PR4 benchmark gate"

## Why this exists

ADR-033 v2 D10 amended via Codex panel push-back:

> v1 dismissed (B) Puppeteer-direct screenshot on theoretical grounds
> ("Hyperframes runtime hooks critical"). v2 requires empirical evidence
> — if (B) produces visually-identical output materially faster, PR5 may
> swap. Until then, PR4-A ships path (A).

PR4-A merge gate: **populate the numbers below** so PR5 has an informed
revisit signal.

## Test setup

- Composition: `video/compositions/thumbnail_youtube/index.html`
- Sample variables: see `data/test_fixtures/thumbnail_bench/v1.variables.json`
  (TO CREATE — minimal cutout + bg + hook text)
- Iterations: render the same composition 5 times each path; take median +
  best to absorb npx cold start.
- Output PNG: 1280×720, JPEG q=85 disabled (no recompress on output side).
- Host: 修修's local box (Windows 11, RTX 5070 Ti, Chrome stable).

## Path (A) — `npx hyperframes render --format png-sequence`

Command (worker invocation, simulated):

```
npx hyperframes render compositions/thumbnail_youtube \
    --variables-file data/test_fixtures/thumbnail_bench/v1.variables.json \
    --format png-sequence \
    -o /tmp/_frames_v1 \
    --fps 30 --quality standard \
    --quiet --no-browser-gpu
```

| Iteration | Wall time (s) | Frame 0 PNG size (KB) | Notes |
|-----------|---------------|------------------------|-------|
| 1 (cold) | TBD | TBD | npx cold start expected ~10-30s |
| 2 | TBD | TBD | |
| 3 | TBD | TBD | |
| 4 | TBD | TBD | |
| 5 | TBD | TBD | |
| **median** | **TBD** | **TBD** | |

## Path (B) — Puppeteer direct screenshot

Command (hypothetical worker invocation):

```
node video/scripts/render_still.js \
    compositions/thumbnail_youtube/index.html \
    --variables data/test_fixtures/thumbnail_bench/v1.variables.json \
    --out /tmp/v1.png
```

`render_still.js` (not yet implemented — needs writing if (B) is evaluated):

```js
import puppeteer from 'puppeteer';
// ... launch headless Chrome, load composition file URL, inject variables
// via window.__hyperframes_variables polyfill, wait for DOMContentLoaded +
// gsap timeline pause(), screenshot().
```

| Iteration | Wall time (s) | PNG size (KB) | Visual match vs (A) | Notes |
|-----------|---------------|----------------|---------------------|-------|
| 1 (cold) | TBD | TBD | TBD (diff PNG vs A2) | |
| 2 | TBD | TBD | TBD | |
| 3 | TBD | TBD | TBD | |
| 4 | TBD | TBD | TBD | |
| 5 | TBD | TBD | TBD | |
| **median** | **TBD** | **TBD** | **TBD** | |

## Visual-identity check

Compare A.5 and B.5 PNGs pixel-by-pixel (e.g. `compare -metric AE A5.png B5.png diff.png` from ImageMagick).

- Mean absolute pixel diff: **TBD**
- Visible artefacts (text rendering, gradient banding, image scaling): **TBD**
- GSAP entrance animation (if any): handled correctly by (B)? **TBD**

## Decision

After running:

- (A) median: **TBD** seconds
- (B) median: **TBD** seconds
- Speedup: **TBD ×**
- Visual identity: **TBD** (perfect / minor diffs / mismatched)

**PR5 action**:

- If (B) ≥2× faster AND visually identical: switch the worker to (B) in PR5; document migration in ADR-033 v3.
- If (B) faster but visually different: investigate; likely keep (A); revisit only if Hyperframes runtime hooks become unnecessary for thumbnail compositions.
- If (B) not materially faster: keep (A); close this open question in ADR-033.

—

**Note (PR4-A)**: This file ships intentionally empty. 修修 + Claude run the bench in a separate worktree once PR4-A is otherwise green and `data/test_fixtures/thumbnail_bench/` exists. Filing this skeleton early prevents the panel gate from being forgotten.
