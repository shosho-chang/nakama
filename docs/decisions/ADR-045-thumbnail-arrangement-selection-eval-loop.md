# ADR-045: Thumbnail Arrangement, Asset Selection, and Evaluation Loop

**Status:** Proposed
**Date:** 2026-05-29
**Related:** [ADR-033](ADR-033-thumbnail-generation-pipeline.md), [ADR-036](ADR-036-title-driven-thumbnail-workflow.md), [ADR-037](ADR-037-staged-thumbnail-production-workflow.md)

---

## Context

ADR-037 made thumbnail production diagnosable by splitting the one-shot render
into staged artifacts. That solved the "why did Step 6 ignore Step 5?" class of
bugs. It did not solve the harder taste problem:

- the right component type may be selected, but arranged poorly;
- the right asset query may be generated, but the selected asset may be
  visually weak at YouTube thumbnail size;
- a layout may pass mechanical checks while still looking like a debug mock,
  a slide deck, or a web UI panel;
- overlap is not binary: a hand can overlap a card and improve depth, but a
  hand covering a keyword is bad;
- one final render gives no search space for evaluation or repair.

The new bottleneck is not cutout quality. Cutouts can be reshot. The bottleneck
is a missing arrangement and selection system.

---

## Decision

Add a template-first arrangement pipeline after ADR-037's staged workflow.

The renderer should not invent composition. Instead, it should:

1. match the title/brief to an executable reference-template contract;
2. generate many deterministic arrangement candidates within that contract;
3. use cheap programmatic gates to remove obviously bad candidates;
4. send only the surviving candidates to an expensive vision critic;
5. apply bounded repair patches;
6. show the user only the top three candidates.

Expensive model calls are used only after a small candidate set exists. They are
not used to brute-force the whole layout search space.

---

## Target Workflow

### Phase A. One-time or Rarely Updated Knowledge

#### A1. Reference Template Contract Extraction

Input:

- local Ali Abdaal, Jeff Su, and Shosho reference thumbnails;
- existing `ReferenceTemplate` catalog.

Output:

- `TemplateContract` per reference template.

Contract fields:

- `template_id`
- `creator_target`
- `intent_tags`
- `host_slots`
- `payload_slots`
- `headline_slots`
- `asset_slots`
- `visual_weight`
- `z_order_policy`
- `overlap_policy`
- `style_policy`
- `cheap_eval_gates`
- `vision_eval_rubric`

Acceptance:

- Contracts are renderer-independent: mostly percent boxes and rules.
- Contract says which overlap is allowed, tolerated, or forbidden.
- Contract distinguishes "component can be under hand" from "keyword cannot be
  under hand".
- Contract is small enough to inject into prompts.

#### A2. Asset Taxonomy and Candidate Metadata

Input:

- HyperFrames built-in components;
- manually downloaded Envato/stock/icon assets;
- generated component layers.

Output:

- asset candidate records with source, license/provenance, type, shape, color,
  semantic tags, and readability metadata.

Acceptance:

- Assets are scored as layers, not as vague search phrases.
- A missing asset never blocks a template that can work without it.
- Icons and stock photos are penalized if they do not read at 160 px wide.

### Phase B. Per Episode Cheap Candidate Generation

#### B1. Title to Template Match

Input:

- 10 or so brainstormed title ideas;
- project brief and evidence angle.

Output:

- ranked title-template pairs.

Acceptance:

- One publish title is selected before thumbnail variants are produced.
- Three thumbnail hypotheses share the same title unless the user explicitly
  asks to compare title-thumbnail pairs.
- Each hypothesis is bound to one template contract.

#### B2. Arrangement Variant Generation

Input:

- template contract;
- parsed thumbnail brief;
- selected host cutout and placement;
- optional asset candidates.

Output:

- 12 to 20 arrangement candidates per hypothesis.

Candidate dimensions:

- payload slot choice;
- payload scale;
- payload x/y nudge;
- headline mode: none, bottom headline, component-only;
- host z-order: behind payload, in front of payload, hand-over-card;
- background brightness and blur;
- asset presence: no asset, one object/icon, component-only;
- text line breaks.

Acceptance:

- Candidate generation is deterministic and reproducible.
- No expensive vision model is needed at this stage.
- Each candidate has explicit layer boxes and z-order.

#### B3. Cheap Eval Gates

Input:

- arrangement candidates and layer boxes.

Output:

- accepted candidates;
- rejected candidates with reasons.

Cheap gates:

- face size and face placement;
- keyword text box visibility;
- forbidden overlap;
- edge margin;
- payload count;
- text length;
- text contrast proxy;
- component area balance;
- background busyness proxy;
- no more than one primary payload;
- no object asset that is smaller than its readability threshold.

Acceptance:

- Cheap gates remove at least half of obviously bad candidates.
- Cheap gates never reject allowed hand-over-card depth unless it covers
  protected text.
- Rejection reasons are machine-readable repair hints.

### Phase C. Expensive Critic and Repair

#### C1. Vision Critic Batch

Input:

- top 4 to 6 candidates after cheap eval;
- target reference thumbnail images;
- template contract;
- thumbnail title.

Output:

- ranked candidates;
- critique;
- repair patches.

Vision rubric:

- first-glance focal clarity;
- reference-template fit;
- face and expression salience;
- component clarity;
- asset relevance;
- thumbnail-size readability at 320x180 and 160x90;
- "does this look like a real YouTube thumbnail, not a debug/PPT mock?"
- trust and health-claim risk.

Acceptance:

- The critic must choose a winner and produce specific patches.
- Patches must be bounded: move, scale, remove, darken, simplify, swap asset.
- The critic cannot invent new structure outside the contract.

#### C2. Repair Loop

Input:

- critic patches;
- original candidate manifest.

Output:

- repaired candidates.

Loop limit:

- max 2 repair rounds per hypothesis;
- max 6 expensive candidate images per round.

Acceptance:

- Repair is deterministic where possible.
- If repair fails twice, the system returns the best imperfect candidate plus
  the failure reason.

### Phase D. User Surface

#### D1. Top Three Presentation

Input:

- final ranked candidates across the three hypotheses.

Output:

- three YouTube Test and Compare ready thumbnails.

Acceptance:

- User sees only three serious candidates, not the whole search space.
- Each candidate shows template, title, hypothesis, critic score, and major
  trade-off.

#### D2. Text Feedback Repair

Input:

- user free-text feedback.

Output:

- patch intent mapped to a specific stage.

Examples:

- "move it left" maps to arrangement candidate nudge.
- "less PPT" maps to component style policy and vision critic prompt.
- "hand can cover the card but not the words" maps to overlap policy.
- "use no big text" maps to headline mode.

Acceptance:

- Feedback does not trigger a full brainstorm by default.
- The system returns to the smallest failed stage.

---

## Data Contracts

### TemplateContract

```json
{
  "template_id": "shosho_benefit_list_card",
  "creator_target": "ali_abdaal_shosho",
  "intent_tags": ["health", "evidence", "benefit_list"],
  "host_slots": [
    {
      "slot_id": "host_left_third",
      "face_box_pct": [0.16, 0.08, 0.43, 0.56],
      "body_box_pct": [-0.06, -0.04, 0.58, 1.18],
      "priority": 1
    }
  ],
  "payload_slots": [
    {
      "slot_id": "card_right_third",
      "box_pct": [0.58, 0.18, 0.95, 0.68],
      "component_types": ["benefit_list_card"],
      "priority": 1
    }
  ],
  "headline_slots": [
    {
      "slot_id": "bottom_headline",
      "box_pct": [0.03, 0.65, 0.97, 0.96],
      "optional": true
    }
  ],
  "overlap_policy": [
    {
      "foreground": "host_hand",
      "background": "payload_card",
      "status": "allowed",
      "max_protected_text_iou": 0.02
    },
    {
      "foreground": "face",
      "background": "payload_card",
      "status": "forbidden"
    }
  ]
}
```

### ArrangementCandidate

```json
{
  "candidate_id": "idea01-hyp01-cand07",
  "template_id": "shosho_benefit_list_card",
  "layers": [
    {
      "layer_id": "background",
      "kind": "background_plate",
      "box": [0, 0, 1280, 720],
      "z": 0
    },
    {
      "layer_id": "payload_card",
      "kind": "benefit_list_card",
      "box": [740, 130, 1220, 500],
      "protected_text_boxes": [
        [862, 220, 1010, 285],
        [862, 290, 1010, 355],
        [862, 360, 1010, 425]
      ],
      "z": 20
    },
    {
      "layer_id": "host",
      "kind": "cutout",
      "box": [70, 40, 990, 830],
      "face_box": [220, 70, 510, 390],
      "hand_boxes": [[780, 320, 990, 515]],
      "z": 30
    },
    {
      "layer_id": "headline",
      "kind": "headline_text",
      "box": [30, 500, 1040, 690],
      "protected_text_boxes": [[320, 530, 930, 665]],
      "z": 40
    }
  ],
  "cheap_eval": {
    "status": "pass",
    "score": 82,
    "reasons": []
  }
}
```

### VisionCriticResult

```json
{
  "candidate_id": "idea01-hyp01-cand07",
  "score": 78,
  "rank": 2,
  "strengths": ["clear face", "component matches benefit-list template"],
  "problems": ["card still reads slightly like a slide", "headline competes with card"],
  "patches": [
    {
      "op": "style_component",
      "target": "payload_card",
      "value": "less-ui-more-paper"
    },
    {
      "op": "scale",
      "target": "headline",
      "value": 0.9
    }
  ]
}
```

---

## Compute Budget

Default per final top-three generation:

- Template matching: no expensive model call.
- Variant generation: no expensive model call.
- Cheap eval: no expensive model call.
- Vision critic: 4 to 6 images per repair round.
- Repair loop: max 2 rounds.

Worst-case expensive images:

- 3 hypotheses x 6 images x 2 rounds = 36 vision-image evaluations.

Expected expensive images:

- 3 hypotheses x 4 images x 1 round = 12 vision-image evaluations.

The expensive model should never see all raw variants.

---

## Implementation Slices

### Slice 1. Template Contract Model

Goal:

- Convert the current reference-template catalog into executable contracts.

Files:

- `shared/thumbnail_template_contracts.py`
- `tests/test_thumbnail_template_contracts.py`

Input:

- template IDs such as `shosho_benefit_list_card`.

Output:

- machine-readable slots, overlap policy, and cheap gate thresholds.

Tests:

- every `REFERENCE_TEMPLATE_IDS` has one contract;
- benefit-list contract allows hand over card but forbids hand over protected
  text;
- each contract has at least one host slot and one primary payload slot.

Can be done by low-cost model after this ADR:

- yes, if it only fills data and tests.

### Slice 2. Arrangement Candidate Schema

Goal:

- Define layer boxes, protected text boxes, z-order, and candidate IDs.

Files:

- `shared/thumbnail_arrangement.py`
- `tests/test_thumbnail_arrangement.py`

Tests:

- candidate serialization is stable;
- protected text boxes survive round-trip;
- z-order sorting is deterministic.

Can be done by low-cost model:

- yes.

### Slice 3. Deterministic Variant Generator

Goal:

- Generate 12 to 20 candidate manifests from one template contract and staged
  artifacts.

Files:

- `shared/thumbnail_arrangement.py`
- router integration behind a new `stage=arrangements`.

Tests:

- generates at least 12 variants for benefit-list template;
- variants differ in payload slot/scale/nudge/headline mode;
- no variant violates absolute canvas bounds.

Can be done by low-cost model:

- yes, if contract and schema exist.

### Slice 4. Cheap Eval Gates

Goal:

- Reject obviously bad candidates before vision critic.

Files:

- `shared/thumbnail_arrangement_eval.py`
- `tests/test_thumbnail_arrangement_eval.py`

Tests:

- face too small fails;
- hand over card passes;
- hand over keyword fails;
- too many payloads fails;
- low contrast text fails.

Can be done by low-cost model:

- yes.

### Slice 5. Arrangement Contact Sheet UI

Goal:

- Show accepted/rejected layout variants in the Title & Thumbnail tab for
  debugging.

Files:

- router partials;
- CSS;
- tests in `tests/test_bridge_project_thumbnails.py`.

Tests:

- `stage=arrangements` writes contact sheet and manifest;
- rejected variants are shown with reasons;
- full render is not run at this stage.

Can be done by low-cost model:

- yes, with existing UI patterns.

### Slice 6. Vision Critic Contract

Goal:

- Define prompt and strict JSON result schema for image-based critic.

Files:

- `prompts/thumbnail/arrangement_critic_v1.md`
- `shared/thumbnail_arrangement_critic.py`
- tests with mocked LLM output.

Tests:

- valid critic JSON parses;
- invalid or vague patches are rejected;
- critic can only output bounded repair ops.

Can be done by low-cost model:

- partially. Prompt/rubric benefits from stronger model, but parser/tests are
  cheap.

### Slice 7. Repair Patch Application

Goal:

- Apply bounded patches to candidate manifests and rerender.

Files:

- `shared/thumbnail_arrangement_repair.py`
- `tests/test_thumbnail_arrangement_repair.py`

Tests:

- move/scale/darken/remove patches apply deterministically;
- patch cannot move layers out of canvas;
- forbidden patch ops are rejected.

Can be done by low-cost model:

- yes.

### Slice 8. Asset Candidate Scoring

Goal:

- Score candidate assets before arrangement uses them.

Files:

- `shared/thumbnail_asset_scoring.py`
- `tests/test_thumbnail_asset_scoring.py`

Tests:

- tiny/detail-heavy icon fails readability;
- wrong semantic asset scores low;
- mismatched visual style scores low;
- provenance is required before final use.

Can be done by low-cost model:

- heuristics yes; vision semantic scoring needs stronger model.

### Slice 9. Top Three Selection

Goal:

- Choose final three YouTube Test and Compare candidates across hypotheses.

Files:

- `shared/thumbnail_candidate_selection.py`
- router integration.

Tests:

- selects at most one near-duplicate;
- keeps hypothesis diversity;
- sorts by critic score and mechanical pass.

Can be done by low-cost model:

- yes.

### Slice 10. Feedback Patch Field

Goal:

- User can type feedback and rerun only the smallest affected stage.

Files:

- UI partial and router endpoint;
- `shared/thumbnail_feedback_patch.py`.

Tests:

- "move card left" maps to arrangement patch;
- "no big words" maps to headline mode patch;
- "less PPT" maps to style patch;
- feedback does not trigger a new brainstorm by default.

Can be done by low-cost model:

- yes, after patch schema exists.

---

## Rollout Order

1. Template Contract Model.
2. Arrangement Candidate Schema.
3. Deterministic Variant Generator.
4. Cheap Eval Gates.
5. Arrangement Contact Sheet UI.
6. Vision Critic Contract.
7. Repair Patch Application.
8. Asset Candidate Scoring.
9. Top Three Selection.
10. Feedback Patch Field.

Do not start with Envato automation. Asset sourcing should wait until the
arrangement/eval loop can reject bad placements.

---

## Consequences

Positive:

- The system gets a real search space instead of one brittle render.
- Expensive models are used only on shortlisted candidates.
- Layout bugs become local: contract, variant generator, cheap eval, critic, or
  repair.
- User feedback can target one stage instead of restarting the whole workflow.

Trade-offs:

- More artifacts per run.
- More code before the first visible improvement.
- The first implementation will still need manual taste review until enough
  eval examples accumulate.

---

## Current First Task

Implement Slice 1 now. It is the foundation for every later low-cost slice.
