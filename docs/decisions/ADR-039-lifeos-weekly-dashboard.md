# ADR-039: LifeOS Weekly Dashboard — Tier B of Vault-as-Substrate

**Date:** 2026-05-31 (v1, pre-panel)
**Status:** Proposed
**Deciders:** shosho-chang, Claude Opus 4.8 (1M)
**Related:** [ADR-028](ADR-028-vault-layout-consolidation.md) (vault layout + 3-tier ownership), [ADR-029](ADR-029-bridge-ia-restructure.md) (Bridge IA dual-axis), [ADR-030](ADR-030-vault-as-substrate-read-strategy.md) (D1 vault SoT / D2 FS-direct / D4 substrate routing — **Tier B named in limitation #1**), [ADR-031](ADR-031-project-workspace-migration.md) (Tier C project workspace — **Tier B explicitly deferred in §Out of scope**; D6 Web-self Pomodoro timer), [`VAULT-LAYOUT.md`](../VAULT-LAYOUT.md), [`CONTENT-PIPELINE.md`](../../CONTENT-PIPELINE.md), [`thousand_sunny/CONTEXT.md`](../../thousand_sunny/CONTEXT.md), [`docs/runbooks/syncthing-folder-types.md`](../runbooks/syncthing-folder-types.md)

> **Status note:** This is the **v1 draft authored from a `/grill-with-docs` interview** (2026-05-31). It is intended to go through `multi-agent-panel` (Codex/GPT-5 + Gemini) before 修修's sign-off, because it carries architectural lock-in (a vault-tier reclassification + a new cross-surface schema contract) and several alternatives were rejected with confidence. v2 will fold the panel adjudication, mirroring the ADR-030/031 pattern.

---

## Context

### 1. 修修's framing (verbatim, 2026-05-31 grill)

> 「我想要把 Nakama 的 Bridge 頁面，改成我目前在工作時真實會使用的 Dashboard。之前那個頁面只是暫時放上去的……我並沒有想要把這個 Dashboard 變成一個只放各種 function 跟 Agent 卡片的地方。」
>
> 「主要的概念是我想要用 Nakama 的 Web UI，來存取以及跟我的 Shosho Life OS 這個 Obsidian Vault 來互動。」
>
> 「我的週計畫第一天是禮拜天，所以禮拜天下午我會打開 Dashboard……在上方明顯的位置會有一個週數（例如 W20）……我希望可以按左右來切換。」
>
> 「第二個關鍵數字是『預計的番茄鐘數目』以及『實際執行的番茄鐘數目』……實際在這個禮拜完成的番茄鐘，這個數字要存在哪裡？你可能要幫我想一下。」
>
> 「這個 task 可能是會跨週的一個大任務，所以有的番茄鐘是在這週執行，有的可能是上一週或者下一週。」
>
> 「只有在上週的 Weekly Review 做完之後，系統才會提示我建立本週的 Weekly Dashboard。」

修修's LifeOS combines **OKR** (high-level life tracking) + **Bullet Journal** (daily execution) + **GTD** (task capture). This ADR scopes the **weekly** layer; OKR rollup is deferred (D9).

### 2. This is Tier B — a parked decision, not a new invention

ADR-030 and ADR-031 both name **Tier B (LifeOS Dashboard)** and defer it:

- ADR-030 §Four limitations #1: *"Future Tier B (LifeOS Dashboard mirror) must either Python-rewrite aggregations OR adopt desktop-resident agent topology."*
- ADR-031 §Out of scope: *"Tier B (LifeOS Dashboard mirror) — daily-note task queries, weekly plan, OKR rollup, time-management dashboard. Same D2 pattern but separate ADR. **Triggered when 修修 hits the desk and wants the weekly view.**"*

修修 has now hit the desk and wants the weekly view. This ADR writes the parked decision.

**Pipeline anchor (per `feedback_pipeline_anchored_planning`):** the Weekly Dashboard is **not** a CONTENT-PIPELINE stage. It is the **LifeOS execution / time-management lens** — orthogonal to the 7-stage content pipeline, though content-creation projects' tasks (`Projects/*` → `TaskNotes/Tasks/*`) flow into it as schedulable work. Formally it is the "another lens / infrastructure" escape hatch CONTENT-PIPELINE.md §規劃原則 permits.

### 3. Codebase inventory (verified 2026-05-31 against repo + vault `E:/Shosho LifeOS/`)

**The existing weekly schema is already ~80% of what 修修 described — but dead:**

- `Templates/tpl-weekly-journal.md` already encodes: frontmatter `type: weekly / year / week_number / start_date / end_date / planned_pomodoros / actual_pomodoros / status: planning`; sections 週初規劃 (本週重點專案 dataviewjs-by-project, 本週主要工作 1/2/3, 目標工時), 本週每日統計, 本週合計, 週末回顧 (Highlight / Lowlight / 感恩×3 / 任務完成狀況 / 下週需調整).
- `Journals/Weekly/` is **empty** — never instantiated.
- Its aggregations are **dataviewjs**, which only runs in desktop Obsidian (ADR-030 limitation #1) — **the VPS-hosted Bridge cannot execute it**.
- Worse: the numbers it sums (`total_pomodoros` / `deep_work_pomodoros` in daily-note frontmatter) are **manual fields that are always 0** — the existing weekly aggregation is non-functional even on desktop.

**Pomodoro sessions are already persisted — in two stores (the "split-brain"):**

- TaskNotes plugin config `.obsidian/plugins/tasknotes/data.json`: `"pomodoroStorageLocation": "daily-notes"`, `"pomodoroWorkDuration": 25`. Completed Obsidian pomodoro sessions are written to that day's `Journals/Daily/{YYYY-MM-DD}.md` frontmatter `pomodoros: [{id, startTime, endTime, plannedDuration, type: work|short-break|long-break, taskPath, completed, activePeriods}]` (verified in `Journals/Daily/2026-04-05.md`). **Rich: day-native + task-attributed (`taskPath`) + typed.**
- The **Bridge** already has its own timer (`thousand_sunny/templates/bridge/projects/_pomodoro_dock.html` + endpoints in `thousand_sunny/routers/bridge_projects.py`, ADR-031 D6) that writes `TaskNotes/Tasks/{slug}.md` frontmatter `timeEntries: [{startTime, endTime}]` via `shared/project_writer.py` (`append_timeentry` / `pop_last_timeentry`, atomic rename). **Poor for weekly: no day grouping, no type.**
- The two stores never write the same session (TaskNotes pomodoro → daily only; Bridge timer → task only). TaskNotes does **not** mirror pomodoro sessions back into task `timeEntries[]`.
- **Correction to ADR-031 D6:** D6 stated the TaskNotes plugin pomodoro writes `timeEntries[]` to the task; the live config shows it writes daily-note `pomodoros[]`. D6's "both write the same field" premise was inaccurate. This ADR's D3 supersedes it.

**Estimated pomodoros + scheduling on a task:**

- `TaskNotes/Tasks/*.md` frontmatter: `預估🍅: int` (estimate; custom field `🍅-Estimated` maps to it), `scheduled: <single ISO datetime>`, `status`, `✅: bool`, `projects: ["[[...]]"]`, `timeEntries: [...]`.
- **`scheduled` holds exactly one date.** There is no native multi-date-with-counts primitive. This is the central schema gap for "排到多天、每天幾個番茄".

**Bridge read/write substrate (the patterns Tier B inherits):**

- FS-direct read: `shared/blob_loader.py` `VaultBlobLoader`; vault root via `shared/config.py:get_vault_path()` (`config.yaml: vault_path`, env `VAULT_PATH` override). Indexer precedent: `shared/digest_indexer.py`, `shared/project_indexer.py` (FS-direct list/get).
- Vault write: `shared/project_writer.py` atomic-rename mutation API (`update_frontmatter`, `update_body_section`, `append_timeentry`), Windows retry-on-lock. **Bridge already writes the vault** for `Projects/` (🟡) and `TaskNotes/Tasks/timeEntries[]` (ADR-031 D1).
- ISO week helpers exist (`agents/franky/news_synthesis.py:_iso_week_display` → `2026-W18`), **Monday-start ISO** — used by Franky + daily-note `week:` backlinks.

**The red line:**

- Vault `CLAUDE.md` §3: `Journals/` = 🔒 Human-only, *"完全禁止寫入"*; `Dashboards/` 🔒; `TaskNotes/` 🟡 "Agent 不主動修改". `Projects/` was **already 🟡** before ADR-031 — so ADR-031 never opened a 🔒 folder. **The Weekly Dashboard would be the first time the Bridge writes into a 🔒 Human-only folder** (`Journals/Weekly/`).

### 4. Week-number contradiction (computed 2026-05-31)

| Date | Weekday | ISO (vault/Franky) | 修修's reckoning |
|---|---|---|---|
| 2026-05-31 | Sun | **W22, last day** | **W23, first day** |
| 2026-06-01 | Mon | W23, first day | W23, second day |

修修 runs a **Sunday→Saturday** week with **US/epi-style numbering** (week 1 contains Jan 1). Verified: US week 23 starts Sunday 2026-05-31, matching 修修's "5/31 是第 23 週第一天". ISO 8601 (Monday-start, the basis of every existing vault template + Franky) labels that Sunday W22. The two systems disagree on **both boundary and number**.

---

## Decision

### D1: Tier B = the LifeOS Weekly Dashboard; one weekly file is SoT; Bridge is an interaction skin

- **Weekly Journal** = the vault file `Journals/Weekly/{start-Sunday-date}.md` (e.g. `2026-05-31.md`). **Single source of truth** for the week: plan caches, the 6-question Weekly Review, top-3, and 隨手筆記.
- **Weekly Dashboard** = the Bridge surface `/bridge/weekly`. A pure interaction skin (ADR-031 Tier C pattern): reads via D2 FS-direct, writes via the `project_writer`-style atomic API. Caches nothing durable beyond the vault.
- **Weekly Review** = a section/phase *inside* the Weekly Journal file (not a separate artifact).
- Daily bullet-journal data stays in `Journals/Daily/*` and is **aggregated read-only** by the dashboard (D6).

Per ADR-030 D1 (vault SoT) + D4 (long-form / human-read-in-Obsidian → vault). Everything is Obsidian-visible; honors 修修's "WebUI 只是把 Markdown 提取出來便於互動".

### D2: Week = Sunday→Saturday, labeled by 修修's number; date-keyed file; range aggregation; ISO infra untouched

- The dashboard week runs **Sunday→Saturday**; the displayed number is **修修's US/epi week number** (e.g. "W23 · 5/31–6/6"), computed — always matches what 修修 says.
- The Weekly Journal file is **keyed by its start-Sunday date** (`2026-05-31.md`), not by `W{n}` — unambiguous, sortable, collision-free with ISO.
- The dashboard groups daily notes + tasks by **explicit `start_date..end_date` range**, **not** by the daily-note `week:` wikilink. This **fully decouples** from the ISO infra: Franky, `tpl-daily-journal`, and daily `week:` backlinks keep using ISO (Monday-start) and are not touched.
- **Cost accepted:** the Obsidian graph backlink daily→weekly won't resolve (daily notes carry ISO `[[W 2026-Wnn]]`, weekly file is date-keyed). The dashboard does not need it. An optional `W{n}` alias may be added later if graph links are wanted.
- Left/right arrows navigate prev/next week by date arithmetic.

**Rejected:** strict ISO Monday-start (overrides 修修's stated habit — same class of error ADR-031 rejected for the 15-second hook); Sunday-start but `W YYYY-Wnn.md` filename (re-introduces the ISO collision the date-key avoids).

### D3: Actual pomodoros = a unified read over both session logs; no new store; no write to `Journals/Daily/`

- **"Actual pomodoros this week" is computed, not stored.** The raw sessions already exist; the dashboard reads the **union** of:
  - task `timeEntries[]` (Bridge timer + manual `+1🍅`), and
  - daily-note `pomodoros[]` work sessions (`type: work && completed: true`, Obsidian timer),
  filtered to the week's Sun–Sat date range, attributed to task (via `taskPath` / task file) and to day (via timestamp / daily-note date).
- The two logs are written by two different timers and **never duplicate a session**, so the union is exact — no dedup.
- **The Bridge timer keeps writing `timeEntries[]`** (ADR-031 D6 mechanism unchanged) so Obsidian's `formula.實際🍅` keeps working and **the dashboard never writes `Journals/Daily/`** (which stays 🔒).
- 1 🍅 = **25 min** (TaskNotes config authority), superseding the daily-template comment "1🍅=30分".
- **Refinement of `thousand_sunny/CONTEXT.md` Example dialogue:** the earlier guess "number-of-pomodoros-today aggregate → state.db" is superseded for this case. The aggregate is **derived on read from vault-resident session logs**; nothing is stored in `state.db`. (Ephemeral in-flight timer state may still use the browser / `state.db`; the *counted* number is not stored.)

**Rejected:** "Bridge-timer-only" (drops mobile/Obsidian sessions from weekly totals); "unify everything into daily `pomodoros[]`" (forces Bridge to write 🔒 `Journals/Daily/` and to replicate TaskNotes' full `pomodoros[]` schema — cost not worth it).

### D4: Multi-date scheduling = a `plan[]` array on the task (SoT); `scheduled` auto-synced; views filter `plan[]`

- New task frontmatter field: **`plan: [{date: YYYY-MM-DD, pomodoros: int, reason?: str}]`** on `TaskNotes/Tasks/*.md`. This is the SoT for "this task, N pomodoros on date D". A cross-week task carries its full multi-week allocation directly.
- `scheduled` is **auto-maintained** = the earliest incomplete `plan[]` date, so the TaskNotes plugin calendar UI keeps working.
- Weekly **planned** = Σ `plan[].pomodoros` where `plan[].date ∈ week`. Daily "today" = tasks whose `plan[]` has an entry for today.
- Three-number glossary: **預估🍅 (estimate, task total budget) ≥ planned (Σ allocations in scope) → actual (sessions done)**. For a single-week task planned == estimate; for a cross-week task planned == this week's slice.
- `reason` is the weekend-work justification (D9).

**TaskNotes contract risk (acknowledged, mitigated):** `plan[]` is a custom field TaskNotes ignores (it tolerates unknown frontmatter; the vault already carries custom `預估🍅`). The TaskNotes plugin UI won't render the per-date breakdown — 修修 uses the Bridge for that (consistent with ADR-031's "Obsidian = read/prose; Web = interactive control"). The Obsidian daily-note dataview keys on single `scheduled` and will only show a multi-date task on its earliest date; **the Bridge Today view is the canonical "today's tasks" surface.** Like ADR-031's `timeEntries[]` dependency, this is an implicit plugin-schema contract; a future task-frontmatter schema doc should formalize it.

**Rejected:** plan on the weekly file (scatters a cross-week task's allocation across week files; task doesn't know its own full plan; not Obsidian-task-visible); `state.db` table (not Obsidian-visible — violates D4 of ADR-030 + 修修's "see it in the daily view"); no per-date (single `scheduled` only — loses the explicit cross-week-with-counts requirement).

### D5: Vault write authority — reclassify `Journals/Weekly/` 🔒→🟡 with a field-level contract

- **Only `Journals/Weekly/`** moves from 🔒 Human-only to **🟡 Collab**. `Journals/{Daily,Quarterly,Yearly}/` stay 🔒 (dashboard reads them, never writes).
- **Provenance principle (the crux):** 🔒 "Human-only" governs **content provenance** (no AI-authored prose masquerading as 修修's voice), **not editing tool**. When the Bridge persists what 修修 *typed into a web form*, it is acting as a **second human-input surface** alongside the Obsidian editor — not as an autonomous agent. This is categorically different from "Robin auto-writes a digest."
- **Field-level contract for the weekly file:**
  - **Body prose** (Weekly Review answers, 隨手筆記) = human-authored (via Bridge form or Obsidian) — `<!-- vault:human-only-section -->` marked.
  - **Enumerated frontmatter keys** (`start_date`, `end_date`, `status`, `top3`, `next3`, `planned_pomodoros` cache, `actual_pomodoros` cache) = Bridge machine-maintained.
  - **No LLM-authored prose** in the weekly file in v1. (A future "review assistant" suggesting reflections would require a separate marked section + human-confirm-before-persist; out of scope here.)
- **Bridge writes** the weekly file via the `project_writer` atomic-rename pattern.
- **Mandatory same-PR doc updates** (per VAULT-LAYOUT §6α): `docs/VAULT-LAYOUT.md` §2/§3, `thousand_sunny/CONTEXT.md`, and the vault `CLAUDE.md` cheat-sheet line. **Vault `CLAUDE.md` forbids agent self-edit (§3, line 101)** — its one-line change is provided as a diff for 修修 to apply by hand (§Vault CLAUDE.md amendment below).
- **Syncthing:** `Journals/Weekly/` is **Send & Receive** on all three devices (bidirectional, like `Daily/` / `Projects/`, per the syncthing-folder-types runbook). Atomic writes + single-user-non-concurrent (`user_vault_edit_pattern_no_concurrent`) keep conflict probability ~0.

**Rejected:** move the weekly file out of `Journals/` into a new Bridge-owned folder (dodges the red line but fragments the weekly journal away from daily/quarterly/yearly and abandons the existing `Journals/Weekly/` + `tpl-weekly-journal` convention); keep `Journals/` fully 🔒 and do reviews only in Obsidian (defeats the core "do the review in the dashboard" requirement).

### D6: Daily bullet-journal section = read-only projection (v1)

- The dashboard's daily section shows, per weekday, the tasks scheduled that day (from `plan[]`) grouped by **category**, plus a **read-only** display of habits.
- **Category model:** 工作 = task whose project has `area: work`; 雜事 = task without a project or other area; **運動 / 冥想 are habits, not tasks** — rendered read-only from the daily note's `exercise_type` / `exercise_duration` / `meditation_minutes`. Optional task-level `category` override for precision.
- **Task completion toggling writes `TaskNotes/Tasks/` (status/`✅`)** — already permitted (ADR-031 envelope). Habit logging, daily reflection, and ad-hoc daily bullets stay in Obsidian → **`Journals/Daily/` stays 🔒**, so the red line opens **only** `Journals/Weekly/`.

**Rejected:** make the daily section fully editable in the dashboard (forces a second 🔒→🟡 reclassification of `Journals/Daily/` + more sync surface — deferred to backlog); no daily section at all (loses the Today view 修修 wants).

### D7: Weekly ritual state machine — soft-gated, review-before-new-week

- **`status` enum:** `planning` → `active` → `reviewed`.
- **Sunday landing:** if today's week has no weekly file yet → the dashboard lands on **last week's Review view** (read-only aggregation: pomodoro execution rate `actual/planned` + incomplete-task list). Once last week is `reviewed` and this week's file is created → it lands on the current week.
- **Carry-forward:** from last week's review, 修修 assigns incomplete tasks' `plan[]` dates into this week → writes the **task** `plan[]`. Because `plan[]` lives on the task (D4), carry-forward works **before this week's weekly file exists**.
- **Create this week:** on submitting last week's review (`status=reviewed`), the "建立本週" action becomes prominent. The new file's `top3` is pre-filled from last week's review `next3` (D8 closed loop); `planned` is computed from `plan[]` already pointing into the week (incl. the just-done carry-forward); `actual` = 0.
- **Soft gate (not hard lock):** the dashboard *guides* the review→create order and shows a reminder if last week is unreviewed, but 修修 can override (escape hatch). Per `feedback_redline_self_discipline_not_enforcement` + ADR-031 D4 soft-gate precedent.

**Rejected:** hard gate (a forgotten review locks 修修 out of planning the new week); no gate at all (loses the ritual discipline 修修 asked for).

### D8: Weekly Review = 修修's exact 6 questions; top-3 linked to real tasks; closed loop

1. Highlight · 2. Lowlight · 3. 學到的東西 (lessons) · 4. 感恩 · 5. **本週最重要 3 件 task 的完成率** · 6. **下週最重要 3 件 task**.

- **top-3 is a property of the week**, stored as weekly-file frontmatter `top3: [<task wikilink ×3>]`, designated at planning (the legacy "本週主要工作 1/2/3" slot).
- **#5 auto-computed**: completion rate = (top-3 tasks with `status: done`) / 3, with pomodoro progress shown alongside.
- **#6 → next week**: review `next3` is copied into the next week's `top3` at creation (D7). plan → execute → score → next top-3 = a closed weekly loop.
- Questions 1–4 + the prose of 5/6 are human-authored body sections; `top3` / `next3` are frontmatter.

**Rejected:** top-3 as free-text only (can't auto-compute completion rate); the legacy template's 5-section set (missing "lessons", no structured top-3, no loop).

### D9: 隨手筆記 / weekend-work / OKR scope / task views

- **隨手筆記** = a week-scoped `## 隨手筆記` body section in the weekly file (human-authored). Not a global scratchpad, not Inbox capture.
- **Weekend work** = scheduling a 工作-category `plan[]` entry on Sat/Sun **requires a `reason`** string (stored on that `plan[]` entry); the weekend 工作 row displays it. Soft-required (must type to save), **not** a hard ban.
- **OKR rollup is deferred** to a later iteration. v1 surfaces no OKR objectives/KR. The `Projects/* → quarter / parent_kr` linkage in data is retained, just not displayed.
- **Task views:** the dashboard task list offers **three views — Today / 整週 / 按專案 (by-project)**. (修修 confirmed the third, previously-unstated view is by-project grouping.)

---

## Consequences

### Positive

- **Revives a dead schema** — the `tpl-weekly-journal` design 修修 already wanted, finally functional via Python aggregation on the VPS Bridge (no dataviewjs dependency).
- **Answers "where do actual pomodoros live?" without new storage** — they were already in two vault logs; the dashboard reads their union.
- **Surgical red line** — only `Journals/Weekly/` opens; `Daily`/`Quarterly`/`Yearly` stay 🔒. Pomodoro counting touches no 🔒 folder.
- **Cross-week tasks modeled natively** — `plan[]` on the task is exactly the shape 修修's "跨週大任務" needs.
- **Closed weekly loop** — next-3 → next week's top-3; plan → execute → score.
- **Reuses proven patterns** — `digest_indexer`/`project_indexer` (read) + `project_writer` (write) + the existing Pomodoro dock (timer). Slices 0/1 ship without touching the red line.

### Negative / risk

- **First 🔒→🟡 reclassification** — sets precedent that the Bridge-as-human-input-surface may write a (narrowly-scoped) Human-only folder. The provenance principle (D5) is the guardrail; reviewers should watch for scope creep into other 🔒 folders.
- **`plan[]` is an implicit TaskNotes contract** — a plugin schema change could desync; the per-date breakdown is invisible in the TaskNotes plugin UI. Mitigation: future task-frontmatter schema doc; Bridge is the canonical per-date surface.
- **Two `scheduled` semantics** — multi-date tasks only show on their earliest date in Obsidian's daily dataview; the Bridge Today view is canonical. Minor Obsidian-side regression, accepted (mirrors ADR-031's accepted Obsidian-interactivity loss).
- **Bridge is a SPOF for interactive weekly flows** — when VPS Bridge is down, vault md stays canonical and Obsidian still edits prose/frontmatter, but the dashboard's aggregation/timer/quick-edit are blocked (same trade as ADR-031).
- **Date-keyed weekly file breaks the Obsidian daily→weekly graph backlink** — accepted; optional `W{n}` alias later.
- **No `*.sync-conflict-*` handling** for the weekly file in v1 — inherits ADR-030's open follow-up (#696); `Journals/Weekly/` being Send&Receive means a conflict is theoretically possible. Detection-only for now.

### Neutral

- Obsidian remains a first-class read + prose-edit surface for the weekly file (YAML-valid frontmatter, plain-markdown body).
- `state.db` may hold ephemeral in-flight timer state + per-call audit, but **no weekly data and no pomodoro aggregate**.
- `KB/`, `OKRs/`, `Inbox/`, `AgentOutputs/` untouched.

---

## Open questions resolved (grill 2026-05-31)

| Q | Decision |
|---|---|
| Artifact/glossary model | D1 — single weekly file = SoT; Dashboard = skin; Review = section |
| Week boundary + numbering | D2 — Sun→Sat, 修修's W-number, date-keyed file, range aggregation, ISO untouched |
| Actual-pomodoro source/storage | D3 — union read of `timeEntries[]` ∪ daily `pomodoros[]`; no new store; no `Daily/` write |
| Multi-date scheduling primitive | D4 — `plan[]` on the task; `scheduled` auto-synced |
| Vault write authority | D5 — `Journals/Weekly/` 🔒→🟡; provenance principle; field-level contract |
| Daily section read/write | D6 — read-only projection; category model; completion via TaskNotes |
| Weekly ritual gate | D7 — soft gate; planning→active→reviewed; carry-forward via task `plan[]` |
| Review question set | D8 — 修修's 6 Qs; top-3 → real tasks; #5 auto; #6 → next week |
| Notes / weekend / OKR / views | D9 — week-scoped notes; weekend reason required; OKR deferred; Today/整週/by-project |

---

## Migration plan — 3 vertical slices (red line isolated to Slice 2)

### Slice 0 — Read-only foundation (zero risk, zero red line)

- `shared/weekly_indexer.py` (new; modeled on `digest_indexer`/`project_indexer`): week-boundary math (Sunday-start, US W-number), `planned` (Σ task `plan[]` in range) + `actual` (union `timeEntries[]` ∪ daily `pomodoros[]` in range) aggregation, incomplete-task scan.
- `shared/pomodoro_aggregator.py` (new): the union read (task `timeEntries[]` + daily-note `pomodoros[]`).
- `thousand_sunny/routers/bridge_weekly.py` (new): `GET /bridge/weekly` (current/`?week=` nav), read-only landing — week header + L/R nav, planned/actual + execution rate, Today/整週/by-project task views (read-only), daily read-only projection, last-week review view (read-only).
- Templates under `thousand_sunny/templates/bridge/weekly/`; chassis-nav slot; tokens-consistent CSS (`docs/design-system.md`).
- Tests: `tests/test_weekly_indexer.py`, `tests/test_pomodoro_aggregator.py`, `tests/test_bridge_weekly.py` (fixture vault).
- **No vault writes.** Proves the revived aggregation.

### Slice 1 — Task scheduling writes (TaskNotes only — ADR-031 envelope, no new red line)

- `shared/project_writer.py` (or sibling `task_writer.py`): write `plan[]` + auto-sync `scheduled`; weekend-work `reason`.
- Carry-forward UI (assign incomplete tasks' `plan[]` dates into a week); reuse the existing Pomodoro dock for actuals.
- Tests for `plan[]` write + `scheduled` sync + idempotency.

### Slice 2 — Weekly-file writes (the red-line slice — gated on this ADR's acceptance, ± panel)

- **Docs (same PR):** `VAULT-LAYOUT.md` §2/§3 (`Journals/Weekly/` 🔒→🟡 + producer/consumer rows), `thousand_sunny/CONTEXT.md` glossary, Syncthing folder-type note; vault `CLAUDE.md` diff handed to 修修.
- Weekly-file create-from-template; write Review answers (6 Qs), `top3`/`next3`, 隨手筆記, `status` transitions (soft gate); closed-loop next3→top3.
- `shared/weekly_writer.py` (atomic, field-level contract); `tests/test_weekly_writer.py` (human-section vs machine-frontmatter separation, status FSM, atomic rename).

### Backlog (post-v1)

OKR rollup; dashboard-side daily habit logging (`Journals/Daily/` 🔒→🟡 — separate decision); mobile layout; pomodoro-complete chime/notification; `W{n}` graph-backlink alias; `*.sync-conflict-*` handling.

---

## Out of scope

- OKR objectives / KR rollup (D9 — deferred).
- Writing `Journals/Daily/` from the dashboard (D6 — habit logging stays in Obsidian).
- Quarterly/Yearly journal surfaces.
- Multi-user / partner-facing weekly views (single-user Bridge only).
- Provider diversity for any future LLM in the weekly surface (none in v1).
- Reverse-syncing the Bridge timer into daily `pomodoros[]` (D3 keeps stores separate, reads union).

---

## Vault `CLAUDE.md` amendment (hand-applied by 修修 — agents may not self-edit it)

In `E:/Shosho LifeOS/CLAUDE.md` §2 cheat-sheet + §3 permissions table, narrow the `Journals/` blanket 🔒 to carve out `Journals/Weekly/`:

```diff
- ├── Journals/         🔒 你寫 (Daily / Weekly / Quarterly / Yearly)
+ ├── Journals/         🔒 你寫 (Daily / Quarterly / Yearly)
+ │   └── Weekly/       🟡 協作 — 你經 Bridge Weekly Dashboard 或 Obsidian 寫 review/筆記；Bridge 機器維護 frontmatter 快取 (ADR-039)
```
```diff
- | `Journals/` | ✅ | ❌ | 完全禁止寫入。可引用、可分析，但不可修改任何內容 |
+ | `Journals/{Daily,Quarterly,Yearly}/` | ✅ | ❌ | 完全禁止寫入。可引用、可分析，但不可修改任何內容 |
+ | `Journals/Weekly/` | ✅ | 🟡 | 經 Bridge Weekly Dashboard：body 散文由人輸入(Bridge 表單或 Obsidian)，列舉的 frontmatter 快取由 Bridge 維護；無 LLM 代筆散文 (ADR-039) |
```

---

## Cross-reference summary

| Concern | Authority |
|---|---|
| Vault is canonical SoT / FS-direct read | ADR-030 D1 / D2 |
| Substrate routing (vault vs state.db) | ADR-030 D4 (this ADR refines the pomodoro-aggregate example) |
| Tier B parked → now unparked | ADR-030 limitation #1 + ADR-031 §Out of scope |
| Web-self Pomodoro timer | ADR-031 D6 (D3 here corrects its `timeEntries[]` premise) |
| Bridge atomic vault write | ADR-031 D1 + `shared/project_writer.py` |
| 3-tier ownership + marker convention | ADR-028 / VAULT-LAYOUT §1, §4 (this ADR adds the first 🔒→🟡 carve-out) |
| Bridge IA (nav slot) | ADR-029 (Weekly slot placement TBD in Slice 0) |
| Soft-gate philosophy | `feedback_redline_self_discipline_not_enforcement` |
| Pipeline anchor (this is Tier B, not a content stage) | CONTENT-PIPELINE.md §規劃原則 escape hatch |
| Syncthing folder type | `docs/runbooks/syncthing-folder-types.md` (Journals/Weekly = Send & Receive) |
