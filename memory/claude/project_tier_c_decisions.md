---
name: project-tier-c-decisions
description: Tier C (Project workspace migration to Bridge) full Q1-Q9 decision freeze from 2026-05-24 grill + panel-integrated v2 corrections + PR1/PR2/PR3 slice plan
metadata:
  type: project
---

**Date:** 2026-05-24 grill freeze + same-day panel v2 integration
**Authority:** [ADR-031](../../docs/decisions/ADR-031-project-workspace-migration.md) (Accepted)
**Schema:** [docs/schemas/project-frontmatter-nested.md](../../docs/schemas/project-frontmatter-nested.md) (closes VAULT-LAYOUT line 162 404)
**Status (PR1):** Committed local on `feat/tier-c-shell-pr1` at `E:\nakama-tier-c-pr1`; NOT pushed (auto-mode classifier denied push pre-HITL). User to verify locally morning 2026-05-25 + decide push.
**Smoke seed:** `肌酸的妙用` (18.9KB → 533B) + `蛋白質攝取量` (5.4KB → 449B) migrated 2026-05-24 in active vault. `Brook 風格訓練` correctly skipped (type=agent-workspace).

## Q1-Q9 freeze (v1) + v2 panel deltas

| Q | v1 decision | v2 panel delta |
|---|---|---|
| Q1 Web-first | A (vault md SoT; Web = interaction surface) | — unchanged |
| Q2 Strip dataviewjs | a (all stripped from youtube/podcast templates) | — unchanged |
| Q3 Persona model | Plan II expert personas (NOT style mimic) | — unchanged (memory: [[feedback-expert-persona-over-style-mimic]]) |
| Q4 chassis-nav placement | A: top-level PROJECTS slot (5→6) | — unchanged |
| Q5 Frontmatter γ | nested with reviews/pomodoro/hook_text/etc. | reviews shape: dict (v1) → list-of-versioned-objects (v2, PR2 ship). Indexer PR1 is dual-shape tolerant. |
| Q6 Tab navigation | B: single URL #tab | — unchanged |
| Q7 Tab count | 7 (Brief/Research/Title&Thumbnail/Hook/Script/Review/Publish) | — unchanged |
| Q8 Persona prompts | Master Storyteller + Writing Coach, domain-agnostic, hook ≤500字 | Hook cap **≤300字** (Taiwanese spoken 200-300字/min × 30-60s ≈ 100-250字). Personas add **Traditional Chinese leakage guard**. PR2 ships scoring rubric + few-shot example. |
| Q9.c content_type | Slim to youtube/podcast | **REVERTED** — `蛋白質攝取量.md` is `research` in vault; tests + Nami callers depend on all 4. Keep `Literal["youtube","blog","research","podcast"]`. |
| Q9.g (new in v2) | — | Migration handles BOTH `%%KW-START%%` + `%%agent-zoro-keywords-*%%` marker families. |

## Architecture freeze

**Vault layout (no change to ADR-028):** `Projects/{title}.md` + `TaskNotes/Tasks/{title} - {task}.md`. Tier C does not move files.

**Bridge routes:**
- `GET /bridge/projects` — index list
- `GET /bridge/projects/new` — create form (Slack-Nami fallback)
- `POST /bridge/projects` — create handler (delegates to existing bootstrap)
- `GET /bridge/projects/{slug}` — detail with 7-tab stage gate
- `POST /bridge/projects/{slug}/frontmatter` — single-field α/γ update
- `POST /bridge/projects/{slug}/section/{section_slug}` — H2 body section update
- `POST /bridge/projects/{slug}/timer/{action}` — Pomodoro state (action ∈ start/complete/cancel)
- `POST /bridge/projects/{slug}/tasks/{task_name}/manual-pomodoro` — +1🍅 manual
- `POST /bridge/projects/{slug}/review/{persona}` — PR2 stub (501 in PR1)

**Read pattern (ADR-030 D2):** `shared/project_indexer.py` — FS-direct, modeled on `shared/digest_indexer.py`. No DB mirror.
**Write pattern:** `shared/project_writer.py` — atomic tmp + rename. `update_frontmatter` (deep merge), `update_body_section` (H2 replace), `append_timeentry` (TaskNotes), `write_review`.

**Pomodoro model:**
- Web-self timer (25 min default), countdown in browser sessionStorage.
- On completion: POST `/timer/complete?task_name=X` writes `timeEntries: [{startTime, endTime}]` to TaskNotes Task md.
- TaskNotes plugin's `formula.實際🍅` reads same field → Obsidian-side display continues to work.
- Manual +1🍅 button (for physical-timer phases like Filming / Post-production) synthesizes a 25-min entry.
- Project frontmatter `pomodoro.{est,actual}_total` is a denormalized cache, recomputed on completion / +1🍅 / explicit save.

## PR slicing

**PR1 (this PR, ready local):**
- ADR-031 + schema doc + panel audits + integration matrix
- chassis-nav 5→6 (PROJECTS slot)
- bridge_projects.py router (8 endpoints)
- 12 templates (index/new/detail + 7 tab partials + dock + chassis nav edit)
- bridge-projects.css + bridge-projects.js
- shared/project_indexer.py + shared/project_writer.py
- project_youtube.md.tpl + project_podcast.md.tpl stripped to minimal scaffold
- scripts/migrate_projects_to_tier_c.py (idempotent; bundle backup at `.tmp/`)
- 85 tests (5 suites, all green)
- Migrated 2 of 3 live projects

**PR2 (next):**
- shared/project_reviews.py — Master Storyteller + Writing Coach prompt module
- LLM dispatch with rubric (1-5 anchor definitions) + few-shot review example + Traditional Chinese guard
- Reviews schema flip: dict → list-of-versioned-objects (`reviews.{persona}: list[{run_at, prompt_version, score, summary, suggestions}]`)
- `state.db api_calls.scope_json` per-review audit log
- Replace `/bridge/projects/{slug}/review/{persona}` 501 stub with real dispatch
- Publish-anyway persisted decision (replace dismissible toast per Codex push)

**PR3+ (backlog):**
- Pomodoro persistence across page reload (sessionStorage start time + server mtime guard)
- 👍/👎 feedback buttons on reviews (per Gemini push) — prompt-quality signal
- TaskNotes frontmatter schema formalization (per Gemini push)
- KB Research endpoint integration (currently stub)
- Sanji social repurpose tab (when Sanji built)
- Image generation for `thumbnail_concept`
- Multi-project Kanban view
- Title&Thumbnail A/B selector → `title` frontmatter on publish

## Worktree state at handoff

- `E:\nakama-tier-c-grill` (branch `docs/tier-c-adr-031`): ADR v1+v2 + schema + audits + matrix committed (commits b66cdae + 09fef26). NOT pushed.
- `E:\nakama-tier-c-pr1` (branch `feat/tier-c-shell-pr1`): cherry-picked docs commits + code commit (HEAD `feat/tier-c-shell-pr1`). NOT pushed. 85/85 tests green; ruff clean.
- `E:\nakama-memory-tier-c` (branch `memory/tier-c-2026-05-24`): memory writes (this file + [[feedback-expert-persona-over-style-mimic]]); not yet committed at write time.

User decision needed in the morning:
1. Boot dev server in PR1 worktree; smoke /bridge/projects + /bridge/projects/肌酸的妙用; verify Pomodoro dock + tabs + soft gate icons + +1🍅 manual button writes timeEntry.
2. If green: push `feat/tier-c-shell-pr1` + open PR + push memory branch.
3. If issues: rollback migrated vault files from `.tmp/project-migration-backup-2026-05-24-223225/`.

## Critical post-handoff gotchas (already mitigated, but bear in mind)

- Migration script needs `--vault "E:/Shosho LifeOS"` on Windows (config.yaml default points to VPS path).
- Windows stdout cp1252 fails on CJK; migration script reconfigures stdout UTF-8 at module load (per [[feedback-windows-stdout-utf8]]).
- Section URL slugs ASCII-only: `/section/script` → "Script / Outline" (slashes in heading text can't ride in FastAPI path params; bridge_projects.py has EDITABLE_SECTIONS lookup).
- `肌酸的妙用` post-migration: `pomodoro.est_total: 7` (from 3 TaskNotes tasks, est=4+3+0). Adjust if owner finds count wrong.

## Related memories

- [[feedback-expert-persona-over-style-mimic]] — the Q3 pivot rationale
- [[feedback-pipeline-anchored-planning]] — Tier C anchors Stage 3+4+5
- [[feedback-aesthetic-first-class]] — UI design discipline (Tier C inherits sho-* design system)
- [[feedback-redline-self-discipline-not-enforcement]] — soft gate philosophy
- [[feedback-panel-triangulated-judgment]] — auto-integrate panel, don't defer
- [[feedback-drive-to-completion-no-checkpoint]] — implementation skill drives to ship
- [[feedback-sandcastle-default]] — Codex/Gemini dispatch via bg bash counts as sandcastle-style isolation
- [[user-vault-edit-pattern-no-concurrent]] — single-user assumption; relaxes Codex's mtime guard ask to PR2 defer
- [[project-content-pipeline-arch]] — 7-stage pipeline (Stage 3+4+5 cross-cut)
- [[reference-bridge-ui-mutation-pattern]] — Bridge form-post / cookie auth / 303 / `<dialog>` pattern PR1 follows
