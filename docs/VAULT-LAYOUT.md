# Vault Layout — Canonical Reference

**Status:** Active (post-ADR-028)
**Last updated:** 2026-05-19 (by ADR-028, this version is the initial canonical version)
**Authority:** Changes to this doc require PR review. Code changes that affect vault contracts MUST update this doc in the same PR.
**Audit:** Monthly via `scripts/vault_layout_audit.py` (cron via Franky). Discrepancies append to §10.

---

## 0. What is the vault?

The Obsidian vault at:
- Windows: `E:\Shosho LifeOS\`
- Mac: `/Users/shosho/Documents/Shosho LifeOS/`
- VPS: `/home/Shosho LifeOS/` (Syncthing-synced from desktop)

is 修修's personal **LifeOS** — the implementation surface of an integrated *Designing Your Life* + *OKR* + *Bullet Journal* system. It is also the contract surface that 7 Nakama agents read and write into.

**Vault scope** = 修修的生活/工作資訊的 develop / settle / collect / capture.
**Vault NOT scope** = Nakama-as-a-system dev artifacts (those live in repo `docs/`).

Heuristic: 「如果把 Nakama 整個砍掉重寫，這個檔案還有意義嗎？」有 = vault；沒有 = repo.

---

## 1. Conceptual model

### Two complementary lenses (PARA + CODE)

Adopted as *conceptual* framework; **not** enforced as folder hierarchy (see §4 Strength 1).

**PARA — WHERE things live**
| PARA bucket | Vault representation |
|---|---|
| **P**rojects | `Projects/` |
| **A**reas | `area:` frontmatter (Work / Play / Love / Health per Designing Your Life). No folder |
| **R**esources | `KB/` (knowledge base) |
| **A**rchives | `status: archived` frontmatter. No folder |

**CODE — HOW things flow**
| Verb | What happens | Typical folders |
|---|---|---|
| **C**apture | 即時、frictionless input | `Inbox/`, `Journals/Daily/`, `KB/Annotations/` |
| **O**rganize | 主動歸位 | `Inbox/` → `KB/Raw/` promotion; `Projects/` bootstrap |
| **D**istill | 沉澱、結晶 | `KB/Wiki/{Sources,Concepts,Entities}/`, `_alias_map.md` |
| **E**xpress | 用沉澱物產出 | `Projects/` output, articles, OKR progress |

Lifecycle: Capture / Collect → Organize → Distill → Express. Express may trigger new Capture (recursive).

### Three-tier ownership (red lines)

Every page belongs to **exactly one tier**:

🔒 **Human only** — AI 不可寫 content. AI 可給 input/suggestion，文字必須由人輸入。
🤖 **Agent only** — 修修 不該手寫；agent 的工作區。
🟡 **協作** — Section-level division within one page. Today the only collab page type is `Projects/{title}.md`.

### Four pillars (Designing Your Life)

Work / Play / Love / Health — carried in `area:` frontmatter, **not** folder structure. Health tracking lives in OKR + TaskNotes (out of scope of this doc; covered in future OKR grill session).

---

## 2. Top-level folder map

```
E:\Shosho LifeOS\
├── CLAUDE.md             — 30-line cheat sheet pointer to this doc
├── Journals/             🔒 Human only
│   ├── Daily/              Obsidian Daily Notes plugin + tpl-daily-journal
│   ├── Weekly/             tpl-weekly-journal
│   ├── Quarterly/          人寫
│   └── Yearly/             人寫
│
├── OKRs/                 🔒 Human only       — 年度 / 季度 OKR
│
├── Projects/             🟡 Collab          — Project pages; bootstrap + agent assist sections + 修修 正文
│
├── TaskNotes/            🟡 Half-collab     — TaskNotes plugin owns
│   ├── Tasks/              lifeos_writer + plugin write
│   ├── Archive/            plugin auto-archive
│   └── Views/              base files
│
├── Dashboards/           🔒 Human only      — dataviewjs queries; 修修 writes the query
│
├── Inbox/                🤖 Capture cache (ephemeral; OK to clean)
│   ├── web/                News Coo extension (FSA root pick 這層)
│   │   ├── *.md
│   │   └── attachments/{slug}/
│   ├── books/              .epub (textbook-ingest source)
│   ├── papers/             .pdf
│   └── snapshots/          .mhtml
│
├── KB/                   🤖 Agent only (R in PARA = Resources)
│   ├── Raw/                原始證據層 (禁改 body)
│   │   ├── Articles/         Robin ingest from Inbox/web
│   │   ├── Papers/           Robin / PubMed digest
│   │   ├── Books/            textbook-ingest Phase 0
│   │   ├── Podcasts/         (reserved, currently empty)
│   │   ├── Videos/           (reserved)
│   │   ├── Repos/            (reserved)
│   │   └── Data/             (reserved)
│   │
│   ├── Wiki/               AI 加工層 (free write)
│   │   ├── Sources/          Robin promotion (ADR-024) + textbook-ingest
│   │   │   └── Books/          textbook-ingest Phase 1 chapter pages
│   │   ├── Concepts/         textbook-ingest + annotation_merger + 4-action dispatcher (ADR-011)
│   │   ├── Entities/         textbook-ingest book entities
│   │   │   └── Books/          book entity index pages
│   │   ├── Digests/          Nami / Robin daily digests
│   │   │   ├── AI/             daily AI news
│   │   │   └── PubMed/         daily PubMed
│   │   └── _alias_map.md     L1 alias index (ADR-020 v3 maturity model)
│   │
│   ├── Annotations/{slug}.md   Robin Reader save-annotations (ADR-017)
│   │
│   ├── Attachments/{source-slug}/   flat per-source; news-coo + pubmed write here
│   │
│   ├── index.md            Robin ingest append (sync drift D3 below)
│   └── log.md              Robin ingest append-only
│
├── Attachments/          🔄 mixed
│   ├── Books/{book-id}/ch{n}/   textbook-ingest Phase 0 figure binaries
│   └── journal-pasted/{YYYY-MM}/  Obsidian default attachment path (Daily Journal pastes)
│
├── AgentOutputs/         🤖 Agent task outputs
│   ├── nami/
│   │   ├── briefs/         Morning Brief (config.yaml agents.nami.brief_path)
│   │   ├── notes/          ad-hoc Nami notes
│   │   └── research/       Nami research handler output
│   ├── brook/
│   │   └── seo-audit/        Brook SEO audit task outputs (per ADR-027 §SEO)
│   └── franky/
│       ├── weekly/         YYYY-WW.md reports
│       └── dev-backlog.md  人寫、Franky 讀
│
├── Templates/            🔒 Human only (Templater plugin owns)
└── Scripts/              🔒 Human only (Templater user scripts + nakama-config.md)
```

**Notable absences** (intentionally not in vault):
- `Schemas/` — declared in legacy vault CLAUDE.md, never had a producer; deleted per ADR-028 §3.
- `Files/` — was a flat image dumping ground; migrated per ADR-028 §8.
- `KB/Wiki/Outputs/`, `Syntheses/`, `Comparisons/` — orphan folders, deleted per ADR-028 §3.
- `Nami/`, `AgentBriefs/`, `AgentReports/` — consolidated to `AgentOutputs/` per ADR-028 §4.
- `Case Studies/`, `Incidents/` — Nakama dev artifacts, moved to repo `docs/` per ADR-028 §9.

---

## 3. Producer / Consumer matrix

| Path | Tier | Producer (code path) | Consumer | Schema authority |
|---|---|---|---|---|
| `Journals/Daily/` | 🔒 | 修修 via Templater `tpl-daily-journal.md` | 修修 reads; agents may read for context | tpl-daily-journal frontmatter |
| `Journals/{Weekly,Quarterly,Yearly}/` | 🔒 | 修修 via Templater | 修修 | tpl-weekly-journal, etc |
| `OKRs/` | 🔒 | 修修 via tpl-okr-{annual,quarterly} | 修修 | tpl-okr-* |
| `Projects/{title}.md` | 🟡 | Bootstrap: `scripts/run_project_bootstrap.py` + `shared/lifeos_writer.py:render_project`<br>Body sections: 修修 (人 only sections) + agent buttons (marker sections, §5) | Brook synthesize, Zoro keyword button, KB Research button | `shared/lifeos_writer.py` + `docs/schemas/project-frontmatter-nested.md` (ADR-027 §7) |
| `TaskNotes/Tasks/` | 🟡 | Bootstrap + Nami handler `gateway/handlers/nami.py` + TaskNotes plugin auto-fields | TaskNotes plugin queries | `shared/lifeos_writer.py:render_task` |
| `TaskNotes/{Archive,Views}/` | 🤖 | TaskNotes plugin | TaskNotes plugin | Plugin config |
| `Dashboards/` | 🔒 | 修修 (dataviewjs queries) | Obsidian render | — |
| `Inbox/web/*.md` | 🤖 | News Coo extension `extensions/news-coo/src/vault/writer.ts` | Robin Reader `/read`, Robin ingest | News Coo frontmatter (`news_coo_version: 1`, `source_url`, `captured_at`) |
| `Inbox/web/attachments/{slug}/` | 🤖 | News Coo image fetcher `extensions/news-coo/src/vault/imageFetcher.ts:122,165` | Robin Reader, KB Raw consumers | binary; ref'd from sibling .md |
| `Inbox/books/` | 🤖 | 修修 drops .epub manually | textbook-ingest skill (Phase 0) | — |
| `Inbox/papers/` | 🤖 | 修修 drops .pdf manually | (future) paper ingest | — |
| `Inbox/snapshots/` | 🤖 | 修修 drops .mhtml manually | (future) web-snapshot ingest | — |
| `KB/Raw/Articles/{slug}.md` | 🤖 | Robin ingest from `Inbox/web/` | KB consumers, RCP builder | ADR-019 frontmatter (`title, source, authors, date_read, tags`) |
| `KB/Raw/Papers/{slug}.md` | 🤖 | Robin ingest + `agents/robin/pubmed_digest.py:477,522` | KB consumers, Brook synthesize | same as Articles |
| `KB/Raw/Books/{book-id}.md` | 🤖 | textbook-ingest Phase 0 (lossless EPUB→markdown via ebooklib) | textbook-ingest Phase 1 LLM | ADR-020 §Phase 0 |
| `KB/Wiki/Sources/{slug}.md` | 🤖 | Robin promotion `shared/promotion_commit.py` + textbook-ingest | Brook synthesize, RCP, `/writing-assist/` | ADR-024 + `shared/promotion_renderer.py` |
| `KB/Wiki/Sources/Books/{book-id}/ch{n}.md` | 🤖 | textbook-ingest Phase 1 (`shared/kb_writer.write_source_page`) | Brook context_bridge, Reader | ADR-020 §Phase 1 |
| `KB/Wiki/Concepts/{slug}.md` | 🤖 | `shared/kb_writer.upsert_concept_page` (4-action dispatcher, ADR-011 §3.3) + `agents/robin/annotation_merger.py` + textbook-ingest | kb_search, Brook synthesize, RCP | ADR-011 §3.3 v2 schema |
| `KB/Wiki/Entities/{slug}.md` | 🤖 | textbook-ingest book entity writer | kb_search | ADR-011 §3.1 v1 schema (frozen — see D2) |
| `KB/Wiki/Entities/Books/{book-id}.md` | 🤖 | textbook-ingest book entity index | 修修, kb_search | ADR-020 §Phase 2 |
| `KB/Wiki/Digests/AI/{YYYY-MM-DD}.md` | 🤖 | Nami daily AI digest | 修修 | — |
| `KB/Wiki/Digests/PubMed/{YYYY-MM-DD}.md` | 🤖 | `agents/robin/pubmed_digest.py:527` | 修修, Brook synthesize | — |
| `KB/Wiki/_alias_map.md` | 🤖 | textbook-ingest L1 alias collector (ADR-020 v3) | textbook-ingest re-evaluation | ADR-020 v3 maturity model |
| `KB/Annotations/{slug}.md` | 🤖 | `shared/annotation_store.AnnotationStore.save` ← Robin Reader `POST /save-annotations` | Reader render, `annotation_merger`, RCP builder | ADR-017 (v1/v2/v3 discriminated union) |
| `KB/Attachments/{source-slug}/` | 🤖 | News Coo image fetcher (post-promotion migration, see D-promotion below) + `agents/robin/pubmed_digest.py:210` PubMed PDFs | source-page image refs | binary |
| `KB/index.md` | 🤖 | `agents/robin/ingest.py:583,600` + `pubmed_digest.py:539` (append) | 修修 manual reads | — |
| `KB/log.md` | 🤖 | `agents/robin/ingest.py` + `pubmed_digest.py:527` (append-only) | 修修 manual reads | append-only |
| `Attachments/Books/{book-id}/ch{n}/` | 🤖 | textbook-ingest Phase 0 figure extractor | book chapter source pages | ADR-020 §Phase 0 |
| `Attachments/journal-pasted/{YYYY-MM}/` | 👤+plugin | Obsidian paste (config: `app.json attachmentFolderPath`) | Daily Journal markdown refs | binary |
| `AgentOutputs/nami/briefs/` | 🤖 | Nami Morning Brief handler (post-ADR-028) | 修修 | `agents/nami/...` |
| `AgentOutputs/nami/notes/` | 🤖 | `gateway/handlers/nami.py` (post-ADR-028 path migration) | Nami handler reads back for context | — |
| `AgentOutputs/nami/research/` | 🤖 | Nami research handler | 修修 | — |
| `AgentOutputs/brook/seo-audit/{YYYY-MM-DD}/` | 🤖 | Brook SEO audit + enrich runners (ADR-027) | 修修 | — |
| `AgentOutputs/franky/weekly/{period}.md` | 🤖 | `agents/franky/reporter.py:277` (post-path-migration) | 修修 | Franky weekly format |
| `AgentOutputs/franky/dev-backlog.md` | 👤+🤖 | 修修 writes; `agents/franky/agent.py:33` reads | Franky weekly digest input | — |
| `Templates/` | 🔒 | 修修 | Templater plugin | Templater conventions |
| `Scripts/nakama-config.md` | 🔒 | 修修 | dataviewjs (reads `robin_url`, `robin_key`) | — |

---

## 4. Marker convention for collab pages

In-scope: `Projects/{title}.md`. Future collab page types must be added to this section.

### Pattern A — HTML-comment markers (default)

Agent writes into .md body, wrapped in canonical markers:

```markdown
%%agent-{agent_name}-{section_id}-start%%

(agent-written content)

%%agent-{agent_name}-{section_id}-end%%
```

**When to use:** content needs to persist in .md (sync via Syncthing, visible on mobile, indexed by Obsidian search). Default for most agent writes ≤50 lines.

**Currently registered Pattern A sections in `Projects/{title}.md`:**

| Section heading | Marker pair | Producer | Lives in template |
|---|---|---|---|
| `## 🗝️ Keyword Research & SEO` | `%%agent-zoro-keywords-start%% / -end%%` | Zoro `/zoro/keyword-research` endpoint | `shared/lifeos_templates/project_blog.md.tpl`, `project_youtube.md.tpl` |

### Pattern B — DOM-only render (exception)

Agent writes to its own persistence store, dataviewjs renders read-only view on the Project page. .md body untouched.

**When to use:** output too large for inline persistence (>50 lines, complex structures) OR naturally lives elsewhere (review page, store).

**Currently registered Pattern B sections in `Projects/{title}.md`:**

| Section heading | Producer | Lives where |
|---|---|---|
| `## 📚 KB Research` | Robin `/kb/research` endpoint | dataviewjs DOM only (cached in localStorage) |
| `## 🪄 Brook: Scaffold` | Brook synthesize via `/api/projects/{slug}/synthesize/run` (ADR-027 PR-6) | `BrookSynthesizeStore` JSON; review page `/projects/{slug}` |

### Human-only sections in `Projects/{title}.md` — agent code MUST NOT write

- `## 專案描述`
- `## 預期成果`
- `## Draft Outline` (修修's own outline, distinct from Brook scaffold's outline)
- `## 專案筆記`

Sections not in either Pattern A or human-only lists (e.g. `## 🎯 對應 OKR`, `## ✅ Tasks`, `## 📊 番茄統計`) are dataviewjs / plugin auto-render with no agent .md write.

### Audit

`scripts/vault_layout_audit.py` runs `grep -rE '%%agent-(\w+)-([\w-]+)-start%%' Projects/` and asserts:
1. Every discovered (agent, section) pair appears in this section's Pattern A table.
2. Every marker pair is balanced (`-start%%` has matching `-end%%`).
3. No marker pair sits inside a human-only section.

---

## 5. Repo vs vault boundary

| Lives in **vault** (`E:\Shosho LifeOS\`) | Lives in **repo** (`E:\nakama\`) |
|---|---|
| Daily Journal, Weekly review, Quarterly review | ADR, PRD, design doc, research note |
| OKR (yearly, quarterly) | Case study about Claude Code session, incident drill, postmortem |
| Project pages (writing project, video script project) | Architecture diagram, system context map |
| Robin / Nami / Brook 為了幫我做事而寫的東西（KB, briefs, reports, digests） | Robin / Nami / Brook **本身**的開發紀錄、test、CI artifact |
| 我的健康 tracking、運動紀錄、食量紀錄 | Nakama system health check raw data, Franky weekly digest raw input |
| `KB/Annotations/`, `KB/Wiki/...` | `agents/`, `shared/`, `thousand_sunny/`, `docs/`, `tests/` |

**Heuristic:** 「如果把 Nakama 整個砍掉重寫，這個檔案還有意義嗎？」 有 = vault；沒有 = repo.

---

## 6. Maintenance discipline

### α — PR discipline (mandatory)

Any PR that does one of:
- Creates / renames / deletes a vault folder
- Changes an agent's vault write path in code
- Adds a new agent that writes to vault
- Changes marker convention (§4)
- Changes ADR-028 / this doc's authoritative claims

MUST update this doc in the same PR. Enforced via:
- A line in `E:\nakama\CLAUDE.md` workflow rules
- (Future) `.github/PULL_REQUEST_TEMPLATE.md` checkbox

### β — Monthly audit (automated)

`scripts/vault_layout_audit.py` runs monthly (cron via Franky), checks:
1. **Folder diff** — actual vault folder tree vs §2 declared structure. Reports orphan folders (in vault but not in §2) and missing folders (in §2 but not in vault).
2. **Code path diff** — `grep` `agents/`, `shared/`, `gateway/`, `thousand_sunny/` for vault path literals (`KB/`, `Inbox/`, `Projects/`, `TaskNotes/`, `Annotations/`, `AgentOutputs/`, etc), verify each appears in §3 producer/consumer matrix.
3. **Marker convention** — see §4 audit rules.
4. **Drift verification** — for each entry in §7, check whether status is still accurate.

Audit output appended to `AgentOutputs/franky/weekly/{period}.md` under a "Vault Audit" section.

---

## 7. Known drift

Drift entries record where this doc and code/vault disagree. Each entry has a status: `[待修]` (open issue), `[已接受]` (accepted; not a bug), `[已修]` (resolved; entry kept for history).

### D1 — Concept dispatcher unreachable from textbook-ingest Phase B `[待修]`

**Claim:** `KB/Wiki/Concepts/` writes go through the 4-action dispatcher (ADR-011 §3.3) in `shared/kb_writer.upsert_concept_page`.
**Reality:** textbook-ingest Phase B writes Concept stubs directly, bypassing `upsert_concept_page`. ADR-011's dedup guarantees do not apply to textbook-ingested concepts.
**Owner:** 待 textbook-ingest v4 redesign or ADR-011 amendment to allow stub-bypass with explicit annotation.
**Tracking:** TBD (open GH issue when scheduled).

### D2 — Entity v1 schema frozen `[已接受]`

**Claim:** `KB/Wiki/Entities/` schema is documented in ADR-011.
**Reality:** ADR-011 §3.1 explicitly defers Entity upgrade ("暫不 cover entity"). v1 schema codified in `shared/kb_writer.py:475`. No v2 design exists.
**Accepted because:** cross-source entity merge has not become a real workflow yet. v2 design will follow first real need.

### D3 — `KB/index.md` sync unmanaged `[待修]`

**Claim (vault CLAUDE.md historic):** "每次新增/更新 Wiki 頁面後必須同步更新 `KB/index.md`".
**Reality:** `agents/robin/ingest.py:583,600` appends index entries after concept writes but no transaction guarantees full coverage. textbook-ingest Phase B does not touch index. Orphan concepts won't auto-register.
**Owner:** 待 ingest pipeline refactor OR vault audit script (§6 β) extended to detect index coverage gaps.
**Tracking:** TBD.

### D-promotion-attachments — Inbox→KB attachment migration broken `[待修]`

**Claim (this doc §2 + §3):** When `Inbox/web/{slug}.md` is promoted to `KB/Raw/Articles/{slug}.md`, the associated `Inbox/web/attachments/{slug}/` is moved to `KB/Attachments/{slug}/` and markdown image refs are rewritten.
**Reality:** As of ADR-028 merge date, `shared/promotion_commit.py` has zero refs to `attach*` / `image` / `shutil`. Bug is latent because 修修 has never cleaned Inbox.
**Owner:** ADR-028 Phase 3 PR-Promotion-Attachment-Fix (post-merge).
**Tracking:** ADR-028 §Phase 3 implementation list.

---

## 8. Cross-references

- [ADR-028](decisions/ADR-028-vault-layout-consolidation.md) — the decision that produced this doc
- [ADR-001](decisions/ADR-001-agent-role-assignments.md) — per-agent role + write scope
- [ADR-011](decisions/ADR-011-textbook-ingest-v2.md) — Concept dispatcher + Entity v1 schema
- [ADR-017](decisions/ADR-017-annotation-kb-integration.md) — Annotation store schema
- [ADR-019](decisions/ADR-019-two-file-source-ingest-pattern.md) — Source ingest two-file pattern
- [ADR-020](decisions/ADR-020-textbook-ingest-v3-rewrite.md) — textbook ingest writes
- [ADR-021](decisions/ADR-021-annotation-substance-store-and-brook-synthesize.md) — Brook synthesize store (NOT in vault)
- [ADR-024](decisions/ADR-024-source-promotion-and-reading-context-package.md) — Source promotion + RCP
- [ADR-027](decisions/ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md) — Brook scope + Project frontmatter §7 + PR-6 button
- [`docs/schemas/project-frontmatter-nested.md`](schemas/project-frontmatter-nested.md) — Project page frontmatter schema (ADR-027 PR-6)
- [`CONTENT-PIPELINE.md`](../CONTENT-PIPELINE.md) — 7-stage content lifecycle (vault is the substrate)
- [`CLAUDE.md`](../CLAUDE.md) (repo) — workflow + memory rules
- Vault `CLAUDE.md` — 30-line pointer to this doc (post-ADR-028 Phase 3 PR-9)
