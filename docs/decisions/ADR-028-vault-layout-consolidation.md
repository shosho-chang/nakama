# ADR-028: Vault Layout Consolidation

**Date:** 2026-05-19
**Status:** Proposed
**Deciders:** shosho-chang, Claude Opus 4.7
**Related:** ADR-001 (agent roles), ADR-011 (Concept dispatcher), ADR-017 (Annotation store), ADR-020 (textbook ingest v3), ADR-021 (Brook synthesize), ADR-024 (Source promotion + RCP), ADR-027 (Brook scope), CLAUDE.md (repo), CLAUDE.md (vault), `docs/VAULT-LAYOUT.md` (canonical reference produced by this ADR)

## Context

The Obsidian LifeOS vault at `E:\Shosho LifeOS\` (Windows) / `/home/Shosho LifeOS` (VPS) is the implementation surface of 修修's personal life-operating system (Designing Your Life + OKR + Bullet Journal integrated). It is also the contract surface that 7 agents (Robin, Nami, Brook, Zoro, Sanji, Franky, Usopp) read and write into.

A 2026-05-19 grilling session ([transcript context: this ADR's grill led by Claude Opus 4.7 in `/grill-with-docs` skill]) audited the vault and found systemic drift:

1. **Vault-side `CLAUDE.md` §Directory Model has not been updated since 2026-04** — claims a Directory Model that the codebase no longer matches. New folders (`KB/Annotations/`, `KB/Wiki/Digests/{AI,PubMed}/`, `KB/Wiki/Sources/Books/`, `KB/Wiki/Entities/Books/`) are touched by code but not documented; declared folders (`Schemas/`) have never been written by any producer.

2. **Agent output area is fragmented across three top-level folders** — `Nami/Notes/` (Nami handler actually writes here), `AgentBriefs/` (`config.yaml` declares this for Nami but no code writes), `AgentReports/franky/` (Franky reporter writes here, but README claims `AgentBriefs/`). Three READMEs disagree with code.

3. **News Coo capture → KB ingest pipeline has a broken contract** — News Coo image fetcher writes to `attachments/{slug}/` adjacent to its FSA root pick (`Inbox/kb/attachments/{slug}/`). Robin ingest (`agents/robin/ingest.py`) and promotion commit (`shared/promotion_commit.py`) have **zero references** to `attach*` / `image` / `shutil` / `copyfile`. After a markdown is promoted from `Inbox/` to `KB/Raw/Articles/`, its image refs still point at `Inbox/attachments/...` — if the user follows the "Inbox is ephemeral" mental model and cleans Inbox, images break. The bug has not surfaced only because 修修 has never cleaned Inbox.

4. **`Files/` at vault root is a 69-file image dumping ground** — 38 paper figures (which belong in `KB/Attachments/{source-slug}/` per the design implied by `KB/Attachments/inbox/` and `KB/Attachments/pubmed/` existing) and 31 Obsidian Pasted images referenced from `Journals/Daily/{date}.md`. No code asserts `Files/` as a contract location.

5. **Three Nakama-internal dev artifacts live in vault** — `Case Studies/2026-04-22 Nakama WP + Community...md`, `Incidents/2026/04/drill-2026-04-26-state-restore.md`, and `KB/Wiki/Outputs/style-extractor-prd-draft.md`. These are documentation about the agent system, not 修修's life/work content. They blur the boundary between "vault = my life" and "repo = my codebase".

6. **`KB/Wiki/Outputs/`, `Syntheses/`, `Comparisons/` are codebase-orphan folders** — declared in vault `CLAUDE.md`, zero code writes there. `Outputs/` has two manually-placed items; `Syntheses/` and `Comparisons/` are fully empty since vault inception.

7. **Project pages (`Projects/{title}.md`) are the only "collab" page type** — they contain both 修修-written sections (`## 專案描述`, `## Draft Outline`, `## 專案筆記`) and agent-written sections (Zoro `%%KW-START%%...%%KW-END%%` keyword block, Brook scaffold button, KB Research button). The marker convention is unwritten — Zoro uses HTML-comment markers, Brook scaffold and KB Research render to DOM only. Future agents will invent their own marker pattern and the page will entropy.

8. **Project bootstrap (`scripts/run_project_bootstrap.py` + `shared/lifeos_writer.py`) emits flat frontmatter** — no `line:`, `zoro_inputs`, `lineN_inputs` blocks per ADR-027 §7. ADR-027 PR-6 added the canonical schema doc (`docs/schemas/project-frontmatter-nested.md`) but bootstrap not yet updated.

The grill produced 11 decisions (Q1-Q11). This ADR freezes them.

## Decision

### 1. Vault身份 — three-tier ownership × four-stage lifecycle × four pillars

**Vault definition (修修's framing, 2026-05-19):**
> 「目前有關生活與工作資訊的發展、沉澱、收集以及擷取的地方」— the place where life and work info develops, settles, is collected, and is captured.

**Three-tier ownership** (every page type belongs to exactly one tier):
- 🔒 **Human only** — AI 不可寫 content. AI 可以給 input/suggestion，but 文字必須由人輸入。Covers `Journals/`, `OKRs/`, `Dashboards/`, and specific sections inside collab pages.
- 🤖 **Agent only** — 修修 不該手寫；agent 的工作區。Covers `KB/`, `AgentOutputs/`, `Inbox/` (capture cache).
- 🟡 **協作 (Collab)** — Section-level division within a single page. Today the only collab page type is `Projects/{title}.md`.

**Four-stage lifecycle** (CODE framework, Tiago Forte 2022):
- **C**apture — 即時、frictionless input. `Inbox/`, `Journals/Daily/`, `KB/Annotations/`.
- **O**rganize — 主動歸位. `Inbox/` → `KB/Raw/` promotion; `Projects/` bootstrap.
- **D**istill — 沉澱、結晶. `KB/Wiki/{Sources,Concepts,Entities}/`; annotation_merger weaves annotations into Concepts.
- **E**xpress — 用沉澱物產出. `Projects/` output content, articles, OKR progress.

**Four pillars** (Designing Your Life 人生儀錶板):
- Work / Play / Love / Health
- Carried in frontmatter `area:` field — NOT folder structure. Health tracking lives inside OKR + TaskNotes per a future separate grill session (not this ADR).

### 2. PARA + CODE adopted as conceptual lens (Strength 1)

**PARA** (Tiago Forte 2022) is adopted as the *conceptual* framework for talking about vault contents:
- **P**rojects — short-term commitments with deadline → `Projects/` (existing)
- **A**reas — ongoing responsibility → represented via OKR + frontmatter `area:`, NO `Areas/` folder
- **R**esources — topical knowledge → `KB/` (existing; NOT renamed to `Resources/`)
- **A**rchives — deactivated → represented via frontmatter `status: archived`, NO `Archives/` folder

**No top-level folder renames for PARA purity.** `KB/` is the highest-traffic agent-writable area (644+ concept pages, daily index/log writes, 7 agents read); renaming it imposes migration cost grossly out of proportion to PARA naming benefit. Folder structure reorganized only where actually broken (Sections 3-7 below).

### 3. Kill three orphan folders

- **`KB/Wiki/Outputs/`** — 0 code refs, 2 manually-placed items. Items relocated (see §8); folder deleted.
- **`KB/Wiki/Syntheses/`** — empty since vault inception. Deleted. Re-create when first real producer code path exists.
- **`KB/Wiki/Comparisons/`** — empty since vault inception. Deleted. Same as above.
- **`Schemas/`** — declared as "Layer C control plane" in legacy vault CLAUDE.md, never implemented. Deleted.

Principle: **空殼餵養「以後實作」拖延文化**。Folders earn their place by having a producer code path.

### 4. Consolidate agent output area → `AgentOutputs/{agent}/{kind}/`

**Before:**
- `Nami/Notes/` (Nami handler writes; README claims otherwise)
- `AgentBriefs/` (config.yaml claims this for Nami; nothing writes)
- `AgentReports/franky/` (Franky writes; README claims `AgentBriefs/`)
- `AgentReports/dev-backlog.md` (人手寫; Franky reads)

**After:**
```
AgentOutputs/
├── nami/
│   ├── briefs/         # Morning Brief (config.yaml retargeted)
│   ├── notes/          # ad-hoc Nami notes (from Nami/Notes/*)
│   └── research/       # Nami research handler outputs (from Nami/Notes/Research/*)
└── franky/
    ├── weekly/         # YYYY-WW.md weekly reports (from AgentReports/franky/*)
    ├── seo-audit/      # YYYY-MM-DD/ SEO audit task outputs (from KB/Wiki/Outputs/2026-04-27-seo-acceptance/)
    └── dev-backlog.md  # 人寫、Franky 讀 (from AgentReports/dev-backlog.md)
```

Note: `AgentOutputs/franky/seo-audit/` is **wrong agent assignment** — SEO audit is Brook's responsibility per ADR-027. Corrected to `AgentOutputs/brook/seo-audit/2026-04-27/`.

**Required code changes:**
- `gateway/handlers/nami.py` — replace `Nami/Notes/` literals with `AgentOutputs/nami/notes/` (~5 occurrences)
- `prompts/nami/agent_system.md` — update path examples
- `agents/franky/agent.py:33` — `AgentReports/dev-backlog.md` → `AgentOutputs/franky/dev-backlog.md`
- `agents/franky/agent.py:119` — `AgentReports/franky` → `AgentOutputs/franky/weekly`
- `agents/franky/reporter.py:277` — path template update
- `agents/franky/weekly_digest.py` — path refs (if any)
- `config.yaml` — `agents.nami.brief_path: AgentOutputs/nami/briefs`
- `agents/nami/README.md` + `agents/franky/README.md` — align with reality

### 5. Inbox structure — sort by source type at capture

**After:**
```
Inbox/
├── web/                       # News Coo writes here (re-pick FSA root)
│   ├── {slug}.md
│   └── attachments/{slug}/    # News Coo image fetcher
├── books/                     # 修修 drops .epub
├── papers/                    # 修修 drops .pdf (currently empty, reserved)
└── snapshots/                 # 修修 drops .mhtml
```

Maps cleanly to KB/Raw/ exit folders: `web → KB/Raw/Articles/`, `books → KB/Raw/Books/`, `papers → KB/Raw/Papers/`.

### 6. KB/Attachments/ flat by source slug

**Before:** `KB/Attachments/{producer}/{slug}/` (producer = `inbox` or `pubmed`)
**After:** `KB/Attachments/{source-slug}/` (no producer prefix; slug is globally unique)

### 7. Fix the broken Inbox → KB attachment migration contract

Promotion code (`shared/promotion_commit.py` or successor) MUST, when promoting `Inbox/web/{slug}.md` → `KB/Raw/Articles/{slug}.md`:

1. `mv Inbox/web/attachments/{slug}/* → KB/Attachments/{slug}/`
2. Rewrite markdown image refs: `attachments/{slug}/...` → `KB/Attachments/{slug}/...`

Without these two steps, the existing pipeline silently produces broken image refs after Inbox cleanup. This bug has not yet manifested because 修修 has never cleaned Inbox; it is a latent contract violation.

Implementation lives in a follow-up issue (out of this ADR's scope).

### 8. `Files/` migration (retroactive cleanup) — granted exception to Journals red line

`Files/` (vault root) contains 69 images split into two categories:

- **Category A — 38 paper figures** referenced from `KB/Raw/Papers/*.md` (9 sources) and `Inbox/kb/*.md` (3 sources). **Action:** migrate to `KB/Attachments/{source-slug}/`, rewrite image refs in source markdowns. Mapping table in [`docs/VAULT-LAYOUT.md` §migration-files-category-a].

- **Category B — 31 Obsidian Pasted images** referenced from `Journals/Daily/{date}.md` (7 daily journal files). **Action:** migrate to `Attachments/journal-pasted/{YYYY-MM}/`, rewrite image refs in Journal markdowns.

**Journals red-line exception (this ADR formally grants):**
> CLAUDE.md (vault) §3 declares `Journals/` write-prohibited. This rule's *intent* is to prevent AI-generated *content* from contaminating 修修's personal voice. **Mechanical path rewrites that do not alter prose are not in scope of this prohibition.** This ADR grants a one-time exception for the `Files/` Category B migration. The exception applies retroactively to any future similar mechanical rewrite (path-only, content-preserving); each invocation must be PR-reviewed.

**Obsidian config change (forward):** set default attachment folder to `Attachments/journal-pasted/{{date:YYYY-MM}}` so future paste-images auto-bucket.

Resulting state: `Files/` empty, deleted.

### 9. Dev artifacts live in repo, not vault

**Boundary heuristic** (write into `docs/VAULT-LAYOUT.md`):
> 「如果把 Nakama 整個砍掉重寫，這個檔案還有意義嗎？」
> 有 = vault；沒有 = repo.

**Relocations:**
- `Case Studies/2026-04-22 Nakama WP + Community 整合架構規劃.md` → `E:\nakama\docs\case-studies/`
- `Incidents/2026/04/drill-2026-04-26-state-restore.md` → `E:\nakama\docs\incidents/`
- `KB/Wiki/Outputs/style-extractor-prd-draft.md` → `E:\nakama\docs\prds/`
- `KB/Wiki/Outputs/2026-04-27-seo-acceptance/` → vault `AgentOutputs/brook/seo-audit/2026-04-27/` (this IS agent task output, stays in vault)

Vault `Case Studies/` and `Incidents/` folders deleted after migration.

### 10. Marker convention for collab pages

**Page type in scope:** `Projects/{title}.md`. (Today the only collab page type. Future collab page types must be explicitly added to `docs/VAULT-LAYOUT.md` §collab-pages.)

**Pattern A (default) — HTML-comment markers, agent writes into .md body:**
```markdown
%%agent-{agent_name}-{section_id}-start%%

(agent-written content; gets persisted in .md, syncs via Syncthing, visible on mobile, indexed by Obsidian search)

%%agent-{agent_name}-{section_id}-end%%
```

Examples:
- `%%agent-zoro-keywords-start%% ... %%agent-zoro-keywords-end%%`
- `%%agent-brook-titles-start%% ... %%agent-brook-titles-end%%`

**Pattern B (exception) — render to DOM only, .md untouched:**
Permitted when agent output is "large" (rule of thumb: >50 lines, or naturally lives elsewhere). Today's Pattern B sections:
- `## 📚 KB Research` (Robin) — dataviewjs renders KB hits to DOM; full result lives at `/kb/research`
- `## 🪄 Brook: Scaffold` (Brook, ADR-027 PR-6) — outline persists at `/projects/{slug}` review page

**Human-only sections in Project pages (agent code MUST NOT write):**
- `## 專案描述`
- `## 預期成果`
- `## Draft Outline` (修修's own outline; ≠ Brook scaffold)
- `## 專案筆記`

**Migration:** Zoro's existing `%%KW-START%% / %%KW-END%%` (in `shared/lifeos_templates/project_blog.md.tpl:195` + `:213`) renamed to canonical `%%agent-zoro-keywords-start%% / -end%%`. One-time migration also rewrites existing `Projects/{title}.md` files that contain the old markers.

**Audit:** `scripts/vault_layout_audit.py` greps for `%%agent-(\w+)-([\w-]+)-start%%` across vault, enumerates discovered (agent, section) pairs, asserts each appears in `docs/VAULT-LAYOUT.md` §collab-pages whitelist, and asserts each is NOT inside a human-only section.

### 11. Documentation location + maintenance discipline

**Canonical:** `E:\nakama\docs\VAULT-LAYOUT.md` (in repo, git-versioned, PR-reviewed).

**Vault-side pointer:** `E:\Shosho LifeOS\CLAUDE.md` §Directory Model rewritten as ~30-line cheat sheet pointing at repo canonical. Vault CLAUDE.md is updated as part of this ADR's Phase 3 implementation, NOT silently — changes go through ADR amendment.

**Maintenance discipline (γ — both human and automated):**

- **α — PR discipline (mandatory):** any PR that changes vault folder structure (creates new folder, renames, deletes), changes agent vault-write paths in code, or changes marker convention MUST update `docs/VAULT-LAYOUT.md` in the same PR. Enforced via PR review (add line to `E:\nakama\CLAUDE.md` workflow rules + `.github/PULL_REQUEST_TEMPLATE.md` if exists).
- **β — Monthly audit (automated):** `scripts/vault_layout_audit.py` runs monthly (cron via Franky), diffs:
  1. Actual vault folder tree vs `docs/VAULT-LAYOUT.md` §folder-map
  2. Code-asserted paths (`grep` `agents/`, `shared/`, `gateway/`, `thousand_sunny/` for `KB/`, `Inbox/`, `Projects/`, `TaskNotes/`, `Annotations/`, `AgentOutputs/`) vs §producer-consumer-matrix
  3. Marker convention violations (see §10)
- Output written to `AgentOutputs/franky/weekly/{period}-vault-audit.md` (appended to franky weekly digest section).

**Content scope (III — folder layout + concepts + drift):**
1. Conceptual model (PARA + CODE + 3-tier + 4 pillars)
2. Top-level folder map (path | tier | producer | consumer | schema | doc ref)
3. Repo vs vault boundary heuristic
4. Marker convention for collab pages
5. **Known drift section** — explicit "doc says X, code does Y" record with status (待修/已接受/已修):

   - **D1 — Concept dispatcher unreachable from textbook-ingest Phase B** ([待修])
     `shared/kb_writer.upsert_concept_page()` implements ADR-011 §3.3 4-action dispatcher (create / update_merge / update_conflict / noop) but textbook-ingest Phase B never calls it. Phase B writes Concept stubs directly. Causes silent dedup bypass.
     Owner: 待 textbook-ingest v4 or ADR-011 amendment.
   - **D2 — Entity v1 schema frozen** ([已接受])
     `KB/Wiki/Entities/` still v1 (per ADR-011 §3.1 "暫不 cover entity"). v2 schema not designed. Acceptable until cross-source entity merge becomes a real workflow.
   - **D3 — `KB/index.md` sync unmanaged** ([待修])
     CLAUDE.md (vault) §3 claims "必須同步更新" but no code enforces. `agents/robin/ingest.py:583,600` appends index entries after concept writes, but no transaction guarantees full coverage.
     Owner: 待 ingest pipeline refactor or vault audit script extends to check index coverage.

Future drift discovered by monthly audit goes into this section as new entries.

## Considered Options

### Rejected: full PARA folder migration (Strength 3)

Top-level rename `KB/` → `Resources/` plus new `Areas/` + `Archives/` folders. Rejected because:
- `KB/` is the highest-traffic agent-writable surface (644+ Concept pages, daily index/log writes, 7 agents). Rename cost is enormous: every ADR (-011, -017, -020, -021, -024), every agent's prompt, every templater config, every dataviewjs query references `KB/`. Touches ~50+ files.
- PARA purity is *naming aesthetic*, not function. Naming alone doesn't change vault behavior.
- 修修 explicitly flagged the framework as "有時候太複雜" — full PARA imposes the complexity 修修 rejected.
- `KB/` is already conceptually PARA's "Resources" — the lens applies without rename.

### Rejected: `AgentOutputs/` Option β (minimal — only fix docs, no folder move)

Keep `Nami/Notes/` + `AgentReports/franky/` + `AgentBriefs/`, just align READMEs + config to reality. Rejected because:
- 修修 explicitly chose α: "想要把資料夾變得有邏輯一點".
- β leaves three top-level agent folders permanently asymmetric; new agents would inherit the chaos.

### Rejected: collab marker Option B (no agent writes to .md, render to DOM only)

Stricter red line, but rejected because:
- 修修 uses Obsidian on mobile. DOM-rendered content invisible without dataviewjs runtime.
- Syncthing syncs .md content; DOM-only results don't propagate to other devices.
- The collab tier was explicitly accepted (Q5 → C1 軟約定) — forbidding all agent .md writes contradicts that.

### Rejected: keep `KB/Wiki/Syntheses/` + `Comparisons/` as reserved slots

Pre-allocated for "future producers". Rejected because the same pre-allocation killed `Schemas/` (declared in 2024, never written by 2026). Empty folders breed "later" debt. Re-create on first real producer.

### Rejected: vault-side canonical doc (vault `VAULT-LAYOUT.md` as source of truth)

Doc lives in vault, repo references vault. Rejected because:
- Vault not git-versioned; doc changes lose history.
- Multi-agent contract requires PR review — review only works on git.
- Vault CLAUDE.md's own drift (un-updated since 2026-04) is the cautionary tale.

### Rejected: Journals red line absolutism (forbid Files/ Category B migration)

Treat CLAUDE.md "Journals/ 完全禁止寫入" literally and require 修修 to manually move 31 Pasted images through Obsidian UI. Rejected because:
- The red line's *purpose* is preventing AI prose contamination, not preventing path rewrites.
- 30 minutes manual work × low-skill repetitive task = poor use of 修修's time.
- One-time exception with explicit PR-review preserves the spirit while permitting the operation.

## Consequences

### Files added (in this ADR's PR, branch `docs/adr-028-vault-layout`)

- `docs/VAULT-LAYOUT.md` — canonical reference (replaces vault `CLAUDE.md` §Directory Model as authority)
- `docs/decisions/ADR-028-vault-layout-consolidation.md` — this file
- `scripts/vault_layout_audit.py` — skeleton + docstring (full implementation in follow-up issue)
- `docs/case-studies/` — directory (becomes home for the relocated case study)
- `docs/incidents/` — directory
- `docs/prds/` (or augment existing) — home for relocated PRD drafts

### Files changed (this ADR's PR — doc-only)

- `E:\nakama\CLAUDE.md` — add 1-line pointer to `docs/VAULT-LAYOUT.md` + workflow rule "PRs that change vault structure / agent write paths MUST update VAULT-LAYOUT.md"
- Vault `CLAUDE.md` — **NOT changed in this PR**; new version included as ADR attachment for review, applied to vault as Phase 3 step (see Phase plan below).

### Phase 3 implementation (NOT in this PR — separate session)

Each numbered item is its own PR for atomic review:

1. **PR-Codebase-Path-Update** — code-only changes for `AgentOutputs/{agent}/{kind}/` paths. Nami + Franky source + config + prompts + READMEs. Tests pass on new paths. No vault movement.
2. **PR-Vault-AgentOutputs-Move** — physically move `Nami/Notes/*` → `AgentOutputs/nami/notes/*`; `AgentReports/franky/*` → `AgentOutputs/franky/weekly/*`; `AgentReports/dev-backlog.md` → `AgentOutputs/franky/dev-backlog.md`. Delete empty `Nami/`, `AgentReports/`, `AgentBriefs/`.
3. **PR-Inbox-Restructure** — create `Inbox/{web,books,papers,snapshots}/`. Move `Inbox/kb/*.md` → `Inbox/web/`; `Inbox/attachments/*` → `Inbox/web/attachments/`; vault-root `Inbox/*.md` (3 files) → `Inbox/web/`; .epubs → `Inbox/books/`; .mhtml → `Inbox/snapshots/`. 修修 re-picks News Coo FSA root to `Inbox/web/`.
4. **PR-KB-Attachments-Flat** — move `KB/Attachments/inbox/*` and `KB/Attachments/pubmed/*` → `KB/Attachments/{slug}/`. Update `agents/robin/pubmed_digest.py:210` to write to flat path.
5. **PR-Files-Cleanup** — Category A (38 paper figs) → `KB/Attachments/{slug}/` + rewrite refs in KB/Raw/Papers markdowns + Inbox/web markdowns. Category B (31 journal pasted) → `Attachments/journal-pasted/{YYYY-MM}/` + rewrite refs in Journals/Daily markdowns (Journal red line exception per §8). Delete empty `Files/`.
6. **PR-Dev-Artifacts-Out** — `Case Studies/...md` → `docs/case-studies/`; `Incidents/2026/04/drill-...md` → `docs/incidents/`; `KB/Wiki/Outputs/style-extractor-prd-draft.md` → `docs/prds/`; `KB/Wiki/Outputs/2026-04-27-seo-acceptance/` → `AgentOutputs/brook/seo-audit/2026-04-27/`. Delete vault `Case Studies/`, `Incidents/`, `KB/Wiki/Outputs/`, `KB/Wiki/Syntheses/`, `KB/Wiki/Comparisons/`, `Schemas/`.
7. **PR-Marker-Convention** — rename `%%KW-START%% / %%KW-END%%` constants in `shared/lifeos_templates/project_blog.md.tpl` and `project_youtube.md.tpl` to `%%agent-zoro-keywords-start%% / -end%%`. One-shot rewrite of existing Project pages that contain the old markers (small N, safe).
8. **PR-Promotion-Attachment-Fix** — `shared/promotion_commit.py` adds attachment move + image ref rewrite when promoting Inbox/web → KB/Raw/Articles. Tests added.
9. **PR-Vault-CLAUDE-Update** — apply the new vault CLAUDE.md §Directory Model (cheat sheet pointer). This is the only change that touches vault `CLAUDE.md`.
10. **PR-Audit-Script-Full** — `scripts/vault_layout_audit.py` fleshed out from skeleton. Cron added to Franky.

### Drift consequences

Documented as Known Drift section in `docs/VAULT-LAYOUT.md`. Drift records aren't a license to ignore — each is owned and tracked.

### Backwards compatibility

- Existing absolute paths in user-written content (e.g. inline markdown `[[Nami/Notes/sales-kit-2026-04]]`) break after PR-2. Mitigated via Obsidian's "rename refactoring" before the physical move (Obsidian updates wikilinks on rename).
- News Coo FSA root re-pick is one-time and manual (cannot be automated — relies on browser File System Access permission grant).
- VPS deployment: VPS Robin reads from `/home/Shosho LifeOS/` (Syncthing-synced). All Phase 3 PRs are vault-state operations that Syncthing replicates; no separate VPS deploy beyond restarting Sunny after PR-1 / PR-8 (code path changes).

### Reversal cost

After Phase 3 lands, reversal cost is high:
- 9 agents' worth of code references on new paths
- Vault structure baked into Syncthing across 3 devices (desktop Windows, Mac, VPS)
- Hence the panel review before merge — get external eyes before we commit to the trajectory.

## References

- `CLAUDE.md` (repo) — workflow + memory rules
- `CLAUDE.md` (vault, `E:\Shosho LifeOS\CLAUDE.md`) — current Directory Model (to be replaced)
- `CONTENT-PIPELINE.md` — 7-stage content lifecycle (vault is the substrate)
- `CONTEXT-MAP.md` — vault is implicit shared kernel; this ADR documents it
- ADR-001 — agent role assignments (each agent's vault write scope referenced)
- ADR-011 — `KB/Wiki/Concepts/` v2 schema + 4-action dispatcher
- ADR-017 — `KB/Annotations/` schema
- ADR-020 — textbook ingest v3 (writes `KB/Raw/Books/`, `KB/Wiki/Sources/Books/`, `KB/Wiki/Entities/Books/`, `_alias_map.md`)
- ADR-021 — Brook synthesize (writes Brook synthesize store at `data/brook_synthesize/`, NOT vault)
- ADR-024 — Source promotion + RCP (writes `KB/Wiki/Sources/`)
- ADR-027 — Brook scope (reads `Projects/{title}.md` frontmatter via `docs/schemas/project-frontmatter-nested.md`)
- `docs/VAULT-LAYOUT.md` — canonical reference produced by this ADR
- `docs/schemas/project-frontmatter-nested.md` — Project frontmatter schema (ADR-027 PR-6)
- 2026-05-19 grilling transcript — `/grill-with-docs` session that produced Q1-Q11 decisions
