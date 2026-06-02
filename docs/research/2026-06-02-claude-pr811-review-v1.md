# Claude v1 review — PR #811 (ADR-040 Slice 2, weekly dashboard execution layer)

Pre-merge cross-model panel. Branch `feat/adr-039-weekly-redesign`, 14 commits / 17 files / +2794-242.
Red-line slice: it writes to the real Obsidian vault `Journals/Weekly/` (🟡). Audit focus = the writer, the
compute-on-read invariant, the CJK top3 resolver, and the mutation routes.

## Artifact under review (key symbols)

- `shared/weekly_writer.py`
  - `write_weekly(vault_root, file_key, *, start_date, end_date, frontmatter, sections, expected_token)` — one
    coalesced atomic write. Frontmatter allowlist `WEEKLY_FRONTMATTER_KEYS=(start_date,end_date,status,top3,next3,targets)`;
    any other key raises. `status` validated against `WEEKLY_STATUSES=(planning,active,reviewed)`.
  - `weekly_file_token` = `st_mtime_ns` or `""` (expect-absent). `_check_token` raises `WeeklyConflictError` on drift.
  - `_replace_section` — regex `r"(^##[ \t]+{heading}[ \t]*\n)"` (the greedy-`\s*` bug that swallowed the next
    heading was fixed; the END boundary uses `re.search(r"\n##\s+", body[start:])`).
  - `_atomic_write` — tmp + `os.replace`, 3-attempt linear backoff on Windows `PermissionError`.
  - A2 `log_time_entry(..., *, planned_minutes, completed, manual)` evidence dict.
- `shared/weekly_indexer.py`
  - `_link_key(v)` = `NFC(v).strip().replace("：", ":")` — CJK/full-width-colon tolerant matching.
  - `_resolve_top3` — task (by slug or title) | project (proj_tasks or `Projects/{name}.md`, with both colon
    spellings) | unresolved (surfaced, never dropped). `_top3_options` = open tasks + all projects.
  - `pomodoro_goal = targets.pomodoro or planned` (auto-sum; 🍅 target never stored).
- `thousand_sunny/routers/bridge_weekly.py`
  - `page_router` (no blanket auth dep) → manual `check_auth` per endpoint; Form POST → 303 redirect with
    `?err=`/`?saved=` banners. Routes: `/weekly/{review,notes,top3,targets,status,plan-save}`.
  - `_write_weekly_or_back` maps `WeeklyConflictError`→`err=conflict`, `WeeklyWriteError`→`err=write`.

## Candidate issues Claude found (to be confirmed/refuted by panel)

1. **`targets` whole-dict replace, not merge** (`weekly_plan_save` L384, `weekly_targets_save` L360). Frontmatter
   merge in `write_weekly` does `fm[k]=v`, so passing `targets={"ufo": N}` REPLACES the stored targets dict. If
   `ufo==0` the panel writes `targets: {}` — erasing any prior `targets.ufo`. The auto-sum 🍅 goal is never stored
   so it's unaffected, but a UFO target could be wiped by a plan-save whose ufo field happens to be 0/blank.
   Severity: data-loss footgun on owner intent. Question: is ufo always round-tripped by the form (so 0 only
   means "really cleared"), or can a partial submit zero it?

2. **Review form blanks unfilled prose sections** (`weekly_review_save` L301-307). All 5 named sections are written
   every submit; an empty textarea → `_replace_section(heading, "")` → blank section body. Mitigated by the GET
   pre-filling the form from the existing file + the token guard catching drift. Residual risk: prose typed
   directly in Obsidian AFTER the page load but the token somehow matches (clock/ns edge) → silent blanking.

3. **TOCTOU window in the If-Match guard.** `_check_token` stats once at the top; `check → _read_weekly → _write`
   is not transactional. A concurrent Obsidian/Syncthing write landing in that ~ms window is not caught. Inherent
   to mtime-token If-Match; acceptable for a single-user vault but should be acknowledged.

4. **`_replace_section` append-branch separator logic** (L432). `sep` picks `""`/`"\n"`/`"\n\n"` by inspecting
   the body tail. Confirm it can't double-up blank lines or weld a heading onto a previous line for a body that
   doesn't end in a newline.

5. **`mark_reviewed`/`advance` status flips are unguarded transitions.** `weekly_review_save` sets
   `status=reviewed` and `weekly_plan_save` sets `status=active` on a checkbox, with no FSM-order check (e.g. can
   jump planning→reviewed). The writer only validates vocabulary, not order. Is "honest FSM" enforced anywhere, or
   is order advisory?

6. **`top3` resolution vs storage asymmetry.** Stored as `[[wikilink]]` of either a task SLUG or a project NAME.
   `_resolve_top3` tries slug, then title, then project. A task whose slug collides with a project name resolves as
   a task. Confirm the dropdown `value`s (slug vs name) can't produce an ambiguous stored link.

## Verdict (Claude v1, pre-panel)

Code is well-structured, test-covered (121 green), lint-clean, and the red-line invariant (machine persists intent
+ verbatim prose; observations compute-on-read) holds in the writer. The above are mostly footguns/edge-cases, not
blockers — but #1 (targets clobber) is the one I'd most want a second pair of eyes on before it touches the real
vault.
