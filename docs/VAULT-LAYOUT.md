# Vault Layout — Canonical Reference

**Status:** **Target (post-ADR-028 Phase 3)** — current vault has drift relative to this doc; see §7 Known Drift
**Last updated:** 2026-05-19 (v2 post-panel)
**Authority:** Changes require PR review. Code that affects vault contracts MUST update this doc in the same PR.
**Audit:** Monthly via `scripts/vault_layout_audit.py` (cron via Franky, Phase 3 PR-C1). Discrepancies append to §7.

---

## 0. What is the vault?

The Obsidian vault at:
- Windows: `E:\Shosho LifeOS\`
- Mac: `/Users/shosho/Documents/Shosho LifeOS/`
- VPS: `/home/Shosho LifeOS/` (Syncthing-synced from desktop)

is 修修's personal **LifeOS** — implementation surface of an integrated *Designing Your Life* + *OKR* + *Bullet Journal* system. Also the contract surface that 7 Nakama agents read and write into.

**Vault scope** = 修修的生活/工作資訊的 develop / settle / collect / capture.
**Vault NOT scope** = Nakama-as-a-system dev artifacts (those live in repo `docs/` or `data/`).

Heuristic: 「如果把 Nakama 整個砍掉重寫，這個檔案還有意義嗎？」 有 = vault；沒有 = repo.

⚠️ **The heuristic requires human judgment.** Edge cases warrant explicit ADR discussion, not silent relocation. Current worked examples: Case study / incident drill / PRD draft moved to repo (ADR-028 §9); Franky weekly reports + dev-backlog moved to repo `data/agent_reports/franky/` (ADR-028 §4); Nami briefs / notes / research stay in vault.

---

## 1. Conceptual model

### Two complementary lenses (PARA + CODE)

Adopted as *conceptual* framework; **not** enforced as folder hierarchy (ADR-028 Strength 1).

**PARA — WHERE things live**
| PARA bucket | Vault representation |
|---|---|
| **P**rojects | `Projects/` |
| **A**reas | `area:` frontmatter (Work / Play / Love / Health per DYL). NO folder — deferred until OKR grill session |
| **R**esources | `KB/` (knowledge base) |
| **A**rchives | `status: archived` frontmatter. NO folder |

**CODE — HOW things flow**
| Verb | What happens | Typical folders |
|---|---|---|
| **C**apture | 即時、frictionless input | `Inbox/`, `Journals/Daily/`, `KB/Annotations/` |
| **O**rganize | 主動歸位 | `Inbox/` → `KB/Raw/` promotion; `Projects/` bootstrap |
| **D**istill | 沉澱、結晶 | `KB/Wiki/{Sources,Concepts,Entities}/`, `_alias_map.md` |
| **E**xpress | 用沉澱物產出 | `Projects/` output, articles, OKR progress |

### Three-tier ownership (red lines)

Every page belongs to **exactly one tier**:

🔒 **Human only** — AI 不可寫 content. AI 可給 input/suggestion，文字必須由人輸入。
🤖 **Agent only** — 修修 不該手寫；agent 的工作區。
🟡 **協作** — Section-level division within one page. Today the only collab page type is `Projects/{title}.md`.

### Four pillars (DYL 人生儀錶板)

Work / Play / Love / Health — carried in `area:` frontmatter, **not** folder structure. Health tracking via OKR + TaskNotes (out of scope; future OKR grill).

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
├── Inbox/                🤖 Capture cache (ephemeral; OK to clean — but only AFTER promotion attachment fix lands)
│   ├── web/                News Coo extension (FSA root pick here)
│   │   ├── *.md
│   │   └── attachments/{slug}/    News Coo image fetcher
│   ├── books/              .epub (textbook-ingest source; absolute-path invocation)
│   ├── papers/             .pdf (reserved, future paper ingest)
│   └── snapshots/          .mhtml (reserved, future web-snapshot ingest)
│
├── KB/                   🤖 Agent only (R in PARA = Resources)
│   ├── Raw/                原始證據層 (禁改 body)
│   │   ├── Articles/         Robin ingest from Inbox/web
│   │   ├── Papers/           Robin ingest (manual `Inbox/papers/` flow)
│   │   ├── Books/            textbook-ingest Phase 0
│   │   ├── Podcasts/         (reserved, currently empty)
│   │   ├── Videos/           (reserved)
│   │   ├── Repos/            (reserved)
│   │   └── Data/             (reserved)
│   │
│   ├── Wiki/               AI 加工層 (free write)
│   │   ├── Sources/          Robin promotion (ADR-024) + textbook-ingest + PubMed digest (pubmed-{pmid}.md)
│   │   │   └── Books/          textbook-ingest Phase 1 chapter pages
│   │   ├── Concepts/         textbook-ingest + annotation_merger + 4-action dispatcher (ADR-011 §3.3 — see drift D1)
│   │   ├── Entities/         textbook-ingest book entities (v1 schema; see drift D2)
│   │   │   └── Books/          book entity index pages
│   │   ├── Digests/          Daily digests
│   │   │   ├── AI/             Nami daily AI news
│   │   │   └── PubMed/         agents/robin/pubmed_digest.py daily digest
│   │   └── _alias_map.md     L1 alias index — see §4 lifecycle subsection
│   │
│   ├── Annotations/{slug}.md   Robin Reader `POST /save-annotations` (ADR-017 v1/v2 + v3 code addition)
│   │
│   ├── Attachments/{source-slug}/   flat per-source; News Coo (post-Phase A migration) + pubmed digest
│   │
│   ├── index.md            Robin ingest append (drift D3 — no coverage check)
│   └── log.md              Robin ingest append-only
│
├── Attachments/          🔄 mixed
│   ├── Books/{book-id}/ch{n}/    textbook-ingest Phase 0 figure binaries
│   ├── journal-pasted/{YYYY-MM}/  Obsidian default attachment path
│   ├── projects/{slug}/          🤖 Bridge thumbnail commit (ADR-033 PR4)
│   │   ├── thumbnail.png           chosen final (Obsidian wikilink target)
│   │   └── _archive/{old_ts}.png   replaced versions on re-commit
│   └── cutouts/                  🤖 thumbnail pipeline assets (ADR-033)
│       ├── shosho/{emotion}/{n}.png            修修 selfie library (B1, YouTube host) — emotion ∈ closed enum
│       ├── podcast/{ep_slug}/                  per-episode funnel output (D8/D9)
│       │   ├── host_v{n}.png
│       │   └── guest_v{n}.png
│       └── reference/                          修修 manual dump for LLM few-shot
│           ├── youtube/{mine,peers}/*.png
│           └── podcast/{mine,peers}/*.png
│
├── AgentOutputs/         🤖 Agent task outputs (vault — durability passes heuristic §0)
│   ├── nami/
│   │   ├── briefs/         Morning Brief (config.yaml.agents.nami.brief_path)
│   │   ├── notes/          ad-hoc Nami notes
│   │   └── research/       Nami research handler output
│   └── brook/
│       └── seo-audit/      Brook SEO audit task outputs (per ADR-027)
│
├── Templates/            🔒 Human only (Templater plugin owns)
└── Scripts/              🔒 Human only (Templater user scripts + nakama-config.md)
```

**Repo-side companion** (`E:\nakama\data\agent_reports\` — durability heuristic §0 fails, lives in repo):
```
data/agent_reports/franky/
├── weekly/             YYYY-WW.md (from agents/franky/reporter.py)
├── vault-audit/        monthly audit script output (Phase 3 PR-C1)
└── dev-backlog.md      人寫, Franky reads
```

**Notable absences** (intentionally not in vault):
- `Schemas/` — declared in legacy CLAUDE.md, never had a producer; deleted (ADR-028 §3).
- `Files/` — flat image dumping ground; migrated (ADR-028 §8). Until Phase B lands, see drift D-files-pending.
- `KB/Wiki/Outputs/`, `Syntheses/`, `Comparisons/` — orphan folders, deleted (ADR-028 §3).
- `Nami/`, `AgentBriefs/`, `AgentReports/` — consolidated to `AgentOutputs/` + repo `data/agent_reports/` (ADR-028 §4).
- `Case Studies/`, `Incidents/` — Nakama dev artifacts moved to repo `docs/case-studies/` + `docs/incidents/` (ADR-028 §9).

---

## 3. Producer / Consumer matrix

| Path | Tier | Producer (code path) | Consumer | Schema authority |
|---|---|---|---|---|
| `Journals/Daily/` | 🔒 | 修修 via Templater `tpl-daily-journal.md` | 修修 reads; agents may read | tpl-daily-journal |
| `Journals/{Weekly,Quarterly,Yearly}/` | 🔒 | 修修 via Templater | 修修 | tpl-weekly-journal, etc |
| `OKRs/` | 🔒 | 修修 via tpl-okr-{annual,quarterly} | 修修 | tpl-okr-* |
| `Projects/{title}.md` | 🟡 | Bootstrap `scripts/run_project_bootstrap.py` + `shared/lifeos_writer.py:render_project` (Tier C strip, ADR-031); Bridge Web mutations `thousand_sunny/routers/bridge_projects.py` | Brook synthesize, Bridge Web `/bridge/projects/{slug}`, Obsidian render (prose-only post-Tier C) | `shared/lifeos_writer.py` + `docs/schemas/project-frontmatter-nested.md` (ADR-031 γ schema) |
| `TaskNotes/Tasks/` | 🟡 | Bootstrap + `gateway/handlers/nami.py:1002` (`write_page` to TaskNotes) + TaskNotes plugin | TaskNotes plugin queries | `shared/lifeos_writer.py:render_task` |
| `TaskNotes/{Archive,Views}/` | 🤖 | TaskNotes plugin | TaskNotes plugin | Plugin config |
| `Dashboards/` | 🔒 | 修修 (dataviewjs queries) | Obsidian render | — |
| `Inbox/web/*.md` | 🤖 | News Coo `extensions/news-coo/src/vault/writer.ts` | Robin Reader `/read` (`thousand_sunny/routers/robin.py:135 _get_inbox_files`); Robin ingest | News Coo frontmatter (`news_coo_version: 1`, `source_url`, `captured_at`) |
| `Inbox/web/attachments/{slug}/` | 🤖 | News Coo `extensions/news-coo/src/vault/imageFetcher.ts:122,165` | sibling .md image refs | binary |
| `Inbox/books/` | 🤖 | 修修 drops .epub | textbook-ingest skill (absolute path) | — |
| `Inbox/papers/` | 🤖 | 修修 drops .pdf | (reserved, future paper ingest) | — |
| `Inbox/snapshots/` | 🤖 | 修修 drops .mhtml | (reserved, future mhtml ingest) | — |
| `KB/Raw/Articles/{slug}.md` | 🤖 | Robin ingest from `Inbox/web/` | KB consumers, RCP builder | ADR-019 |
| `KB/Raw/Papers/{slug}.md` | 🤖 | Robin manual ingest from `Inbox/papers/` | KB, Brook synthesize | ADR-019 |
| `KB/Raw/Books/{book-id}.md` | 🤖 | textbook-ingest Phase 0 (lossless EPUB→md via ebooklib) | textbook-ingest Phase 1 LLM | ADR-020 §Phase 0 |
| `KB/Wiki/Sources/{slug}.md` | 🤖 | Robin promotion `shared/promotion_commit.py` + textbook-ingest | Brook synthesize, RCP, `/writing-assist/` | ADR-024 + `shared/promotion_renderer.py` |
| `KB/Wiki/Sources/pubmed-{pmid}.md` | 🤖 | `agents/robin/pubmed_digest.py:476-477` (NOT KB/Raw/Papers as previously documented) | Brook synthesize, KB consumers | PubMed Source schema |
| `KB/Wiki/Sources/Books/{book-id}/ch{n}.md` | 🤖 | textbook-ingest Phase 1 via `shared/kb_writer.write_source_page` | Brook context_bridge, Reader | ADR-020 §Phase 1 |
| `KB/Wiki/Sources/Books/{book-id}/digest.md` | 🤖 | `agents/robin/book_digest_writer.py:211` | Brook synthesize, Reader | ADR-020 §Phase 2 digest |
| `KB/Wiki/Sources/Books/{book-id}/notes.md` | 🤖 | `agents/robin/book_notes_writer.py:26` | 修修 reader, kb_search | ADR-020 §Phase 2 notes |
| `KB/Wiki/Sources/{slug}/whole.md` | 🤖 | `shared/source_map_builder.py:557` (single-block whole source map) | Reader, Brook synthesize | source-map schema |
| `KB/Wiki/Sources/{slug}/index.md` | 🤖 | `shared/source_map_builder.py:598` (multi-block index) | source-map navigation | source-map schema |
| `KB/Wiki/Sources/{slug}/{chapter_ref}.md` | 🤖 | `shared/source_map_builder.py:628` (per-chapter block) | Reader, Brook synthesize | source-map schema |
| `KB/Wiki/Concepts/{slug}.md` | 🤖 | `shared/kb_writer.upsert_concept_page` (4-action dispatcher, ADR-011 §3.3 — but textbook-ingest Phase B bypasses, drift D1) + `agents/robin/annotation_merger.py` | kb_search, Brook synthesize, RCP | ADR-011 §3.3 v2 schema |
| `KB/Wiki/Entities/{slug}.md` | 🤖 | textbook-ingest book entity writer (v1 schema, drift D2) | kb_search | ADR-011 §3.1 v1 (frozen) |
| `KB/Wiki/Entities/Books/{book-id}.md` | 🤖 | textbook-ingest book entity index | 修修, kb_search | ADR-020 §Phase 2 |
| `KB/Wiki/Digests/AI/{YYYY-MM-DD}.md` | 🤖 | Nami daily AI digest | 修修 | — |
| `KB/Wiki/Digests/PubMed/{YYYY-MM-DD}.md` | 🤖 | `agents/robin/pubmed_digest.py:521-522` | 修修, Brook synthesize | — |
| `KB/Wiki/_alias_map.md` | 🤖 | `shared/concept_classifier.py` + `scripts/run_s8_preflight.py` (staging patches) | textbook-ingest re-evaluation | ADR-020 v3 maturity model — see §4 lifecycle |
| `KB/Annotations/{slug}.md` | 🤖 | `shared/annotation_store.AnnotationStore.save` ← Robin Reader `POST /save-annotations` | Reader render, `annotation_merger`, RCP builder | ADR-017 (v1/v2) + v3 code addition |
| `KB/Attachments/{source-slug}/` | 🤖 | News Coo image fetcher (post-Phase A migration) + `agents/robin/pubmed_digest.py:210` | source-page image refs | binary |
| `KB/index.md` | 🤖 | `agents/robin/ingest.py:583,600` + `pubmed_digest.py:539` (append) | 修修 manual reads | drift D3: no enforcement |
| `KB/log.md` | 🤖 | `agents/robin/ingest.py` + `pubmed_digest.py:527` (append-only) | 修修 manual reads | append-only |
| `Attachments/Books/{book-id}/ch{n}/` | 🤖 | textbook-ingest Phase 0 figure extractor | book chapter source pages | ADR-020 §Phase 0 |
| `Attachments/journal-pasted/{YYYY-MM}/` | 👤+plugin | Obsidian paste (config `app.json attachmentFolderPath`) | Daily Journal markdown refs | binary |
| `Attachments/projects/{slug}/thumbnail.png` | 🤖 | `thousand_sunny/routers/bridge_projects.py` thumbnail commit endpoint (ADR-033 PR4) | Obsidian preview, frontmatter wikilink (`thumbnail` field) | binary |
| `Attachments/projects/{slug}/_archive/{ts}.png` | 🤖 | Same endpoint, rotation on re-commit | audit only | binary |
| `Attachments/cutouts/shosho/{emotion}/{n}.png` | 🤖 | `scripts/import_shosho_cutouts.py` (PR4 one-off) + u2net via hyperframes-media | `shared/cutout_library.pick_youtube_host` | binary |
| `Attachments/cutouts/podcast/{ep_slug}/{host,guest}_v{n}.png` | 🤖 | `shared/thumbnail_funnel.py` confirm step + u2net | `shared/cutout_library.pick_podcast_{host,guest}` | binary |
| `Attachments/cutouts/reference/{youtube,podcast}/{mine,peers}/` | 👤 | 修修 manual dump | Brainstorm LLM few-shot (Sonnet 4.6 vision) | binary |
| `AgentOutputs/nami/briefs/` | 🤖 | Nami Morning Brief handler (post-ADR-028) | 修修 | `agents/nami/...` |
| `AgentOutputs/nami/notes/` | 🤖 | `gateway/handlers/nami.py:458-525,:1773` (Nami write_vault_note); whitelisted by `shared/vault_rules.py:14-20` | Nami handler reads for context | — |
| `AgentOutputs/nami/research/` | 🤖 | Nami research handler | 修修 | — |
| `AgentOutputs/brook/seo-audit/{YYYY-MM-DD}/` | 🤖 | Brook SEO audit + enrich runners (ADR-027) | 修修 | — |
| (repo) `data/agent_reports/franky/weekly/` | 🤖 | `agents/franky/reporter.py:277` (post-path-migration) | 修修, Franky weekly digest | Franky weekly format |
| (repo) `data/agent_reports/franky/dev-backlog.md` | 👤+🤖 | 修修 writes; `agents/franky/agent.py:33` reads | Franky weekly digest input | — |
| (repo) `data/agent_reports/franky/vault-audit/` | 🤖 | `scripts/vault_layout_audit.py` (Phase 3 PR-C1) | 修修, Franky weekly | — |
| `Templates/` | 🔒 | 修修 | Templater plugin | — |
| `Scripts/nakama-config.md` | 🔒 | 修修 | dataviewjs (reads `robin_url`, `robin_key`) | — |

**Reader UI scope contract:** `thousand_sunny/routers/robin.py:135 _get_inbox_files` reads ONE folder (`inbox.iterdir()`, no recursion). `config.yaml.agents.robin.inbox_path = Inbox/web` (post-Phase A). Reader UI lists only `Inbox/web/*.md`. Books/papers/snapshots in sibling Inbox subfolders are invoked by their own ingest tools via absolute path; intentionally not in Reader UI scope.

---

## 4. Marker convention for collab pages

In-scope: `Projects/{title}.md`. Future collab page types must be added here.

### Pattern A — Agent-written sections (default)

Agent writes into .md body wrapped in canonical positional markers:

```markdown
%%agent-{agent_name}-{section_id}-start%%
<!-- agent-meta: {"schema_version": 1, "updated_at": "<ISO8601>"} -->   (optional)

(agent-written content; persists in .md, syncs via Syncthing, mobile-visible, Obsidian-search-indexed)

%%agent-{agent_name}-{section_id}-end%%
```

**When to use:** content needs to persist in .md (≤50 lines typical).

**Currently registered Pattern A sections in `Projects/{title}.md`:**

> **Tier C scope note (ADR-031 PR1):** youtube / podcast templates strip these markers per D3; blog template retains them (D3 D9.c v2 only mandates youtube + podcast strip). Bridge Web `/bridge/projects/{slug}#title-thumbnail` is the canonical interactive surface; legacy md-body marker path retained only for blog projects that would still benefit from in-md persistence.

| Section heading | Marker pair | Producer | Lives in template |
|---|---|---|---|
| `## 🗝️ Keyword Research & SEO` | `%%agent-zoro-keywords-start%% / -end%%` | Zoro `/zoro/keyword-research` endpoint (md-body write path) | `shared/lifeos_templates/project_blog.md.tpl` (blog only post-ADR-031; youtube/podcast stripped per D3) |

### Pattern B — DOM-only render (exception)

Agent writes to its own persistence store; dataviewjs renders read-only view. .md body untouched.

**When to use:** output too large for inline persistence (>50 lines) OR naturally lives elsewhere.

**Currently registered Pattern B sections in `Projects/{title}.md`:**

| Section heading | Producer | Lives where |
|---|---|---|
| `## 📚 KB Research` | Robin `/kb/research` endpoint | dataviewjs DOM (cached in localStorage) |
| `## 🪄 Brook: Scaffold` | Brook synthesize `/api/projects/{slug}/synthesize/run` (ADR-027 PR-6) | `BrookSynthesizeStore` JSON; review page `/projects/{slug}` |

### Human-only section declaration (HTML-comment marker is the contract authority)

**The contract is the marker, NOT the heading text.** Heading text is human-facing only; 修修 can edit headings (translation, emoji, synonym) freely without breaking the audit.

```markdown
## 專案描述 <!-- vault:human-only-section -->

(content; agents are forbidden from writing here)
```

`scripts/vault_layout_audit.py` (Phase 3 PR-C1 full impl) MUST audit by marker presence, not heading text.

**Currently registered human-only sections in `Projects/{title}.md`** (the marker is what enforces; heading text is the human label):

- `<!-- vault:human-only-section -->` after `## 專案描述`
- After `## 預期成果`
- After `## Draft Outline` (修修's outline; NOT Brook scaffold's outline)
- After `## 專案筆記`

Sections not in either Pattern A or human-only list (e.g. `## 🎯 對應 OKR`, `## ✅ Tasks`, `## 📊 番茄統計`) are dataviewjs/plugin auto-render with no agent .md write.

### Audit (Phase 3 PR-C1 full impl)

`scripts/vault_layout_audit.py` runs:
1. `grep -rE '%%agent-(\w+)-([\w-]+)-start%%' Projects/` — assert each (agent, section) pair appears in Pattern A table above
2. `grep -rE '%%agent-(\w+)-([\w-]+)-(start|end)%%' Projects/` — assert balanced markers
3. `grep -rF '<!-- vault:human-only-section -->' Projects/` — for each occurrence, assert no agent marker pair appears in that section
4. Unicode-normalize all path comparisons via `unicodedata.normalize('NFC', path)` (cross-platform safety; see drift D-unicode-norm)

### `_alias_map.md` lifecycle (ADR-020 v3 L1 alias index)

**Purpose:** Synonym resolution layer for low-value mentions that don't warrant their own Concept page (e.g. `Vitamin C` → `Ascorbic Acid`).

**Producers:**
- `shared/concept_classifier.py` — primary writer (classifies concepts into L1/L2/L3 maturity tiers; L1 lands in alias map)
- `scripts/run_s8_preflight.py` — staging patches during textbook-ingest v3 staging phase

**Schema:** Markdown table — `term | source` (canonical alias `term`, vault-relative wikilink to first-seen Source).

**Conflict resolution:** When two sources suggest the same alias for different concepts, the **first source wins**. Subsequent conflicting suggestions are logged to `data/agent_reports/franky/vault-audit/{period}.md` with `category: alias-collision` for manual review.

**Removal protocol:** Aliases removed via manual edit only (human-curated). Agents must not silently drop aliases — if an alias is found to be incorrect at L2/L3 promotion time, the promotion code logs a `category: alias-incorrect-flagged` warning and leaves the alias intact for human review.

**Re-evaluation:** When a previously-L1 term is mentioned by a second source with high-value signal (section heading, bold definition, ≥2 paragraphs), `shared/concept_classifier.py` promotes it to L2 (stub Concept page) and removes the alias row.

---

## 5. Repo vs vault boundary

| Lives in **vault** (`E:\Shosho LifeOS\`) | Lives in **repo** (`E:\nakama\`) |
|---|---|
| Daily Journal, Weekly review, Quarterly review | ADR, PRD, design doc, research note |
| OKR (yearly, quarterly) | Case study about Claude Code session, incident drill, postmortem |
| Project pages (writing project, video script project) | Architecture diagram, system context map |
| Robin / Nami / Brook **為了幫我做事** 而寫的東西（KB, briefs, notes, research, SEO audit outputs） | Robin / Nami / Brook **本身**的開發紀錄、test、CI artifact, Franky system health reports, vault audits, dev-backlog |
| 我的健康 tracking、運動紀錄、食量紀錄 | Nakama system health check raw data |
| `KB/Annotations/`, `KB/Wiki/...`, `AgentOutputs/{nami,brook}/` | `agents/`, `shared/`, `thousand_sunny/`, `docs/`, `tests/`, `data/agent_reports/franky/` |

**Heuristic:** 「如果把 Nakama 整個砍掉重寫，這個檔案還有意義嗎？」 有 = vault；沒有 = repo.

⚠️ **The heuristic is a recurring discussion, not an algorithm.** When edge cases arise, raise them in ADR amendment. Current worked examples (ADR-028 §4 + §9):
- ✅ vault: Nami briefs (修修 reads them as life context), Brook SEO audit (content quality artifacts)
- ✅ repo: Franky weekly system reports (purely system-side), dev-backlog (Nakama TODO), Case Studies / Incidents / PRDs (Nakama dev memos)

---

## 6. Maintenance discipline

### α — PR discipline (mandatory)

Any PR that does one of:
- Creates / renames / deletes a vault folder
- Changes an agent's vault write path in code
- Adds a new agent that writes to vault
- Changes marker convention (§4)
- Changes ADR-028 / this doc's authoritative claims

MUST update this doc in the same PR. Enforced via line in `E:\nakama\CLAUDE.md` workflow rules.

### β — Monthly audit (automated, Phase 3 PR-C1)

`scripts/vault_layout_audit.py` runs monthly via Franky cron:
1. **Folder diff** — actual vault tree vs §2 declared structure
2. **Code path diff** — `grep agents/ shared/ gateway/ thousand_sunny/` for vault path literals; verify §3 producer/consumer matrix coverage
3. **Marker convention** — §4 audit rules
4. **Drift verification** — for each §7 entry, check whether status is still accurate

**Wiring (PR-C1):** audit runs inside `FrankyAgent._run_vault_audit()` (`agents/franky/agent.py`) before `reporter.write(report)`; the rendered markdown is appended to `report.body_markdown`, so each weekly digest at `data/agent_reports/franky/weekly/{period}.md` carries a `## Vault Audit` section. Error-severity findings are logged via `kb_log` for monitoring.

The separate `data/agent_reports/franky/vault-audit/{period}.md` path declared in §3 is reserved for ad-hoc audit runs invoked via `python -m scripts.vault_layout_audit --append-to <path>`; the monthly cron wiring uses the weekly digest body instead.

---

## 7. Known drift

Drift entries record where this doc and code/vault disagree. Each has a status: `[待修]` (open), `[已接受]` (won't fix), `[已修]` (resolved; entry kept for history). Phase 3 migration moves several `[待修]` items to `[已修]`.

### Pre-Phase 3 migration drift (resolved during Phase B/C)

#### D-files-pending — `Files/` migrated `[已修]`

**Claim:** §2 lists `Files/` as absent.
**Reality at flip:** `Files/` deleted (recycle-bin) post-Phase B; Category A 35 figs moved to `KB/Attachments/{slug}/` + Category B 34 figs moved to `Attachments/journal-pasted/{YYYY-MM}/`; refs rewritten in 9 source markdowns + 7 Journal markdowns.
**Resolution:** Phase B sub-ops #10/#11/#12 — see `data/migrations/2026-05-20-vault-cleanup/manifest.json` (PR #673, ADR-028 §8).

#### D-agentoutputs-pending — `AgentOutputs/` consolidation done `[已修]`

**Claim:** §2 + §3 list `AgentOutputs/{nami,brook}/` (vault) + `data/agent_reports/franky/` (repo).
**Reality at flip:** Vault has `AgentOutputs/nami/{briefs,notes,research}/` + `AgentOutputs/brook/seo-audit/`. Repo has `data/agent_reports/franky/{weekly/,dev-backlog.md}`. Legacy `Nami/` / `AgentBriefs/` / `AgentReports/` deleted (recycle-bin).
**Resolution:** PR #621 (PR-A2 codebase paths) + PR #654 (PR-B1 safe moves) + Phase B sub-ops #1-3.

#### D-inbox-pending — Inbox restructured `[已修]`

**Claim:** §2 lists `Inbox/{web,books,papers,snapshots}/`.
**Reality at flip:** All four subfolders exist; legacy `Inbox/kb/` deleted; News Coo FSA re-picked to `Inbox/web`; `config.yaml.agents.robin.inbox_path = Inbox/web` (per PR #630).
**Resolution:** PR #629 (PR-A3 markers) + PR #630 (config flip) + Phase B sub-op #8 (vault moves).

### Persistent drift (not Phase 3 scope)

#### D1 — Concept dispatcher unreachable from textbook-ingest Phase B `[待修]`

**Claim:** `KB/Wiki/Concepts/` writes go through `shared/kb_writer.upsert_concept_page` 4-action dispatcher (ADR-011 §3.3).
**Reality:** textbook-ingest Phase B writes Concept stubs directly, bypassing the dispatcher. ADR-011 dedup guarantees do not apply to textbook-ingested concepts.
**Owner:** Future textbook-ingest v4 or ADR-011 amendment.

#### D2 — Entity v1 schema frozen `[已接受]`

**Claim:** `KB/Wiki/Entities/` schema documented in ADR-011.
**Reality:** ADR-011 §3.1 explicitly defers Entity upgrade. v1 schema codified in `shared/kb_writer.py` (Entity v1 writer, NOT line 475 which is `_append_to_section`). No v2 design.
**Accepted:** Cross-source entity merge has not become a real workflow. v2 design follows first real need.

#### D3 — `KB/index.md` sync unmanaged `[待修]`

**Claim (legacy vault CLAUDE.md):** "每次新增/更新 Wiki 頁面後必須同步更新".
**Reality:** `agents/robin/ingest.py:583,600` appends entries after concept writes but no coverage check. textbook-ingest Phase B doesn't touch index.
**Owner:** Future ingest pipeline refactor OR audit script `--index-coverage` extension.

#### D-promotion-attachments — Inbox→KB attachment migration fixed `[已修]`

**Claim:** When `Inbox/web/{slug}.md` is promoted to `KB/Raw/Articles/{slug}.md`, attachments are moved + image refs rewritten.
**Reality at flip:** `shared/promotion_commit.py` now performs attachment migration (move `Inbox/web/attachments/{slug}/*` → `KB/Attachments/{slug}/` + rewrite image refs); `agents/robin/agent.py` legacy ingest path patched in tandem.
**Resolution:** PR #616 (PR-A1, re-sequenced first per Codex audit §4).

#### D-unicode-norm — Cross-platform Unicode normalization risk `[已接受]`

**Risk:** macOS uses NFD (decomposed) for filenames; Windows/Linux/VPS use NFC (composed). CJK filenames (e.g. `專案.md`) may differ at byte level across devices.
**Mitigation:** Syncthing handles most cases; scripts that compare paths byte-wise MUST call `unicodedata.normalize('NFC', path)` before comparison. Audit script (Phase 3 PR-C1) enforces this in folder/code-path diff.
**Accepted:** Operational risk, mitigated at audit layer; full enforcement at write boundary deferred until first observed corruption.

#### D-audit-stub — `vault_layout_audit.py` was a stub `[已修]`

**Resolution:** PR-C1 (`feat(adr-028): vault_layout_audit.py full implementation`) replaced all four `audit_*` stubs with real implementations + 23 test cases covering each dimension and end-to-end fixture vault. The `STUB IMPLEMENTATION` sentinel is gone; the audit is wired into Franky weekly digest via `FrankyAgent._run_vault_audit()`. See §6 β for the wiring.

---

## 8. Cross-references

- [ADR-028](decisions/ADR-028-vault-layout-consolidation.md) — the decision producing this doc
- [ADR-001](decisions/ADR-001-agent-role-assignments.md) — per-agent role + write scope
- [ADR-011](decisions/ADR-011-textbook-ingest-v2.md) — Concept dispatcher + Entity v1 schema (D1, D2)
- [ADR-017](decisions/ADR-017-annotation-kb-integration.md) — Annotation store schema (v1/v2)
- [ADR-019](decisions/ADR-019-two-file-source-ingest-pattern.md) — Source ingest two-file pattern
- [ADR-020](decisions/ADR-020-textbook-ingest-v3-rewrite.md) — textbook ingest writes + `_alias_map.md`
- [ADR-021](decisions/ADR-021-annotation-substance-store-and-brook-synthesize.md) — Brook synthesize store (NOT in vault)
- [ADR-024](decisions/ADR-024-source-promotion-and-reading-context-package.md) — Source promotion + RCP
- [ADR-027](decisions/ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md) — Brook scope (PR-6 added Project nested frontmatter schema)
- [`docs/schemas/project-frontmatter-nested.md`](schemas/project-frontmatter-nested.md) — Project page frontmatter schema (ADR-027 PR-6)
- [`CONTENT-PIPELINE.md`](../CONTENT-PIPELINE.md) — 7-stage content lifecycle (vault is the substrate)
- [`CLAUDE.md`](../CLAUDE.md) (repo) — workflow + memory rules
- Vault `CLAUDE.md` — 30-line pointer to this doc (post-Phase 3 PR-C1)
- [`docs/research/2026-05-19-codex-adr028-audit.md`](research/2026-05-19-codex-adr028-audit.md) — Codex panel audit
- [`docs/research/2026-05-19-gemini-adr028-audit.md`](research/2026-05-19-gemini-adr028-audit.md) — Gemini panel audit
- [`docs/research/2026-05-19-adr028-panel-integration-matrix.md`](research/2026-05-19-adr028-panel-integration-matrix.md) — 3-way adjudication

---

## 9. `Files/` migration mapping table (§migration-files-category-a)

For Phase 3 PR-B1 reference. Verified 2026-05-19 by basename grep across vault.

### Category A — 35 distinct paper figures → `KB/Attachments/{source-slug}/`

| Source markdown | Image files | New attachment dir |
|---|---|---|
| `Inbox/kb/Low back pain.md` | `1-s2.0-S0140673621007339-gr1-622b03e9.jpg`, `-gr2-f90a14a1.jpg` | `KB/Attachments/low-back-pain/` |
| `Inbox/kb/Cardiorespiratory fitness is associated with cognitive function...IGNITE study.md` | `F1.medium-5e1c8da6.gif`, `F2.medium-4b9aa3f0.gif`, `F3.medium-f8722a78.gif`, `image-8617dd05.jpg` | `KB/Attachments/cardiorespiratory-fitness-ignite-baseline/` |
| `Inbox/kb/The impact of creatine supplementation...aged...md` | `11556_2025_392_Fig{1-5}_HTML-*.png` | `KB/Attachments/creatine-resistance-training-aged/` |
| `KB/Raw/Papers/Ultra-processed foods and human health the main thesis and the evidence.md` | `1-s2.0-S014067362501565X-gr{1-5}-*.jpg` | `KB/Attachments/ultra-processed-foods-thesis/` |
| `KB/Raw/Papers/Efficacy and safety profile of oral creatine monohydrate...md` | `1-s2.0-S0924977X24007405-fx1-*.jpg`, `-gr1-*.jpg` | `KB/Attachments/oral-creatine-depression-rct/` |
| `KB/Raw/Papers/Effect of creatine supplementation on kidney function...md` | `12882_2025_4558_Fig{1-8,a}_HTML-*.png` | `KB/Attachments/creatine-kidney-function/` |
| `KB/Raw/Papers/International Society of Sports Nutrition position stand...md` | `12970_2017_173_Fig{1-4}_HTML-*.gif` | `KB/Attachments/issn-creatine-position-stand/` |
| `KB/Raw/Papers/Effects of creatine supplementation on memory...md` | `m_nuac064f{1,2,3a}-*.jpeg` | `KB/Attachments/creatine-memory-meta-analysis/` |
| `KB/Raw/Papers/Creatine and Cognition in Aging...md` | `m_nuaf135f1-*.jpeg` | `KB/Attachments/creatine-cognition-aging-review/` |

**Total: 35 distinct images across 9 source files (6 from `KB/Raw/Papers/`, 3 from `Inbox/kb/`).**

### Category B — 34 distinct Journal-pasted images → `Attachments/journal-pasted/{YYYY-MM}/`

| Source Journal | Image count | New attachment dir |
|---|---|---|
| `Journals/Daily/2024-03-09.md` | 13 `Pasted image 20240309*` + `Pasted image 20240310*` | `Attachments/journal-pasted/2024-03/` |
| `Journals/Daily/2024-03-31.md` | 9 `Pasted image 2024033{1}*` + `Pasted image 20240329*` | `Attachments/journal-pasted/2024-03/` |
| `Journals/Daily/2024-10-20.md` | 1 `febcdd94-616e-4fb1-af33-a50b07fd29ac_498x230.webp` | `Attachments/journal-pasted/2024-10/` |
| `Journals/Daily/2025-01-14.md` | 1 `Pasted image 20250114094954.png` | `Attachments/journal-pasted/2025-01/` |
| `Journals/Daily/2025-03-24.md` | 4 `Pasted image 20250324*` | `Attachments/journal-pasted/2025-03/` |
| `Journals/Daily/2025-03-31.md` | 1 `Pasted image 20250407173659.png` | `Attachments/journal-pasted/2025-04/` |
| `Journals/Daily/2025-05-27.md` | 5 `Pasted image 20250528*` | `Attachments/journal-pasted/2025-05/` |

**Total: 34 distinct images across 7 Journal files. Bucketing by image timestamp (paste time), not Journal date.**

Phase 3 PR-B1 migration script reads this table + image-timestamp metadata + executes per ADR-028 §8 verification protocol.

---

## 10. Open follow-ups (not in this doc's scope)

- Areas/ folder representation — OKR grill session
- Knowledge Gardener / decay detection (orphan concepts, stale pages)
- Primary media assets (video, audio, project files) — `§11-primary-media-assets`
- Entity v2 schema
- Concept dispatcher reachability (D1)
- `KB/index.md` coverage enforcement (D3)
- Content-addressable attachment storage (revisit if dup rate climbs)

---

## 11. Primary media assets (out of vault scope)

**Status:** Acknowledged blind spot in current layout; explicit out-of-scope decision.

The owner is a Health & Wellness content creator. Primary content includes:
- Raw video (YouTube production: `.mp4`, `.mov`, `.r3d`)
- Audio (podcast: `.wav`, `.mp3`, `.m4a`)
- Editor project files (`.prproj`, `.drp`, `.aep`)

**These are NOT stored in vault.** Vault is text-and-small-image-only by design (Syncthing replication budget; mobile sync friction). Primary media lives on external NAS / cloud storage / video editor's own asset management.

**Vault reference convention:** `Projects/{slug}/assets.md` (when created) contains pointers to external media — local paths, NAS share URLs, cloud links, asset DB IDs. Format is convention-only, not enforced — owner adapts per production pipeline.

**Future strategy:** Out of ADR-028 scope. Candidates for future ADR: standardize assets.md schema, add Robin or Brook integration for asset DB lookup, integrate with `agents/brook/script_video/` Manifest.
