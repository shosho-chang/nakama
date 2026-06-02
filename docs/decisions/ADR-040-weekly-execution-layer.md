# ADR-040: Weekly Dashboard execution layer — timer/UFO, top-3 granularity, task-as-workspace (extends ADR-039)

**Status:** Proposed (v2 — incorporates 3-way panel review, 2026-06-02). Pending 修修 sign-off + one narrow red-line reclassification (`Journals/Weekly/` 🔒→🟡). Slice E (task-page timer) already shipped on branch `feat/adr-039-weekly-redesign` (commit `870136a`); some E-slice semantics are corrected here and land as Slice-2 fixups.

**Extends:** ADR-039 (LifeOS Weekly Dashboard — Tier B). Refines its D5/D6/D7/D8; does not supersede it.

---

## Panel review trail (v2, 2026-06-02)

Drafted v1 → audited by Codex (GPT-5, verdict *approve-with-modifications*) + Gemini 2.5 Pro (verdict *reject — different architecture*). Audits verbatim: `docs/research/2026-06-02-{codex,gemini}-adr040-audit.md`; integration matrix: `docs/research/2026-06-02-adr040-panel-integration.md`.

**Adopted from the panel (changed v1 → v2):**
- **A1 reframe** (3-way): the boundary is *human-generated intent* vs *machine-generated observation* — NOT "structured vs prose" (that dichotomy is false: `plan[]`/`top3`/targets are structured **and** human). The machine **persists** intent and **computes** observations; it never **selects** intent or **authors** prose.
- **A2 evidence-not-claims** (3-way): `timeEntries` stores a verifiable record; UFO is a **derived** property (deep + duration-met + completed), not a bare tag; the live timer logs **actual elapsed**, not a fixed block.
- **A4** (Codex): one project-top3 scoring rule chosen (done-tasks / total-tasks).
- **A8 → out of ADR-040** (3-way): the `Journals/Daily/` carve-out is removed to its own future ADR; the dual-track habit model is rejected in favor of a single unified record (see "Deferred").
- Plus: enumerate weekly-file frontmatter keys + update D5 allowlist; task-page accuracy must use the D3 union; aggregator must category-filter; NFC + CJK-wikilink resolution called out for Slice 2; the 週交接 ritual decomposed into composable steps.

**Escalated to 修修 — resolved 2026-06-02:**
- **#5 Where machine state lives** → *keep the weekly file in `Journals/Weekly/`, re-justified by the A1 reframe* (below). The panel's machine-owned-folder alternative (`System/BridgeState/`) was considered and declined: it would fragment the one-SoT weekly journal and force the Weekly Review out of the dashboard. The reframe + "compute observations on read, store none" (ADR-039 D5) answers the contamination concern.
- **#6 Task-body authoring** → *keep it, but scoped* (A7).
- **#7 UFO/🍅 conflicting incentives** → *don't adjust yet; observe a few weeks first* (noted in A2).

---

## Context

ADR-039 shipped Slice 0 (read-only) + Slice 1a (task `plan[]` writes). Using the redesign hands-on (grill 2026-06-02), 修修 surfaced decisions that (a) extend the dashboard from a *view* into an *execution layer* and (b) **reframe the red line's governing principle**.

### The reframe (verbatim intent)

> 「當初設定這條紅線的精神，是我不希望 AI agent 去影響我產出的內容。但我的 weekly plan 裡面大多是時間的安排以及統計數據，這是我目標要讓 AI 幫我做的。」

The folder-level 🔒 on `Journals/` was a proxy for a finer boundary. The panel sharpened it: the real line is not *structured vs prose* but **human-generated intent vs machine-generated observation** — and the machine's role is *scribe and calculator*, never *author or chooser*.

---

## Decision

### A1 — Governing principle: human intent (machine persists, never selects) vs machine observation (computed, never stored as authored content)

| Class | Examples | Who | Rule |
|---|---|---|---|
| **Human intent** | `plan[]`, `top3`/`next3` selections, weekly targets, `status` chosen by 修修 | 修修 decides; **Bridge persists verbatim** | machine may store, **never auto-select** |
| **Human prose** | Weekly Review answers, 隨手筆記, Task/Project bodies | 修修 types (Bridge form or Obsidian); **stored verbatim** | **no LLM authorship** (v1) |
| **Machine observation** | actual🍅, execution rate, top3 achievement, UFO count | derived from logs | **computed on read, not stored** (ADR-039 D5) |

**Consequence for the red line (resolves escalation #5):** because observations are computed-on-read and never written, **the Bridge never writes a machine-*authored* value into `Journals/`.** Everything it persists to the weekly file is 修修's own intent or prose. The `Journals/Weekly/` 🔒→🟡 carve-out is therefore "Bridge persists 修修's intent + prose," which is squarely *within* the red line's spirit — not a breach of it. The clause stays scoped to `Journals/Weekly/` only and **does not generalize** (ADR-039 D5 non-generalization clause preserved; A1 is a sharper *test*, not a reusable authorization to open other folders — each future carve-out still needs its own ADR).

### A2 — Execution timer: 25/75 dual-length, evidence-based logging, derived UFO

- Two lengths: **25-min pomodoro** and **75-min deep block**. 🍅 = duration ÷ 25 floored (aggregator already does this; 75 min → 3🍅 *by duration*, no special case — Codex §1).
- **`timeEntries` stores evidence, not a claim** (panel 3-way): each entry =
  `{startTime, endTime, mode, planned_minutes, actual_minutes, completed}`.
  The live timer logs **actual elapsed** time (fixes the v1 bug where "提早完成" logged a full block — Codex §3).
- **UFO 🤩 is derived, not tagged**: a session counts as 1 UFO iff `mode == "deep"` **and** `actual_minutes ≥ 70` (tolerance under the 75 nominal) **and** `completed`. `deep_sessions_in()` / `ufo_total` are computed from this rule (not from a bare `mode:deep`). The manual `+1🤩` backup writes an explicitly-asserted full block (`completed: true`, flagged as manual) — accepted as a single-user convenience claim, distinct from timer evidence.
- **🍅 = work-hours ceiling** (anti-burnout); **UFO = deep-focus target**. **Incentive note (escalation #7, not changed in v2):** hitting a UFO target consumes 🍅 toward the ceiling, so the two goals can pull against each other. 修修 chose to **observe before adjusting**; revisit after a few weeks of real data.

### A3 — Weekly targets are human intent, stored in named weekly-file keys

The weekly 🍅 target and UFO target are **set by 修修 during the Sunday Weekly Plan** (human intent, A1) and stored as weekly-file frontmatter under enumerated keys:

```yaml
targets: { pomodoro: 35, ufo: 5 }   # 修修-set; Bridge persists, never auto-fills
```

**Updates ADR-039 D5's allowlist** — the machine-maintained weekly-file frontmatter keys become: `start_date`, `end_date`, `status`, `top3`, `next3`, **`targets`**. (Slice E ships a code constant `ufo_target=5`; Slice 2 moves it into `targets`.)

### A4 — top-3 granularity: a wikilink to a project OR a task; ONE scoring rule

Each ≤3 `top3` entry is a wikilink to **either a project or a task** (amends ADR-039 D8). Resolution:
- **task** → `done` state (complete → strikethrough);
- **project** → **completion ratio = done tasks / total tasks** in that project (the single chosen rule — Codex §2/§4; NOT "or Σ🍅"). All tasks done → strikethrough.

Stored as weekly-file `top3: [<wikilink> ×≤3]` (human intent — A1: 修修 selects, Bridge persists), pre-filled from last week's `next3` but **always editable** (the pre-fill is a default, not a lock — see A6). **Slice-2 implementation note:** the wikilink resolver must handle CJK filenames, spaces, and full-width colons (`：`) — Obsidian-flexible resolution that the slug-based project indexer does not yet do (Gemini §1); an unresolved entry must surface visibly, never silently drop.

### A5 — `weekly_priority` is a transitional stopgap → reconcile to weekly-file `top3`

Slice E displays `top3` from a task-frontmatter `weekly_priority: <week-key>` flag — a deliberate non-red-line stopgap so the hero strip works before any weekly file exists. Slice 2 moves `top3` to its canonical home (weekly-file frontmatter, A4); `weekly_priority` is retired (optionally a read-only fallback during transition). Display-only — no migration risk.

### A6 — Weekly Plan / Review = composable steps, not a monolithic wall (amends ADR-039 D7)

The Sunday "週交接" is **not** a single blocking ritual (Gemini §4 "Sunday Wall"). It is a set of **independently-completable, non-blocking steps**, each persisting on its own:

1. **回顧上週** — scorecard (computed) + 6-Q Review (prose, human-typed) — completable Friday.
2. **Carry-forward** — incomplete tasks' `plan[]` into this week (writes **task** `plan[]`; works before this week's weekly file exists — D7).
3. **本週三大要事** — `top3` (project|task, A4), pre-filled from `next3` as an editable default; the UI nudges zero-based re-validation ("still your top 3 *this* week?") rather than silently rolling goals over (Gemini §4).
4. **目標** — `targets` (A3).
5. **逐日排程** — lay tasks across Mon–Fri (Slice-1a scheduler).

**Honest state (Codex §2/§6):** `status: planning → active` flips only when 修修 explicitly completes planning; the UI must **never imply a step is done before its state is truly written**. A week may run `active` with a partially-filled plan — that is shown honestly, not hidden. The Weekly Review is step (1) revisited at week-end.

### A7 — Task (and Project) pages are *scoped* writing surfaces (resolves escalation #6)

The Slice-E task page (timer + stats + plan + Obsidian link) gains an **editable note body** — 修修 writes directly on the page (his ask: drafting "本週電子報" on the task). To avoid fragmenting the writing surface (Codex alt#3, Gemini §4/§5):
- The Bridge editor is scoped to **quick drafts / structured task notes**; it writes the note **body** verbatim (TaskNotes/Projects already 🟡; no LLM authorship — A1).
- A **prominent "在 Obsidian 繼續編輯" deep-link** is always present; **long-form prose is encouraged into Obsidian**, the writing sanctum.
- **v1:** draft content lives in the task body. Agent-assisted publishing is deferred — a separate decision when 修修 wants it.

### A8 — *(removed from ADR-040)* Daily habits → its own future ADR, single-record model

Daily habit logging is **out of scope for ADR-040** (Codex §6, Gemini verdict). It is the **second** red-line breach (`Journals/Daily/` 🔒→🟡) and reverses ADR-039 D6 — it needs its own evidence and writer contract. See "Deferred." The v1 dual-track idea (task toggle + daily-note quantity) is **rejected**: it splits one event across two unrelated files (Gemini §3) — a future ADR must model a habit as a **single unified record** (one Bridge action → one entry, e.g. a structured `habits: [{name, timestamp, duration, status}]` log), so "show the duration of every workout I marked done" is answerable.

---

## Deferred (own future ADRs, not approved here)

- **Daily habit logging** — single-record model; `Journals/Daily/` carve-out decided separately (corrects the rejected A8 dual-track).
- **Project-page authoring** — same scoped-editor pattern as A7 (later slice).
- **Agent-assisted publishing** of task-body drafts.
- **Aggregator hardening (surfaced by Codex §3, fix during Slice 2):** (a) task-page accuracy must use the D3 **union** (`timeEntries[]` ∪ daily `pomodoros[]`), not `timeEntries[]` alone; (b) `collect_daily_intervals()` must **category-filter** so a non-work TaskNotes "work" timer can't inflate weekly work🍅; (c) handle daily sessions with missing/renamed `taskPath` to avoid surviving duplicates.

---

## Consequences

### Positive
- The intent-vs-observation lens (A1) is a *sharper test* than structured-vs-prose, and it shows the `Journals/Weekly/` carve-out honors 修修's original intent (machine never authors; only persists his intent + prose, computes observations).
- Evidence-based `timeEntries` (A2) keeps the vault an auditable primary source — UFO can be re-derived under new rules later.
- top-3 as project|task wikilink lines up with the eventual OKR rollup; one scoring rule keeps it deterministic.
- Composable steps (A6) reduce the "skipped Sunday → stale week" failure mode.

### Negative / risk
- One red-line carve-out remains (`Journals/Weekly/`). Mitigation: A1's reframe + compute-on-read means nothing machine-authored enters it; non-generalization preserved; reviewers still watch for prose-authorship creep.
- Task page grows into a (scoped) workspace. Mitigation: body-only verbatim writes + Obsidian deep-link for long-form.
- UFO/🍅 incentive tension is *accepted unmonitored for now* (escalation #7) — explicitly flagged to revisit.
- The machine-owned-folder architecture (panel's preferred) is **not** adopted; if the single carve-out ever feels wrong in practice, that alternative remains on the table (documented here so the trade-off is not lost).

---

## Slice plan (updates ADR-039's 3-slice plan)

| Slice | Scope | Red line | Status |
|---|---|---|---|
| **E** | Task-page 25/75 timer → `timeEntries[]`, UFO, accuracy, `top3` via `weekly_priority` stopgap | none (TaskNotes 🟡) | **Done** — `870136a` (A2/accuracy fixups land in Slice 2) |
| **2** | `weekly_writer` + 週交接 composable Plan/Review + `top3` (project\|task, one scoring rule) + `targets` in weekly file + reconcile `weekly_priority` + A2 evidence schema + aggregator hardening | **`Journals/Weekly/` 🔒→🟡** | Pending sign-off + vault `CLAUDE.md` diff |
| **W** | Task/Project pages → scoped editable body + Obsidian deep-link (A7) | none (TaskNotes/Projects 🟡) | Planned |
| **D** | Daily habit logging — single-record model (own ADR) | **`Journals/Daily/` 🔒→🟡** (separate decision) | Deferred to own ADR |

---

## Cross-reference

| Concern | Authority |
|---|---|
| Weekly dashboard foundation, slices, week math, red-line framing | ADR-039 |
| `timeEntries[]` write mechanism | ADR-031 D6 |
| Vault is canonical SoT / no parallel store | ADR-030 D1/D4 |
| 3-tier ownership + 🔒/🟡 markers + non-generalization precedent | ADR-028 / VAULT-LAYOUT |
| Red line = self-discipline, not enforcement | `feedback_redline_self_discipline_not_enforcement` |
| 3-way panel audit (Codex + Gemini) | `docs/research/2026-06-02-{codex,gemini}-adr040-audit.md` + `-adr040-panel-integration.md` |
