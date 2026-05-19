# ADR-028: Vault Layout Consolidation

**Date:** 2026-05-19 (v1) / 2026-05-19 (v2 post-panel)
**Status:** Accepted
**Deciders:** shosho-chang, Claude Opus 4.7
**Related:** ADR-001 (agent roles), ADR-011 (Concept dispatcher), ADR-017 (Annotation store), ADR-019 (two-file source ingest), ADR-020 (textbook ingest v3), ADR-021 (Brook synthesize), ADR-024 (Source promotion + RCP), ADR-027 (Brook scope), CLAUDE.md (repo), CLAUDE.md (vault), `docs/VAULT-LAYOUT.md`

> **v2 audit trail (2026-05-19):** Multi-agent panel review (Claude + Codex GPT-5 + Gemini 2.5 Pro) ran on v1.
> - **Codex** caught factual drift (Concept count 2,762 not 644; Files/ split 35/34 not 38/31; file path errors in nami.py / kb_writer.py / pubmed_digest.py citations; self-contradiction in §4 seo-audit tree; missing mapping table; latent attachment bug fix scope incomplete — also needs `agents/robin/agent.py:105-129`).
> - **Gemini** raised structural concerns Codex missed (human-only section heading text is brittle contract → use HTML comment marker; Unicode NFC/NFD risk; multimodal media blind spot; Areas/ deferral compounds debt; 10-PR plan migration fatigue).
> - Owner adjudicated 29 distinct push-back points: 20 adopted verbatim, 5 modified, 1 rejected (content-addressable attachments — complexity not warranted for low-dup corpus), 3 escalations decided (Franky outputs → repo split adopted; Areas/ → defer to OKR grill; Inbox β kept based on actual code behavior of Reader UI).
>
> Audits preserved at `docs/research/2026-05-19-codex-adr028-audit.md` and `docs/research/2026-05-19-gemini-adr028-audit.md`. Integration matrix at `docs/research/2026-05-19-adr028-panel-integration-matrix.md`.

---

## Context

The Obsidian LifeOS vault at `E:\Shosho LifeOS\` (Windows) / `/home/Shosho LifeOS` (VPS, Syncthing-synced) is 修修's personal LifeOS — implementation surface of *Designing Your Life* + *OKR* + *Bullet Journal*. It is also the contract surface that 7 Nakama agents read and write into.

A 2026-05-19 grilling session audited the vault and found systemic drift:

1. **Vault-side `CLAUDE.md` §Directory Model has not been updated since 2026-04** — claims a Directory Model that the codebase no longer matches. New folders (`KB/Annotations/`, `KB/Wiki/Digests/{AI,PubMed}/`, `KB/Wiki/Sources/Books/`, `KB/Wiki/Entities/Books/`, `_alias_map.md`) are touched by code but not documented; declared folders (`Schemas/`) have never been written by any producer.

2. **Agent output area is fragmented across three top-level folders** — `Nami/Notes/` (Nami handler writes; canonical Nami note contract at `gateway/handlers/nami.py:458-525, :1773` enforced by `shared/vault_rules.py:14-20`), `AgentBriefs/` (config.yaml declares this for Nami but no code writes), `AgentReports/franky/` (Franky reporter writes here, but README claims `AgentBriefs/`). Three READMEs disagree with code.

3. **News Coo capture → KB ingest pipeline has a broken contract** — News Coo image fetcher writes to `attachments/{slug}/` adjacent to its FSA root pick (`extensions/news-coo/src/vault/imageFetcher.ts:122,165`). After promotion `Inbox/{slug}.md → KB/Raw/Articles/{slug}.md`, the image refs still point at `Inbox/attachments/...`. **Two production paths bypass attachment migration**:
   - `shared/promotion_commit.py` — zero refs to `attach*` / `image` / `shutil` / `copyfile`
   - `agents/robin/agent.py:105-129` — legacy path: `shutil.copy2` of markdown, calls ingest, `file_path.unlink()` deletes Inbox markdown, never touches sibling `attachments/{slug}/`
   
   Bug has not surfaced because 修修 has never cleaned Inbox.

4. **`Files/` at vault root is a 69-file image dumping ground** — verified count: 47 .png + 10 .jpg + 4 .jpeg + 7 .gif + 1 .webp. Split: **35 distinct images** referenced from `KB/Raw/Papers/*.md` (6 sources) + `Inbox/kb/*.md` (3 sources); **34 distinct images** (33 `Pasted image*` + 1 `febcdd94-…_498x230.webp`) referenced from `Journals/Daily/*.md` (7 daily journal files). Zero overlap. No code asserts `Files/` as a contract location.

5. **Three Nakama-internal dev artifacts live in vault** — `Case Studies/2026-04-22 Nakama WP + Community...md`, `Incidents/2026/04/drill-2026-04-26-state-restore.md`, and `KB/Wiki/Outputs/style-extractor-prd-draft.md`. These are documentation about the agent system, not 修修's life/work content.

6. **`KB/Wiki/Outputs/`, `Syntheses/`, `Comparisons/` are codebase-orphan folders** — declared in vault `CLAUDE.md`, zero code writes. `Outputs/` has two manually-placed items; `Syntheses/` and `Comparisons/` fully empty since vault inception.

7. **Project pages (`Projects/{title}.md`) are the only "collab" page type** — both 修修-written sections (`## 專案描述`, `## Draft Outline`, etc.) and agent-written sections (Zoro `%%KW-START%%...%%KW-END%%` keyword block, Brook scaffold button, KB Research button) co-exist. Marker convention unwritten — Zoro uses HTML-comment markers, Brook scaffold and KB Research render to DOM only.

8. **Project bootstrap (`scripts/run_project_bootstrap.py`)** does not emit `line:` / `zoro_inputs:` / `lineN_inputs:` nested frontmatter per ADR-027 §7. ADR-027 PR-6 added the canonical schema (`docs/schemas/project-frontmatter-nested.md`) but bootstrap not yet updated. Out of scope for ADR-028.

The grill produced 11 decisions (Q1-Q11). Panel review applied 25 modifications (20 adopt + 5 mod). This ADR freezes the v2 outcome.

## Decision

### 1. Vault身份 — three-tier ownership × four-stage lifecycle × four pillars

**Vault definition (修修's framing, 2026-05-19):**
> 「目前有關生活與工作資訊的發展、沉澱、收集以及擷取的地方」

**Three-tier ownership:**
- 🔒 **Human only** — AI 不可寫 content. AI 可給 input/suggestion，文字必須由人輸入。Covers `Journals/`, `OKRs/`, `Dashboards/`, sections inside collab pages explicitly marked human-only (see §10).
- 🤖 **Agent only** — 修修 不該手寫；agent 的工作區. Covers `KB/`, `AgentOutputs/`, `Inbox/`.
- 🟡 **協作 (Collab)** — Section-level division within one page. Today's only collab page type: `Projects/{title}.md`.

**Four-stage lifecycle** (CODE framework, Tiago Forte 2022): Capture / Organize / Distill / Express.

**Four pillars** (Designing Your Life 人生儀錶板): Work / Play / Love / Health. Carried in frontmatter `area:` field, **not** folder structure. Health tracking via OKR + TaskNotes (out of scope; future OKR grill session).

**Heuristic subjectivity disclaimer (post-panel addition):**
The repo-vs-vault heuristic 「如果把 Nakama 整個砍掉重寫，這個檔案還有意義嗎？」 (Gemini §1) **requires human judgment** — not deterministic algorithm. When in doubt, raise the question in the next ADR amendment; do not silently relocate. See `docs/VAULT-LAYOUT.md` §5 for current worked examples (PRD / case study / incident drill relocated).

### 2. PARA + CODE adopted as conceptual lens (Strength 1)

**PARA** (Tiago Forte 2022): Projects / Areas / Resources / Archives — adopted as the *conceptual* framework for talking about vault contents.

| PARA bucket | Vault representation |
|---|---|
| **P**rojects | `Projects/` (existing) |
| **A**reas | `area:` frontmatter; **NO folder** (deferred — see "Considered Options") |
| **R**esources | `KB/` (existing; NOT renamed to `Resources/` — see "Considered Options") |
| **A**rchives | `status: archived` frontmatter; NO folder |

**No top-level folder renames.** `KB/` is the highest-traffic agent-writable surface with **2,762 Concept pages** (`E:\Shosho LifeOS\KB\Wiki\Concepts\*.md` verified count) and **73 non-doc code/prompt files** containing `KB/` literal. Rename cost is migration churn without behavioral gain.

### 3. Kill orphan folders

- **`KB/Wiki/Outputs/`** — 0 code refs, 2 manually-placed items (relocated, see §9). Folder deleted.
- **`KB/Wiki/Syntheses/`** — empty since vault inception. Deleted; re-create on first real producer.
- **`KB/Wiki/Comparisons/`** — empty since vault inception. Deleted; re-create on first real producer.
- **`Schemas/`** — declared as "Layer C control plane", never implemented. Deleted.

Principle: 空殼餵養「以後實作」拖延文化. Folders earn their place by having a producer code path.

### 4. Agent output area — split by durability across vault and repo

**Before:**
- `Nami/Notes/` (Nami handler writes; README claims `AgentBriefs/`)
- `AgentBriefs/` (config.yaml claims this for Nami; nothing writes — orphan)
- `AgentReports/franky/` (Franky writes; README claims `AgentBriefs/`)
- `AgentReports/dev-backlog.md` (人手寫; Franky reads)

**After — durability-aware split (panel adjudication A8):**

Apply the vault-vs-repo heuristic from §1: 「Nakama 砍掉重寫還有意義嗎？」
- **Nami briefs / notes / research** — meaningful as 修修's life/work artifacts even without Nakama → **vault**
- **Brook SEO audit outputs** — content artifacts about 修修's published work → **vault**
- **Franky weekly system reports + vault audits + dev-backlog** — about Nakama system itself, not 修修's life → **repo `data/agent_reports/`**

```
# Vault
E:\Shosho LifeOS\AgentOutputs\
├── nami/
│   ├── briefs/         # Morning Brief (config.yaml.agents.nami.brief_path retargeted)
│   ├── notes/          # ad-hoc Nami notes (from Nami/Notes/*)
│   └── research/       # Nami research handler output (from Nami/Notes/Research/*)
└── brook/
    └── seo-audit/      # YYYY-MM-DD/ Brook SEO audit + enrich outputs (per ADR-027)

# Repo
E:\nakama\data\agent_reports\
└── franky/
    ├── weekly/         # YYYY-WW.md (from vault AgentReports/franky/*)
    ├── vault-audit/    # monthly audit script output (Phase 3 PR-11)
    └── dev-backlog.md  # 人寫、Franky 讀 (from vault AgentReports/dev-backlog.md)
```

**Required code changes:**
- `gateway/handlers/nami.py:458-525, :1773` — replace `Nami/Notes/` literals with `AgentOutputs/nami/notes/`
- `shared/vault_rules.py:14-20` — update `_NAMI_WRITE_WHITELIST` to `AgentOutputs/nami/`
- `prompts/nami/agent_system.md` — update path examples (multiple occurrences)
- `agents/franky/agent.py:33, :119` — `AgentReports/dev-backlog.md` → `data/agent_reports/franky/dev-backlog.md`; `AgentReports/franky` → `data/agent_reports/franky/weekly`
- `agents/franky/reporter.py:277` — path template update
- `agents/franky/weekly_digest.py` — path refs (if any)
- `config.yaml` — `agents.nami.brief_path: AgentOutputs/nami/briefs`
- `agents/nami/README.md` + `agents/franky/README.md` — align with reality

### 5. Inbox structure — sort by source type at capture

**After:**
```
Inbox/
├── web/                       # News Coo writes here (re-pick FSA root)
│   ├── *.md
│   └── attachments/{slug}/    # News Coo image fetcher
├── books/                     # 修修 drops .epub
├── papers/                    # 修修 drops .pdf (reserved, currently empty)
└── snapshots/                 # 修修 drops .mhtml
```

**Reader UI contract:** `thousand_sunny/routers/robin.py:135` (`_get_inbox_files`) reads **one folder only**, the one pointed to by `config.yaml.agents.robin.inbox_path`. Set to `Inbox/web`. Other Inbox subfolders are **invoked by their respective ingest tools via absolute path** (textbook-ingest for `books/`, future paper ingest for `papers/`, future mhtml ingest for `snapshots/`). Reader UI does not recurse and intentionally does not need to.

Inbox-to-KB mapping: `Inbox/web → KB/Raw/Articles`, `Inbox/books → KB/Raw/Books` (textbook-ingest), `Inbox/papers → KB/Raw/Papers`.

### 6. KB/Attachments/ flat by source slug

**Before:** `KB/Attachments/{producer}/{slug}/` (producer = `inbox` or `pubmed`)
**After:** `KB/Attachments/{source-slug}/` (no producer prefix; slug is globally unique)

Update `agents/robin/pubmed_digest.py:210` to write flat path.

### 7. Fix the broken Inbox → KB attachment migration contract

Promotion code MUST, when promoting `Inbox/web/{slug}.md` → `KB/Raw/Articles/{slug}.md`:

1. `mv Inbox/web/attachments/{slug}/* → KB/Attachments/{slug}/`
2. Rewrite markdown image refs: `attachments/{slug}/...` → `KB/Attachments/{slug}/...`

**Both production paths must be patched (Codex §1, §4):**
- `shared/promotion_commit.py` — current zero `attach*` refs
- `agents/robin/agent.py:105-129` — legacy path with `shutil.copy2` + `unlink`, also ignores `attachments/{slug}/`

Without both, one live path remains broken.

**Implementation lands in Phase 3 PR-1 (re-sequenced from PR-8 per Codex §4).**

### 8. `Files/` migration (retroactive cleanup) — narrowly-scoped Journals exception

**Verified counts (Codex §3):**

- **Category A — 35 distinct paper figures** referenced from `KB/Raw/Papers/*.md` (6 source files) and `Inbox/kb/*.md` (3 source files). Mapping table at `docs/VAULT-LAYOUT.md §migration-files-category-a`.
- **Category B — 34 distinct Obsidian-pasted/embedded images** referenced from `Journals/Daily/{date}.md` (7 daily journal files). 33 `Pasted image*` + 1 `febcdd94-…_498x230.webp`.

**Actions:**
- Category A → migrate to `KB/Attachments/{source-slug}/`, rewrite image refs in source markdowns
- Category B → migrate to `Attachments/journal-pasted/{YYYY-MM}/`, rewrite image refs in Journal markdowns

**Journals red-line exception (narrowly scoped — Codex §4):**
> CLAUDE.md (vault) §3 declares `Journals/` write-prohibited. This ADR grants a **single-use** exception, scoped to this specific operation only: mechanical path-only rewrites of image references inside `Journals/Daily/{date}.md` files, applied as part of ADR-028 §8 Category B `Files/` migration.
>
> **This exception does NOT generalize.** Future Journal rewrites — even mechanical ones — require their own ADR amendment + PR with a diff-only verifier proving path-only edits (no prose content changes). The phrase "applies retroactively to similar future operations" is **explicitly rejected** from earlier drafts; precedent language risks dilution of the red line.

**Obsidian config change (forward):** set default attachment folder to `Attachments/journal-pasted/{{date:YYYY-MM}}` so future paste-images auto-bucket.

**Migration verification + rollback (Codex §4):**
- Generate migration manifest before any move: `{old_path, new_path, sha256, referring_markdown_files}` for each of 69 files
- Dry-run: assert zero remaining references to every root `Files/` basename across vault before delete
- Post-move re-hash verification
- Recycle-bin semantics (PowerShell `Microsoft.VisualBasic.FileIO.FileSystem::DeleteFile` with `SendToRecycleBin`), not permanent removal
- Manifest committed to `data/migrations/2026-05-XX-files-cleanup.json` (repo) for auditability

Resulting state: `Files/` empty, deleted (recycle-bin).

### 9. Dev artifacts live in repo, not vault

Heuristic from §1. Relocations:
- `Case Studies/2026-04-22 Nakama WP + Community 整合架構規劃.md` → `E:\nakama\docs\case-studies/`
- `Incidents/2026/04/drill-2026-04-26-state-restore.md` → `E:\nakama\docs\incidents/`
- `KB/Wiki/Outputs/style-extractor-prd-draft.md` → `E:\nakama\docs\prds/`
- `KB/Wiki/Outputs/2026-04-27-seo-acceptance/` → vault `AgentOutputs/brook/seo-audit/2026-04-27/` (this IS agent task output per §4 split, stays in vault)

Vault `Case Studies/` and `Incidents/` folders deleted after migration.

### 10. Marker convention for collab pages

**In-scope:** `Projects/{title}.md`. Future collab page types must be explicitly added to `docs/VAULT-LAYOUT.md §collab-pages`.

**Pattern A — Agent-written sections (positional HTML-comment markers, default):**

```markdown
%%agent-{agent_name}-{section_id}-start%%

(agent-written content; persists in .md, syncs via Syncthing, mobile-visible, Obsidian-search-indexed)

%%agent-{agent_name}-{section_id}-end%%
```

**Currently registered Pattern A sections:**

| Section heading | Marker pair | Producer | Template file |
|---|---|---|---|
| `## 🗝️ Keyword Research & SEO` | `%%agent-zoro-keywords-start%% / -end%%` | Zoro `/zoro/keyword-research` | `shared/lifeos_templates/project_blog.md.tpl:195-196` (currently `%%KW-START / %%KW-END`; rename in Phase 3 PR-7) |

**Pattern B — DOM-only render (exception):** when agent output is "large" (rule of thumb: >50 lines) or naturally lives elsewhere.

**Currently registered Pattern B sections:**

| Section heading | Producer | Lives where |
|---|---|---|
| `## 📚 KB Research` | Robin `/kb/research` | dataviewjs DOM (cached in localStorage) |
| `## 🪄 Brook: Scaffold` | Brook synthesize `/api/projects/{slug}/synthesize/run` (ADR-027 PR-6) | `BrookSynthesizeStore` JSON; review at `/projects/{slug}` |

**Human-only section declaration (panel adjudication M2 — Gemini §1):**

Previous draft listed heading text as the contract (`## 專案描述`, `## 預期成果`, `## Draft Outline`, `## 專案筆記`). This is brittle — emoji prefix, synonym, or translation changes silently break the audit.

**Canonical contract uses a non-rendering HTML comment marker as the sole authority:**

```markdown
## 專案描述 <!-- vault:human-only-section -->

(Content here is protected regardless of heading text changes — owner may edit
the heading freely; the marker is the contract authority.)
```

`scripts/vault_layout_audit.py` (Phase 3 PR-11 full impl) MUST audit by marker presence, not heading text. Heading text is human-facing only.

**Currently registered human-only sections (per Project page, marker authority):**
- `<!-- vault:human-only-section -->` after `## 專案描述`
- After `## 預期成果`
- After `## Draft Outline` (修修's outline; NOT Brook scaffold's)
- After `## 專案筆記`

Phase 3 PR-7 (marker convention rollout) updates `shared/lifeos_templates/project_*.md.tpl` to emit the human-only markers in bootstrapped pages. Existing Project pages get a one-shot retro-fit in same PR.

**Marker schema upgrade path (panel adjudication M1):**
The positional `-start/-end` token pair is chosen over YAML-in-comment (Codex §5 alternative) for grep simplicity. To support future metadata (schema_version, updated_at, agent_run_id), the agent may emit an optional `<!-- agent-meta: {...} -->` HTML comment immediately after `-start%%`:

```markdown
%%agent-zoro-keywords-start%%
<!-- agent-meta: { "schema_version": 2, "updated_at": "2026-05-19T10:00:00Z", "run_id": "..." } -->

(content)

%%agent-zoro-keywords-end%%
```

Parsers must tolerate missing meta block (v1 backward compat).

### 11. Documentation location + maintenance discipline

**Canonical:** `E:\nakama\docs\VAULT-LAYOUT.md` (in repo, git-versioned, PR-reviewed). Status: **Target (post-ADR-028 Phase 3)** — known drift entries §7 list pre-migration state delta.

**Vault-side pointer:** `E:\Shosho LifeOS\CLAUDE.md` §Directory Model rewritten as ~30-line cheat sheet pointing at repo canonical. Applied in Phase 3 PR-C-Activation.

**Maintenance discipline (γ — both human and automated):**

- **α — PR discipline (mandatory):** any PR that changes vault folder structure, agent vault-write paths, marker convention, or ADR-028 / VAULT-LAYOUT.md authoritative claims MUST update `docs/VAULT-LAYOUT.md` in the same PR. Enforced via line in `E:\nakama\CLAUDE.md` workflow rules.
- **β — Monthly audit (automated):** `scripts/vault_layout_audit.py` runs monthly via Franky cron, checks: (1) folder diff, (2) code-asserted path diff vs producer/consumer matrix, (3) marker convention violations, (4) drift entry status verification. Output written to `data/agent_reports/franky/vault-audit/{period}.md` (repo, per §4 split).

**Content scope (III — folder + concepts + drift):** see `docs/VAULT-LAYOUT.md` §1-§10.

## Considered Options

### Rejected: full PARA folder migration (Strength 3)

Top-level rename `KB/` → `Resources/` plus new `Areas/` + `Archives/` folders. Rejected because:
- `KB/` is highest-traffic agent-writable surface (2,762 Concept pages, 73 code refs). Rename cost: every ADR (-011, -017, -020, -021, -024), every agent's prompt, every templater config, every dataviewjs query.
- PARA purity is *naming aesthetic*, not function.
- 修修 explicitly flagged framework as "有時候太複雜" in Q4 grill — full PARA imposes the complexity 修修 rejected.

**Gemini push-back acknowledged (Gemini §2):** "`Resources/`" is human-centric while "`KB/`" is system-centric; over 5-10 years this framing affects how the owner perceives their knowledge. **This trade-off is real.** The decision favors short-term engineering pragmatism. Re-evaluate if future PKM friction surfaces; reserve for separate ADR.

### Rejected: `AgentOutputs/` Option β (minimal — only fix docs, no folder move)

Keep `Nami/Notes/` + `AgentReports/franky/` + `AgentBriefs/`, align READMEs + config to reality. Rejected per Q6 grill: 修修 chose α — "想要把資料夾變得有邏輯一點" — and β leaves three top-level agent folders permanently asymmetric, breeding future drift.

### Rejected: collab marker Option B (no agent writes to .md, render to DOM only)

Stricter red line. Rejected: mobile Obsidian needs persisted .md content; Syncthing replicates .md not DOM state; Pattern A + B mix is the right compromise.

### Rejected: keep `KB/Wiki/Syntheses/` + `Comparisons/` as reserved slots

Pre-allocated for "future producers". Rejected: same pre-allocation logic produced the `Schemas/` orphan. Empty folders breed "later" debt.

### Rejected: vault-side canonical doc

Doc lives in vault, repo references vault. Rejected: vault not git-versioned (no review history); multi-agent contract needs PR review; vault `CLAUDE.md`'s own drift is the cautionary tale.

### Rejected: Journals red line absolutism

Treat CLAUDE.md "Journals/ 完全禁止寫入" literally and require 修修 to manually move 34 Pasted images. Rejected per Q8 grill: rule's *intent* is preventing AI prose contamination, not preventing path rewrites. **Narrowly-scoped exception** with explicit non-precedent language (per Codex §4 push-back on v1's overbroad phrasing).

### Rejected: content-addressable attachment storage (panel A7 — Gemini §4)

Gemini proposed `Attachments/by-hash/{sha256}.png` to prevent duplicate storage when two sources reference the same figure. Rejected: complexity not warranted. Real-world dup rate in this corpus is <1% (every paper has its own original figures; News Coo articles rarely embed same image as another). Reserve as future ADR if dup surfaces. The flat `KB/Attachments/{source-slug}/` keeps grep simple and Obsidian-graph reasoning straightforward.

### Deferred: Create `Areas/` folder now (panel A2 — Gemini §3)

Gemini argued strongly for creating `Areas/{Work,Play,Love,Health}/` folders now even if sparsely populated, citing compounding cognitive debt — Dataview queries scattered across un-folder'd notes will grow brittle as vault grows. **Codex argued for deferral** until OKR grill consolidates Areas + OKR + tracking design.

**Resolution: defer to OKR grill session** (Q4 grill outcome; consistent with §1 "OKR/Tasks is another grill"). Gemini's compound-debt observation is recorded as **open issue** for that future session:

> When OKR grill decides Areas/ representation (folder vs frontmatter-only vs hybrid), explicitly evaluate Gemini's compound-debt argument (Gemini ADR-028 audit §3). If frontmatter-only chosen, document Dataview query strategy and budget for friction monitoring.

### Considered + deferred: split current-state vs target-state docs (panel S1 — Codex §5)

Codex proposed two docs: `VAULT-LAYOUT-current.md` (current vault state) + `VAULT-LAYOUT-target.md` (post-Phase 3). Considered, but adopted simpler resolution:
- `docs/VAULT-LAYOUT.md` clearly labeled **Status: Target (post-ADR-028 Phase 3)** at top
- Drift entries §7 explicitly enumerate pre-migration deltas
- After each Phase 3 PR lands, corresponding drift entry resolved to `[已修]` in same PR

Two-doc approach is heavier than necessary given Phase 3 will close fast.

### Considered + deferred: Knowledge Gardener / decay detection (panel A5 — Gemini §2)

Gemini raised valid concern: ADR-028 addresses accretion (Capture → Express) but ignores decay (orphan concepts, stale notes, archived-project leftovers). Out of scope for this ADR (folder layout, not graph health). Added to "Open follow-ups" below.

## Consequences

### Files added (this ADR's PR — branch `docs/adr-028-vault-layout`)

- `docs/VAULT-LAYOUT.md` — canonical reference (target state + known drift §7)
- `docs/decisions/ADR-028-vault-layout-consolidation.md` — this file
- `docs/vault-claude-md-replacement-draft.md` — proposed replacement for vault `CLAUDE.md` §Directory Model (applied at Phase 3 PR-C-Activation)
- `scripts/vault_layout_audit.py` — skeleton stub (full impl: Phase 3 PR-C-Activation)
- `docs/case-studies/`, `docs/incidents/`, `docs/prds/` — repo destination dirs for Phase 3 PR-B relocations (empty placeholders to land in this PR or as part of PR-B)
- `data/agent_reports/franky/` — repo destination for Phase 3 (per §4 split)
- `docs/research/2026-05-19-codex-adr028-audit.md` + `docs/research/2026-05-19-gemini-adr028-audit.md` + `docs/research/2026-05-19-adr028-panel-integration-matrix.md` — audit trail

### Files changed (this PR — doc-only)

- `E:\nakama\CLAUDE.md` — pointer to `docs/VAULT-LAYOUT.md` + PR-discipline workflow rule
- Vault `CLAUDE.md` — **NOT changed in this PR**; replacement applied in Phase 3 PR-C-Activation

### Phase 3 implementation — three atomic phases (panel A3 — Gemini §5)

Gemini's "10 atomic PRs is operationally fragile (migration fatigue risk)" pushed reorganization into **three named phases with go/no-go gates**. PR atoms preserved for clean review, but landing windows compressed:

#### **Phase A — Code Prep (no vault movement)** — 3 PRs

- **PR-A1 — Promotion Attachment Fix** (CRITICAL, re-sequenced to first per Codex §4)
  - Patches `shared/promotion_commit.py` + `agents/robin/agent.py:105-129` (both production paths)
  - Adds attachment migration: move `Inbox/web/attachments/{slug}/*` → `KB/Attachments/{slug}/`, rewrite image refs
  - Tests
  - **Gate to Phase B:** PR-A1 must land. Without it, all subsequent vault migrations risk silent image breakage.
- **PR-A2 — Codebase Path Update** — Nami + Franky source/config/prompt/README changes for `AgentOutputs/{nami,brook}/` (vault) + `data/agent_reports/franky/` (repo) per §4 split. No vault movement yet.
- **PR-A3 — Marker Convention Rollout** — rename Zoro `%%KW-START%% / %%KW-END%%` → `%%agent-zoro-keywords-start%% / -end%%` in `shared/lifeos_templates/project_blog.md.tpl` + `project_youtube.md.tpl`; add `<!-- vault:human-only-section -->` markers to template; one-shot retro-fit existing Project pages.

**Phase A go/no-go gate:** PR-A1, A2, A3 all green + manual smoke test on dev vault clone. Then Phase B.

#### **Phase B — Bulk Migration (vault state changes)** — 1 PR (big bang)

- **PR-B1 — Bulk Vault Migration** — single migration script run as one PR:
  1. Move `Nami/Notes/*` → `AgentOutputs/nami/notes/*`
  2. Move `AgentReports/franky/2026-W15.md` → `data/agent_reports/franky/weekly/2026-W15.md` (cross-filesystem — actually a copy from vault Syncthing-tracked path into repo; vault original deleted via recycle-bin)
  3. Move `AgentReports/dev-backlog.md` → `data/agent_reports/franky/dev-backlog.md`
  4. Move `KB/Wiki/Outputs/2026-04-27-seo-acceptance/` → `AgentOutputs/brook/seo-audit/2026-04-27/`
  5. Move `KB/Wiki/Outputs/style-extractor-prd-draft.md` → repo `docs/prds/`
  6. Move `Case Studies/...md` → repo `docs/case-studies/`
  7. Move `Incidents/2026/04/...md` → repo `docs/incidents/`
  8. Restructure `Inbox/{kb,attachments} → Inbox/web/{*,attachments}`; create `Inbox/{books,papers,snapshots}/`; move .epub → books, .mhtml → snapshots
  9. Flatten `KB/Attachments/{inbox,pubmed}/*` → `KB/Attachments/{slug}/`
  10. Migrate `Files/` Category A (35 figs) → `KB/Attachments/{slug}/` + rewrite refs in 9 source markdowns
  11. Migrate `Files/` Category B (34 figs) → `Attachments/journal-pasted/{YYYY-MM}/` + rewrite refs in 7 Journal markdowns (one-shot exception)
  12. Delete `Files/`, `Nami/`, `AgentReports/`, `AgentBriefs/`, `Case Studies/`, `Incidents/`, `Schemas/`, `KB/Wiki/{Outputs,Syntheses,Comparisons}/` (recycle-bin)

**Pre-migration:**
- Generate `data/migrations/2026-05-XX-vault-cleanup.json` manifest: 修修 12 sub-operations × per-file `{old_path, new_path, sha256, referring_md_files}`
- Dry-run verifier: for each old path, assert zero remaining references in vault after migration
- 修修 manually closes News Coo browser popups (handles FSA root re-pick — see "News Coo transition" below)

**Post-migration:**
- Re-hash verifier: all `new_path` files have expected sha256
- Reference verifier: zero references to old paths in any vault file
- 修修 re-picks News Coo FSA root to `E:\Shosho LifeOS\Inbox\web` in extension popup

**News Coo FSA transition (panel S6 — Codex §4):**
- Codex flagged: `extensions/news-coo/src/options/handle.ts:24-40` persists one FileSystemDirectoryHandle in IndexedDB; `options.ts:20-24` overwrites on re-pick. But popups already open at PR-B1 land time hold the OLD handle (`popup.ts:75-76`).
- **Mitigation:** PR-B1 deploy notes instruct 修修 to (a) close all News Coo popups on all devices, (b) re-pick FSA root in options page after deploy. Robin retains a one-release "dual-read" of `Inbox/kb` (deprecated) + `Inbox/web` (canonical) — straggler scan flags any new writes to deprecated path; Phase C removes dual-read.

**Phase B rollback plan:**
- Manifest contains old→new bidirectional mapping
- Reverse script reads manifest, restores each file to old path (recycle-bin recovery for deleted folders)
- 24-hour observation window after PR-B1 land before Phase C (catch latent break)

**Phase B go/no-go gate:** post-migration verifier green + 24h observation + 修修 explicit "go" before Phase C.

#### **Phase C — Activation & Cleanup** — 2 PRs

- **PR-C1 — Vault CLAUDE.md Update + Audit Script Full** — apply `docs/vault-claude-md-replacement-draft.md` to vault `CLAUDE.md` §Directory Model; flesh out `scripts/vault_layout_audit.py` from skeleton (full folder-diff + code-path-diff + marker-violation + drift-status check); wire Franky monthly cron.
- **PR-C2 — Drift Records Resolved** — flip §7 drift entries from `[待修]` to `[已修]` with commit hashes; remove deprecated `Inbox/kb` dual-read from Robin; close ADR-028.

### ADR amendments to other ADRs (post-Phase 3)

- ADR-001 — Brook scope amendment already covers SEO audit (per ADR-027); no further change.
- ADR-011 — D1 (Concept dispatcher unreachable) remains `[待修]`; out of scope for ADR-028.
- ADR-017 — citation correction: ADR-017 documents v1/v2 only; v3 schema is implicit code addition. `docs/VAULT-LAYOUT.md` cites correctly.
- ADR-024 — unchanged; this ADR adds the attachment migration contract ADR-024 didn't specify.

### Open follow-ups (NOT in this ADR's scope)

- **Areas/ folder decision** — owner adjudication deferred to OKR grill; Gemini's compound-debt argument logged for that session
- **Knowledge Gardener / decay detection** — Gemini §2 raised orphan-concept + stale-page detection. Future ADR or Franky weekly digest feature
- **Multimodal primary assets** (video, audio, project files) — Gemini §3 raised this. `docs/VAULT-LAYOUT.md §11-primary-media-assets` documents current state ("out of vault scope; reference via `Projects/{slug}/assets.md` pointer to external storage"). Full strategy = future ADR
- **Concept dispatcher unreachable from textbook-ingest Phase B** (ADR-011 D1) — Future textbook-ingest v4 OR ADR-011 amendment
- **Entity v2 schema** — `KB/Wiki/Entities/` v1 frozen since ADR-011 §3.1; v2 design pending first real cross-source entity merge workflow
- **`KB/index.md` sync enforcement** — Currently append-only without coverage check (D3); future ingest pipeline refactor OR audit script extension
- **Unicode normalization** (NFC vs NFD) — Gemini §1 raised cross-platform risk. Mitigation noted in `docs/VAULT-LAYOUT.md §7-D-unicode-norm`; full audit hooks pending Phase C PR-C1 audit script impl
- **Content-addressable attachment storage** — rejected for now; revisit if dup rate climbs

### Reversal cost

After Phase 3 lands, reversal cost is high:
- Multiple agents' code on new paths
- Vault state baked into Syncthing across 3 devices (Windows / Mac / VPS)
- Hence panel review before merge — done; verdicts incorporated above.

## References

- `CLAUDE.md` (repo)
- `CLAUDE.md` (vault, `E:\Shosho LifeOS\CLAUDE.md`)
- `CONTENT-PIPELINE.md` — 7-stage content lifecycle
- `CONTEXT-MAP.md` — bounded-context map
- ADR-001 — agent role assignments
- ADR-011 — Concept dispatcher + Entity v1 schema
- ADR-017 — Annotation store
- ADR-019 — two-file source ingest pattern
- ADR-020 — textbook ingest v3
- ADR-021 — Brook synthesize
- ADR-024 — Source promotion + RCP
- ADR-027 — Brook scope (PR-6 added `docs/schemas/project-frontmatter-nested.md`)
- `docs/VAULT-LAYOUT.md` — canonical reference (target state)
- `docs/research/2026-05-19-codex-adr028-audit.md` — Codex (GPT-5) panel audit
- `docs/research/2026-05-19-gemini-adr028-audit.md` — Gemini 2.5 Pro panel audit
- `docs/research/2026-05-19-adr028-panel-integration-matrix.md` — 3-way adjudication
- 2026-05-19 grilling transcript — `/grill-with-docs` session producing Q1-Q11 decisions
