**1. CODE GROUNDING**
No: `write_task_body()` does not preserve frontmatter byte-for-byte. It checks the token, YAML-loads the task, then calls `_write_task()` (`shared/weekly_writer.py:196-198`). `_write_task()` re-dumps the whole frontmatter through PyYAML (`shared/weekly_writer.py:147`, `shared/weekly_writer.py:80-89`), so comments, quoting, scalar style, blank/null spelling, and possibly ordering/format are not preserved as bytes.

`_write_task()` also normalizes body framing: it prepends `\n` if missing and always forces a final newline (`shared/weekly_writer.py:148-152`). That is acceptable only if documented as normalization; it is not “body verbatim.”

Token logic is mostly correct for normal stale POSTs: token is SHA-1 of file bytes (`shared/weekly_writer.py:409-415`), and `_check_token()` rejects mismatch (`shared/weekly_writer.py:432-436`). But GET reads body and token in separate file reads (`thousand_sunny/routers/bridge_weekly.py:512-513`), so a narrow race can show old body with a new token.

**2. DATA-LOSS**
Blocker: LF-only frontmatter parsing. `_FRONTMATTER_RE` only matches `\n` delimiters (`shared/weekly_writer.py:38`). If a task file has CRLF frontmatter or a BOM, `_read_task()` can return `{}` plus the whole file as body (`shared/weekly_writer.py:133-136`), and the save then rewrites with empty parsed frontmatter via `_write_task()` (`shared/weekly_writer.py:147-150`). That is real frontmatter loss.

Empty-body save intentionally clears the body and leaves a trailing newline; that part is fine. CRLF submitted from the browser is normalized to LF (`thousand_sunny/routers/bridge_weekly.py:536`), and `_atomic_write()` writes UTF-8 LF (`shared/weekly_writer.py:104-105`). Invalid UTF-8 is not mapped to `WeeklyWriteError`.

**3. INVARIANT**
Not truly body-only. The body save path rewrites frontmatter every time through YAML serialization (`shared/weekly_writer.py:196-198`, `shared/weekly_writer.py:80-89`).

Also, `read_task_body()` returns the body from `_read_task()` (`shared/weekly_writer.py:181-182`), but the route strips all leading/trailing newlines before rendering (`thousand_sunny/routers/bridge_weekly.py:512`). The textarea posts that stripped value (`thousand_sunny/templates/bridge/task.html:138-139`), so a no-op save can remove intentional leading/trailing blank lines. It likely converges after the first save, but the first save is still newline drift.

No LLM authorship path found; this is direct form POST to writer.

**4. ROUTE CORRECTNESS**
Auth is present on GET and POST (`thousand_sunny/routers/bridge_weekly.py:484`, `thousand_sunny/routers/bridge_weekly.py:532`). Token round-trip exists: route passes `task_token` (`thousand_sunny/routers/bridge_weekly.py:513`), template posts `expected_token` (`thousand_sunny/templates/bridge/task.html:135-139`).

Conflict/write mapping is wired (`thousand_sunny/routers/bridge_weekly.py:539-544`). Unknown-task handling is awkward: GET unknown redirects dashboard with `err=task` (`thousand_sunny/routers/bridge_weekly.py:490-493`), but POST unknown first redirects to task detail with `err=write` (`thousand_sunny/routers/bridge_weekly.py:541-543`), then GET will bounce. Prefer direct `err=task` or 404.

**5. ASSUMPTION PUSH-BACK / ALTERNATIVES**
Do not reuse `_write_task()` for this feature. Implement a raw body splice: read one file snapshot, identify the frontmatter byte/string prefix, preserve it exactly, replace only the body, and derive the token from that same snapshot. If CRLF/BOM frontmatter is unsupported, fail closed with `WeeklyWriteError`; do not reinterpret the whole file as body.

Add tests that assert byte-identical frontmatter before/after body save, including comments, quotes, flow-style YAML, blank/null fields, CRLF, and leading/trailing body blank lines. Current tests only assert semantic YAML values (`tests/test_weekly_writer.py:518-524`, `tests/test_bridge_weekly.py:378-393`).

**6. VERDICT**
**Block as-is.** The PR violates the stated invariant: frontmatter is not “never mutated,” and CRLF/BOM parsing can lose it.

Prioritized fixes:
1. **Blocker:** replace `write_task_body()`’s `_read_task()` + `_write_task()` path with raw frontmatter-preserving body splice (`shared/weekly_writer.py:196-198`, `shared/weekly_writer.py:147-150`).
2. **Blocker:** remove `.strip("\n")` or make body normalization explicit and tested (`thousand_sunny/routers/bridge_weekly.py:512`).
3. **Blocker:** make GET body+token a single snapshot, not two separate reads (`thousand_sunny/routers/bridge_weekly.py:512-513`).
4. **Follow-up:** map unknown task POST to `err=task`/404 instead of `err=write` (`thousand_sunny/routers/bridge_weekly.py:541-543`).

I did not run tests because this workspace is read-only in the current sandbox.
