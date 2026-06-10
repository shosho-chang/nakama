# ADR-046: External Image Model Thumbnail Workflow

**Status:** Draft v2, panel-integrated
**Date:** 2026-06-03
**Owner:** Shosho
**Related:** [ADR-033](ADR-033-thumbnail-generation-pipeline.md), [ADR-036](ADR-036-title-driven-thumbnail-workflow.md), [ADR-037](ADR-037-staged-thumbnail-production-workflow.md), [ADR-045](ADR-045-thumbnail-arrangement-selection-eval-loop.md)

> Numbering note: this document was assigned `ADR-046` during the thumbnail
> pipeline wrap-up.

---

## Supersedes And Amends

- Amends ADR-033 D2 for YouTube V2/V3 thumbnail work: the current YouTube
  workflow follows ADR-036's "one selected publish title, three thumbnail
  variants" model rather than independent title/thumbnail A/B units.
- Amends ADR-036 D7: a concrete template/style contract is still required, but
  V3 uses it for prompt compilation and reference matching rather than requiring
  every idea to be directly renderable by the deterministic renderer.
- Preserves ADR-037 as accepted support infrastructure. Its staged renderer
  remains useful for previews, diagnostic checks, deterministic text overlay,
  and commit plumbing.
- Defers ADR-045 as the primary final-art path. Its template contracts,
  arrangement gates, and critique rubrics remain useful inputs to V3, but the
  full deterministic arrangement rollout is no longer the default route to
  upload-grade thumbnails.

---

## Context

ADR-036 moved thumbnail generation upstream: choose a publish title from a
title pool, then design three thumbnail variants for that title. ADR-037 and
ADR-045 tried to make final rendering diagnosable by decomposing it into person
placement, layout blocking, typography, background, components, arrangement
candidates, and eval gates.

That decomposition improved debugging, but it did not solve the taste problem.
The current deterministic renderer can gradually move boxes into the right
places, but it still struggles with:

- overall visual polish;
- component aesthetics;
- stock/icon/object selection;
- typography taste;
- natural integration between host, object, background, and text;
- producing images that feel like real YouTube thumbnails rather than debug
  mocks, slide decks, or web UI screenshots.

Recent user testing made the failure mode explicit: a render may pass mechanical
or deterministic gates while the human-visible result is still not acceptable.
For upload-grade thumbnails, "all layers are present" is not the same as "this
earns a click."

The user then provided transcripts for two current AI-thumbnail workflows:

- Aref / Agent A: an internal console app that accepts a working title, short
  description, and optional person reference image; asks a strong LLM for three
  thumbnail concepts; writes image prompts; sends them to Gemini; displays three
  downloadable thumbnails with rationale and history.
- Prompt Edit / Nano Banana Pro: a workflow centered on the "Triple Threat"
  formula: one subject, one object, and one curiosity element. The model uses
  reference images for the person and objects, generates 16:9 thumbnails, then
  iterates with "recreate this exact same image; only change X" prompts.

The shared lesson is that the highest-leverage system boundary is not "build a
Photoshop-like renderer in Nakama." It is:

```text
Nakama: title strategy, concept, reference package, prompt compiler, critique,
feedback memory
External image model: final visual synthesis
```

This ADR pivots the thumbnail workflow accordingly.

This work belongs to Stage 5 YouTube production in `CONTENT-PIPELINE.md`: it
turns an already selected video/project/title direction into channel packaging.
It should not expand into Stage 4 content writing or general image editing.

---

## Decision

Nakama will stop treating the deterministic renderer as the primary path to
final upload-grade YouTube thumbnails.

Instead, Nakama will implement a V3 external image model workflow:

```text
Title pool
  -> title-thumbnail pairing
  -> Triple Threat thumbnail brief
  -> visual strategy compiler
  -> reference package
  -> model-specific prompt
  -> external image generation
  -> generated image import and optional deterministic text overlay
  -> critique and edit prompt
  -> prompt history and later style memory
  -> accepted thumbnail candidate
```

The deterministic renderer from ADR-037/038 remains useful as a diagnostic,
wireframe, typography, and commit-support system. It is no longer the system of
record for final visual synthesis.

The first implementation should not require an image-generation API. It should
produce a copyable prompt package for tools such as Prompt Edit, Gemini,
Scenario, Nano Banana Pro, or future providers. API adapters can be added later
after the prompt package and feedback loop prove useful.

All workflow sections below describe target capability unless explicitly marked
as existing behavior.

---

## Non-Goals

- Do not build a full Photoshop/canvas editor in this phase.
- Do not automate Envato/stock downloads in this phase.
- Do not require external provider API integration for V1.
- Do not claim AI-generated thumbnails are upload-ready without user review.
- Do not train or fine-tune a custom image model.
- Do not delete ADR-037/038 renderer work; demote it to diagnostic/wireframe
  use plus deterministic text/commit support.
- Do not rely on external image models to write exact Traditional Chinese text
  in V1.

---

## Takeaways Applied From The Two Video Workflows

### Aref / Agent A Pattern

Adopt:

- Use a short working title and short description as the primary input.
- Generate three distinct concepts, not three small variations of the same bad
  idea.
- For each concept, explain why it should earn clicks.
- For each concept, expose the image prompt sent to the image model.
- Show history so multiple generations can be compared side-by-side.
- Use a best-practices document or style pack as a persistent system input.

Modify:

- Aref's examples can lean toward bright/hype thumbnail aesthetics. Shosho's
  default should be warmer, more credible, and closer to Ali Abdaal / Jeff Su
  educational thumbnails.
- Agent A can use Ahrefs data for SEO. Nakama already has title/keyword/project
  context, but that is not required for the first V3 prompt workflow.

Reject:

- Do not optimize for MrBeast shock as the default. High contrast and clear
  expression are useful; extreme reaction faces are not the default brand.

### Prompt Edit / Triple Threat Pattern

Adopt:

- Every thumbnail concept must have no more than three focal points.
- The three focal points should map to:
  - subject;
  - object;
  - curiosity.
- Text should be short and should not simply repeat the title.
- Reference images matter:
  - person reference;
  - object/product reference;
  - style/template reference.
- Iteration should use preservation prompts:
  - "Recreate this exact same image. The only change is..."

Modify:

- The transcript describes English thumbnail text with five words max. For
  Traditional Chinese thumbnails, the default should be 2-8 Chinese characters,
  with exceptions for metrics such as "$0 -> $1M" or "5g".
- The person reference does not need dozens of expressions. A small set of
  clean, well-lit host references is enough because the image model can adapt
  expression.

Reject:

- Do not let the image model invent extra background objects or extra labels.
  The prompt package must explicitly limit focal points and text count.

---

## Core Algorithm

### Phase 1. Title And Angle Selection

Input:

- checked title pool, ideally 8-12 titles;
- project one-sentence description;
- optional script notes or evidence summary;
- channel style memory.

Output:

- one selected `publish_title`;
- up to three thumbnail hypotheses for that title.

Rules:

- The default YouTube Test & Compare unit is one title with three thumbnails.
- The system may propose a title fusion only if it preserves the selected
  angle and improves clarity.
- Each hypothesis must be visually executable with three or fewer focal points.

### Phase 2. Triple Threat Brief

Each thumbnail hypothesis becomes a `ThumbnailBriefV3`.

Required fields:

- `title_pairing`
- `viewer_promise`
- `subject`
- `object`
- `curiosity`
- `thumbnail_text`
- `reference_template_id`
- `style_contract_id`
- `reference_style`
- `focal_points`
- `trust_risk`
- `why_it_earns_clicks`

Rules:

- `subject` is usually Shosho, but may be a product/object for non-host-driven
  thumbnails.
- `object` must add episode-specific context, not generic decoration.
- `curiosity` must create a question the title alone does not fully answer.
- `thumbnail_text` is optional. When present, it must be short and complementary
  to the title.
- `reference_template_id` must bind the idea to a concrete deconstructed
  thumbnail/template family, even when final art is generated externally.
- `focal_points` must contain at most three entries.

Example:

```yaml
title_pairing: "肌酸的 6 個健康效益：不只是增肌，更是護腦與抗老"
viewer_promise: "Creatine is not just a gym supplement; it may matter for brain and aging."
subject: "Shosho, large close-up, warm credible explainer expression"
object: "Creatine bottle plus a small paper benefit card"
curiosity: "Why would a muscle supplement matter for the brain?"
thumbnail_text: "不只增肌"
reference_template_id: "shosho_benefit_list_card"
style_contract_id: "ali_warm_evidence_list"
reference_style: "Ali warm explainer + Jeff clean component layout"
focal_points:
  - "Shosho face"
  - "Creatine bottle and benefit card"
  - "Short text: 不只增肌"
trust_risk: "Avoid medical-cure framing; present as evidence-backed benefits."
why_it_earns_clicks: "It violates the gym-only expectation while staying credible."
```

### Phase 3. Visual Strategy Compiler

The brief is not sent directly to the image model.

Nakama compiles it into a `VisualStrategyV3`, converting strategy language into
visual-director constraints.

Compiler inputs:

- `ThumbnailBriefV3`;
- channel style memory;
- YouTube thumbnail best-practice rules;
- selected reference template;
- provider capability profile.

Compiler outputs:

- `visual_strategy_id`
- `reference_template_id`
- `style_contract_id`
- `click_psychology`
- `composition_rules`
- `style_rules`
- `text_rules`
- `reference_bindings`
- `negative_rules`
- `provider_notes`

The compiler is a pure, testable transformation:

```text
ThumbnailBriefV3
  + ReferencePackageV3
  + ProjectStyleMemoryV3
  + ProviderProfileV3
  -> VisualStrategyV3
  -> GenerationPromptV3
```

Golden tests should cover this transformation before UI work depends on it.

Default strategy rules:

- exactly one primary subject;
- at most one primary object group;
- at most one curiosity cue;
- face should be large enough to read at 160x90;
- object must be recognizable at YouTube feed size;
- text must be short, legible, and complementary to title;
- exact Traditional Chinese text defaults to deterministic overlay in Nakama;
- strong contrast between subject, text, and background;
- no extra readable text beyond the approved thumbnail text and approved
  object/card labels;
- avoid medical-ad, hospital, miracle-cure, and fake-science aesthetics unless
  explicitly approved.

### Phase 4. Reference Package

Each concept gets a `ReferencePackageV3`.

Required fields:

- `person_refs`
- `object_refs`
- `style_refs`
- `generated_refs`
- `stable_reference_ids`
- `provider_binding_order`
- `usage_notes`

Binding convention for manual external tools:

```text
[img 1] person reference
[img 2] object reference
[img 3] style/reference thumbnail
[img 4] optional previous generated result for edit loop
```

The `[img N]` labels are provider-order aliases, not stable ids. The stored
package must keep stable reference ids, file hashes, dimensions, role, source,
rights/license notes, and whether the asset is approved for external provider
upload.

Reference image policy:

- Prefer clean original host photos over cutouts for image-model generation.
- Cutouts may be included as a supplemental reference if the model keeps
  copying a messy background.
- Host reference set should be small and high quality:
  - neutral front;
  - warm smile;
  - thoughtful/chin;
  - point left;
  - point right;
  - explaining/open hands.
- Object references should be real products or clean stock references when
  product identity matters.
- Style references should be concrete thumbnails, not abstract style labels.

### Phase 5. Model-Specific Prompt Generation

The compiler writes a `GenerationPromptV3`.

Prompt sections:

- task;
- reference bindings;
- scene;
- composition;
- text;
- style;
- lighting/color;
- constraints;
- negative prompt;
- output requirements.

V1 Traditional Chinese text policy:

- External image models generate the person, object, background, lighting, and
  composition.
- Exact Traditional Chinese thumbnail text is rendered by Nakama's existing
  typography layer by default.
- The prompt should ask the model to leave an intentional clean text zone or
  blank card when text is needed.
- Model-rendered Chinese is opt-in experiment mode only, and failures should be
  rejected or repaired through the edit loop.

Example prompt:

```text
Create a professional 16:9 YouTube thumbnail.

Reference bindings:
- Use [img 1] as the main person reference. Keep the person recognizable as the
  same person, but improve lighting and thumbnail polish.
- Use [img 2] as the creatine bottle reference.
- Use [img 3] as visual style reference for a clean educational YouTube
  thumbnail. Do not copy it exactly.

Scene:
A large close-up of the person on the left third, warm confident explainer
expression, looking toward the object area. The face must be large and readable
at small YouTube size.

On the right side, place a clean creatine bottle and a simple warm paper note
card with three blank label rows reserved for later Traditional Chinese text
overlay. Keep the rows blank and readable.

Reserve a large lower-third text area for later Traditional Chinese overlay.
Do not render the final Chinese title text inside the generated image.

The image has exactly three focal points:
1. the person's face
2. the creatine bottle plus note card
3. the lower-third text zone reserved for "不只增肌"

Style:
Bright, high-contrast, clean educational YouTube thumbnail. Inspired by Ali
Abdaal warm explainer thumbnails and Jeff Su clean component-based thumbnails.
Modern, crisp, premium, clickable, not cluttered.

Lighting and color:
Warm background, strong subject separation, vibrant but tasteful colors, high
contrast between person, text, and object. Slight background blur.

Rules:
- Do not add extra text.
- Do not add more objects.
- Do not make it look like a medical ad.
- Do not make it look cartoonish.
- Do not distort the face.
- Leave the reserved text areas clean, high contrast, and easy for Nakama to
  overlay exact Traditional Chinese text later.
```

### Phase 6. External Generation

V1 is manual:

1. Nakama displays the prompt package.
2. User opens Prompt Edit / Scenario / Gemini / Nano Banana Pro.
3. User uploads references in the order specified.
4. User copies the prompt.
5. User downloads the result.
6. User imports the result into Nakama.

Future provider adapters may automate this, but the prompt package is the source
of truth. API automation must not be required for the workflow to work.

### Phase 7. Critique And Edit Loop

After importing a generated image, Nakama stores it as a `GenerationAttemptV3`.
Nakama may run critique; user approval remains sufficient for commit if
provenance and deterministic checks are present.

Critique dimensions:

- subject clarity;
- object clarity;
- curiosity clarity;
- focal point count;
- face size;
- text length and readability;
- title-thumbnail complementarity;
- reference-style fit;
- health/trust risk;
- "would this look clickable in a YouTube feed?"

User feedback is then compiled into an edit prompt.

Example:

Input:

```text
臉再大一點，字不要那麼像醫療廣告，便條紙更像 Jeff Su 的乾淨卡片。
```

Output:

```text
Recreate this exact same image using [img 1] as the previous generated result.
Only make these changes:
1. Make the person's face about 20% larger while keeping the same composition.
2. Make the Chinese text feel like a bold educational YouTube thumbnail, not a
   medical advertisement.
3. Make the note card cleaner and more premium, closer to a Jeff Su style UI
   card, while preserving the same three labels.

Do not change the person identity, the title text, the creatine bottle, or the
overall 16:9 composition.
```

### Phase 8. Prompt And Style Memory

Each feedback cycle always updates project-level prompt iteration memory.
Global style memory is a later, evidence-gated feature.

V1 records:

1. user feedback;
2. critique summary when available;
3. previous prompt and next edit prompt;
4. parent/child attempt lineage.

Project-level memory records the exact local evolution of one video.

Global style memory may later aggregate repeated preferences:

- prefer large, readable face;
- avoid hospital/medical-ad look for health topics;
- avoid excessive on-canvas text;
- prefer warm credible expressions over shock;
- prefer clean component cards over random stickers;
- prefer 3-focal-point composition;
- allow vivid contrast but avoid extreme MrBeast shock unless explicitly
  requested.

Global memory must not update from one-off feedback automatically. It should be
proposed only after repeated patterns or explicit user confirmation, and
provider workaround memory must stay separate from Shosho taste/style memory.

---

## Data Contracts

### ThumbnailBriefV3

```json
{
  "schema_version": "thumbnail_brief_v3",
  "project_slug": "creatine-health-benefits",
  "content_type": "youtube",
  "title_pool_run_id": "title-run-20260603-001",
  "selected_title_id": "T-A1",
  "idea_id": "thumb-idea-001",
  "concept_id": "concept-001",
  "brief_version": 1,
  "status": "active",
  "created_at": "2026-06-03T14:00:00+08:00",
  "created_by": "thumbnail_v3_brainstorm",
  "supersedes_brief_id": null,
  "title_pairing": "肌酸的 6 個健康效益：不只是增肌，更是護腦與抗老",
  "viewer_promise": "Creatine has credible benefits beyond muscle.",
  "reference_template_id": "shosho_benefit_list_card",
  "style_contract_id": "ali_warm_evidence_list",
  "subject": {
    "kind": "person",
    "description": "Shosho, warm credible explainer",
    "expression_goal": "calm confident curiosity"
  },
  "object": {
    "kind": "product_plus_card",
    "description": "Creatine bottle plus benefit card",
    "labels": ["護腦", "抗老", "增力"]
  },
  "curiosity": "Not just muscle: what else does creatine do?",
  "thumbnail_text": "不只增肌",
  "text_rendering_policy": "deterministic_overlay",
  "reference_style": ["ali_warm_explainer", "jeff_clean_component"],
  "focal_points": ["face", "creatine_bottle_card", "short_text"],
  "trust_risk": "No treatment or miracle-cure implication.",
  "why_it_earns_clicks": "Expectation violation plus clear health relevance."
}
```

### ReferencePackageV3

```json
{
  "schema_version": "thumbnail_reference_package_v3",
  "package_id": "refpkg-001",
  "brief_id": "thumb-idea-001",
  "provider_binding_profile": "prompt_edit_nano_banana_pro",
  "created_at": "2026-06-03T14:05:00+08:00",
  "bindings": [
    {
      "reference_id": "ref-person-front-smile-001",
      "role": "person_reference",
      "provider_label": "img 1",
      "path": "E:/Shosho LifeOS/Attachments/thumbnail_refs/person/front_smile.jpg",
      "sha256": "sha256-placeholder",
      "mime_type": "image/jpeg",
      "width": 1800,
      "height": 2400,
      "source_uri": "local",
      "rights_license_status": "owned_by_shosho",
      "identity_usage": "host_identity",
      "approved_for_provider_upload": true,
      "usage": "identity, face, upper-body reference"
    },
    {
      "reference_id": "ref-creatine-bottle-001",
      "role": "object_reference",
      "provider_label": "img 2",
      "path": "E:/Shosho LifeOS/Attachments/thumbnail_refs/objects/creatine_bottle.jpg",
      "sha256": "sha256-placeholder",
      "mime_type": "image/jpeg",
      "width": 1200,
      "height": 1200,
      "source_uri": "local_or_stock",
      "rights_license_status": "needs_review",
      "identity_usage": "product_shape_reference",
      "approved_for_provider_upload": false,
      "usage": "product shape and label inspiration"
    },
    {
      "reference_id": "ref-jeff-clean-component-001",
      "role": "style_reference",
      "provider_label": "img 3",
      "path": "E:/thumbnail-example/Jeff Su/Learn 80% of NotebookLM in Under 13 Minutes!.jpg",
      "sha256": "sha256-placeholder",
      "mime_type": "image/jpeg",
      "width": 1280,
      "height": 720,
      "source_uri": "local_reference_corpus",
      "rights_license_status": "style_reference_only",
      "identity_usage": "none",
      "approved_for_provider_upload": true,
      "usage": "clean component layout, not direct copying"
    }
  ],
  "provider_order": ["ref-person-front-smile-001", "ref-creatine-bottle-001", "ref-jeff-clean-component-001"],
  "notes": [
    "Upload references in this order when using manual external tools.",
    "Do not include more than two style references unless the prompt compiler asks for it."
  ]
}
```

### GenerationPromptV3

```json
{
  "schema_version": "thumbnail_generation_prompt_v3",
  "prompt_id": "prompt-001",
  "prompt_version": 1,
  "provider_profile": "generic_image_model",
  "provider_profile_version": 1,
  "compiler_version": "thumbnail_prompt_compiler_v3.0",
  "brief_id": "thumb-idea-001",
  "reference_package_id": "refpkg-001",
  "visual_strategy_id": "strategy-001",
  "compiled_at": "2026-06-03T14:10:00+08:00",
  "sections": {
    "task": "Create a professional 16:9 YouTube thumbnail base image.",
    "reference_bindings": ["Use [img 1] for host identity.", "Use [img 2] for product shape."],
    "scene": "Warm educational studio thumbnail with large host and clean component card.",
    "composition": "Host on left third, object card on right third, lower third reserved for text overlay.",
    "text_policy": "Leave text zones blank; Nakama will overlay exact Traditional Chinese.",
    "style": "Ali warmth plus Jeff clean component clarity.",
    "negative_rules": ["No extra text", "No medical ad", "No clutter", "No distorted face"]
  },
  "prompt_text": "...compiled provider-ready text...",
  "negative_prompt": "No extra text. No medical ad. No clutter. No distorted face.",
  "output": {
    "aspect_ratio": "16:9",
    "target_resolution": "1280x720 or higher",
    "generations": 3
  }
}
```

### VisualStrategyV3

```json
{
  "schema_version": "thumbnail_visual_strategy_v3",
  "visual_strategy_id": "strategy-001",
  "brief_id": "thumb-idea-001",
  "reference_template_id": "shosho_benefit_list_card",
  "style_contract_id": "ali_warm_evidence_list",
  "click_psychology": "Expectation violation: a gym supplement also matters for brain/aging.",
  "composition_rules": {
    "subject_position": "left_third",
    "subject_scale": "face_readable_at_160x90",
    "object_group_position": "right_third",
    "text_zone": "lower_third_reserved"
  },
  "style_rules": ["warm credible", "clean educational", "high contrast"],
  "text_rules": {
    "rendering_policy": "deterministic_overlay",
    "thumbnail_text": "不只增肌",
    "max_characters": 8
  },
  "reference_bindings": ["ref-person-front-smile-001", "ref-creatine-bottle-001", "ref-jeff-clean-component-001"],
  "negative_rules": ["No medical-ad look", "No extra labels", "No clutter"],
  "provider_notes": ["Ask provider to leave blank text zones."]
}
```

### GenerationAttemptV3

```json
{
  "schema_version": "thumbnail_generation_attempt_v3",
  "attempt_id": "attempt-001",
  "brief_id": "thumb-idea-001",
  "reference_package_id": "refpkg-001",
  "visual_strategy_id": "strategy-001",
  "prompt_id": "prompt-001",
  "generation_index": 1,
  "parent_attempt_id": null,
  "provider": "prompt_edit",
  "model": "nano_banana_pro",
  "provider_job_id": null,
  "manual_import": true,
  "imported_at": "2026-06-03T14:30:00+08:00",
  "imported_by": "shosho",
  "original_filename": "promptedit-creatine-1.png",
  "output_path": "data/thumbnails/肌酸的妙用/external/attempt-001.png",
  "file_sha256": "sha256-placeholder",
  "width": 1280,
  "height": 720,
  "status": "needs_revision",
  "accepted_at": null,
  "promoted_thumbnail_path": null,
  "user_feedback": "臉太小，卡片太像醫療廣告",
  "critique": {
    "status": "needs_revision",
    "focal_points_ok": true,
    "text_readable": true,
    "face_large_enough": false,
    "reference_fit": "medium",
    "trust_risk": "medical-ad feel"
  },
  "next_edit_prompt": "Recreate this exact same image..."
}
```

### StyleMemoryV3

```json
{
  "schema_version": "thumbnail_style_memory_v3",
  "memory_id": "style-memory-shosho-youtube-001",
  "scope": "channel",
  "channel": "shosho_youtube",
  "version": 1,
  "status": "approved",
  "rules": [
    {
      "rule_id": "large-readable-face",
      "polarity": "positive",
      "text": "Large readable face",
      "source_attempt_ids": ["attempt-001"],
      "feedback_count": 3,
      "confidence": 0.8,
      "approved_by": "shosho",
      "approved_at": "2026-06-03T16:00:00+08:00",
      "last_used_at": null
    },
    {
      "rule_id": "avoid-medical-ad",
      "polarity": "negative",
      "text": "Avoid hospital or medical-ad background for general health topics",
      "source_attempt_ids": ["attempt-001"],
      "feedback_count": 2,
      "confidence": 0.7,
      "approved_by": "shosho",
      "approved_at": "2026-06-03T16:00:00+08:00",
      "last_used_at": null
    }
  ],
  "pending_user_confirmation": []
}
```

---

## UI Workflow

The Title & Thumbnail tab should evolve into a V3 workflow surface.

### Section 1. Title Pool

Existing title pool remains the first input.

### Section 2. Thumbnail Concepts

Generate three `ThumbnailBriefV3` cards.

Each card shows:

- title pairing;
- subject / object / curiosity;
- thumbnail text;
- focal points;
- why it earns clicks;
- trust risk.

### Section 3. Reference Package

For each card:

- list required references;
- show local file paths when known;
- allow user to attach or replace references;
- show binding order: `[img 1]`, `[img 2]`, `[img 3]`.
- show readiness state: missing, not approved for provider upload, ready.
- show stable reference ids and provider-order aliases separately.

### Section 4. Prompt Package

For each card:

- show compiled prompt;
- show negative prompt;
- show provider notes;
- provide copy buttons.
- show the deterministic Traditional Chinese overlay text separately from the
  external image prompt.
- show a small wireframe/overlay preview when text will be added by Nakama.

### Section 5. Import Generated Image

User imports one or more generated images.

Nakama records:

- provider;
- model;
- prompt used;
- reference package used;
- output image path.
- imported file hash and dimensions;
- parent attempt when this is an edit generation.

### Section 6. Critique And Feedback

For each imported image:

- display image;
- run critique;
- show pass/warn/fail dimensions;
- allow free-text feedback;
- generate edit prompt;
- preserve iteration history.
- allow manual accept even if AI critique is not run, as long as provenance and
  deterministic checks are present.

### Section 7. History Comparison

Show all attempts for the project in a grid.

Each attempt should show:

- concept id;
- prompt version;
- reference package version;
- provider/model;
- parent attempt;
- feedback summary;
- accepted/rejected status;
- final thumbnail candidate flag.

---

## Provider Profiles

Provider profiles capture capability and syntax differences without changing
the brief or visual strategy.

Each provider profile records:

- `supports_negative_prompt`;
- `supports_multi_reference_order`;
- `supports_image_edit`;
- `edit_preserves_composition_reliably`;
- `text_reliability`: `poor | medium | good`;
- `max_reference_images`;
- `aspect_ratio_syntax`;
- `generation_count_syntax`;
- `reference_binding_syntax`;
- `recommended_text_policy`.

### Generic Image Model

- Use `[img N]` textual bindings.
- Single prompt field.
- Optional negative prompt.
- Default `recommended_text_policy`: deterministic overlay for Traditional
  Chinese.

### Prompt Edit / Nano Banana Pro

- Use explicit reference binding phrases:
  - "Use [img 1] as..."
  - "Replace the screen with [img 2]..."
- Recommended generation count: 3 for new concepts, 1 for edit prompts.
- For edits, include previous output as `[img 1]` and start with:
  - "Recreate this exact same image. Only change..."
- Treat model-rendered Traditional Chinese as experimental unless the user
  explicitly chooses it.

### Gemini / Scenario

- Same conceptual structure, but provider-specific adapters may eventually tune
  prompt phrasing, image count, and reference-binding syntax.

V1 should keep provider profiles declarative. Do not hard-code one provider into
the core data model.

---

## Relationship To Existing Renderer

ADR-037/038 renderer stages remain useful for:

- diagnosing subject scale;
- producing wireframes;
- comparing deterministic layout intent;
- creating internal previews;
- validating rough subject/object/text placement;
- compiling concrete template/style constraints;
- rendering exact Traditional Chinese text overlays;
- running cheap post-import mechanical checks;
- preserving existing candidate/commit semantics;
- future fallback when external generation is unavailable.

They are not the primary path for final upload-grade art.

The final thumbnail candidate in V3 is an imported generated image, optionally
with deterministic Nakama text overlay, plus its prompt/reference/feedback
history.

This avoids the previous trap where Nakama spent effort recreating a design
tool but still produced visually weak outputs.

---

## Implementation Slices

### Slice 1. V3 Prompt Package Panel For Existing Ideas

Add a V3 prompt package panel to the existing three thumbnail idea cards.

Use existing `thumbnail_ideas` frontmatter as the source concept input. Do not
replace the brainstorm generator yet.

Acceptance:

- Each existing idea can show a compiled V3 prompt package.
- The panel clearly marks V3 as external-generation workflow.
- No provider API key is needed.

### Slice 2. Prompt Compiler Contracts And Golden Tests

Implement pure compiler outputs:

- `VisualStrategyV3`;
- `GenerationPromptV3`;
- capability-based `ProviderProfileV3`;
- deterministic Traditional Chinese text policy.

Acceptance:

- Golden tests cover brief/reference/profile to prompt conversion.
- Every compiled prompt names reference bindings.
- Every prompt includes focal-point limit.
- Every prompt separates model-generated image content from Nakama-rendered
  Traditional Chinese text overlay.
- Every prompt includes negative rules for clutter, medical-ad look, and face
  distortion.

### Slice 3. Reference Package Readiness

Implement:

- stable reference ids;
- file hashes;
- roles and rights/license status;
- ordered provider binding aliases;
- missing/not-approved/ready UI states.

Acceptance:

- User knows exactly which files to upload to the external tool and in what
  order.
- Provider-order aliases like `[img 1]` never replace stable ids in storage.

### Slice 4. Manual External Generation Surface

Add UI for:

- full prompt copy;
- negative prompt copy;
- provider profile notes;
- ordered upload checklist;
- feed-size preview and deterministic text overlay preview.

Acceptance:

- User can run the external tool manually from the UI without reading source
  files or copying paths from dev output.

### Slice 5. Import And Attempt History

Add UI and route for importing generated images.

Store `GenerationAttemptV3` under the existing `data/thumbnails/{slug}/...`
working area and preserve lineage to prompt/reference package.

Acceptance:

- Each imported image is tied to the prompt and reference package that produced
  it.
- Attempts record parent attempt, file hash, dimensions, provider/model, and
  status.
- History grid shows attempts and statuses.

### Slice 6. Accept/Promote Imported Candidate

Connect imported attempts to the existing thumbnail commit semantics.

Acceptance:

- Accepted imported images can become the project thumbnail.
- Frontmatter remains compatible with existing `thumbnail`, `thumbnail_run`,
  and `thumbnail_chosen_at` consumers.
- Vault output path follows the existing thumbnail commit pattern.

### Slice 7. Critique And Edit Prompt

Add critique and feedback loop.

Acceptance:

- User feedback produces a preservation edit prompt.
- The edit prompt starts from the previous image, not from scratch.
- Attempt history records the feedback and next prompt.

### Slice 8. Concept Generator Upgrade

Update the thumbnail brainstorm step to output `ThumbnailBriefV3` natively
after the prompt package workflow proves useful.

Acceptance:

- Each concept has subject/object/curiosity.
- Each concept has a concrete `reference_template_id` or `style_contract_id`.
- Each concept has at most three focal points.
- Each concept has why-it-earns-clicks rationale.
- Each concept has trust-risk language for health topics.

### Slice 9. Style Memory

Persist project-level iteration memory first. Add global style memory only
after repeated accepted/rejected attempts create enough evidence.

Acceptance:

- Repeated feedback can be proposed as a global style memory update.
- Global style changes require explicit user approval before becoming default.
- Provider workaround memory is stored separately from Shosho style memory.

---

## Acceptance Criteria

V3 concept:

- Given 8-12 title ideas and a project description, Nakama produces three
  thumbnail concepts for one publish title.
- Each concept has subject/object/curiosity.
- Each concept has at most three focal points.
- Each concept explains why it earns clicks.

V3 prompt package:

- Each concept has a reference package with ordered image bindings.
- Each concept has a model-specific prompt and negative prompt.
- The prompt is visual-director language, not raw strategy brief.
- The prompt separates image-model work from deterministic Traditional Chinese
  text overlay by default.
- The prompt includes YouTube thumbnail best practices: large subject,
  high contrast, short text, clear object, no clutter.

V3 manual generation:

- User can copy prompt and upload references into an external tool.
- User can import generated images into Nakama.
- Imported images preserve provider/model/prompt/reference provenance.
- Imported images can be promoted through existing thumbnail commit semantics.
- History preserves parent attempt lineage for edit loops.

V3 edit loop:

- User can write free-text feedback.
- Nakama produces a preservation edit prompt.
- Iteration history is visible.

V3 style learning:

- Project-level feedback is recorded.
- Global style memory is not silently updated from one attempt.
- Accepted global preferences affect future prompt compilation.
- Provider workaround memory is separated from Shosho taste/style memory.

---

## Open Questions For Panel Review

1. How long should V3 run in parallel with renderer-first thumbnail ideas before
   the old final-render path is hidden from the default UI?
2. Is the Triple Threat formula sufficient for Shosho's health/longevity niche,
   or should evidence/trust be a fourth internal axis that does not become a
   fourth visual focal point?
3. Should global style memory eventually live in repo data, vault frontmatter,
   or a global profile file?
4. How much should the prompt compiler constrain provider output before it
   kills useful creativity?
5. Should imported generated images be allowed to become final thumbnails
   without a second AI critique, if Shosho manually approves them?
6. What is the minimum viable UI that avoids another overbuilt tool?
7. After deterministic overlay works, should model-rendered Traditional Chinese
   remain an experiment mode or be removed from the normal workflow?
8. Should Envato/stock/Scenario asset search be a separate later agent, or part
   of the reference package builder from day one?

---

## Consequences

Positive:

- Moves final visual synthesis to tools that are good at final visual synthesis.
- Preserves Nakama's strengths: strategy, memory, prompt generation, critique,
  and history.
- Reduces time spent polishing a deterministic renderer that still lacks taste.
- Makes user feedback compound into better prompts over time.
- Keeps the workflow usable before external API automation exists.

Trade-offs:

- Manual external tool steps remain in V1.
- Prompt quality becomes a first-class system component.
- Deterministic Traditional Chinese overlay adds implementation work but avoids
  gambling on model text accuracy.
- Generated images introduce provider variance and provenance questions.
- The renderer work from ADR-037/038 becomes secondary, not central.

Risks:

- The system may over-trust external image models and under-specify prompts.
- Prompt history can become noisy if not structured.
- Style memory can overfit to one bad generation unless updates require user
  confirmation.
- Provider-specific tricks may leak into the core model if profiles are not
  isolated.

---

## Panel Review Record

Used the user-level `multi-agent-panel` workflow:

1. Claude/Codex session wrote the v1 draft.
2. Codex CLI audit ran with push-back posture and saved
   `docs/research/2026-06-03-codex-thumbnail-v3-audit.md`.
3. Gemini audit was skipped because `GEMINI_API_KEY` was not configured in the
   current shell.
4. Integration matrix saved at
   `docs/research/2026-06-03-thumbnail-v3-panel-integration.md`.
5. This v2 draft incorporates the accepted audit findings.

Panel-specific audit focus:

- product workflow practicality;
- prompt compiler quality;
- data model sufficiency;
- renderer migration risk;
- feedback memory safety;
- provider lock-in risk;
- handling of Traditional Chinese text;
- whether the first implementation slice is small enough.
