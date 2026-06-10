# ADR-037: Staged Thumbnail Production Workflow

**Status:** Accepted
**Date:** 2026-05-28
**Related:** [ADR-033](ADR-033-thumbnail-generation-pipeline.md), [ADR-036](ADR-036-title-driven-thumbnail-workflow.md)

---

## Context

ADR-036 made the thumbnail funnel title-driven and produced useful creative briefs. The render result exposed a new bottleneck: the brief was strong, but the generated thumbnail looked far from the Ali Abdaal / Jeff Su target.

The failure was not a single visual bug. The pipeline compressed the brief into a few coarse variables:

- `emotion_key`
- `title_hook`
- `accent_decoration`
- `bg`
- `archetype`
- `palette`

That is not enough information to execute a thumbnail. It caused design instructions such as "circle badge under the headline" to become literal overlay text, selected cutouts by a coarse emotion folder, and skipped explicit asset placement like a sticky note with three lines of text.

---

## Decision

Replace "brief directly to final render" with a staged production workflow. Each stage produces an inspectable artifact and may fail independently. AI may help with decisions, but the renderer only executes structured layout specs.

The final renderer must be deliberately boring: compose approved layers, not invent design.

---

## Workflow

### Stage 1. Brief Normalization

Input: one thumbnail idea brief.

Output: `brief_spec.json`.

Required fields:

- `publish_title`
- `thumbnail_hypothesis`
- `viewer_promise`
- `trust_risk`
- `target_style`
- `primary_text`
- `supporting_text`
- `person_role`
- `cutout_requirements`
- `background_requirements`
- `asset_requirements`
- `layout_intent`

Acceptance:

- Design instructions are structured, not treated as visible text.
- On-canvas text is explicit and short.
- Health claims are framed as curiosity or evidence, not treatment promises.

### Stage 2. Reference Target Selection

Input: `brief_spec.json`.

Output: `reference_target.json`.

Required fields:

- `creator_target`: `ali_abdaal` or `jeff_su`
- `reference_ids`
- `style_constraints`
- `avoid_patterns`

Acceptance:

- At least one reference target is selected before layout work.
- The target explains typography density, person scale, background complexity, and asset count.

### Stage 3. Thumbnail Hypothesis Lock

Input: `brief_spec.json` plus reference target.

Output: `hypothesis.json`.

Acceptance:

- One visual promise is selected.
- The thumbnail does not try to communicate the whole video.
- The three YouTube test candidates remain different hypotheses for the same title, not random styles.

### Stage 4. Cutout Casting

Input: `cutout_requirements` and the tagged cutout library.

Output:

- `cutout_contact_sheet.png`
- `cutout_candidates.json`
- `cutout_choice.json`

Acceptance:

- 3-6 candidates are shown.
- Candidate selection uses pose tags, not only emotion.
- Rejects over-exaggerated meme expressions unless the hypothesis explicitly needs comedy or shock.

### Stage 5. Person Placement Preview

Input: selected cutout.

Output:

- `person_placement.png`
- `person_placement.json`

Required fields:

- `x`
- `y`
- `scale`
- `crop_anchor`
- `z_index`
- `shadow_style`

Acceptance:

- Person reads as credible at thumbnail size.
- Face is large enough for expression contagion.
- Direction of gaze or gesture supports the planned text/asset position.

### Stage 6. Layout Blocking

Input: hypothesis, reference target, person placement.

Output:

- `layout_blocking.png`
- `layout_spec.json`

Required regions:

- `person_region`
- `headline_region`
- `asset_region`
- `negative_space`
- `safe_margin`

Acceptance:

- Black-and-white layout works before color, texture, or AI background.
- The visual hierarchy is obvious at 320x180.
- There is no dependency on AI generation to fix composition.

### Stage 7. Typography Spec

Input: layout spec and on-canvas text.

Output:

- `typography_layer.png`
- `typography_spec.json`

Required fields:

- `lines`
- `font_family`
- `font_size`
- `line_height`
- `stroke_width`
- `shadow`
- `position`
- `max_width`

Acceptance:

- Text is short enough to fit without emergency shrink.
- Line breaks are explicit.
- No design instruction is rendered as text.
- Text placement matches the target style.

### Stage 8. Background Plate

Input: layout spec and background requirements.

Output:

- `background_plate.png`
- `background_prompt.txt`
- `background_report.json`

Acceptance:

- Background is generated or selected after layout is known.
- It preserves negative space for text and person.
- It contains no people and no text unless explicitly approved.

### Stage 9. Asset Sourcing / Prep

Input: asset requirements.

Output:

- `asset_manifest.json`
- prepared transparent or framed asset files

Acceptance:

- Envato/stock/icon candidates are few and specific.
- Provenance is recorded before use.
- Assets are prepared as layers, not baked into the AI background when deterministic placement is required.

### Stage 10. Asset Layer Placement

Input: prepared assets and layout spec.

Output:

- `asset_layer.png`
- `asset_layout.json`

Acceptance:

- Example: if the brief calls for a sticky note with `護腦 / 抗老 / 認知`, the sticky note is a deterministic layer with those exact lines.
- Asset scale, angle, and z-index are explicit.
- Asset does not compete with face or headline.

### Stage 11. Composite Render

Input: approved person, background, typography, and asset layers.

Output:

- `composite.png`
- `render_manifest.json`

Acceptance:

- The renderer makes no new creative decisions.
- Layer order and coordinates are traceable.
- Output is reproducible from manifest.

### Stage 12. QA / Critic / Iterate

Input: composite and all stage artifacts.

Output:

- `mechanical_qa.json`
- `semantic_qa.json`
- `iteration_decision.json`

Mechanical QA:

- dimensions
- image readability
- nonblank output
- contrast proxy
- text bounding boxes
- obvious overlap

Semantic QA:

- Ali/Jeff target fit
- trust and health-claim risk
- expression credibility
- title-thumbnail tension
- asset clarity
- mobile readability at 320x180

Acceptance:

- `mechanical_qa.status == fail` blocks commit.
- `semantic_qa.status == fail` blocks upload readiness.
- Iteration returns to the specific failed stage, not the whole pipeline.

---

## Cutout Requirement Model

Thumbnail briefs must stop asking for a vague emotion such as `surprised`. They should request a pose package:

```json
{
  "body_angle": "three_quarter_left",
  "gaze": "screen_right",
  "expression": "thoughtful",
  "intensity": "mild",
  "mouth": "closed",
  "brow": "slight_frown",
  "hands": "chin",
  "crop": "waist",
  "credibility": "high",
  "avoid": ["meme", "panic", "open_mouth_extreme"]
}
```

The selector may fall back within the same expression family, but it must not jump from `mild_surprise` to an extreme reaction face without explicit approval.

---

## Implementation Plan

Slice 1: Define the staged artifacts and cutout taxonomy.

Slice 2: Build a cutout contact-sheet generator from the tagged library.

Slice 3: Add a `Cutout Casting` panel in the Title & Thumbnail tab.

Slice 4: Render only `person_placement.png` from selected cutout and layout intent.

Slice 5: Add typography-layer preview with explicit line breaks.

Slice 6: Reintroduce background and asset layers after person and typography are approved.

---

## Implementation Notes

2026-05-28 v1 lands the first executable stage gate:

- `shared/cutout_casting.py` reads the tagged pose manifest and ranks candidates by expression family, use context, intensity, credibility, and avoid rules.
- `scripts/cast_thumbnail_cutouts.py` writes a cutout contact sheet, candidate JSON, and optional person-placement preview for offline review.
- `shared/thumbnail_person_preview.py` renders the Step 1 person-placement artifact on a guide canvas, independent of any AI background or final composite.
- The Title & Thumbnail tab now posts `stage=person` by default, so "try this idea" produces only the Step 1 person-placement preview and does not call AI background generation or the full thumbnail renderer.
- `thousand_sunny/routers/bridge_project_thumbnails.py` records `stage`, `person_placement`, `cutout_casting`, and `full_render_filename` in the run manifest; staged `person` artifacts cannot be committed as final thumbnails.
- The render-result partial shows `v*_person.png` as the primary artifact when available. Full renders are treated as later-stage/debug artifacts, not the Step 1 review surface.

Known next slices:

- Add a dedicated Cutout Casting UI with 3-6 selectable candidates instead of auto-accepting the top rank.
- Add recipe-to-template compatibility gates so an Ali warm evidence-list brief cannot accidentally use a split-screen comparison template.
- Add typography-layer preview with explicit line breaks and measured text boxes before background or asset generation.
- Move sticky-note/checklist/card assets into deterministic layers instead of baking them into AI background prompts.

---

## Consequences

Positive:

- Failures become diagnosable.
- 修修 can approve or correct the exact production stage that went wrong.
- AI is used for judgment and search, not uncontrolled final composition.
- New cutout shoots can be planned precisely.

Trade-offs:

- More steps than the current one-click render.
- Requires a better tagged cutout library.
- Requires UI changes before it feels smooth.
