# Ali/Jeff Thumbnail Template Corpus Spec v1

Date: 2026-05-29
Scope: `E:/thumbnail-example/Ali Abdaal` and `E:/thumbnail-example/Jeff Su`

## Purpose

Build a template-first reference corpus that can drive the thumbnail workflow:

1. Match a title idea to a concrete reference template.
2. Select a compatible host pose/cutout.
3. Compose host, background, typography, and components into deterministic slots.
4. Evaluate whether the render still matches the selected reference template.

This corpus is narrower than the 140-image playbook. The playbook captures broad
click psychology; this corpus captures executable visual grammar.

Related production input:

- `docs/research/2026-05-29-thumbnail-cutout-shot-list-v1.md` translates the
  first Ali/Jeff pass into new host photo and cutout requirements.
- `prompts/thumbnail/ali_jeff_template_family_taxonomy_v1.json` is the
  machine-readable family taxonomy and current 54-image assignment map. Use it
  as the bridge between per-image deconstruction and production template
  matching.

## Output Layers

### 1. Per-Image Deconstruction

One JSON object per reference image. This is an observation record, not yet a
general template. Sub-agents should fill this layer first.

Required fields:

```json
{
  "schema_version": "ali_jeff_template_corpus_v1",
  "reference_id": "jeff_su_001",
  "creator": "Jeff Su",
  "title": "10 INCREDIBLE things Google Sheets can do Right Now!",
  "image_path": "E:/thumbnail-example/Jeff Su/10 INCREDIBLE things Google Sheets can do Right Now!.jpg",
  "image_size": [1280, 720],
  "priority": "gold|silver|archive",
  "renderable_v1": true,
  "template_family_candidate": "jeff_ui_panel_host_side",
  "family_confidence": "high|medium|low",
  "title_intent_tags": ["tutorial", "tool", "numbered_list"],
  "composition": {},
  "background": {},
  "host": {},
  "components": [],
  "typography": [],
  "overlap_rules": [],
  "style_tokens": [],
  "generator_constraints": {},
  "evaluation_targets": {},
  "notes": []
}
```

Coordinate convention:

- Use normalized percent boxes: `[x0, y0, x1, y1]`.
- Canvas origin is top-left.
- Values may exceed `0..1` if the host/body is intentionally cropped past the
  canvas edge.
- Estimate boxes visually; exact pixel-perfect annotation is not required in
  v1. Prefer useful, consistent estimates over false precision.

### 2. Template-Family Contract

One JSON object per reusable template family after several per-image records
show the same grammar.

Required fields:

```json
{
  "family_id": "jeff_tool_header_panel",
  "creator_style": "Jeff Su",
  "label": "Tool label + UI panel + host",
  "example_reference_ids": ["jeff_su_013", "jeff_su_014", "jeff_su_015"],
  "best_for": ["tool tutorial", "learn X in N minutes"],
  "avoid": ["health claim with no tool", "dense paragraph payload"],
  "slot_model": {},
  "allowed_components": [],
  "host_policy": {},
  "background_policy": {},
  "typography_policy": {},
  "overlap_policy": [],
  "cheap_eval_gates": {},
  "vision_eval_rubric": []
}
```

## Per-Image Field Definitions

### `priority`

- `gold`: strong reference; should influence production templates.
- `silver`: useful variant, but weaker or less directly relevant.
- `archive`: keep record, but do not use for v1 generation.

### `renderable_v1`

`true` only if we can reasonably recreate the layout with current tools:
blurred A-roll background, host cutout, text, simple UI cards, logos, arrows,
badges, and simple icons. Mark `false` for templates that require complex
photo-real object compositing or too many exact screenshots.

### `composition`

```json
{
  "layout_summary": "host right third, large UI panel left, tool label top",
  "focal_hierarchy": ["host face", "tool label", "UI panel", "logo tile"],
  "reading_path": "top label -> host face -> panel",
  "visual_balance": "left_heavy|right_heavy|centered|split",
  "negative_space": "low|medium|high",
  "dominant_zones": ["left_payload", "right_host"]
}
```

### `background`

```json
{
  "type": "blurred_aroll|studio_room|dark_ui|light_ui|split_screen|object_scene",
  "detail_level": "low|medium|high",
  "color_temperature": "warm|cool|neutral|mixed",
  "brand_reuse_potential": "high|medium|low",
  "notes": []
}
```

### `host`

```json
{
  "present": true,
  "box_pct": [0.52, -0.02, 1.04, 1.08],
  "face_box_pct": [0.68, 0.08, 0.89, 0.40],
  "face_height_ratio": 0.32,
  "placement": "left_third|center|right_third|full_center|none",
  "gaze": "camera|left|right|up_left|up_right|down_left|down_right",
  "expression": "excited|thoughtful|surprised|explaining|serious|laughing|pointing|neutral|n/a",
  "gesture": "none|pointing_left|pointing_right|open_hands|chin_touch|holding_object|two_hands",
  "crop": "head_shoulders|torso|tight_face|half_body|multi_pose",
  "occlusion_role": "in_front_of_payload|behind_payload|separate|n/a",
  "placement_rule": "what a generator must preserve"
}
```

Host rule:

- If host gaze points right, the host should usually sit left or center-left
  unless the reference template intentionally breaks that rule.
- If host points at a component, the component must sit in the pointed direction.
- Face salience is usually more important than full-body preservation.
- For no-host references, set `present=false` and allow `box_pct` /
  `face_box_pct` to be `null`.

### `components`

Use one record per non-host visual element.

```json
{
  "component_id": "primary_panel",
  "role": "primary_payload|supporting_asset|connector|brand_logo|metric|badge",
  "type": "ui_panel|command_panel|tool_label|logo_tile|app_icon_cluster|metric_badge|arrow|quote_card|social_post_card|before_after_panel|whiteboard_diagram|object_cutout|text_banner|step_card|screenshot_panel",
  "box_pct": [0.05, 0.12, 0.58, 0.66],
  "text": ["DO THIS INSTEAD"],
  "visual_style": "dark rounded panel with blue action strip",
  "semantic_job": "shows the corrected action",
  "asset_needs": ["ChatGPT logo"],
  "z_index": 20,
  "required": true
}
```

Component rule:

- Record the semantic job, not just the shape. For example, a blue rectangle can
  be a command label, a progress bar, or a comparison result. The renderer needs
  to know why it exists.

### `typography`

Use typography records for prominent text that is not already inside a specific
component.

```json
{
  "text_id": "bottom_headline",
  "text": "DO THIS INSTEAD",
  "box_pct": [0.04, 0.68, 0.62, 0.92],
  "style": "bold_sans|handwritten_glow|white_stroke|black_panel|pill_label|none",
  "case": "uppercase|title_case|mixed|chinese",
  "max_words": 4,
  "color": "white",
  "stroke_or_shadow": "black stroke + drop shadow",
  "readability": "high|medium|low"
}
```

Typography rule:

- If a template has no big headline, explicitly set typography to `[]`.
- Do not force Shosho bottom handwritten text into Jeff/Ali templates unless
  the reference actually uses that grammar.

### `overlap_rules`

```json
{
  "foreground": "host_hand",
  "background": "primary_panel",
  "status": "allowed|tolerated|forbidden",
  "protected_region": "face|keyword_text|logo|none",
  "note": "hand may overlap card edge, but not keyword text"
}
```

### `generator_constraints`

```json
{
  "must_preserve": [
    "host face occupies at least 35% of canvas height",
    "primary payload is opposite host"
  ],
  "must_avoid": [
    "more than one competing primary panel",
    "tiny unreadable UI screenshot"
  ],
  "max_primary_components": 1,
  "max_total_components": 4,
  "minimum_face_height_ratio": 0.32,
  "template_breakers": [
    "host gaze points away from payload",
    "component text becomes paragraph-length"
  ]
}
```

### `evaluation_targets`

```json
{
  "first_glance_should_read_as": "clean tutorial for a named tool",
  "small_size_checks": [
    "tool name readable",
    "host expression visible",
    "only one main action phrase"
  ],
  "reference_fit_checks": [
    "same host/payload side relationship",
    "same component count",
    "similar text density"
  ],
  "repair_hints": [
    "increase host scale",
    "move payload opposite gaze",
    "reduce text to 3 words"
  ]
}
```

## Initial Family Candidates

These are starting hypotheses only. Sub-agents may propose merges/splits, but
they must still assign every image to one candidate or mark it `archive`.

### Ali Abdaal

- `ali_toggle_metaphor`: floating toggle/pill or simple metaphor control.
- `ali_icon_cluster_host`: host plus several app/object icons.
- `ali_year_hero`: huge year number with host.
- `ali_income_cards`: money/social-platform cards around host.
- `ali_command_text_host`: big blunt phrase plus host.
- `ali_whiteboard_diagram`: board, diagram, system, brain, or schedule.
- `ali_before_after_split`: before/after split-screen transformation.
- `ali_evidence_stack`: books/tools stack as evidence object.
- `ali_metric_arrow`: two metrics connected by arrow.
- `ali_social_quote_card`: tweet/social card plus host.

### Jeff Su

- `jeff_tool_header_panel`: top tool label, clean UI panel, logo tile, host.
- `jeff_command_panel`: dark command/code panel with correction phrase.
- `jeff_logo_cluster_dark`: host surrounded by app logos on dark background.
- `jeff_ui_panel_host_side`: one large UI card opposite host.
- `jeff_metric_time_saving`: before/after or time-saving metric bar.
- `jeff_dual_tool_cards`: two tool cards compared around host.
- `jeff_text_overlay_panel`: dominant textual panel or slogan with host.
- `jeff_step_cards`: two-step or checklist cards.
- `jeff_roadmap_board`: roadmap/whiteboard/diagram style.

## Sub-Agent Assignment Protocol

Each sub-agent receives:

1. This spec.
2. A batch of image paths.
3. The contact sheet for its creator.

Return:

```json
{
  "batch": "ali_01",
  "records": [
    {
      "...": "one per-image deconstruction object"
    }
  ],
  "family_feedback": [
    "suggested merge/split or unclear category"
  ],
  "schema_feedback": [
    "fields that were hard to fill consistently"
  ]
}
```

Quality bar:

- Prefer explicit uncertainty over confident guessing.
- Do not invent a new family if an existing candidate is close; propose a split
  in `family_feedback` instead.
- Keep component count literal. If a thumbnail has 8 icons, record an
  `app_icon_cluster` component rather than 8 separate logo components unless
  individual logos are semantically important.
- The record must be useful for a renderer, not just descriptive prose.
