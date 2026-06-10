# Ali/Jeff Template Corpus Normalization Plan

Date: 2026-05-29

## Current State

The scoped reference corpus is now:

- 21 Ali Abdaal thumbnails.
- 33 Jeff Su thumbnails.
- 54 total assignments in
  `prompts/thumbnail/ali_jeff_template_family_taxonomy_v1.json`.

The taxonomy is intentionally family-level. It answers:

- Which repeatable visual families exist.
- Which source image belongs to which family.
- Which families are safe for v1 rendering.
- What host poses, payload slots, component types, typography rules, and
  template breakers each family needs.

It is not yet the full per-image corpus. The next layer should add exact
per-image deconstruction records.

## Slice 1: Normalize Per-Image Records

Create a JSONL corpus:

`prompts/thumbnail/reference_corpus/ali_jeff_deconstructions_v1.jsonl`

Each line should follow
`prompts/thumbnail/reference_template_deconstruction_schema_v1.json`.

One record per image should include:

- `reference_id`
- `creator`
- `title`
- `image_path`
- `template_family_candidate`
- host placement, face size, gaze, expression, gesture, crop
- background type and color temperature
- component records with semantic jobs
- typography records
- overlap rules
- generator constraints
- evaluation targets

Acceptance tests:

- 54 records exist.
- Every record validates against the deconstruction schema.
- Every `template_family_candidate` exists in the family taxonomy.
- Every `image_path` exists on disk.
- Every family in the taxonomy has at least one assigned record.

## Slice 2: Corpus Loader

Add a small loader module:

`shared/thumbnail_reference_corpus.py`

Responsibilities:

- Load the family taxonomy.
- Load JSONL per-image records.
- Query by family, creator, title intent tags, renderable status, and priority.
- Expose compact records for prompts without dumping the full corpus.

Acceptance tests:

- Can load taxonomy and records.
- Can return only `renderable_v1` records.
- Can return best examples for `jeff_tool_header_panel`,
  `jeff_command_panel`, `ali_metric_arrow`, and `ali_social_quote_card`.
- Fails loudly on unknown family IDs.

## Slice 3: Template Selector Upgrade

Update `shared/thumbnail_reference_templates.py` so hard-coded production
templates are backed by the taxonomy/corpus instead of a small manually curated
list.

Important rule:

The selector should output a concrete reference image plus a family contract,
not just a loose creator style.

Acceptance tests:

- A tool tutorial title can match Jeff tool families.
- A mistake/correction title can match `jeff_command_panel`.
- A measurable transformation title can match `ali_metric_arrow`.
- A soft personal advice title can match `ali_social_quote_card`.
- A health/evidence title can still fall back to the Shosho benefit-card
  production template until we add a stronger Ali/Jeff health-specific family.

## Slice 4: Host Pose Compatibility Gate

Before arrangement generation, compare the selected template record with the
available cutout metadata.

Gate requirements:

- Host side and gaze cannot contradict the reference.
- Center-host templates require camera-facing or near-camera-facing cutouts.
- Pointing templates require a matching pointing direction.
- Face height target must be within the template family's accepted range.
- If no compatible cutout exists, return a clear "needs new photo" result
  instead of rendering a bad composition.

Acceptance tests:

- A right-looking host is rejected for a left-side Jeff panel if the reference
  needs camera-facing center host.
- `ali_metric_arrow` prefers center/two-hand comparison poses.
- `jeff_command_panel` prefers pointing poses toward the panel.

## Slice 5: Arrangement Eval Loop

Use the selected reference record as the scoring target.

The cheap deterministic evaluator should score:

- Host scale and face salience.
- Host side and gaze direction.
- Payload side and component count.
- Text density and text role.
- Whether the selected components match the family allowed types.
- Whether the output preserves the reference's first-glance hierarchy.

The vision evaluator should only run after deterministic gates pass.

Acceptance tests:

- Wrong-side host/payload relationship fails before render.
- Too many components fail before render.
- Archive/reference-only families cannot be selected for production render.
- The eval output includes repair hints that can be fed back into the next
  arrangement attempt.

## Slice 6: Production UI Feedback Loop

Add the user feedback field later, after the selector and arrangement gate are
stable.

The feedback text should become a patch instruction against:

- selected template family
- selected reference image
- host pose
- payload component
- typography
- background

Do not treat feedback as a free-form regenerate prompt. Treat it as a constrained
edit request against the current structured thumbnail plan.

## Immediate Recommendation

Do Slice 1 and Slice 2 next. The renderer should not consume the taxonomy
directly as if it were a finished visual record. The taxonomy tells us which
families exist; the per-image records tell us what a specific reference actually
looks like.
