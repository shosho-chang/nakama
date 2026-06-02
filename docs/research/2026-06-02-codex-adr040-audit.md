**1 — CODE GROUNDING**

Slice E is mostly real, but ADR-040 overstates a few semantics. The repo is at `870136a` and that commit does include the claimed files. `log_time_entry()` exists and appends `{startTime, endTime, mode}` to task frontmatter `timeEntries[]`, with `mode ∈ {"pomodoro","deep"}` and no `Journals/` write (`shared/weekly_writer.py:27-31`, `shared/weekly_writer.py:253-290`). `GET /bridge/weekly/task/{slug}` and `POST /bridge/weekly/task/{slug}/log` exist (`thousand_sunny/routers/bridge_weekly.py:239-295`). The task page has 25/75 buttons, manual backup buttons, and hidden forms that submit the mode (`thousand_sunny/templates/bridge/task.html:80-117`).

A2’s “75 → 3🍅 by duration, no special case” is true for pomodoro math. `POMODORO_MINUTES = 25`, `collect_timeentry_intervals()` ignores `mode`, and `summarize()` computes `int(total_minutes // 25)` (`shared/pomodoro_aggregator.py:41`, `shared/pomodoro_aggregator.py:182-199`, `shared/pomodoro_aggregator.py:237-253`). A 75-minute `deep` entry becomes 3🍅 because it is 75 minutes, not because `mode == "deep"`.

UFO counting is different: UFO is tag-based, not duration-based. Weekly UFO count comes from `mode == "deep"` in task `timeEntries[]` (`shared/weekly_indexer.py:273-281`, `shared/weekly_indexer.py:559-560`). `ufo_total` checks positive duration, but weekly `deep_sessions_in()` does not validate duration at all (`shared/weekly_indexer.py:258-264`, `shared/weekly_indexer.py:273-281`). The Bridge route normally creates 75-minute deep entries via `_MODE_MINUTES = {"pomodoro": 25, "deep": 75}` (`thousand_sunny/routers/bridge_weekly.py:207`, `thousand_sunny/routers/bridge_weekly.py:288-291`), but the writer itself accepts any positive span up to 6 hours with `mode="deep"` (`shared/weekly_writer.py:270-276`). So the shipped invariant is “UFO = deep tag emitted by the Bridge UI,” not “UFO = verified 75-minute interval.”

The “accuracy” claim is implemented only for task-local Bridge timeEntries. `WeeklyTask.actual_pomodoros` and `accuracy_pct` sum `self.time_entries` only (`shared/weekly_indexer.py:248-271`). They do not include daily-note `pomodoros[]`, even though ADR-039 D3 says actual pomodoros are the union of task `timeEntries[]` and daily `pomodoros[]` (`docs/decisions/ADR-039-lifeos-weekly-dashboard.md:108-120`). Weekly totals use the union aggregator; task-page accuracy does not.

Top-3 is exactly the stopgap ADR-040 says it is, not A4’s final model. Current `view.top3` is `WeeklyTask` objects selected by task frontmatter `weekly_priority` (`shared/weekly_indexer.py:428-429`, `shared/weekly_indexer.py:561-562`). `read_review()` can parse weekly-file `top3`, but `view()` does not use it (`shared/weekly_indexer.py:500-501`, `shared/weekly_indexer.py:561-562`). Project-or-task top3 is not shipped.

I could not run the test suite because `pytest` is not installed in this environment. This audit is therefore code-inspection grounded, not test-execution grounded.

**2 — DRIFT DETECTION**

ADR-040 materially changes ADR-039. Some changes are coherent amendments; others weaken invariants ADR-039 relied on.

D5 in ADR-039 was deliberately narrow: “Only `Journals/Weekly/` moves from 🔒 Human-only to 🟡 Collab” and `Journals/{Daily,Quarterly,Yearly}/` stay locked (`docs/decisions/ADR-039-lifeos-weekly-dashboard.md:136-143`). ADR-040 A1 turns that into a general folder-independent lens and explicitly says future `Journals/Daily/` reclassification honors the same spirit (`docs/decisions/ADR-040-weekly-execution-layer.md:23-31`). That is drift. It may be the owner’s new intent, but do not present it as a harmless refinement of D5. D5’s non-generalization clause was a core guardrail; A1 turns it into a reusable authorization theory.

D6 drift is stronger. ADR-039 D6 says the daily section is read-only, habit logging stays in Obsidian, and `Journals/Daily/` stays 🔒 (`docs/decisions/ADR-039-lifeos-weekly-dashboard.md:154-160`). ADR-039 also lists writing `Journals/Daily/` as out of scope (`docs/decisions/ADR-039-lifeos-weekly-dashboard.md:268-269`). ADR-040 A8 puts Daily writes on the roadmap as a second carve-out (`docs/decisions/ADR-040-weekly-execution-layer.md:76-83`). That is not just an amendment; it reverses an explicit rejected boundary.

D7 is a softer drift. ADR-039’s Sunday landing is review-first: if today’s week has no file, land on last week’s Review; then “建立本週” becomes prominent after review (`docs/decisions/ADR-039-lifeos-weekly-dashboard.md:162-168`). ADR-040 A6 changes this to a combined “REVIEW-last-week + PLAN-this-week” handoff (`docs/decisions/ADR-040-weekly-execution-layer.md:57-67`). That can be coherent, but the ADR must define transactional state: where do `top3`, targets, and draft planning data live before the weekly file exists? Do not let the UI imply the ritual is complete before `status` honestly moves.

D8 drift is risky. ADR-039 D8 defines top-3 as task wikilinks and auto-computes completion as top-3 tasks done / 3 (`docs/decisions/ADR-039-lifeos-weekly-dashboard.md:172-179`). ADR-040 A4 expands top3 to project-or-task wikilinks and punts project scoring to “done tasks / total, or Σ🍅” (`docs/decisions/ADR-040-weekly-execution-layer.md:44-51`). That “or” is not an implementation decision. It breaks the exact D8 scoring invariant until one scoring rule is chosen.

ADR-040 A3 also amends D5 without saying so. ADR-039’s weekly-file machine-maintained frontmatter allowlist is `start_date`, `end_date`, `status`, `top3`, `next3` (`docs/decisions/ADR-039-lifeos-weekly-dashboard.md:144-147`). ADR-040 adds weekly 🍅 target and UFO target in weekly-file frontmatter (`docs/decisions/ADR-040-weekly-execution-layer.md:40-42`) but does not name the keys or update the D5 allowlist.

**3 — NUMERICAL / FACTUAL CLAIMS**

The basic numbers are correct: 25 minutes = 1🍅, 75 minutes = 3🍅, and UFO target currently defaults to 5. The code constant is `UFO_WEEKLY_TARGET = 5` (`shared/weekly_indexer.py:53`) and the view exposes that constant (`shared/weekly_indexer.py:586-587`). ADR-040 A3 correctly says Slice E ships a constant and Slice 2 should move it into the weekly file (`docs/decisions/ADR-040-weekly-execution-layer.md:40-42`).

The edge cases are not clean enough for the ADR’s wording. The task page has a “完成 · 記一段” button while the timer is running (`thousand_sunny/templates/bridge/task.html:88-91`). Clicking it submits immediately (`thousand_sunny/templates/bridge/task.html:149-159`, `thousand_sunny/templates/bridge/task.html:168-169`). The server then logs a full 25 or 75 minutes ending “now” (`thousand_sunny/routers/bridge_weekly.py:288-291`). So an early finish after 10 seconds can log 75 minutes and 1 UFO. This is not a pause/early-finish timer; it is a fixed-block logger. A2 should say that plainly or remove the early-complete affordance.

Merged-interval double-counting is partially handled. The aggregator merges overlapping intervals per same `task_key` and warns on cross-source overlaps (`shared/pomodoro_aggregator.py:202-234`). But it only merges when `task_key` matches. Daily sessions derive task keys from `taskPath` stem (`shared/pomodoro_aggregator.py:137-141`); Bridge entries use the task slug passed by the indexer (`shared/pomodoro_aggregator.py:182-199`, `shared/weekly_indexer.py:551-556`). If a daily session lacks `taskPath`, has a renamed stem, or points to a task not in the Bridge task set, the duplicate can survive as a separate task bucket.

There is also a category-counting mismatch. ADR-040 says 🍅 = work hours and only work-category tasks count (`docs/decisions/ADR-040-weekly-execution-layer.md:38`). The indexer only passes work-category `timeEntries[]` into the aggregator (`shared/weekly_indexer.py:549-556`), but `collect_daily_intervals()` includes every daily-note `pomodoros[]` row where `type == "work" && completed`, with no task category filter (`shared/pomodoro_aggregator.py:144-179`). A non-work LifeOS task logged through Obsidian as a TaskNotes “work” timer can inflate weekly work 🍅.

**4 — ASSUMPTION PUSH-BACK**

A1’s “structured data = machine-OK, prose = human-only” boundary is useful, but it is not clean enough to govern red-line expansion by itself. Structured fields can encode judgment. `top3` is the obvious example: selecting the week’s three most important items is editorial prioritization, not neutral bookkeeping. It is acceptable for Bridge to persist the human’s selected wikilinks; it is not acceptable for the ADR to classify `top3`/`next3` as simply “Machine maintains” without distinguishing “human-selected, machine-persisted” from “machine-selected” (`docs/decisions/ADR-040-weekly-execution-layer.md:27-31`).

A6 has the same problem with auto-scored top3 achievement. Scoring a task top3 is straightforward; scoring a project top3 requires a product judgment about what “done” means. ADR-040 offers two incompatible metrics for projects (`docs/decisions/ADR-040-weekly-execution-layer.md:48-49`). Choose one before approval. Do not let the system silently decide what counts as success for a project.

A8’s dual-track habit model is not as safe as stated. ADR-040 says drift is mitigated by one Bridge action writing both the task toggle and daily frontmatter atomically (`docs/decisions/ADR-040-weekly-execution-layer.md:95-99`). Across two Markdown files in a Syncthing vault, “atomic” is aspirational unless the ADR defines failure handling, rollback, stale-write checks, and reconciliation. A task done-toggle plus daily quantity can diverge through Obsidian edits, mobile edits, sync conflicts, or partial Bridge failure. This is a real data-model risk, not a minor UI risk.

The “two carve-outs both honor the spirit” claim should be narrowed. Weekly was justified because the weekly journal is the artifact under design and has an explicit field-level contract in ADR-039 D5 (`docs/decisions/ADR-039-lifeos-weekly-dashboard.md:136-150`). Daily is a much broader operational substrate with more existing human ritual surface. Approving Weekly does not imply Daily is safe. Daily needs its own ADR with exact keys, writer behavior, conflict handling, and a rollback/reconciliation story.

**5 — ALTERNATIVES NOT CONSIDERED**

First, keep machine-maintained structured state outside `Journals/` entirely. A Bridge-owned folder such as `BridgeData/Weekly/` or `LifeOS/System/WeeklyState/` could hold targets, top3, habit quantities, and timer-derived state while `Journals/Weekly/` and `Journals/Daily/` remain human-only prose. Tradeoff: this fragments the “one weekly file SoT” idea ADR-039 preferred, but it avoids turning one red-line exception into a pattern.

Second, make UFO an explicit validated event, not merely `mode == "deep"`. Store `mode`, `plannedMinutes`, `actualMinutes`, and maybe `completed: true`; count UFO only when `mode == "deep"` and duration meets the accepted threshold. Tradeoff: slightly more schema and migration work, but it prevents a 5-minute manually written `deep` entry from becoming a UFO and makes early-finish behavior honest.

Third, keep task/project body writing in Obsidian via deep-link for v1. The Bridge task page can remain timer + stats + plan + “open in Obsidian.” Tradeoff: less seamless drafting, but it preserves Obsidian as the prose surface and avoids expanding the Bridge from execution layer into content authoring before the red-line model is fully settled.

**6 — FINAL VERDICT**

Approve with modifications. Do not approve ADR-040 as-is.

Make these changes before sign-off:

1. **A1:** Replace “structured data = machine-OK” with a stricter rule: human-selected structured fields may be persisted; machine-derived fields must be enumerated; autonomous selection of `top3`, `next3`, goals, or review outcomes is not allowed.

2. **A2:** Document code reality: 🍅 is duration-floor math, but UFO is currently `mode == "deep"`. Either enforce a 75-minute minimum for UFO counting or explicitly define UFO as a tagged fixed-block event. Fix or rename the early-complete button because it currently logs a full block.

3. **A3/A4/A5:** Add exact weekly-file frontmatter keys for tomato target, UFO target, `top3`, and `next3`; update ADR-039 D5’s allowlist. Choose one project-top3 scoring rule before implementation.

4. **A6:** Define transaction/state behavior for the combined 週交接 view when this week’s weekly file does not exist. The UI must not create hidden state or imply review completion before `status` is true.

5. **A8:** Move the `Journals/Daily/` carve-out to a separate future ADR. In ADR-040, keep it as a non-approved backlog direction, not a decision. Daily is the second red-line breach and needs its own evidence and writer contract.
