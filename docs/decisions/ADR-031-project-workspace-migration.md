# ADR-031: Project Workspace Migration to Bridge — Tier C of Vault-as-Substrate

**Date:** 2026-05-24 (v1) · 2026-05-24 (v2 post-panel)
**Status:** Superseded by [ADR-068](ADR-068-project-as-long-running-thread.md)（2026-08-31 — Project 重定義為長期戰線，workspace 全面退役）
**Deciders:** shosho-chang, Claude Opus 4.7
**Related:** [ADR-017](ADR-017-annotation-kb-integration.md) (Annotation store), [ADR-021](ADR-021-annotation-substance-store-and-brook-synthesize.md) (Brook synthesize HITL), [ADR-027](ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md) (Brook scope; referenced nested schema but never delivered), [ADR-028](ADR-028-vault-layout-consolidation.md) (vault layout), [ADR-029](ADR-029-bridge-ia-restructure.md) (Bridge IA dual-axis), [ADR-030](ADR-030-vault-as-substrate-read-strategy.md) (D2/D3/D4 substrate routing), [`VAULT-LAYOUT.md`](../VAULT-LAYOUT.md), [`CONTENT-PIPELINE.md`](../../CONTENT-PIPELINE.md)

**Tier B (deferred):** LifeOS Dashboard mirror (daily-note task aggregation / weekly plan / OKR rollup) — uses the same D2 FS-direct + frontmatter-filter pattern; separate ADR when scope opens.

> **v2 audit trail (2026-05-24):** Multi-agent panel review (Claude + Codex GPT-5 + Gemini 2.5 Pro) ran on v1. Owner was asleep; panel results auto-integrated per [`feedback_panel_triangulated_judgment`](../../memory/claude/feedback_panel_triangulated_judgment.md).
>
> - **Codex** caught 5 factual errors in v1: (a) `蛋白質攝取量.md` is `content_type: research`, not youtube — D9.c slim would silently break it; (b) `Brook 風格訓練.md` is `type: agent-workspace` (filtered by type, not name heuristic); (c) hook math wrong (75-200 字/min × 30-60s = 37.5-200 字, not 500); (d) live `肌酸的妙用.md` uses legacy `%%KW-START%%` markers (migration must cover both families); (e) `~600 tasks vault-wide` claim is false (actual: 11 task files).
> - **Gemini** added 4 high-merit pushbacks: (a) Mandarin speaking rate is 200-300 字/min not 75-200 → 30-60s ≈ 100-250 字, soft cap ≤300 字; (b) Simplified-Chinese leakage hazard in Sonnet 4.6 — persona prompts must include Traditional Chinese instruction; (c) overwriting reviews on re-run destroys prompt-iteration data → reviews should be list-of-versioned-objects; (d) zero-shot persona prompts will produce shallow output — PR2 must ship with scoring rubric + few-shot example.
> - **Two architectural pushes adopted** with code changes: D9.c reverted to keep all 4 content_types; hook cap tightened to ≤300 字; migration script handles both marker families.
> - **Three pushes deferred to PR2**: persona prompt fortification (rubric + few-shot + zh-Hant guard), publish-anyway persisted decision, reviews list-of-versioned-objects schema (PR1 indexer is dual-shape tolerant to enable seamless PR2 flip).
> - **Two pushes rejected** with rationale: hybrid Obsidian markdown buttons (would re-introduce Syncthing reparse footprint Tier C is solving), Electron-bundled offline mode (out of scope; Obsidian read remains the offline escape hatch).
>
> Owner adjudicated 20 distinct push-back items: 11 adopted in v2 (8 with PR1 code changes, 3 with PR2 commitment), 3 deferred with documented follow-up, 2 rejected with rationale, 4 subsumed.
>
> Audits preserved at [`docs/research/2026-05-24-codex-adr031-audit.md`](../research/2026-05-24-codex-adr031-audit.md) and [`docs/research/2026-05-24-gemini-adr031-audit.md`](../research/2026-05-24-gemini-adr031-audit.md). Integration matrix at [`docs/research/2026-05-24-adr031-panel-integration-matrix.md`](../research/2026-05-24-adr031-panel-integration-matrix.md).

---

## Context

### 1. 修修's framing (verbatim, 2026-05-24 grill)

> 「Obsidian 的 Project 頁面已經卡到打不開——dataviewjs 加 base query 加 Zoro 的關鍵字研究 blob，再加上 Syncthing 一動就 reparse，整個閃退。我要加的功能（Branding components / Hook / Logic / Structure check）根本擠不進去。」
>
> 「Web UI 只是我跟 Agent 以及 Obsidian 的文件互動的一個介面，對吧？所有的文字還是會存成 Markdown file 存在 Obsidian 的 page，WebUI 只是把它提取出來便於互動。」
>
> 「我通常沒有一個 profile 可以參考，hook 都是我自己憑感覺寫出來的。我需要他扮演以下兩種角色：1. 社群媒體專家 2. 寫作教練，而不是根據我過去文章的形式。」
>
> 「不一定只專精在 Health and Wellness YouTube 頻道，我也寫過很多 book review 以及個人的成長故事。我只要他是一個最好的 storyteller 和 writing coach。」
>
> 「Hook 15 秒太少，大概 30–60 秒，這是我的習慣。」

### 2. Pain points (root cause analysis)

The Obsidian-rendered Project page (`Projects/{title}.md` opened in Obsidian desktop) is currently the primary work surface. It crashes regularly. Root cause is **read-side cost compounding**:

| Component | Cost source | Per-open cost |
|---|---|---|
| `## ✅ Tasks` base query ([`project_youtube.md.tpl:9-44`](../../shared/lifeos_templates/project_youtube.md.tpl)) | Bases plugin scans all `TaskNotes/Tasks/**` filtering by `projects.contains(link("__TITLE__"))` | O(N_tasks) — ~600 tasks vault-wide as of 2026-05-24 |
| `## 📊 番茄統計` dataviewjs ([same file:48-76](../../shared/lifeos_templates/project_youtube.md.tpl)) | Iterates all Task pages, sums `timeEntries[]` and `預估🍅` | O(N_tasks × avg_timeEntries) |
| `## 📚 KB Research` dataviewjs ([same file:88-164](../../shared/lifeos_templates/project_youtube.md.tpl)) | XHR to Robin `/kb/research`, renders results to DOM, caches in `localStorage` per-project | Render cost cheap; **localStorage cache + DOM append on every reparse** |
| `## 🗝️ Keyword Research & Title Ideas` dataviewjs ([same file:168-275](../../shared/lifeos_templates/project_youtube.md.tpl)) | XHR to Zoro `/zoro/keyword-research`, writes result back into md body between `%%agent-zoro-keywords-start/end%%` markers | **5-50 KB markdown blob persisted in md body**; Syncthing reparses on any change |
| Syncthing | Three-device sync (VPS + Win + Mac). Every md edit triggers reparse cascade. | Multiplier on all above |

**Two compounding factors:**

1. **Bases plugin** (post-Q2 strip rationale): Obsidian-rendered `base` queries hold the entire query result tree in memory across the file lifetime. The plugin was deprecated by Obsidian 2026 release; even when it loaded, latency was the primary failure mode (not crash, but 5–15s open time).
2. **dataviewjs reparse trigger**: any md edit to the same file → Obsidian re-runs all dataviewjs blocks. Zoro keyword research writing a 30KB blob into the body and committing it back via `app.vault.modify` triggers exactly this cascade.

修修's stated frustration ("已經卡到打不開") is structurally caused by these costs all happening in the page's own render lifecycle. Adding **more** checks (Branding / Hook / Logic / Structure) into the same surface compounds the failure.

### 3. Codebase inventory (verified 2026-05-24, NFC-normalized paths)

**Existing Project authoring:**

- [`shared/lifeos_writer.py`](../../shared/lifeos_writer.py) — Python writer `render_project` / `render_task` / `create_project_with_tasks`. Currently `ContentType = Literal["youtube", "blog", "research", "podcast"]` (line 21). `DEFAULT_TASKS` (line 28) has 4 entries; only `youtube` and `podcast` have real-world callers. Bootstrap entrypoint `scripts/run_project_bootstrap.py`.
- [`shared/lifeos_templates/project_youtube.md.tpl`](../../shared/lifeos_templates/project_youtube.md.tpl) (286 lines) — current template with the 4 expensive blocks documented above.
- [`shared/lifeos_templates/project_podcast.md.tpl`](../../shared/lifeos_templates/project_podcast.md.tpl) — present, similar structure.
- [`shared/lifeos_templates/project_blog.md.tpl`](../../shared/lifeos_templates/project_blog.md.tpl), `project_research.md.tpl` — present in repo; **zero callers in vault** (verified by `ls E:/Shosho LifeOS/Projects/` → 3 files: `Brook 風格訓練.md` / `肌酸的妙用.md` / `蛋白質攝取量.md`, all `content_type: youtube`).

**Existing vault Projects (`E:/Shosho LifeOS/Projects/`):**

| File | content_type | Status | Tier C migration |
|---|---|---|---|
| `肌酸的妙用.md` | youtube | active (gold standard, has `## 👄 One Sentence`) | Migrate (PR1 smoke seed) |
| `蛋白質攝取量.md` | youtube | active | Migrate (PR1 batch) |
| `Brook 風格訓練.md` | youtube | active (but **meta** — Brook training project, not a real content piece) | **Skip** (manual decision; migration script flags via `--skip-meta` flag) |

**Existing Bridge surfaces (`thousand_sunny/routers/`):**

- [`bridge.py`](../../thousand_sunny/routers/bridge.py) — main bridge router, `AGENT_ROSTER`, asset-versioning helper `_shosho_asset_version()`.
- [`bridge_digests.py`](../../thousand_sunny/routers/bridge_digests.py) — Tier A precedent (ADR-030, PR #690/#692). FS-direct read via [`shared/digest_indexer.py`](../../shared/digest_indexer.py); LLM-over-vault Q&A via `shared/digest_ask.py`. **Exact pattern Tier C inherits.**
- [`projects.py`](../../thousand_sunny/routers/projects.py) — **distinct namespace**: Brook synthesize HITL review (ADR-021, `/projects/{slug}`, NOT `/bridge/projects/{slug}`). Tier C does not collide.
- [`bridge_zoro.py`](../../thousand_sunny/routers/bridge_zoro.py) — existing Zoro keyword research surface (`/bridge/zoro/keyword-research`). Tier C **reuses this endpoint** as a backend (Web-driven; no in-md write).

**Chassis nav** ([`templates/bridge/_chassis_nav.html`](../../thousand_sunny/templates/bridge/_chassis_nav.html)):

- Current top-level slots (line 25 comment): `direct: bridge / drafts / digests / seo`; `fleet: <8 agents>`; `ops: cost / logs / memory / docs`. **5 top-level slots** today (Fleet ▾ / DRAFTS / DIGESTS / SEO / Ops ▾), not the 4 in ADR-029 v2 (DIGESTS was added post-ADR per PR #690).
- Tier C adds `projects` slug → **6 top-level slots** (Fleet ▾ / PROJECTS / DRAFTS / DIGESTS / SEO / Ops ▾). See D2 for placement rationale.
- Macro normalizes legacy slugs (`health → franky`, `repurpose → brook`, `vault → ''`). Tier C does **not** introduce additional aliases.

**Vault layout authority** ([`docs/VAULT-LAYOUT.md`](../VAULT-LAYOUT.md)):

- §3 line 162 references `docs/schemas/project-frontmatter-nested.md` — **404 today**. ADR-031 D9.f closes this with [`docs/schemas/project-frontmatter-nested.md`](../schemas/project-frontmatter-nested.md) (bundled in PR1).
- §4 marker convention currently registers `%%agent-zoro-keywords-start/end%%` for Pattern A. Tier C drops keyword research from md body entirely (Web reads endpoint directly); marker registry retained for other potential users.

**TaskNotes plugin contract:**

- Per-task md files at `TaskNotes/Tasks/{project} - {task}.md` (verified: 6 files for `肌酸的妙用` / `蛋白質攝取量`).
- Frontmatter: `預估🍅: int`, `timeEntries: list[{startTime, endTime}]` (ISO 8601 strings), `projects: ["[[{project}]]"]`, `status: to-do|in_progress|done`.
- Web must write `timeEntries[]` in the **same** shape so the plugin's `formula.實際🍅` (`(timeEntries.filter(value.endTime).map((number(date(value.endTime)) - number(date(value.startTime))) / 60000).reduce(acc+value,0) / 25).floor()`) continues to compute correctly Obsidian-side.

### 4. CONTENT-PIPELINE anchoring (per `feedback_pipeline_anchored_planning`)

Tier C is a **Stage 3 + 4 + 5 cross-cut**:

| Stage | What Tier C does |
|---|---|
| **3 Synthesis** | Brief tab (one_sentence) · Research tab (KB Research + Zoro keyword research, Web-rendered) · Hook tab (hook_text) — synthesize raw inputs into project-shaped intent |
| **4 Atomic Content** | Title&Thumbnail tab (CTR pair) · Script tab (body `## Script / Outline`) · Review tab (2 expert persona LLM, advisory) — produce the atomic deliverable |
| **5 Multi-channel** | Publish tab (set `status=published` + `publish_date`; emit YouTube description / podcast show notes as body sections) — channel-specific outputs |

Stages 1 (Discovery) and 2 (Reading) feed Tier C via existing Robin / Reader surfaces (vault `KB/`); Tier C does not re-implement them. Stages 6 (Publishing) and 7 (Monitoring) are owned by Usopp (under construction) and Franky respectively; Tier C exposes hand-off via Publish tab.

---

## Decision

### D1: Project workspace is a Bridge Web UI surface; vault md remains canonical SoT

**The Web UI is an interaction skin over vault md** — exactly per ADR-030's principle ("vault is the canonical knowledge substrate"). All durable state lives in `Projects/{title}.md`. The Web layer:

- **Reads** md via D2 FS-direct ([`shared/project_indexer.py`](../../shared/project_indexer.py), modeled on [`shared/digest_indexer.py`](../../shared/digest_indexer.py)) — no DB mirror, no FTS index, no cache by default.
- **Writes** md via a small mutation API: `update_frontmatter(slug, patch)` + `update_section(slug, marker, body)` + `append_timeentry(task_slug, entry)`. Each mutation is a file-level fsync; concurrent edits handled via `O_EXCL` rename pattern (atomic on Win/Mac/Linux).
- **Caches nothing** in the browser beyond ephemeral session state (active tab, draft input, timer position). Page reload re-fetches md.

**Obsidian remains a first-class read surface** (and remains the write surface for human-only sections). Mobile sync works because md is intact; Obsidian frontmatter editor works because frontmatter shape is YAML-valid.

**No Bridge → vault contract change beyond ADR-030 D4** — Tier C is purely a new Web reader/writer composed of existing primitives.

### D2: chassis-nav 5 → 6 top-level slots — PROJECTS added between Fleet ▾ and DRAFTS

```
[● NAKAMA]  Fleet ▾   PROJECTS   DRAFTS   DIGESTS   SEO   Ops ▾   [☀ toggle]
```

**Placement rationale:** PROJECTS is the **"do the work"** surface; DRAFTS / DIGESTS are downstream (review draft / read input). Left-to-right reading order in 修修's mental model: pick a project → do work → review drafts → consume digests → audit SEO. Fleet stays leftmost as the agent dimension.

**6 slots fits** on one row at standard viewport (1280px+); responsive collapse at narrow widths is out of scope (deferred per ADR-029 §Out of scope).

`nav_active='projects'` added to the chassis-nav macro slug list (no new dropdown — direct top-level item).

### D3: Strip all dataviewjs / base / `%%agent-zoro-keywords-*%%` from `project_youtube.md.tpl`

Template becomes a minimal scaffold:

```markdown
# __TITLE__

<!-- vault:human-only-section -->
## 專案描述


## 預期成果


## Draft Outline


## Script / Outline


## 專案筆記
```

(Identical structure for `project_podcast.md.tpl` — body sections stay; YouTube/Podcast-specific sections like `## 🎥 B-roll List` / `## 🎙️ Show Notes` move under content_type-specific Web tabs, **not** in the md template.)

**Why strip the dataviewjs blocks entirely** (Q2 = a, chosen):

- Removes the crash root cause definitively (no Bases plugin dependency; no per-file query trees in Obsidian memory; no Syncthing reparse cascade from Zoro keyword writes).
- Keyword research moves to Web Research tab — same Zoro endpoint, rendered server-side, **not persisted in md body**. Saves 5-50 KB per project md.
- KB Research moves to Web Research tab — same Robin endpoint, Web caches result in `state.db api_calls` audit log (per ADR-030 follow-up); no DOM persistence problem.
- Pomodoro statistics move to Web Pomodoro dock — recomputed from TaskNotes scan, cached to `pomodoro.{est,actual}_total` frontmatter on save.

**Obsidian-side regression scope:** previously, 修修 could click "🔍 從 KB 抓取相關素材" inside the Obsidian Project page. Post-Tier C, that interaction lives on Web. Obsidian becomes a read/edit surface for prose; Web becomes the interactive control surface. 修修 explicitly accepted this trade-off ("WebUI 只是把它提取出來便於互動").

### D4: 7-tab stage gate — single URL with `#tab` fragment

Tabs (in order): **Brief · Research · Title & Thumbnail · Hook · Script · Review · Publish**

```
/bridge/projects/{slug}                 → defaults to #brief
/bridge/projects/{slug}#research        → Research tab active
/bridge/projects/{slug}#title-thumbnail → ...
/bridge/projects/{slug}#hook            → ...
/bridge/projects/{slug}#script          → ...
/bridge/projects/{slug}#review          → ...
/bridge/projects/{slug}#publish         → ...
```

**Soft gate + remind** (Q9.a — chosen, "嚴格度 soft 就好，但是就是要有一個 remind"):

- Each tab shows a per-tab status icon in the tab bar: `✓` complete, `◐` partial, `○` empty.
- Clicking forward past an incomplete tab fires a **toast** ("Hook 還沒填，確定要跳到 Script 嗎？") — non-blocking; 修修 can dismiss and proceed.
- Publish tab shows a **pre-publish banner** listing incomplete prerequisites; the Publish button works regardless (advisory, not gate).

**Why single URL** (Q6 = B):

- Single page = persistent Pomodoro dock across tab switches (a separate route would unmount the timer).
- Browser back/forward + bookmark + share preserve tab state via fragment.
- Tab content is server-rendered per-tab partial (Jinja `_tab_brief.html`, `_tab_research.html`, etc.); client-side just toggles `display` on partials already in DOM.

**Stage gate semantics** are **advisory, not enforced**. Per [`feedback_redline_self_discipline_not_enforcement`](../../memory/claude/feedback_redline_self_discipline_not_enforcement.md): the system's role is reminder + path-of-least-resistance, not physical enforcement. 修修 may iterate non-linearly (jump to Hook before Title) — the UI nudges, doesn't block.

### D5: Frontmatter γ (Tier C additions) per [docs/schemas/project-frontmatter-nested.md](../schemas/project-frontmatter-nested.md)

**Existing α fields retained verbatim**: `type` · `content_type` · `created` · `status` · `priority` · `area` · `search_topic` · `quarter` · `parent_kr` · `publish_date` · `tags`.

**Dropped**: `target_date` (was research-only; research dropped per D9.c).

**γ added**:
- `one_sentence` — lifted from legacy `## 👄 One Sentence About This Video` H2 prose (D9.d)
- `hook_text` — 30-60s spoken hook (**≤300 字 soft cap, v2 panel-tuned**; spoken Mandarin at 200-300 字/min → 30-60s ≈ 100-250 字, ≤300 字 leaves buffer without becoming a 2-minute monologue)
- `title_candidates: list[str]` + `thumbnail_concept: str` — CTR pair, merged tab (D4 Tab 3)
- `reviews: {storyteller: {...}, coach: {...}}` — advisory persona reviews (D8) **— v1 dict-per-persona shape; v2 panel push for list-of-versioned-objects deferred to PR2. PR1 indexer is dual-shape tolerant.**
- `pomodoro: {est_total, actual_total}` — denormalized cache for `/bridge/projects` index list. **Recomputed on Pomodoro completion or manual +1🍅 only — never per-second tick (v2 schema doc clarification per Codex push).**

Full schema in [`docs/schemas/project-frontmatter-nested.md`](../schemas/project-frontmatter-nested.md). State-separation rationale (ADR-030 D4) in §3 of that doc.

### D6: Web-self Pomodoro timer (NOT TaskNotes plugin control)

**TaskNotes plugin's pomodoro is Obsidian-only** — its timer runs in the Obsidian app and writes `timeEntries[]` to the Task md. Bridge Web cannot drive it (no API surface from Web → plugin).

**Tier C implements Web-self timer** with these behaviors:

- **25-min Pomodoro default** (configurable per-project frontmatter `pomodoro.duration_minutes`, future PR; default 25 hard-coded in v1).
- **Bidirectional shape compat with TaskNotes**: Web writes `timeEntries: [{startTime: ISO, endTime: ISO}]` to `TaskNotes/Tasks/{slug}.md` frontmatter. TaskNotes plugin's `formula.實際🍅` ([`project_youtube.md.tpl:16`](../../shared/lifeos_templates/project_youtube.md.tpl)) continues to compute correctly because the field shape matches exactly.
- **Manual `+1🍅` button** writes a synthetic timeEntry `{startTime: now-25min, endTime: now}` — supports the user's habit of running physical timers for Filming / Post-production phases ("數位番茄鐘自動增加的功能，目前只能在 pre-production").
- **No reverse sync** — TaskNotes plugin writes timeEntries are read by Web on next page load; the source-of-truth direction is **Task md ↔ Web**, both write to the same field, last-write-wins per file (per-task contention probability is near-zero — 修修 won't run both timers simultaneously).

**Rationale for Web-self over TaskNotes integration** (修修's question: "番茄鐘的實作是你自己會做一個，還是用 Task Notes 裡面的？"):

- Implementing a 25-min countdown in JS is ~50 LOC. Wrapping Obsidian plugin IPC is engineering bottomless pit (no documented API, requires Obsidian app running on the same host as Bridge — defeats VPS-side Bridge access).
- **One timer surface, multiple write modes** (auto-increment from Web timer + manual +1🍅 from physical Pomodoro) is cleaner than two competing timers.

### D7: Persistent bottom Pomodoro dock — project-level rollup

Dock sits at the bottom of every tab on `/bridge/projects/{slug}`:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 🎬 肌酸的妙用    ●▼ Pre-production    [▶ 25:00]    🍅 8 / 12    Tasks ▾ │
└─────────────────────────────────────────────────────────────────────────┘
```

- **Project name** — left, links to `/bridge/projects/{slug}#brief`.
- **Active task selector (●▼)** — dropdown lists all TaskNotes Tasks under this project. Defaults to "尚未選擇". Pomodoro completions write timeEntries to the selected task. Switching task mid-timer aborts current timer (with confirm toast).
- **Timer (▶ 25:00)** — start/pause/reset; counts down from 25:00; on completion, auto-emits timeEntry to active task + plays soft chime + increments `🍅` counter.
- **Project rollup (🍅 N / M)** — `actual_total / est_total`. Recomputed from TaskNotes scan on dock mount; updated optimistically after each completion + reconciled from disk on next reload.
- **Tasks ▾ expand** — opens a panel showing all tasks with `status / est_pomodoros / actual_pomodoros / +1🍅 manual button`. The +1🍅 button is the manual increment for physical-timer phases.

**Persistence:** dock state (active task selection, timer in-progress) survives tab switches within the project page (no remount). Page reload resets active task to "尚未選擇" and timer to idle — survival across reload is **out of scope v1** (revisit if 修修 reports friction).

### D8: 2 expert persona LLM reviews — domain-agnostic, advisory only

**Personas (Q3 pivot from style-mimic to expert-advisor):**

> **v2 PR2 hardening requirements (panel push-back from Gemini):** Persona prompts MUST ship in PR2 with (a) a 1-5 scoring rubric (each score level defined by anchor examples), (b) at least one few-shot example showing the ideal review format, and (c) an explicit Traditional Chinese / Taiwan-locale instruction to prevent Sonnet 4.6 Simplified-Chinese leakage. **The v1 prompts below are the prose specification, NOT the ship-ready prompt.** PR2 acceptance gate enforces all three.

#### Master Storyteller

> 你是世界級的故事教練。你的工作是判斷一篇文章 / 影片腳本 / Podcast 開場 / 書評 /
> 個人散文，是否能在 30 秒內勾住讀者，並讓他們**自願**讀完全部。
>
> **語言要求：請務必使用台灣慣用的正體中文回覆，避免使用簡體字或中國大陸用語。** (v2)
>
> 你關心的是：
> - **Hook 強度**：開場 30–60 秒（口語約 100–250 字，台灣慣用語速 200–300 字/分）有沒有立刻製造「我必須知道結局」的張力？(v2 corrected from 75–200)
> - **敘事弧線**：是否有清晰的 setup → confrontation → resolution 結構，即使在短內容裡？
> - **情緒節奏**：每 2–3 段是否有情緒峰谷變化，避免單調？
> - **讀者拉力**：每段結尾是否埋下「下一段一定要看」的鉤子？
>
> 你不關心：
> - 主題對不對（那是作者的權利）
> - 是否符合作者過去風格（作者要進化）
> - 文法是否完美（那是 Writing Coach 的工作）
>
> 給 1–5 分，附一段判斷摘要，列出 3–7 條具體可執行的建議（不要寫 "make it more engaging" 這種空話，要寫 "把第 2 段第 1 句的『非常重要』改成『關乎你下一個十年的健康』"）。
>
> _(PR2 ships with: rubric defining 1/2/3/4/5 score anchors + 1 few-shot review example)_

#### Writing Coach

> 你是世界級的寫作教練。你的工作是判斷一篇文章 / 腳本 / Podcast 開場的**句子層次品質**。
>
> **語言要求：請務必使用台灣慣用的正體中文回覆，避免使用簡體字或中國大陸用語。** (v2)
>
> 你關心的是：
> - **句長變化**：長短句交錯，避免每句都是 25 字。
> - **節奏呼吸**：是否有意識地放短句做節奏對比？
> - **語體一致性**：書面語 / 口語 / 半口語不要混雜（除非刻意對比）。
> - **可讀性**：被動式、嵌套子句、抽象名詞濃度是否過高？
> - **專有名詞與術語**：是否需要白話化？（「統計顯著」→「不是運氣造成的」）
>
> 你不關心：
> - Hook 是否抓人（那是 Storyteller 的工作）
> - 主題的廣度（那是作者的權利）
> - 是否符合作者過去風格
>
> 給 1–5 分，附一段判斷摘要，列出 3–7 條具體可執行的建議。
>
> _(PR2 ships with: rubric + few-shot example, same as Master Storyteller)_

**Architecture:**

- Each persona = separate LLM call (separate API cost, separate audit log entry).
- Input: full project md (frontmatter + body) — typical 5–15 KB, well under 1M-context budget; D3 LLM-over-vault pattern (ADR-030).
- Model: `claude-sonnet-4-6` (matches Tier A precedent; provider-diversity deferred per ADR-030 D3 provider note).
- Output: structured JSON per persona, validated against `reviews.{persona}` schema, written to project frontmatter.
- Re-runnable: each persona has a "重跑" button; latest overwrites previous (no version history in frontmatter; `state.db api_calls.scope_json` keeps audit trail per ADR-030 follow-up).
- **Advisory only** — not a publishing gate (per D4 soft gate + remind philosophy).

**Domain-agnostic** (修修 push-back: "不一定只專精在 Health and Wellness YouTube"): prompts say "文章 / 影片腳本 / Podcast 開場 / 書評 / 個人散文" — five content types — covering 修修's actual output range.

**Why two personas (not one combined)** (Q3 design):

- Single-prompt "全方位審查" tends to produce shallow generic feedback; splitting forces each lens to be deep.
- Separate cost cells let 修修 re-run only the persona that matters for the current iteration (e.g., re-run Coach after editing sentences, not Storyteller).
- Lower per-call output tokens → faster wall time per call.

**Why no third persona** (rejected: SEO / Hook-only / Audience-fit / Fact-checker):

- SEO: already covered by Zoro keyword research + Brook SEO audit (separate ADR-027 surface).
- Hook-only: subsumed by Master Storyteller (which weighs hook heavily).
- Audience-fit: not a single-pass LLM task; needs audience data, deferred.
- Fact-checker: high-stakes; refusal to dispatch without grounding sources; deferred.

### D9: Migration script `scripts/migrate_projects_to_tier_c.py` (PR1 bundle, with 6 sub-decisions)

Sub-decisions per Q9 (with v2 panel corrections in **bold**):

- **a. NFC-normalized title as slug** — cross-platform safety; macOS uses NFD for CJK filenames, Win/Linux/VPS use NFC. Migration script calls `unicodedata.normalize('NFC', stem)` before all comparisons (aligns with `scripts/vault_layout_audit.py` per VAULT-LAYOUT D-unicode-norm).
- **b. `Projects/{title}.md` filename unchanged** — no rename; reuse Obsidian existing wikilinks; reuse TaskNotes `projects: [[link]]` references.
- **c. `content_type` retained as `Literal["youtube", "blog", "research", "podcast"]` (v2 REVERSED from v1).** Codex panel audit verified `蛋白質攝取量.md` has `content_type: research` in live vault + `tests/test_lifeos_writer.py:285-287` asserts blog/research/podcast defaults + `tests/test_gateway_handlers.py:180/227` exercises Nami creating a research project. Slimming would silently break 2 of 3 live projects + 4 callers. Tier C does **not** modify the type system; legacy `research` projects retain their type. Future cleanup ADR can revisit when use case actually opens.
- **d. `one_sentence` lifted from H2 prose to frontmatter** — migration script regex-matches `^##\s+👄[^\n]*\n+([\s\S]*?)(?=\n##|\Z)` (both Video and Episode Sentence variants) and writes the captured prose to `one_sentence:` frontmatter (multiline YAML block scalar). H2 heading removed from body to avoid duplicate display.
- **e. Migration script bundled with PR1** — keeps the ADR/schema/code/migration coherent in one reviewable unit. **Migration filters on `type: project` (not name heuristic, per v2 panel push) — `Brook 風格訓練.md` is `type: agent-workspace` and is filtered by type-check alone; no `--skip-meta` name-heuristic flag needed.**
- **f. Schema doc bundled with PR1** — closes the 404 reference in VAULT-LAYOUT line 162.
- **g. Migration handles BOTH marker families (v2 added per Codex panel finding)** — `%%KW-START%%`/`%%KW-END%%` (legacy emitted by `肌酸的妙用.md` line 296+375) AND `%%agent-zoro-keywords-start%%`/`%%agent-zoro-keywords-end%%` (canonical per VAULT-LAYOUT §4 PR-A3). Both regex patterns in `_LEGACY_MARKER_BLOCKS`.

**Script invocation:**

```bash
# Dry run on all projects
python -m scripts.migrate_projects_to_tier_c --dry-run

# Apply to specific project
python -m scripts.migrate_projects_to_tier_c --write --target 肌酸的妙用

# Apply to all (type-filter excludes agent-workspace files automatically)
python -m scripts.migrate_projects_to_tier_c --write

# Custom vault root (e.g., from another OS or test fixture)
python -m scripts.migrate_projects_to_tier_c --write --vault "E:/Shosho LifeOS"
```

Idempotent: detects "already migrated" by checking `one_sentence` key presence in frontmatter. Files with `type != project` skipped by type filter alone.

### D10: Brief flow via Nami Slack (existing) — Web bidirectional edit

修修's stated flow:

> 「brief 會在 slack 中用自然語言跟 nami 說，除了你這裡列到的以外，還會加上 Content Type，目前有兩個：YouTube 影片，Podcast。」

Nami flow (existing, no change):

1. 修修 Slack DM: `@nami 新專案：[title] / [youtube|podcast] / 主題：[search_topic]`
2. Nami invokes `scripts/run_project_bootstrap.py` with parsed args
3. `shared/lifeos_writer.create_project_with_tasks` writes Project md + 3 default tasks
4. Nami replies with vault path + Bridge URL

**Web bidirectional** (Tier C addition):

- `/bridge/projects/new` form for non-Slack create flow (covers the "Slack 沒接通時" fallback)
- Brief tab on `/bridge/projects/{slug}` allows post-create edit of all α fields

**No Nami code change in PR1** — the existing bootstrap path produces α-only frontmatter, which Web reads tolerantly (γ fields optional). PR1 migration script back-fills γ for the 2 existing projects.

### D11: Backwards compatibility + meta-project handling (v2 revised)

| Existing file | Action |
|---|---|
| `Projects/肌酸的妙用.md` | Migrate (PR1 smoke seed; `content_type: youtube`, lifts existing `## 👄 One Sentence`) |
| `Projects/蛋白質攝取量.md` | Migrate (PR1 batch; **retains `content_type: research`** + `target_date:` field; no `## 👄` section so `one_sentence` initialized empty) |
| `Projects/Brook 風格訓練.md` | **Skip** — `type: agent-workspace`, not a project. Migration script's `type` filter handles this; no name heuristic. |

**No `content_type` breaks** — D9.c v2 revert ensures all four legacy types (`youtube`/`blog`/`research`/`podcast`) keep working. Existing tests in `tests/test_lifeos_writer.py` + `tests/test_gateway_handlers.py` continue to pass unchanged.

**Migration backup**: every `--write` invocation copies originals to `.tmp/project-migration-backup-{YYYY-MM-DD-HHMMSS}/` before write. Reversible if migration produces wrong output.

---

## Consequences

### Positive

- **Crash root cause eliminated** — dataviewjs / base / Syncthing-reparse cascade gone from Project pages.
- **All Tier C checks fit** (Branding components via Title&Thumbnail tab, Hook check via Hook tab + persona, Logic + Structure via Review tab personas) — no compounding on one render lifecycle.
- **Single mental model** for project state location — γ schema doc is the canonical reference; ADR-030 D4 routing rule applies.
- **VAULT-LAYOUT 404 closed** (line 162 reference now resolves).
- **Tier B (LifeOS Dashboard) pattern proven** — Tier C is the second use of D2 FS-direct + frontmatter-filter; Tier B can adopt the same `project_indexer.py`-style module for daily-note task queries.
- **Mobile / cross-device sync intact** — Obsidian still renders md; nothing in vault layout changes.
- **Web Pomodoro decoupled from Obsidian plugin** — works on any device with browser access, including VPS-hosted Bridge.
- **Expert persona model future-proof** — single-prompt rewrites (PR2+) can swap models without schema change; advisory semantics don't lock in 修修's iteration habit.
- **Per-project audit trail** — `state.db api_calls.scope_json` (ADR-030 follow-up) logs each LLM persona call's prompt-hash + model + cost; cross-session searchable.

### Negative / risk

- **Loss of Obsidian-side interactive buttons** — 修修 previously could trigger KB Research from inside the Obsidian Project page. Post-Tier C, that interaction lives on Web only. Acceptable trade per 修修's framing (WebUI 提取出來便於互動), but worth noting.
- **D2 path drift risk** — if `shared.lifeos_writer.PROJECTS_DIR` is renamed without updating `project_indexer.py`, Bridge silently 404s on every project. Mitigation: both modules import from a shared constant (added in PR1).
- **Schema migration preserves `target_date`** (v2 revised) — for legacy `research` projects (`蛋白質攝取量.md`) the field is left intact; new writes don't add it but reads tolerate it. Backup at `.tmp/project-migration-backup-{timestamp}/` on every `--write`.
- **Web Pomodoro state non-persistent across reload** — known v1 limitation; revisit if friction reported.
- **Two writers to TaskNotes Tasks `timeEntries[]`** (Web + Obsidian plugin) — collision probability low (modeled as 0 for single-user system per [`user_vault_edit_pattern_no_concurrent`](../../memory/claude/user_vault_edit_pattern_no_concurrent.md)); **v2 panel pushed for mtime/hash guard** — deferred to PR2 follow-up issue.
- **Bridge availability becomes SPOF for interactive flows (v2 added per Gemini)** — when VPS Bridge is down: vault md remains canonical, Obsidian still works for prose edit + frontmatter edit. Only Web-driven interactions (timer auto-tick, LLM review, in-Web frontmatter quick-edit) are blocked. The offline escape hatch is "open the md in Obsidian and edit frontmatter directly." Acknowledged trade — not addressed by an Electron bundle in PR1.
- **TaskNotes plugin schema is implicit contract (v2 added per Gemini)** — if the plugin updates its frontmatter shape, `project_writer.append_timeentry` would silently write wrong shape. Mitigation deferred: known follow-up to formalize task frontmatter schema. PR1 ships informal-contract shape that matches today's plugin (`timeEntries: [{startTime, endTime}]`).
- **Persona prompts hand-crafted, not iterated** — first-pass quality may surface in feedback. PR2 will include a prompt iteration log; mitigation is the "重跑" button + per-call audit log for prompt-tuning data.
- **No version history of reviews in frontmatter** — overwrite-on-rerun. If 修修 wants prompt comparison, must consult `state.db api_calls`.
- **Bases plugin templates referenced** in current `project_youtube.md.tpl` — even after strip, any lingering `base` block in older un-migrated files (none in vault today) would still fail render. Migration script's idempotency check covers this only if migrated; manual cleanup needed for future stragglers.
- **`/bridge/projects` becomes a high-traffic surface** — every work session 修修 lands here. Web rendering must be sub-300ms on the index list (10 projects max → 10 file reads → easy budget) and sub-500ms on detail (1 project file + TaskNotes scan for that project + frontmatter parse). Verified in PR1 acceptance.
- **No fallback when LLM API down** — Review tab "重跑" button surfaces error; 修修 can manually fill `reviews.{persona}` via Obsidian frontmatter editor as escape hatch.

### Neutral

- Obsidian render mode for `Projects/{title}.md` becomes "plain markdown" — body sections render directly, no plugin runtime. CSS snippets unaffected.
- Mobile Obsidian remains a viable read-only surface (no dataviewjs to fail on mobile).
- `TaskNotes/Tasks/*.md` files untouched in Tier C scope (Web only writes `timeEntries[]`; plugin owns task lifecycle).
- `KB/`, `Daily/`, `OKRs/`, `Inbox/`, `AgentOutputs/` — no changes.
- Nami / Brook / Zoro / Robin / Franky / Usopp / Chopper agent code — unchanged in PR1 (PR2 adds shared persona prompt module under `shared/project_reviews.py`).

---

## Open questions resolved

### Q1 — Web-first vs Obsidian-plugin-first vs dual surface
**A: Web-first (chosen).** Vault md is canonical SoT (per ADR-030 D1). Web reads via D2 FS-direct, writes via small mutation API. Obsidian is a read/edit surface for prose; Web is the interactive control surface.

**Rejected:**
- **B: Obsidian-plugin-first** — would replicate the dataviewjs crash pattern; Bases plugin deprecated; reverse direction (plugin → Web) is the wrong dependency.
- **C: Dual rich surface** — two interaction SoTs invite state divergence; Obsidian crash root cause unaddressed.

### Q2 — How aggressive to strip Obsidian-side interactivity
**a: Strip all dataviewjs + base + `%%agent-zoro-keywords-*%%` markers (chosen).** Definitive crash fix; small migration cost; Web replicates each function in its own tab.

**Rejected:**
- **b: Keep KB Research dataviewjs (most-used) + strip rest** — partial strip keeps Syncthing reparse cascade; defeats the fix; 修修 would re-encounter crashes when scaling up project count.
- **c: Keep all, just add Web parallel** — two SoTs for interaction state; doubles the surface for sync bugs.

### Q3 — Style profile mimic vs expert persona advisor
**Expert persona advisor (chosen).** 2 personas: Master Storyteller + Writing Coach. Domain-agnostic, universal craft principles.

**Rejected: Style profile mimic** (originally Plan III with parallel subagent extracting profile from 修修's past articles).

**Reasoning (修修 push-back 2026-05-24):**

> 「我通常沒有一個 profile 可以參考，hook 都是我自己憑感覺寫出來的。我需要他扮演以下兩種角色：1. 社群媒體專家 2. 寫作教練，而不是根據我過去文章的形式。」

Style profile mimic has structural problems documented in [`feedback_expert_persona_over_style_mimic.md`](../../memory/claude/feedback_expert_persona_over_style_mimic.md) (memory write bundled with PR1):

1. **Locks in mediocre patterns** — if past hooks are mid-tier, the mimic forces continued mediocrity.
2. **Suppresses evolution** — 修修 explicitly wants to grow; mimic pulls toward past mean.
3. **Suspect ground truth** — 修修 doesn't curate a "best hooks" corpus; the mimic would average across all past hooks indiscriminately.
4. **False discipline** — mimic enforces consistency, which is not the same as quality.

### Q4 — Where does Tier C land in chassis-nav
**A: top-level PROJECTS slot (chosen).** Chassis 5 → 6 slots, between Fleet ▾ and DRAFTS.

**Rejected:**
- **B: Nested under Fleet ▾** — Fleet is the agent dimension; projects span all agents.
- **C: Under Ops ▾** — Ops is instrumentation (cost / logs / memory / docs); PROJECTS is the primary "do the work" surface.

### Q5 — Frontmatter schema shape
**γ chosen.** Existing α retained verbatim; drop `target_date`; add `one_sentence`, `hook_text`, `title_candidates`, `thumbnail_concept`, `reviews`, `pomodoro`. Reviews keyed by persona (`storyteller` / `coach`).

**Rejected:**
- **α-only + single `checks` field** — under-specifies multi-persona; loses per-persona re-run granularity.
- **β: full restructure to nested-by-stage** — would break Obsidian frontmatter editor (key paths get long); loses backwards compat with existing files.

### Q6 — Tab navigation pattern
**B: Tab navigation with single URL fragment (chosen).** Preserves Pomodoro dock across tab switches; bookmark / share works; server-rendered partials with client-side `display` toggle.

**Rejected:**
- **A: Separate routes per tab** — unmounts Pomodoro dock on tab switch; doubles route count; breaks single-page interaction model.
- **C: Sidebar list of stages** — extra clicks; doesn't fit the sequential "stage gate" mental model 修修 stated.

### Q7 — Stage count + division
**Option II.2-a chosen: 7 tabs** (Brief / Research / Title&Thumbnail / Hook / Script / Review / Publish).

Title&Thumbnail merged into one tab (修修 push-back): "我想把 title 跟 thumbnail 放在同一個 tab，這兩個是相輔相成的，而且重要性甚至比 hook 還高."

**Rejected variants:**
- **5 tabs** (Brief / Research / Hook+Title / Script / Publish) — too coarse; Review folded into Script loses re-run granularity; CTR pair gets squashed.
- **9 tabs** (split Title / Thumbnail / Description / Tags / Distribution) — over-granularized; Distribution / Tags belong inside Publish tab.

### Q8 — Persona prompt design (v2 hook-math corrected)
**Domain-agnostic chosen.** Hook duration **30–60 seconds** (修修's habit). **Hook_text soft cap ≤300 字 (v2 panel-tuned, Gemini-grounded)**: Taiwanese Mandarin spoken at 200-300 字/min → 30-60s ≈ **100-250 字**; ≤300 字 cap leaves buffer without becoming a 2-minute monologue. Two personas only.

**Rejected:**
- **Health-Wellness-specific personas** — 修修 push-back: "不一定只專精在 Health and Wellness YouTube，我也寫過很多 book review 以及個人的成長故事."
- **15-second hook** (from 2026 generic best-practice web search) — 修修 push-back: "Hook 15 秒太少，大概 30~60 秒，這是我的習慣." Generic best-practice doesn't override author's stated habit.
- **≤500 字 hook cap** (v1) — Codex+Gemini panel both pushed back. At ≤500 字 the hook becomes a 2-minute spoken monologue, far past the 30-60s habit. Adopted ≤300 字 in v2.
- **75-200 字/min speaking rate** (v1) — Codex+Gemini both corrected. Taiwanese Mandarin is 200-300 字/min (TED Taipei / popular health YT channel reference samples).
- **3+ personas** (SEO / Hook-only / Audience / Fact-check) — covered elsewhere or deferred; see D8 rationale.

### Q9 — Migration sub-decisions
**v2 revised**: a (NFC slug ✓), b (filename unchanged ✓), **c (content_type slim REVERTED — keep all 4)**, d (one_sentence lift ✓), **e (script with PR1; `--skip-meta` flag REMOVED — type filter handles it)**, f (schema doc with PR1 ✓), **g new (handle BOTH marker families)**. Migration script details in D9.

---

## Migration plan

Sequenced to keep each PR atomic and reviewable. Sandcastle parallel dispatch where possible (per `feedback_sandcastle_default`).

### PR1 — Tier C shell + 4-panel lift + migration

**Bundle scope** (single PR; sandcastle-friendly if needed):

- **Docs**:
  - [`docs/decisions/ADR-031-project-workspace-migration.md`](ADR-031-project-workspace-migration.md) — this file
  - [`docs/schemas/project-frontmatter-nested.md`](../schemas/project-frontmatter-nested.md) — γ schema spec
  - [`docs/VAULT-LAYOUT.md`](../VAULT-LAYOUT.md) — line 162 now resolves; §4 marker registry annotated to note Project pages no longer emit `%%agent-zoro-keywords-*%%`

- **Code (shared)**:
  - [`shared/lifeos_writer.py`](../../shared/lifeos_writer.py) — `ContentType = Literal["youtube", "podcast"]`; `DEFAULT_TASKS` slim; render_project drops `target_date`
  - `shared/lifeos_templates/project_youtube.md.tpl` — strip dataviewjs/base/markers; minimal scaffold
  - `shared/lifeos_templates/project_podcast.md.tpl` — same strip pattern
  - **Delete**: `shared/lifeos_templates/project_blog.md.tpl`, `shared/lifeos_templates/project_research.md.tpl`
  - `shared/project_indexer.py` — **new** module; FS-direct list/get/load_body; modeled on `shared/digest_indexer.py`
  - `shared/project_writer.py` — **new** module; small mutation API (`update_frontmatter`, `update_section`, `append_timeentry`)

- **Code (Bridge router)**:
  - `thousand_sunny/routers/bridge_projects.py` — **new** module
  - Routes: `GET /bridge/projects` (index) · `GET /bridge/projects/new` (create form) · `POST /bridge/projects` (create handler, calls existing bootstrap) · `GET /bridge/projects/{slug}` (detail, defaults to #brief) · `POST /bridge/projects/{slug}/frontmatter` (form update of α + γ fields) · `POST /bridge/projects/{slug}/section/{section_id}` (long-form section update) · `POST /bridge/projects/{slug}/timer/start|complete|cancel` (Pomodoro state transitions) · `POST /bridge/projects/{slug}/tasks/{task_slug}/manual-pomodoro` (+1🍅)
  - Reviews **stub** in PR1 (renders "尚未審查" + disabled button) — real LLM dispatch lands in PR2

- **Code (Bridge templates)** under `thousand_sunny/templates/bridge/projects/`:
  - `index.html` — list of projects with status, priority, pomodoro rollup
  - `new.html` — create form
  - `detail.html` — top-level shell with 7-tab navigation, persistent Pomodoro dock
  - `_tab_brief.html`, `_tab_research.html`, `_tab_title_thumbnail.html`, `_tab_hook.html`, `_tab_script.html`, `_tab_review.html`, `_tab_publish.html` — per-tab partials
  - `_pomodoro_dock.html` — persistent bottom dock partial

- **Chassis-nav**:
  - [`thousand_sunny/templates/bridge/_chassis_nav.html`](../../thousand_sunny/templates/bridge/_chassis_nav.html) — add PROJECTS slot between Fleet ▾ and DRAFTS; update slug list comment

- **Static assets**:
  - `thousand_sunny/static/shosho/bridge-projects.css` — Tier C-specific styles (tab bar, dock, soft-gate icons)
  - `thousand_sunny/static/shosho/bridge-projects.js` — tab switching, timer countdown, dock active-task selector

- **Migration**:
  - `scripts/migrate_projects_to_tier_c.py` — Python; idempotent; --dry-run / --write / --target / --skip-meta
  - Run script during PR1 acceptance against `肌酸的妙用` (smoke seed) + `蛋白質攝取量` (batch). `Brook 風格訓練` skipped.

- **Tests**:
  - `tests/test_project_indexer.py` — D2 FS-direct unit tests (modeled on `tests/test_digest_indexer.py`)
  - `tests/test_project_writer.py` — frontmatter update, section update, timeEntry append (with atomic rename verification)
  - `tests/test_bridge_projects.py` — route smoke tests with fixture vault
  - `tests/test_lifeos_writer_tier_c.py` — content_type slim regression; render_project field shape
  - `tests/test_migrate_projects_to_tier_c.py` — idempotency, one_sentence lift regex, NFC normalization, target_date drop

**PR1 acceptance**:

1. `pytest -k "indexer or writer or projects or migrate" -x` green
2. `ruff check shared/ thousand_sunny/ scripts/` clean
3. `ruff format --check shared/ thousand_sunny/ scripts/` clean
4. Browser smoke test (Playwright headless or 修修 morning manual):
   - `GET /bridge/projects` lists 2 projects (post-migration), status + 🍅 rollup correct
   - `GET /bridge/projects/肌酸的妙用` renders, Tab 1 (Brief) shows `one_sentence` populated from migration
   - Tab switch updates fragment, dock persists
   - +1🍅 button writes synthetic timeEntry to TaskNotes Tasks md (verify with `cat` after click)
5. Obsidian-side: open `肌酸的妙用.md` in Obsidian — md renders cleanly, no errors in console, no dataviewjs blocks visible (only frontmatter + plain body sections).

### PR2 — LLM persona reviews (Storyteller + Coach)

- `shared/project_reviews.py` — persona prompt definitions, LLM dispatch, JSON schema validation
- `thousand_sunny/routers/bridge_projects.py` — `POST /bridge/projects/{slug}/review/storyteller` + `POST /bridge/projects/{slug}/review/coach` — non-stub
- `tests/test_project_reviews.py` — mocked LLM dispatch, schema validation, frontmatter write
- `state.db api_calls.scope_json` — log per persona run: `{persona, model, prompt_hash, tokens_in, tokens_out, cost_usd, latency_ms}`

### PR3+ — Backlog (post-PR2)

- Sanji social repurpose tab integration (when Sanji built)
- Pomodoro persistence across page reload (sessionStorage or `state.db pomodoro_sessions`)
- Audio chime / native notification on Pomodoro complete
- Multi-project Kanban view (`/bridge/projects?view=kanban`)
- Markdown editor (CodeMirror) embedded in Script tab for body sections
- Image generation for `thumbnail_concept` → actual image draft (Flux / Imagen integration)
- Title&Thumbnail A/B selector (write candidate chosen → `title` frontmatter; `title_candidates` retained for archive)

Each PR independently reversible.

---

## Out of scope

- **Tier B (LifeOS Dashboard mirror)** — daily-note task queries, weekly plan, OKR rollup, time-management dashboard. Same D2 pattern but separate ADR. Triggered when 修修 hits the desk and wants the weekly view.
- **Brook synthesize review page** — already lives at `/projects/{slug}` (ADR-021, `thousand_sunny/routers/projects.py`). Distinct namespace from Tier C `/bridge/projects/*`. PR1 documents the boundary (Reviewer note).
- **Obsidian plugin-side interactivity restoration** — write-off. Vault md is read+prose-edit; Web is the interactive surface. If 修修 reverses this, future ADR.
- **TaskNotes plugin write surface** — Web only writes `timeEntries[]` (one field) to Task md. Task lifecycle (`status: to-do → in_progress → done`) is owned by 修修 via Obsidian or Web "complete task" button (PR1 includes the button; future polish may expose drag-to-reorder).
- **Provider diversity for LLM personas** — locked to Anthropic Claude Sonnet 4.6 in PR2; consistent with ADR-030 Tier A choice. Separate ADR if a persona benefits from non-Anthropic capability.
- **Audio cue / native browser notification on Pomodoro complete** — PR3+ polish.
- **Project archive / soft-delete UI** — PR3+ (current archive flow: 修修 sets `status: archived` in frontmatter; Web filters from index).
- **Multi-machine concurrent edit** — single user, Bridge auth is HMAC cookie; concurrent edit collision not a real scenario per [`user_vault_edit_pattern_no_concurrent`](../../memory/claude/user_vault_edit_pattern_no_concurrent.md) memory.
- **Mobile responsive `/bridge/projects/{slug}` layout** — deferred per ADR-029 §Out of scope. 7-tab nav on narrow viewport is a Phase-2 design pass.
- **Multi-modal embedding in Web tabs (video clips, audio waveforms)** — not in Tier C scope; deferred to script-driven video workspace (separate axis, ADR-015).
- **Real-time collaboration with future co-authors** — single-user system; out of architectural scope.

---

## Cross-reference summary

| Concern | Authority |
|---|---|
| Vault is canonical SoT | ADR-030 D1 |
| FS-direct read pattern | ADR-030 D2 |
| Substrate routing rule | ADR-030 D4 |
| Bridge IA dual-axis | ADR-029 §1 (Tier C extends 5 → 6 slots) |
| Vault folder layout | VAULT-LAYOUT.md §2 |
| Marker convention | VAULT-LAYOUT.md §4 (Pattern A retained for non-Project pages) |
| Project frontmatter schema | [docs/schemas/project-frontmatter-nested.md](../schemas/project-frontmatter-nested.md) |
| Pipeline stage anchor | CONTENT-PIPELINE.md Stages 3 + 4 + 5 |
| Aesthetic baseline | docs/design-system.md (Tier C UI inherits Bridge tokens) |
| Soft gate philosophy | `feedback_redline_self_discipline_not_enforcement` |
| Drive-to-ship discipline | `feedback_drive_to_completion_no_checkpoint` |
| Pivot from style-mimic to persona | `feedback_expert_persona_over_style_mimic` (PR1 memory bundle) |
