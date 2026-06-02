**1. CODE GROUNDING**

The weekly writer does enforce the top-level frontmatter allowlist: `WEEKLY_FRONTMATTER_KEYS` is exactly `(start_date,end_date,status,top3,next3,targets)` at `shared/weekly_writer.py:350`, and `write_weekly` rejects other top-level keys at `shared/weekly_writer.py:476-479`. It also validates `status` vocabulary at `shared/weekly_writer.py:480-482`.

Atomic write is mostly as claimed: same-directory temp file, UTF-8 LF write, `os.replace`, three `PermissionError` retries, and tmp cleanup on exceptions at `shared/weekly_writer.py:91-123`. It is atomic against half-files, but not durable against power loss because there is no `fsync`.

The If-Match token is `st_mtime_ns` or `""` for absent files at `shared/weekly_writer.py:363-379`, and routes pass it from the page at `thousand_sunny/routers/bridge_weekly.py:118`. This is a guard, not a transaction: the check happens before read/merge/write at `shared/weekly_writer.py:484-501`.

The greedy heading bug is fixed in the heading matcher: `_replace_section` uses `[ \t]*\n`, not `\s*`, at `shared/weekly_writer.py:425-428`. The end boundary `re.search(r"\n##\s+", ...)` at `shared/weekly_writer.py:435` should not reintroduce the old “swallow next heading” bug, but it treats any line-start `## ` inside prose or a code block as a section boundary. Safer symmetry would be `(?m)^##[ \t]+`.

One docstring/ADR claim is overstated: non-allowlisted frontmatter keys are semantically preserved, but not verbatim. `_read_weekly` parses YAML and `_write_weekly` dumps the whole mapping again, so comments, quoting, flow style, and formatting are lost on any weekly write (`shared/weekly_writer.py:395-401`, `shared/weekly_writer.py:79-88`, `shared/weekly_writer.py:441-448`).

**2. DATA-LOSS / DRIFT DETECTION**

Blocker: normal review saves can erase `隨手筆記`. `weekly_review_save` always writes `"隨手筆記": notes.strip()` at `thousand_sunny/routers/bridge_weekly.py:301-307`, but the review form does not contain a `notes` textarea; notes are a separate form at `thousand_sunny/templates/bridge/weekly.html:107-110`. So a normal `/weekly/review` POST defaults `notes=""` and blanks the notes section.

Blocker: Claude’s `targets` warning is confirmed and stronger. `weekly_targets_save` builds a partial `targets` dict at `thousand_sunny/routers/bridge_weekly.py:360-366`, `weekly_plan_save` writes only `{"ufo": ufo}` or `{}` at `thousand_sunny/routers/bridge_weekly.py:384`, and `write_weekly` replaces the whole key at `shared/weekly_writer.py:494-496`. Yes: `plan-save` with `ufo=0` wipes stored `targets.ufo`; `plan-save` with `ufo=4` also wipes any stored `targets.pomodoro`.

Also, `plan-save` can materialize machine default as owner intent. The input value is `view.ufo_target` at `thousand_sunny/templates/bridge/weekly.html:145`; when no target exists, `view.ufo_target` falls back to constant `5` at `shared/weekly_indexer.py:703-704`, so saving top3 can persist `targets.ufo: 5` even if the owner did not set it.

Review prose is not truly verbatim: route handlers call `.strip()` on review fields and notes at `thousand_sunny/routers/bridge_weekly.py:301-307` and `thousand_sunny/routers/bridge_weekly.py:326-327`; the indexer also `strip()`s section bodies at `shared/weekly_indexer.py:541-544`.

TOCTOU remains. A change after `_check_token` but before `_write_weekly` can be overwritten (`shared/weekly_writer.py:484-501`). For absent files, creation after the `""` token check but before replace is also overwritten.

**3. INVARIANT CHECK**

The compute-on-read invariant mostly holds for observations. Weekly actuals/rate/UFO are computed in `WeeklyIndexer.view` from task entries and the aggregator at `shared/weekly_indexer.py:677-709`; `weekly_actual` computes from daily/task intervals at `shared/pomodoro_aggregator.py:305-307`. The weekly writer does not persist 🍅 actuals, UFO actuals, or rate.

`log_time_entry` writes evidence to `TaskNotes/Tasks`, not `Journals/Weekly`, at `shared/weekly_writer.py:322-338`; the aggregator derives minutes from `startTime/endTime`, not `actual_minutes`.

Top-level allowlist is not bypassable through `write_weekly`, but the allowed keys are under-validated. `targets` can carry arbitrary nested keys if a future caller passes them, and `top3/next3` types and lengths are not checked (`shared/weekly_writer.py:476-496`). Existing HTTP routes do not expose arbitrary nested target keys, but the writer API itself does not enforce the semantic schema.

The biggest invariant breach is not observation persistence; it is machine-authored/default intent and non-verbatim prose: default UFO target persistence, `.strip()`, and full YAML reserialization.

**4. RESOLVER CORRECTNESS**

CJK/full-width colon normalization is implemented: `_link_key` NFC-normalizes and maps `：` to `:` at `shared/weekly_indexer.py:167-171`; project file probing tries both colon spellings at `shared/weekly_indexer.py:611-617`.

Claude’s ambiguity warning is confirmed. `_top3_options` emits task values as slug and project values as plain project name (`shared/weekly_indexer.py:568-579`), but storage loses the option group. `_resolve_top3` checks task slug/title before project (`shared/weekly_indexer.py:592-604` before `shared/weekly_indexer.py:611-618`). If a task slug/title equals a project name, selecting the project can render as the task.

Valid Obsidian links can render unresolved if they include paths. `_strip_wikilink` returns the whole target (`shared/weekly_indexer.py:160-164`), and `_resolve_top3` compares that directly to slug/title/project name (`shared/weekly_indexer.py:600-603`). `[[Projects/專案]]` or `[[TaskNotes/Tasks/foo]]` will not resolve to `專案` or `foo`.

Duplicate task titles also collapse silently in `by_title = {...}` at `shared/weekly_indexer.py:592-593`; the later task wins.

**5. ASSUMPTION PUSH-BACK + ALTERNATIVES**

I would not use bare mtime as the real-vault concurrency token. Use a content hash token, and re-check immediately before replace; if this needs to be truly strong, add a lock or platform-specific compare-and-swap strategy. `mtime_ns` is vulnerable to Syncthing/restored mtimes and same-window edits.

I would not replace whole `targets`. Treat `targets` as a schema with merge/delete semantics: update `ufo` without dropping `pomodoro`, and distinguish “unset/default display” from “owner explicitly submitted this target”.

I would enforce status transitions route-side. Right now the writer only validates vocabulary. `weekly_review_save` can jump to `reviewed` at `thousand_sunny/routers/bridge_weekly.py:309-310`, `weekly_plan_save` can set `active` at `thousand_sunny/routers/bridge_weekly.py:385-386`, and the template shows the advance checkbox whenever `st != 'active'` at `thousand_sunny/templates/bridge/weekly.html:146`, including `reviewed`.

**6. VERDICT**

Block before merge.

Top fixes:

1. Pre-merge blocker: stop `/weekly/review` from blanking `隨手筆記`. Remove notes from that route or include/prefill the field. Add a regression test with existing notes. `thousand_sunny/routers/bridge_weekly.py:301-307`, `thousand_sunny/templates/bridge/weekly.html:107-110`.

2. Pre-merge blocker: fix `targets` merge/default semantics. Do not let `plan-save` replace the whole dict or persist fallback `5` as owner intent. `thousand_sunny/routers/bridge_weekly.py:360-366`, `thousand_sunny/routers/bridge_weekly.py:384`, `shared/weekly_writer.py:494-496`, `thousand_sunny/templates/bridge/weekly.html:145`.

3. Pre-merge blocker if the owner uses comments/custom formatting in weekly frontmatter: stop claiming verbatim preservation, or use a round-trip/targeted frontmatter editor. `shared/weekly_writer.py:395-401`, `shared/weekly_writer.py:79-88`.

4. Follow-up: strengthen tokening from mtime to content hash plus late recheck/locking. `shared/weekly_writer.py:371-379`, `shared/weekly_writer.py:484-501`.

5. Follow-up: disambiguate top3 storage/resolution, especially task-vs-project collisions and pathful wikilinks. `shared/weekly_indexer.py:568-618`.
