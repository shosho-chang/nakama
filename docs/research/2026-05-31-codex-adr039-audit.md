# ADR-039 — Codex (GPT-5) Panel Audit (verbatim)

_Multi-agent panel step 2 · 2026-05-31 · model: codex/gpt-5 · read-only grounding in worktree_

## 1 — CODE GROUNDING

The cited code paths mostly exist and support the ADR’s broad implementation direction, but several claims are stronger than the code warrants.

`shared/project_writer.py` is the correct precedent for vault writes. It uses tmp-file plus `os.replace` with Windows lock retries in `_atomic_write` (`shared/project_writer.py:74-139`), writes `Projects/{slug}.md` frontmatter/body via `update_frontmatter` and `update_body_section` (`:145-201`), and mutates TaskNotes `timeEntries[]` via `append_timeentry` / `pop_last_timeentry` (`:350-388`, `:470-499`). The ADR is accurate that Bridge already writes `Projects/` and `TaskNotes/Tasks/`. It is not accurate to imply all writer paths have the same concurrency protection: `append_timeentry` and `pop_last_timeentry` have an mtime guard, but `update_frontmatter`, `update_body_section`, and `update_task_status` do not.

`thousand_sunny/routers/bridge_projects.py` confirms the Bridge timer path. `POMODORO_MINUTES = 25` is hard-coded (`:99`), `/timer/complete` writes a 25-minute synthetic entry (`:492-511`), manual `+1🍅` writes the same shape (`:649-680`), and `_write_pomodoro_entry` appends `timeEntries[]`, auto-flips `to-do` to `doing`, then recomputes project rollup (`:787-832`). `_scan_tasks` computes actual pomodoros from `timeEntries[]` duration divided by 25 and floored (`:884-944`). The template `thousand_sunny/templates/bridge/projects/_pomodoro_dock.html` contains the active task selector, timer display, rollup, status dropdown, and `+1/-1` forms. This grounds ADR-039 D3’s Bridge-side source, but it also exposes a future mismatch: the current implementation recognizes status values `("to-do", "doing", "done", "paused")` in `shared/project_writer.py:391`, while older ADR/task prose sometimes says `in_progress` or mentions `✅`.

The FS-direct read precedents are real. `shared/blob_loader.py:52-129` is a sandboxed vault/books-root blob reader. `shared/digest_indexer.py:80-255` and `shared/project_indexer.py:117-304` are no-DB, direct filesystem indexers. `shared/digest_indexer.py` also now has explicit Syncthing conflict-file detection (`:35-40`, `:147-172`), so ADR-039 should not ship a new weekly reader without equivalent detection.

`agents/franky/news_synthesis.py:_iso_week_display` exists and is ISO-based (`:236-239`). ADR-039 is right that this helper must not be reused for Sunday-start week labels.

No implemented weekly surface exists yet: I found no `shared/weekly_indexer.py`, `shared/pomodoro_aggregator.py`, `thousand_sunny/routers/bridge_weekly.py`, `shared/weekly_writer.py`, or corresponding tests. ADR-039 is still architectural design, not partially implemented code. However, `docs/VAULT-LAYOUT.md` and `thousand_sunny/CONTEXT.md` in this worktree already contain ADR-039’s reclassification and `/bridge/weekly` glossary entries. That is governance drift: a Proposed ADR has already changed the canonical layout doc as if accepted.

## 2 — DRIFT DETECTION

D1 and D2 mostly align with ADR-030: vault as canonical SoT, FS-direct reads, no default DB mirror. The direction is correct. The drift is the word “cache.” D1/D5 mention `planned_pomodoros` and `actual_pomodoros` frontmatter caches, while D3 says actual pomodoros are computed on read and “no new store.” A weekly-file frontmatter cache is still persistence. Do not store `actual_pomodoros` in v1 unless the ADR defines it as a stale display snapshot with `computed_at`, invalidation rules, and “not source of truth” semantics. Cleaner: remove `actual_pomodoros` and `planned_pomodoros` writes from v1 weekly frontmatter and compute them live.

D5 is the largest drift against ADR-028. ADR-028 says Human-only covers `Journals/`, `OKRs/`, and `Dashboards/`, and that today’s only collab page type is `Projects/{title}.md` (`docs/decisions/ADR-028-vault-layout-consolidation.md:53-55`). ADR-028 did allow a Journals exception, but it was explicitly single-use, mechanical, path-only, and “does NOT generalize” (`:179-182`). ADR-039’s provenance principle is broader: “Bridge persisting human-typed input” becomes a general rationale for writing Human-only folders. That erodes the red line unless it is narrowed. The ADR must say this carve-out does not generalize to Daily, Quarterly, Yearly, OKRs, Dashboards, Templates, or Scripts, and future carve-outs require a separate ADR.

The field-level contract is directionally compatible with ADR-028’s marker convention, but it is not yet enforceable. `docs/VAULT-LAYOUT.md` audits human-only sections only for `Projects/{title}.md` markers (`:265-291`). Weekly needs its own registered marker policy and tests. `weekly_writer.py` must write only allowlisted frontmatter keys and named form-backed body sections. It must refuse to run if the target file has unknown machine markers, malformed YAML, or a changed mtime/hash.

D6 also drifts from current code. ADR-039 says completion toggling writes `status/✅`; current Bridge writer updates `status` only. `shared/lifeos_writer.py` emits both an estimate field and a boolean done field, but `shared/project_writer.update_task_status` does not keep the boolean in sync. Pick one canonical completion field for Bridge, or update both atomically. Do not leave TaskNotes, Bridge, and dataview with three competing completion meanings.

## 3 — NUMERICAL / FACTUAL CLAIMS

The 25-minute claim is grounded today. The router hard-codes 25, ADR-031 says 25, and the provided vault fact says TaskNotes config has `pomodoroWorkDuration: 25`. But ADR-039 calls TaskNotes config the authority. Then the implementation must read a central config or TaskNotes config at startup, not keep an unrelated hard-coded Bridge constant forever. Otherwise the first config change silently splits Obsidian and Bridge math.

The “no double-count” claim in D3 is false as an architectural invariant. Different stores do not prove different sessions. They only prove different write paths. A user can run the Obsidian timer and click Bridge `+1🍅` for the same physical session; a future TaskNotes setting change can mirror to `timeEntries[]`; a manual repair can duplicate intervals. D3 must replace “never duplicate” with deterministic dedup or overlap detection. Use normalized records with `source`, `source_id`, `task_key`, `start`, `end`, `active_seconds`, and `count_policy`. Exact duplicate IDs are easy; overlapping intervals for the same task within a tolerance must raise a warning instead of silently summing.

D3 also needs precise time semantics. Count only daily `pomodoros[]` where `type == "work"` and `completed == true`. For paused/resumed sessions, use `activePeriods` for duration; do not blindly use `endTime - startTime`, because pauses inflate work. For weekly inclusion, use `endTime` converted to Asia/Taipei as the completion date. Do not use the daily-note filename as authority, because sessions can cross midnight or be repaired into the wrong note. For Bridge `timeEntries[]`, keep the existing duration/25 floor for project rollups, but weekly “actual pomodoros” should define whether it counts sessions or minutes. Mixing “one completed Obsidian work session = 1” with “Bridge arbitrary minutes / 25” is acceptable only if documented.

D2’s example is correct: Sunday 2026-05-31 is ISO W22 but Sunday-start W23 under “week 1 contains Jan 1.” The danger is year boundary math. Python `%U` is not enough because it has week 00 before the first Sunday. The ADR must require tests for `2025-12-28` through `2026-01-03` and `2026-12-27` through `2027-01-02`. A file named `2026-12-27.md` may display as 2027 W1 if the week contains Jan 1, 2027. That is fine, but `year` and `week_number` must be derived from the week-year, not `start_date.year`.

Scan cost is acceptable for v1, not free. ADR-031 documents roughly 600 TaskNotes files as of 2026-05-24. A weekly view must scan all TaskNotes files for `plan[]` and Bridge `timeEntries[]`, plus seven Daily notes for Obsidian `pomodoros[]`, plus projects for category/area. That is still within D2 if done once per page load and measured. Do not rescan all tasks on every timer tick or every htmx fragment. Add a performance test budget and request-scoped or process-local mtime cache if needed; do not add a durable DB mirror without ADR-030 D5 measurements.

## 4 — ASSUMPTION PUSH-BACK

The D5 provenance principle is not sound as written. Human-only is not only about prose authorship; it is also about blast radius. A web form is still software mutating a sacred folder. Bugs can erase review prose, normalize YAML badly, reorder fields in noisy ways, or write stale data over an Obsidian edit. The correct principle is narrower: `Journals/Weekly/` may become a form-backed human journal surface with machine-maintained metadata, under an allowlisted writer and audit rules. Do not state a general “Bridge as human-input surface may write Human-only folders” principle.

Single-user does not eliminate concurrency. The existing system already acknowledges Syncthing, Obsidian locks, and mtime races. `project_writer.append_timeentry` has a guard because this risk is real. Weekly writer must use an `If-Match`-style hidden hash/mtime from the rendered page and reject stale writes with a 409. Atomic rename prevents torn writes; it does not prevent lost updates.

The soft gate in D7 is the right product philosophy, but it needs honest state. If 修修 overrides and creates this week before last week is reviewed, the weekly file must record that state as an override or leave last week explicitly unreviewed. Do not make the UI look ritually complete when the ritual was skipped. Persisting a tiny `review_override_reason` or `created_before_previous_review: true` field is enough.

The Slice plan is good, but the current worktree violates its own sequencing. Slice 2 says docs reclassifying `Journals/Weekly/` happen after acceptance. `docs/VAULT-LAYOUT.md` already says Weekly is 🟡 and cites `thousand_sunny/routers/bridge_weekly.py`, a file that does not exist. Revert that doc change until ADR-039 is accepted, or mark it clearly as proposed in the ADR branch. Do not let canonical docs describe non-existent code as producer reality.

## 5 — ALTERNATIVES NOT CONSIDERED

1. Keep `plan[]` on the task, but do not auto-rewrite `scheduled` except on explicit Bridge plan writes. Treat `scheduled` as a TaskNotes convenience projection, not a source. If Obsidian/TaskNotes later changes `scheduled`, Bridge should surface “scheduled differs from plan” and ask 修修 to reconcile. Tradeoff: Obsidian’s calendar is less magical. Benefit: no hidden tug-of-war with plugin-owned UI.

2. Use dated child TaskNotes files for multi-day work: `Project - Task - 2026-06-02.md`, each with its own estimate and `scheduled`. The parent task remains the conceptual work item. Tradeoff: more files and noisier task lists. Benefit: native TaskNotes calendar behavior, no custom `plan[]` contract, no derived `scheduled` field.

3. Store weekly allocations in the weekly file as a task-linked planning ledger, then optionally project today’s slice into TaskNotes. Tradeoff: cross-week task planning is scattered across week files. Benefit: the weekly plan is fully visible in the weekly journal, and TaskNotes frontmatter stays closer to plugin-native semantics. This is the safer fallback if D4’s plugin contract proves brittle.

Do not use TaskNotes recurrence/rrule for this problem. Recurrence models repeated tasks; ADR-039 needs finite allocation slices of one cross-week task. Recurrence will make the data look native while encoding the wrong concept.

## 6 — FINAL VERDICT

Approve with modifications. Slice 0 is safe to build now. Slice 1 is acceptable only after the task schema is formalized. Slice 2 must wait for the D5 red-line changes and doc sequencing cleanup.

Required changes:

1. D5: Replace the broad provenance principle with a narrow, non-generalizing carve-out for `Journals/Weekly/` only. Add a writer allowlist, mtime/hash stale-write rejection, human-section marker audit, and same-PR `VAULT-LAYOUT` update only after acceptance.

2. D3: Remove “never duplicate.” Implement normalized session records, overlap warning/dedup, `activePeriods` handling, `type: work && completed`, and `endTime`/Asia-Taipei week inclusion tests.

3. D4/D6: Publish a TaskNotes frontmatter schema before writing `plan[]`. Define `plan[]` completion math, `scheduled` projection rules, plugin-side conflict behavior, and whether Bridge writes `✅` or `status` only.

4. D2: Add year-boundary tests and define `week_year` separately from weekly file `start_date.year`. Do not reuse ISO helpers or Python `%U` without wrapping the Jan 1 rule.

5. D1/D5: Stop persisting `actual_pomodoros` as a weekly frontmatter cache in v1, or label it as a stale snapshot with `computed_at`. The clean v1 path is computed-on-read.
