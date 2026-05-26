---
name: feedback-expert-persona-over-style-mimic
description: When designing LLM review for the owner's writing, use expert personas (Master Storyteller + Writing Coach) not style mimicry from past articles — owner explicitly rejected the latter
metadata:
  type: feedback
---

When the owner needs LLM critique on his writing (article / video script / podcast intro / book review / personal essay), default to **expert persona advisor** — NOT **style-profile mimicry** built from his past output.

**Why:** During the 2026-05-24 Tier C grill the original plan (Plan III) included parallel subagent extraction of a "shosho style profile" from past articles, used as ground-truth for review feedback. Owner pushed back hard:

> 「我通常沒有一個 profile 可以參考，hook 都是我自己憑感覺寫出來的。我需要他扮演以下兩種角色：1. 社群媒體專家 2. 寫作教練，而不是根據我過去文章的形式。」

Style-mimic has four structural problems for this owner specifically:

1. **Locks in mediocre patterns** — if past hooks are mid-tier, mimic forces continued mediocrity. Owner sees writing as a growth dimension, not steady-state.
2. **Suppresses evolution** — mimic pulls toward past mean. Owner explicitly wants to grow beyond past style.
3. **Suspect ground truth** — owner doesn't curate a "best hooks" corpus; mimic would average across ALL past output indiscriminately, treating mediocre samples as exemplars.
4. **False discipline** — mimic enforces consistency, which is NOT the same as quality.

**How to apply:** When designing LLM-review for the owner's output (current Tier C personas; future review systems for SEO copy, social posts, episode arcs, etc.):

- Default to **2-4 expert personas with universal craft principles**, domain-agnostic.
- Each persona = one lens (hook strength, sentence variety, narrative arc, structure clarity, etc.). Don't try to combine into one mega-persona — splitting forces depth.
- Per-persona scoring 1-5 + actionable suggestions, NOT generic "make it more engaging."
- Re-runnable per persona (separate API calls = re-run only what changed) UNLESS cost dominates iteration cycle.
- **Never** train / fine-tune / few-shot the personas on owner's past output as ground truth. Few-shot examples of "good reviews" are fine; "good hooks from past episodes" are not.
- Domain-agnostic prompts: cover article / video / podcast / book review / personal essay. Owner writes across these — health & wellness is only one slice.

**Related**:
- [[project-tier-c-decisions]] — the freeze where this pivot happened
- ADR-031 D8 — the persona prompt specification (Master Storyteller + Writing Coach)
- [[feedback-aesthetic-first-class]] — same "quality not conformance" philosophy
- [[feedback-identity-anchor-over-enumeration]] — adjacent: positive identity anchor beats prohibitive enumeration; same model-cognition heuristic
