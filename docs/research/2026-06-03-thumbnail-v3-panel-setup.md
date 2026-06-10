# Thumbnail V3 External Image Model Workflow — Panel Setup

**Date:** 2026-06-03
**Artifact under review:** [ADR-046: External Image Model Thumbnail Workflow](../decisions/ADR-046-external-image-model-thumbnail-workflow.md)
**Panel skill:** `C:/Users/Shosho/.claude/skills/multi-agent-panel/SKILL.md`

---

## Step 0 Model Availability

- Codex CLI: available
- Gemini API key: not set in current shell

Current viable panel:

- 2-way: drafting session + Codex audit

Full 3-way panel becomes available after `GEMINI_API_KEY` is configured.

---

## Codex Audit Focus

Use the multi-agent-panel Step 2 push-back posture. The audit should not
rubber-stamp the ADR. It should challenge:

1. Whether V3 correctly demotes the deterministic renderer without wasting the
   ADR-037/038 work.
2. Whether the first implementation slice is small enough.
3. Whether the data contracts are overbuilt or insufficient.
4. Whether the prompt compiler is specified tightly enough to prevent generic
   AI thumbnails.
5. Whether the feedback/style-memory design risks overfitting to one bad image.
6. Whether the manual external-tool workflow is practical inside Nakama.
7. Whether Chinese text rendering should be handled by the image model, an edit
   loop, or deterministic overlay.
8. Whether provider-specific syntax leaks into the core model.

Suggested verdict format:

- approve as-is;
- approve with modifications;
- reject and propose alternative architecture.

---

## Gemini Audit Focus If Enabled

Gemini should read both the ADR and the Codex audit, then focus on:

1. Multimodal/prompt-image reference workflow risks.
2. Image-model behavior with person identity and object references.
3. Chinese text legibility and edit-loop reliability.
4. Style-reference copying vs inspiration.
5. Whether the Triple Threat formula misses health/education-specific trust
   dimensions.
6. Whether user feedback can be converted into stable prompt memory without
   accumulating noise.

---

## Integration Plan

After audits return, create:

- `docs/research/2026-06-03-codex-thumbnail-v3-audit.md`
- optional `docs/research/2026-06-03-gemini-thumbnail-v3-audit.md`
- `docs/research/2026-06-03-thumbnail-v3-panel-integration.md`

Then revise ADR-046 to v2 with:

- adopted audit changes;
- rejected audit changes with rationale;
- unresolved questions for Shosho sign-off.
