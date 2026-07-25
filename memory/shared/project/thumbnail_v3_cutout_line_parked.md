---
type: project
visibility: shared
agent: shared
confidence: high
created: 2026-07-13
expires: permanent
tags: [thumbnail, cutout, foundry, brook, parked-work, adr-045, adr-046]
name_zh: Thumbnail v3 cutout/casting 線 parked 於 branch
name_en: Thumbnail v3 cutout/casting line parked on a branch
description_zh: 縮圖 v3 cutout/casting 探索（唯一存檔）保存在 branch chore/thumbnail-pipeline-wrapup；PR #873 已關但 branch 未刪，續作從此起。
description_en: Thumbnail v3 cutout/casting exploration (only surviving copy) is preserved on branch chore/thumbnail-pipeline-wrapup; PR #873 closed but branch kept — resume from there.
---

# Thumbnail v3 cutout/casting line — parked

**Status:** Parked exploration, not integrated / not endorsed. PR #873 closed 2026-07-13 (Option A: close PR, keep branch).

**Where the work lives:** branch `chore/thumbnail-pipeline-wrapup` (HEAD `5346ae7`). This is the ONLY copy of the cutout/casting pipeline — `shared/cutout_casting.py`, `agents/foundry/render_workers/ai_image_gen.py`, `agents/foundry/thumbnail_templates.py`, `prompts/thumbnail/reference_corpus/` (Ali/Jeff corpus). `main` does not have these.

**On resume, watch for:**
1. **ADR number collision** — the branch renumbered thumbnail ADRs to ADR-045 / ADR-046, but `main` now uses ADR-045 = robin-kb-role, ADR-046 = three-source-kb-ingest. Re-number on resume.
2. **Partial supersession** — `shared/thumbnail_{funnel,idea,playbook}.py` and brainstorm prompts already shipped to `main` separately; diff before re-adding.
3. **Path remap** — `agents/foundry/` was folded into `agents/brook/script_video` per ADR-050; remap paths.

**Owner note:** 修修 recalls this line was Codex's work. Git authorship is all under `shosho-chang`, so the record doesn't distinguish Claude vs Codex. Breadcrumb left in `shared/` so either agent can find the parked work without digging through closed PRs.
