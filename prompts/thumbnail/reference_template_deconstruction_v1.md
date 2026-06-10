# Reference Template Deconstruction v1

You are decomposing YouTube thumbnail reference images into executable template
records for 修修's thumbnail generator.

Authoritative spec:

`docs/research/2026-05-29-ali-jeff-thumbnail-template-corpus-spec.md`

Current family taxonomy:

`prompts/thumbnail/ali_jeff_template_family_taxonomy_v1.json`

## Task

For each assigned image, return one compact JSON object. Do not write prose
outside the JSON envelope.

Required envelope:

```json
{
  "batch": "ali_01",
  "records": [],
  "family_feedback": [],
  "schema_feedback": []
}
```

Each record must include:

```json
{
  "schema_version": "ali_jeff_template_corpus_v1",
  "reference_id": "ali_abdaal_001",
  "creator": "Ali Abdaal",
  "title": "5 Easy Ways to Become More Self-Disciplined",
  "image_path": "E:/thumbnail-example/Ali Abdaal/5 Easy Ways to Become More Self-Disciplined.jpg",
  "image_size": [1280, 720],
  "priority": "gold",
  "renderable_v1": true,
  "template_family_candidate": "ali_toggle_metaphor",
  "family_confidence": "high",
  "title_intent_tags": ["numbered_list", "self_improvement"],
  "composition": {
    "layout_summary": "host right third, metaphor pill left",
    "focal_hierarchy": ["metaphor pill", "host face"],
    "reading_path": "left text -> host face",
    "visual_balance": "left_heavy",
    "negative_space": "medium",
    "dominant_zones": ["left_payload", "right_host"]
  },
  "background": {
    "type": "studio_room",
    "detail_level": "medium",
    "color_temperature": "warm",
    "brand_reuse_potential": "medium",
    "notes": []
  },
  "host": {
    "present": true,
    "box_pct": [0.48, 0.02, 0.98, 1.02],
    "face_box_pct": [0.61, 0.10, 0.80, 0.38],
    "face_height_ratio": 0.28,
    "placement": "right_third",
    "gaze": "down_left",
    "expression": "thoughtful",
    "gesture": "holding_object",
    "crop": "torso",
    "occlusion_role": "separate",
    "placement_rule": "host should not compete with the metaphor pill"
  },
  "components": [
    {
      "component_id": "primary_payload",
      "role": "primary_payload",
      "type": "tool_label",
      "box_pct": [0.04, 0.08, 0.44, 0.26],
      "text": ["Discipline"],
      "visual_style": "white rounded pill with green toggle",
      "semantic_job": "turns discipline into an easy switch metaphor",
      "asset_needs": [],
      "z_index": 20,
      "required": true
    }
  ],
  "typography": [],
  "overlap_rules": [],
  "style_tokens": ["rounded white UI", "green accent", "soft studio"],
  "generator_constraints": {
    "must_preserve": ["one simple metaphor component", "host face visible"],
    "must_avoid": ["extra icon clutter", "paragraph text"],
    "max_primary_components": 1,
    "max_total_components": 2,
    "minimum_face_height_ratio": 0.25,
    "template_breakers": ["component no longer reads as a simple metaphor"]
  },
  "evaluation_targets": {
    "first_glance_should_read_as": "simple switch-like habit metaphor",
    "small_size_checks": ["pill text readable", "host face visible"],
    "reference_fit_checks": ["same left payload / right host relationship"],
    "repair_hints": ["simplify component", "increase face if expression is lost"]
  },
  "notes": []
}
```

## Rules

- Use normalized percent boxes `[x0, y0, x1, y1]`.
- Estimate visually; do not pretend pixel precision.
- Use an existing family candidate if it is close.
- Put uncertainty in `notes`, `family_feedback`, or `schema_feedback`.
- If a thumbnail has many icons, use one `app_icon_cluster` component unless
  individual icons have different semantic jobs.
- The output must help a renderer recreate the grammar. Avoid generic art
  critique.
