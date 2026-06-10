# ADR-036: Title-driven Thumbnail Workflow

**Date:** 2026-05-28
**Status:** Accepted
**Owner:** 修修
**Related:** [ADR-033](ADR-033-thumbnail-generation-pipeline.md), [ADR-031](ADR-031-project-workspace-migration.md)

---

## Context

ADR-033 made the thumbnail pipeline brainstorm-driven: the LLM writes three thumbnail idea descriptions, then Hyperframes renders them. After testing the first implementation, we found the next bottleneck is not rendering mechanics but upstream selection quality.

The current playbook is useful as a reference library, but too broad for direct prompt injection. It distills 140 thumbnails across Ali Abdaal, Jeff Su, Alex Hormozi, and Cleo Abram into a flat archetype catalog. That flattening makes the LLM over-consider patterns that are valid in the corpus but poor fits for 修修's current goal: health/longevity thumbnails in Traditional Chinese, visually closer to Ali Abdaal and Jeff Su.

修修's updated goal:

> Given roughly 10 brainstormed title ideas, select one publish title and produce three thumbnail candidates for that same title that are most likely to attract clicks when uploaded to YouTube thumbnail Test & Compare.

---

## Decision

Build a **title-driven thumbnail funnel**:

```text
Project brief + 8-12 title ideas
  -> focused Ali/Jeff workflow pack
  -> LLM selects 1 publish title
  -> LLM designs 3 thumbnail variants for that same title
  -> each variant becomes a renderable thumbnail recipe
  -> optional stock/Envato asset plan
  -> render 3 candidates
  -> visual QA gate
  -> YouTube thumbnail Test & Compare
```

The thumbnail brainstorm step no longer treats the broad playbook as a flat menu. It receives a compact **Ali/Jeff workflow pack** with recipe cards, selection rules, disallowed patterns, and asset-needs guidance.

---

## Decisions

### D1. Title pool is the primary thumbnail input

Thumbnail brainstorm consumes the checked title pool first. If 修修 has checked 8-12 titles in the title-pool UI, those titles are the input set. If no checked title pool exists, the workflow falls back to frontmatter `title_candidates`.

Rationale: 修修 can use title brainstorm as a divergent phase, mark promising title directions, and let the thumbnail LLM choose the strongest publish title before making thumbnail variants. This keeps the eventual YouTube thumbnail test scoped to thumbnails instead of mixing title and thumbnail variables.

### D2. The workflow outputs one publish title plus three thumbnail variants

The three output ideas must share the same `title_pairing`. The LLM may choose that title from the checked title pool or make a very small fusion of two checked titles, but the three thumbnails are variants for one upload title.

Each thumbnail idea records:

- `title_pairing`
- `lane`
- `recipe`
- `viewer_promise`
- `evidence_fit`
- `trust_risk`
- `archetype`
- `大字`
- `我的表情`
- `視覺`
- `數字/圖示`
- `背景`
- `素材需求`

Rationale: thumbnail quality depends on the title-thumbnail relationship, but upload testing should isolate the thumbnail variable. The renderer only needs the old 5-line fields, but the workflow and future asset agent need the extra metadata.

### D3. Ali and Jeff are primary lanes

The focused workflow pack prioritizes:

- **Ali Warm Explainer**: warm, evidence-backed, approachable, lifestyle/workspace, science authority.
- **Jeff Clean Tutorial**: clean tutorial UI, icon/card/checklist payloads, friendly efficiency.

Hormozi and Cleo patterns are not deleted from the reference history, but they are no longer equal-weight defaults for this workflow.

### D4. T-A9 and T-A10 are modifiers

`T-A9` year/recency and `T-A10` loss/risk are not standalone strategies. They can attach to a primary title archetype only when justified by the script.

Rationale: year and risk cues can improve specificity, but if the LLM treats them as primary strategies it over-produces shallow freshness/fear hooks.

### D5. T-V6 is disallowed for health/longevity

Question-mark breakthrough overlays are excluded from the focused workflow.

Rationale: in health/longevity, "really found it?" visual language can read as miracle-cure hype and damage trust.

### D6. Asset sourcing happens after creative selection

The LLM may output `素材需求`, but it must not download assets. The asset plan is a controlled shopping list for a later Envato/stock agent.

For v1, Envato/stock output is search intent only. Automated scraping, bulk download, or license registration is out of scope. A future Asset Sourcing Agent must keep a manifest with provider, URL or asset ID, author/attribution if available, license/project registration evidence, downloaded timestamp, local path, and usage notes.

Rationale: this keeps downloads small, project-specific, and tied to selected thumbnail recipes rather than speculative hoarding.

### D7. YouTube v2 outputs must match renderable templates

For this workflow, each idea must include exactly one renderable `T-V*` tag supported by the current template registry:

- `T-V1`
- `T-V2`
- `T-V3`
- `T-V8`
- `T-V10`

Unsupported visual tags are rejected before persistence instead of silently falling back to `T-V1`.

Rationale: if the LLM says it chose a visual grammar the renderer cannot execute, the rendered candidate no longer represents the brainstorm decision.

### D8. Rendered candidates are not automatically upload-ready

The workflow can produce candidates ready for side-by-side review, but a rendered thumbnail must pass visual QA before upload: text readability, no overlapping layers, believable host treatment, asset/license provenance, and no health overclaim.

V1 only enforces deterministic mechanical QA in code: valid image decode, 1280x720 dimensions, 16:9 aspect ratio, non-blank output, and contrast/readability proxies. Semantic checks such as medical overclaim, host believability, and final upload taste remain a human review gate or v2 automation.

Rationale: the first renderer implementation is still improving. "Renderable" means testable inside the workflow, not automatically publish-grade.

---

## Implementation

Initial implementation:

- `shared/thumbnail_workflow.py`
  - focused Ali/Jeff recipe cards
  - modifier and disallowed-pattern rules
  - prompt-pack formatter
- `prompts/thumbnail/brainstorm_youtube_v2.md`
  - title-driven system prompt
  - strict metadata + render-field output shape
- `shared/thumbnail_idea.py`
  - parser remains backward compatible with the original 5-line format
  - optional metadata is preserved when present
- `shared/thumbnail_assets.py`
  - converts `素材需求` into `asset_manifest.json`
  - marks asset rows as search intent only with empty provenance/license fields
  - adds provider search links, status inference, provenance updates, and rebuild preservation for unchanged asset needs
- `shared/thumbnail_quality.py`
  - deterministic local visual QA for rendered PNG candidates
  - checks readability proxies, dimensions, aspect ratio, valid image decode, and blank renders
- `thousand_sunny/routers/bridge_project_thumbnails.py`
  - thumbnail brainstorm prefers checked title-pool inputs
  - falls back to frontmatter `title_candidates`
  - persists new metadata in brainstorm history
  - enforces the YouTube v2 contract: exactly 3 ideas, same title pairing, required metadata, and supported renderable visual tags
  - writes visual QA into each render manifest and blocks commit when a stored QA result is `fail`
  - refreshes the asset manifest after brainstorm, reroll, or parseable manual edits
  - exposes Bridge routes to view/rebuild the asset manifest and update per-asset provenance/license fields
- `thousand_sunny/templates/bridge/projects/_thumbnail_asset_manifest.html`
  - renders search links, status, policy, and provenance forms inside the Title & Thumbnail tab
  - refreshes via HTMX when brainstorm, reroll, or save-edit updates the manifest

---

## Acceptance Criteria

V1 brainstorm:

- YouTube thumbnail brainstorm produces exactly three ideas.
- All three ideas share the same normalized `title_pairing`.
- Each idea includes `lane`, `recipe`, `viewer_promise`, `evidence_fit`, `trust_risk`, `archetype`, the five render fields, and `素材需求`.
- Each idea includes exactly one supported renderable `T-V*` tag.
- Contract failures return an error and do not persist the new brainstorm.

V1 asset manifest:

- Brainstorm, single-idea reroll, parseable manual edit, and manual rebuild write `asset_manifest.json`.
- The manifest is search intent only: no route may download assets, scrape Envato, or register licenses.
- Each asset need has stable IDs, provider search links, capped candidate count, status, and empty provenance fields by default.
- Rebuild preserves provenance/status for unchanged asset needs and leaves removed/renamed needs out of the active manifest.

V1 render and commit:

- Render manifests include `visual_qa` for each candidate.
- `visual_qa.status == "fail"` blocks commit.
- `visual_qa.status == "warn"` may still be committed, but should remain visible in the UI for human review.
- V1 code-enforced QA is mechanical only; semantic upload readiness remains human-gated.

## Operator Checklist

1. Brainstorm or import roughly 8-12 title ideas and check only the titles that are credible upload candidates.
2. Run the thumbnail brainstorm from the checked title pool. The output must be one selected publish title with exactly three thumbnail variants.
3. Review the three variants for Ali/Jeff fit, health-claim risk, and title-thumbnail tension before spending time on assets.
4. Open the asset sourcing panel and use the bounded Envato/stock search links to record only the few assets needed for the selected variants.
5. Render each candidate and inspect the UI plus `visual_qa` status. Re-render or edit the idea if QA fails.
6. Commit only a candidate whose mechanical QA is not failing and whose semantic review is upload-ready for YouTube Test & Compare.

---

## Consequences

Positive:

- The LLM sees fewer, more relevant patterns.
- 修修 can iterate title ideas first, then ask for three thumbnail tests.
- Future Envato/asset sourcing has a structured input.
- Render remains backward compatible with existing thumbnail idea cards.

Trade-offs:

- The focused pack is more opinionated than the broad playbook.
- A poor title-pool selection will constrain thumbnail quality.
- Podcast thumbnails still use the older path for now.

---

## Follow-ups

1. Add an Asset Sourcing Agent that consumes `素材需求` and returns candidate stock/Envato links before download.
2. Add a scoring layer for the three recipe outputs before rendering.
3. Track YouTube thumbnail Test & Compare results back to recipe IDs and title archetypes.
4. Add more Ali/Jeff-specific visual templates as rendering quality improves.
5. Promote `素材需求` from free-form search strings to structured LLM output (`type`, `quantity`, `provider`, `candidate_limit`) so the Asset Sourcing Agent relies less on heuristics.
