# Project Frontmatter — Nested Schema (γ)

**Status:** Active (PR1 bundle, pending merge — modules land with ADR-031 PR1)
**Authority:** [`ADR-031-project-workspace-migration.md`](../decisions/ADR-031-project-workspace-migration.md)
**Authoring code:** [`shared/lifeos_writer.py:render_project`](../../shared/lifeos_writer.py) (Python bootstrap; PR1 retains all 4 content_types per v2 panel) · `thousand_sunny/routers/bridge_projects.py` (Web mutations, lands in PR1) · `scripts/migrate_projects_to_tier_c.py` (one-shot lift, PR1)
**Consumer:** Bridge Web UI `/bridge/projects` + `/bridge/projects/{slug}` · Obsidian frontmatter editor (read-write) · `shared/project_indexer.py` (FS-direct read, D2 per ADR-030; lands in PR1 — dual-shape tolerant for forward-compatible reviews schema)

> **History:** `ADR-027 PR-6` referenced this schema doc but never delivered it. `docs/VAULT-LAYOUT.md` line 162 has been pointing at a 404 since 2026-05-12. ADR-031 closes that gap and extends the schema with Tier C fields (`one_sentence`, `hook_text`, title/thumbnail, `reviews`, `pomodoro`).

---

## 1. Full example

```yaml
---
# ── α (existing fields, retained) ─────────────────────────────────────────────
type: project
content_type: youtube           # Literal["youtube", "blog", "research", "podcast"] — all 4 retained per v2 panel
created: 2026-04-10
status: active                  # active|paused|published|archived
priority: first                 # first|high|medium|low
area: work                      # work|play|love|health (DYL pillar)
search_topic: 肌酸
quarter:                        # null until OKR-bound
parent_kr:                      # null until OKR-bound
publish_date:                   # null until status=published

# ── γ (Tier C additions) ──────────────────────────────────────────────────────
one_sentence: |
  探討肌酸對非運動族群（認知、情緒、抗老化）的潛在用途，超越運動表現的窄義。

hook_text: |
  你以為肌酸只是健身房裡那罐白白的粉？最新的隨機對照試驗顯示，它對 65 歲以上
  族群的記憶力，有 12% 統計顯著的改善——而且 5g/天的劑量、沒有任何副作用報告。
  今天我們不講運動表現。我們講「肌酸」這個被誤解了 30 年的營養素，到底還能
  在你身上做什麼。

title_candidates:
  - "肌酸不只練肌肉：3 個你沒聽過的妙用"
  - "65 歲開始吃肌酸？最新研究說：來得及"
  - "每天 5g，改變你大腦的化學反應"

thumbnail_concept: |
  分割版面：左側是健身房肌酸罐（褪色處理表示「過時印象」），右側是大腦 MRI
  剖面圖（高飽和度橙色，表示「新發現」）。中間黃色閃電符號連接。

reviews:
  storyteller:
    run_at: 2026-05-24T22:15:00+08:00
    score: 4
    summary: |
      Hook 抓得很穩——「以為只是健身房粉末」是廣泛共有的誤解，立刻製造認知
      落差。12% 數字 + 5g 劑量 + 0 副作用是黃金三段（事實、可操作、無代價），
      但「我們不講運動表現」這句反向收束略硬，建議改成更具體的轉場。
    suggestions:
      - "把「我們不講運動表現」改成「我們今天先把運動表現放一邊」——軟化"
      - "12% 後面加一個對照：「相當於把腦齡往回撥 X 年」——把抽象百分比落地"
      - "三個妙用的並列順序應該由弱到強，最強的留到最後（climax）"

  coach:
    run_at: 2026-05-24T22:16:00+08:00
    score: 3
    summary: |
      句長太均勻——平均 25 字、變化度低，讀起來節奏單調。專有名詞密度合理
      但「統計顯著」可以白話化。第二段缺少呼吸點，整段沒有單字句斷句。
    suggestions:
      - "段落 2 末尾加一句短句，如「真的。」做節奏對比"
      - "「統計顯著」→「不是運氣造成的」"
      - "「沒有任何副作用報告」可分行強調：「沒副作用。」單獨成段"

pomodoro:
  est_total: 12
  actual_total: 8

# ── tags ───────────────────────────────────────────────────────────────────────
tags:
  - project
  - youtube
---
```

---

## 2. Field reference

### 2.1 α fields (existing — retained verbatim)

> **v2 panel-revised**: `content_type` slim to `{youtube, podcast}` was reverted. All 4 legacy types (`youtube`/`blog`/`research`/`podcast`) remain valid. See [ADR-031 D9.c v2](../decisions/ADR-031-project-workspace-migration.md#d9-migration-script-scriptsmigrate_projects_to_tier_cpy-pr1-bundle-with-6-sub-decisions) for rationale.

| Field | Type | Required | Validation | Source of truth | Notes |
|---|---|---|---|---|---|
| `type` | `string` | yes | `== "project"` | Bootstrap (one-shot) | Obsidian query marker |
| `content_type` | `enum` | yes | `youtube` \| `podcast` | Bootstrap | **ADR-031 D9.c slim: dropped `blog`/`research`**. Existing-vault scan 2026-05-24 confirmed zero non-{youtube,podcast} projects. |
| `created` | `date` | yes | `YYYY-MM-DD` | Bootstrap | Never mutated; `date` type yaml (no time). |
| `status` | `enum` | yes | `active` \| `paused` \| `published` \| `archived` | Web Brief tab \| Obsidian manual | Drives chassis grid colour + sort. |
| `priority` | `enum` | yes | `first` \| `high` \| `medium` \| `low` | Web Brief tab \| Obsidian manual | `first` reserved for the one current focus project. |
| `area` | `enum` | yes | `work` \| `play` \| `love` \| `health` | Web Brief tab \| Obsidian manual | DYL pillar; null falls back to `work` at write time. |
| `search_topic` | `string` | yes | non-empty | Bootstrap default `= title`; editable | Zoro keyword-research input + KB Research seed. |
| `quarter` | `string` \| null | no | OKR quarter slug | Web Brief tab | null until OKR-bound. |
| `parent_kr` | `string` \| null | no | OKR KR slug | Web Brief tab | null until OKR-bound. |
| `publish_date` | `date` \| null | no | `YYYY-MM-DD` | Web Publish tab | null until `status=published`. |
| `tags` | `list[str]` | yes | must include `"project"` + content_type | Bootstrap | Obsidian tag index. |

**Dropped from earlier schema versions:**

- `target_date` — was used only for `content_type=research`. Research content-type dropped (ADR-031 D9.c). Field removed from `render_project` write path. Migration script (D9.e) drops `target_date` on existing files; idempotent skip if absent.

### 2.2 γ fields (Tier C additions — ADR-031)

| Field | Type | Required | Validation | Writer | Frequency |
|---|---|---|---|---|---|
| `one_sentence` | `str` (multiline) | no | ≤300 字 soft cap | Web Brief tab; migration script lifts from legacy `## 👄 One Sentence About This Video` H2 prose | Mid (per session) |
| `hook_text` | `str` (multiline) | no | **≤300 字 soft cap (v2 panel-tuned)** | Web Hook tab | Mid |
| `title_candidates` | `list[str]` | no | 1–10 items typical; each ≤80 字 | Web Title&Thumbnail tab | Mid |
| `thumbnail_concept` | `str` (multiline) | no | ≤300 字 soft cap | Web Title&Thumbnail tab | Mid · **deprecated by ADR-033** in favour of `thumbnail_ideas`; read-fallback only |
| `thumbnail_ideas` | `list[str]` (each multiline) | no | 1–3 items typical; each ≤500 字 soft cap; each follows 5-line format (ADR-033 D3) | Web Title&Thumbnail tab | Mid · ADR-033 PR4 |
| `thumbnail` | `str` (vault-relative path) | no | `Attachments/projects/{slug}/thumbnail.png` shape | Web commit endpoint (ADR-033 D7) | Per commit (rare) |
| `thumbnail_chosen_at` | ISO 8601 with TZ | no | `+08:00` recommended | Web commit endpoint | Per commit |
| `thumbnail_run` | `str` | no | shape `{ts}/v{n}` referencing `data/thumbnails/{slug}/runs/` audit trail | Web commit endpoint | Per commit |
| `host_video_path` | `str` (repo-relative) | podcast only | e.g. `data/podcasts/{ep_slug}/host_angle.mp4` | 修修 manual entry | Once per episode |
| `guest_video_path` | `str` (repo-relative) | podcast only | same shape as host | 修修 manual entry | Once per episode |
| `thumbnail_active_cutouts` | `dict` | podcast only | shape `{host: list[path], guest: list[path]}`; 2–3 entries each per ADR-033 D9 | Web funnel-confirm endpoint | Once per episode |
| `reviews` | `dict` (v1) → `list` (v2 future) | no | keys ∈ `{storyteller, coach}` | Web Review tab (LLM dispatch) | Per re-run |
| `reviews.{persona}.run_at` | ISO 8601 with TZ | yes (if persona present) | `+08:00` recommended (Asia/Taipei) | LLM handler | Per re-run |
| `reviews.{persona}.score` | `int` | yes | 1–5 | LLM handler | Per re-run |
| `reviews.{persona}.summary` | `str` (multiline) | yes | non-empty | LLM handler | Per re-run |
| `reviews.{persona}.suggestions` | `list[str]` | yes | 0–10 items | LLM handler | Per re-run |
| `pomodoro` | `dict` | no | — | Pomodoro dock; recomputed on **completion / manual +1🍅 / explicit save** (NOT per-second tick, per v2 panel) | Mid (~1/25min during work) |
| `pomodoro.est_total` | `int` | yes (if `pomodoro` present) | ≥0 | Derived: `sum(task.預估🍅 for task in TaskNotes scan)` | Mid |
| `pomodoro.actual_total` | `int` | yes (if `pomodoro` present) | ≥0 | Derived: `floor(sum(timeEntries duration in min) / 25)` | Mid |

### v2 panel evolution note (reviews shape)

The v1 `reviews.{persona}` schema is a **single dict per persona** (latest overwrites). v2 panel (Gemini) pushed for **list-of-versioned-objects** to preserve prompt-iteration data:

```yaml
reviews:
  storyteller:
    - run_at: 2026-05-24T22:15:00+08:00
      prompt_version: 1
      score: 4
      summary: ...
      suggestions: [...]
    - run_at: 2026-05-25T11:00:00+08:00
      prompt_version: 2
      score: 5
      ...
```

**PR1 indexer (`shared/project_indexer.py`) implements dual-shape tolerance** — reads dict-per-persona OR list-of-dicts (taking latest). PR2 flips the schema to list-only; v1 dict-shape stays readable. UI shows latest by default with a "歷史" toggle (PR3+).

**Soft caps** are enforced as warnings (yellow toast in Web UI; non-blocking). 修修 can override. The cap exists to keep the field readable in the Obsidian frontmatter editor, not as a hard constraint.

### 2.3 Reviews structure rationale

Reviews are **advisory, not a publishing gate** (ADR-031 D8 explicit). Each persona is a separate LLM call to keep prompts focused and to allow re-running one persona without re-paying for the other. The schema is identical per persona so Web UI can render both with the same partial template.

`run_at` lets the UI show "reviewed 3 hours ago" + a "stale since edit" indicator when the project body changed after the last review.

Re-running a persona **overwrites** the previous entry — no version history in frontmatter. If history is needed later, it lands in `state.db api_calls` (already logged per ADR-030 observability follow-up).

---

## 3. State separation (ADR-030 D4 mapping)

Tier C maintains the three-tier separation introduced by ADR-030:

| Concept | Substrate | Reasoning |
|---|---|---|
| Long-form prose body (`## 專案描述`, `## 預期成果`, `## Draft Outline`, `## Script / Outline`, `## 專案筆記`) | Vault md body | Human-readable, mobile-syncable, Obsidian search-indexable. |
| Hook + thumbnail prose (`hook_text`, `thumbnail_concept`) | Vault md frontmatter | Length-bounded; benefits from structured editing (Web tab UI); read by LLM personas. |
| Low-mutation structured state (`tags`, `status`, `priority`, `area`, `created`, `publish_date`, OKR bindings) | Vault md frontmatter | Versioned with content; rare write (≤1/day typical). |
| Mid-mutation structured state (`reviews`, `title_candidates`) | Vault md frontmatter | Persistent across Web sessions; readable in Obsidian; write rate ≤10/day per project. |
| Mid-frequency counters (`pomodoro.est_total`, `pomodoro.actual_total`) | Vault md frontmatter | Denormalized cache for `/bridge/projects` index render (avoids per-project TaskNotes scan). Recomputed on save. Write rate: ~1/25min during active work. |
| Per-task pomodoro state (`預估🍅`, `timeEntries[]`) | `TaskNotes/Tasks/{title} - {task}.md` frontmatter | **SoT is the Task file, not the project.** Project `pomodoro.*` is derived. TaskNotes plugin's auto-formulas (`formula.實際🍅`, `formula.accuracy`) continue to work unchanged. |
| Live timer tick / transient UI / drag state | Browser ephemeral (sessionStorage) | Not durable; resets on tab close. |
| LLM review audit trail (cost, model, latency, prompt-snapshot hash) | `state.db api_calls.scope_json` | FSM-grade observability; cross-session search; per ADR-030 follow-up. |

**Pomodoro write-rate analysis** — boundary call for ADR-030 D4. Actual rate is **1 write per 25 min per active task** (timer completion) **plus** occasional manual `+1🍅` clicks. Daily upper bound during a focused work day is ~24 writes/project (8h × 60min / 25min × 1.25 manual fudge). This is well below the "high-frequency mutation → state.db" threshold (ADR-030 D4 line 125). Vault frontmatter handles it. The dataviewjs render-crash issue (ADR-031 §Context) was driven by **read** cost (per-page-load query scans), not write rate; the Web UI sidesteps it by precomputed counters.

---

## 4. Write authority matrix

| Field | Writer | Trigger |
|---|---|---|
| `type`, `content_type`, `created`, `tags` | Bootstrap (`scripts/run_project_bootstrap.py`, `shared/lifeos_writer.py:render_project`) | On `nami create-project` or `/bridge/projects/new` |
| `status`, `priority`, `area`, `quarter`, `parent_kr` | 修修 manual (Obsidian frontmatter editor) **OR** Web Brief tab | Low frequency |
| `search_topic` | Bootstrap default `= title`; 修修 may edit | Low frequency |
| `one_sentence` | Web Brief tab; migration script (one-shot lift from legacy H2 prose) | Mid; one-shot at migration |
| `hook_text` | Web Hook tab | Mid frequency |
| `title_candidates`, `thumbnail_concept` | Web Title&Thumbnail tab | Mid frequency |
| `reviews.{persona}` | Web Review tab → LLM handler (`shared/project_reviews.py`, PR2) | Per persona re-run (advisory, not gate) |
| `pomodoro.{est_total, actual_total}` | Pomodoro dock; recomputed from TaskNotes scan on each timer tick **or** manual +1🍅 button | High during active work; idle when no timer |
| `publish_date` | Web Publish tab | Once, on status → `published` |

**Nobody else writes the project md.** No agent cron, no Slack bot. Bootstrap + Web are the only durable writers. (Migration script writes once during Tier C rollout.)

---

## 5. Backwards compatibility

Old Projects (pre-Tier C, 2 in vault: `肌酸的妙用.md`, `蛋白質攝取量.md`) **lack γ fields**. Web behavior on read:

| Missing field | Web behavior |
|---|---|
| `one_sentence` | Brief tab shows "尚未填入" placeholder + "從正文擷取" helper button (scans `## 👄 One Sentence About This Video` H2 if present) |
| `hook_text` | Hook tab empty; placeholder text invites first input |
| `title_candidates` | Title&Thumbnail tab shows empty list + "從 Zoro 關鍵字研究擷取" button (if Zoro keyword results exist in body) |
| `thumbnail_concept` | Title&Thumbnail tab empty |
| `reviews` | Review tab shows "尚未審查" + 2 run buttons (Storyteller, Coach) |
| `pomodoro` | Computed on the fly from TaskNotes scan on first visit; **persisted to frontmatter on first save** so subsequent visits hit the cache |
| `tags` missing content_type tag | Migration script appends; Web read-tolerant |

**Migration script** [`scripts/migrate_projects_to_tier_c.py`](../../scripts/migrate_projects_to_tier_c.py) (PR1 bundle):

- **Idempotent**: detects "already migrated" by checking `one_sentence` key presence in frontmatter (cheap probe; no full-body diff)
- **Lifts** `## 👄 One Sentence About This Video` H2 prose body → `one_sentence` frontmatter (Pattern A regex match)
- **Drops** `target_date` legacy field if present (was research-only)
- **Validates** `content_type` ∈ `{youtube, podcast}`; emits `WARN` + skip if `blog`/`research`/unknown (no such files in vault as of 2026-05-24)
- **Recomputes** `pomodoro.{est_total, actual_total}` via TaskNotes scan
- **Modes**: `--dry-run` prints unified diff; `--write` applies; `--target <slug>` limits to one project
- **NFC-normalizes** title for filename comparison (Q9.a — cross-platform safety; macOS NFD vs Windows/Linux NFC)

---

## 6. Marker convention (VAULT-LAYOUT §4 alignment)

Tier C body uses **Pattern B** (DOM-only render, no in-md persistence) for all Web-driven interactive panels:

| Section in Web UI | Lives where | Reason |
|---|---|---|
| Brief tab content (one_sentence, status, priority, area, OKR binding) | Frontmatter | Structured + length-bounded |
| Research tab content (KB Research results, Zoro keyword research) | Web ephemeral (Robin/Zoro endpoint response cached in Web; not written to md) | Large transient blobs (5-50KB); avoid the dataviewjs crash root cause |
| Title&Thumbnail tab | Frontmatter (`title_candidates`, `thumbnail_concept`) | Length-bounded |
| Hook tab | Frontmatter (`hook_text`) | Length-bounded ≤500 字 |
| Script tab | Body `## Script / Outline` section | Long-form prose; human-only marker |
| Review tab | Frontmatter (`reviews.{persona}`) | Structured + advisory |
| Publish tab | Frontmatter (`publish_date`, `status` flip); body `## YouTube Description` (if youtube) | Mixed |
| Pomodoro dock | Frontmatter (`pomodoro.*`) + TaskNotes Tasks files (`預估🍅`, `timeEntries[]`) | Project-level cache + per-task SoT |

**Pattern A markers (legacy)** previously embedded `%%agent-zoro-keywords-start/end%%` for Zoro keyword research in-md body. **Tier C strips this entirely** — keyword research becomes a Web-rendered panel reading Zoro endpoint results directly (no in-md write). The marker convention `%%agent-zoro-keywords-start/end%%` remains registered in [`VAULT-LAYOUT.md`](../VAULT-LAYOUT.md) §4 for **non-Project pages** that may use it; Project pages no longer emit it.

**Human-only sections** (marker `<!-- vault:human-only-section -->`) retained verbatim in `project_youtube.md.tpl`:

- `## 專案描述`
- `## 預期成果`
- `## Draft Outline` (修修's outline; distinct from Brook scaffold's output)
- `## 專案筆記`

These are direct-edit in Obsidian; Web UI renders them read-only (or via embedded markdown editor in a future PR).

---

## 7. Related

- [`ADR-031-project-workspace-migration.md`](../decisions/ADR-031-project-workspace-migration.md) — the decision authoring this schema
- [`ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md`](../decisions/ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md) — first reference to "Project nested frontmatter schema", never delivered (Tier C closes the gap)
- [`ADR-029-bridge-ia-restructure.md`](../decisions/ADR-029-bridge-ia-restructure.md) — dual-axis nav; Tier C adds 6th top-level slot `PROJECTS`
- [`ADR-030-vault-as-substrate-read-strategy.md`](../decisions/ADR-030-vault-as-substrate-read-strategy.md) — D2 (FS-direct) + D4 (substrate routing) framework Tier C builds on
- [`VAULT-LAYOUT.md`](../VAULT-LAYOUT.md) — §2 folder map · §3 producer/consumer matrix · §4 marker convention
- [`CONTENT-PIPELINE.md`](../../CONTENT-PIPELINE.md) — Stage 3 (Synthesis) + Stage 4 (Atomic Content) + Stage 5 (Multi-channel) all cross-cut by Tier C
- [`shared/lifeos_writer.py`](../../shared/lifeos_writer.py) — Python authoring code (`render_project`, `render_task`, `create_project_with_tasks`)
- [`shared/digest_indexer.py`](../../shared/digest_indexer.py) — D2 precedent for `shared/project_indexer.py` (PR1)
