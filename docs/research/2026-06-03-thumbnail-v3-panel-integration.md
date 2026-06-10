# Thumbnail V3 Panel Integration

**Date:** 2026-06-03
**Draft under review:** `docs/decisions/ADR-046-external-image-model-thumbnail-workflow.md`
**Panel inputs:**

- `docs/research/2026-06-03-thumbnail-v3-panel-setup.md`
- `docs/research/2026-06-03-codex-thumbnail-v3-audit.md`

Gemini review was not run because `GEMINI_API_KEY` is not configured in the
current shell. This integration therefore uses the available two-way panel:
drafter plus Codex audit.

## Integration Matrix

| Audit finding | Decision | ADR change |
| --- | --- | --- |
| ADR must distinguish accepted ADR-037 from proposed ADR-045. | Adopt | Add explicit supersedes/amends block. ADR-037 remains accepted support infrastructure; ADR-045 final-art rollout is deferred. |
| ADR-033 and ADR-036 conflict on title/thumbnail pairing. | Adopt | State that ADR-036 supersedes ADR-033 D2 for YouTube V2/V3 thumbnail work. |
| V3 should not discard prior renderer work. | Adopt | Reframe renderer as prompt constraint, wireframe, typography overlay, and post-import critique support. |
| Concepts still need a concrete reference template/style contract. | Adopt | Add `reference_template_id` / `style_contract_id` to brief and visual strategy. |
| Data contracts lack stable ids, lifecycle, provenance, hashes, and parent links. | Adopt | Expand contract examples for brief, reference package, prompt, attempt, and style memory. |
| `VisualStrategyV3` is referenced but not specified. | Adopt | Add `VisualStrategyV3` as a first-class data contract and pure compiler output. |
| Chinese text cannot remain an open prompt-only risk. | Adopt | V1 defaults to deterministic Traditional Chinese overlay; model-rendered Chinese becomes opt-in experiment. |
| Provider profiles should be capability based. | Adopt | Add capability fields such as negative prompt, multi-reference support, edit preservation, text reliability, max refs. |
| MVP is too broad. | Adopt | Reorder slices: prompt package for existing ideas, compiler golden tests, reference readiness, manual import, accept/promote, edit prompt, then style memory. |
| Global style memory is too early. | Adopt | Defer global memory until real accepted/rejected attempts exist; keep project-level iteration history first. |
| Manual workflow may be too little better than a note. | Adopt | Add UI acceptance criteria for ordered upload checklist, one-click prompt copying, drag/drop import, feed-size preview, lineage, and status. |
| Imported images need second AI critique before commit. | Modify | AI critique is recommended but user approval remains sufficient for commit; deterministic checks and provenance are required. |

## V2 Direction

The pivot remains approved: Nakama should use external image models for final
visual synthesis instead of trying to hand-build a taste-complete renderer.

The implementation should be smaller and more anchored than the first draft:

1. Keep the existing three thumbnail idea cards and add a V3 prompt package
   panel to them.
2. Compile each current idea into a structured `VisualStrategyV3` and provider
   prompt.
3. Package references with stable ids, hashes, roles, and ordered provider
   bindings.
4. Let the user generate externally and import the result.
5. Promote an imported image through the existing thumbnail commit semantics.
6. Use deterministic typography for exact Traditional Chinese text by default.
7. Add critique/edit prompts after imported attempts exist.
8. Add global style memory only after repeated accepted preferences appear.

## Deferred Items

- Direct Prompt Edit / Gemini / Scenario API integration.
- Envato or stock download automation.
- Global style memory auto-learning.
- Full deterministic final-art arrangement rollout from ADR-045.
- Model-rendered Traditional Chinese as default.

## Implementation Gate

Before code implementation, the ADR should be updated to include:

- supersedes/amends language;
- concrete V3 contracts with lifecycle/provenance;
- deterministic Chinese text policy;
- capability-based provider profiles;
- reduced MVP slices;
- clear relationship to existing `thumbnail_ideas`, `thumbnail_run`,
  `data/thumbnails/{slug}/runs`, asset manifest, and vault thumbnail commit.
