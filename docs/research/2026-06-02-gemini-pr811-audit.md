Here is a focused, independent audit of PR #811 from a Gemini perspective.

***

### 1. MULTILINGUAL / RESOLVER LENS

The CJK handling is thoughtful and correctly identifies the most common issues (NFC normalization, full-width colons). However, its implementation has subtle flaws and misses some real-world Obsidian link patterns.

*   **Correct but Incomplete Colon Handling:** `_link_key` at `shared/weekly_indexer.py:169` correctly normalizes `：` to `:`, which is the right direction for a canonical key. However, the project resolver at `shared/weekly_indexer.py:611-617` then tries *both* spellings when probing for a file. This is redundant if the project list is also normalized via `_link_key`. More critically, it only handles colons. Other full-width ASCII characters (`！`, `？`, `（`, `）`) are common in CJK filenames and wikilinks but are not normalized, which could lead to resolution failures.

*   **Path-style Wikilinks Will Fail:** Codex correctly identified that `[[Projects/專案]]` will not resolve. I'll add that this is a very common pattern in Obsidian, especially for disambiguation. The current implementation at `shared/weekly_indexer.py:162` (`_strip_wikilink`) and `shared/weekly_indexer.py:598` (`_resolve_top3`) assumes the link target is a bare name/slug. A robust resolver should use `os.path.basename` or equivalent on the link target before matching.

*   **NFC vs. NFD Blind Spot:** The code correctly normalizes to NFC at `shared/weekly_indexer.py:169`. This is the right choice for web and most systems. However, macOS HFS+/APFS file systems often store filenames in a variant of NFD. If the owner creates a file `專案：子題.md` on a Mac, and Syncthing syncs it to the Linux server running this code, the filename on the server's filesystem might be in NFD. The code normalizes the *wikilink* to NFC but reads the *filename* from the filesystem as-is (e.g., `p.stem` at `shared/weekly_indexer.py:576`). This creates a mismatch: `NFC("é") != NFD("é")`. The resolver will fail to find the project file. **This is a genuine, subtle bug.** The fix is to apply `unicodedata.normalize("NFC", ...)` to all filenames read from the filesystem before they are used as keys.

*   **Silent Task Title Collision:** Codex noted that duplicate task titles collapse silently in the `by_title` dict at `shared/weekly_indexer.py:593`. This is more than a minor issue. If two active tasks are named "Weekly Review Prep", selecting one in the UI could resolve to the *other* one, leading the owner to mark the wrong task as a top3 priority. This is a data integrity issue, not just a display quirk.

### 2. DIFFERENT PRIOR

My prior differs from Claude's and Codex's on the nature of the "verbatim prose" invariant and the risk of YAML formatting loss.

*   **Claude's Prior:** Claude treated the system as a well-behaved partner, flagging "footguns" but generally trusting the GET-prefill/token flow to mitigate data loss.
*   **Codex's Prior:** Codex treated the system as a potential adversary to the vault's integrity, focusing on strict data preservation, including YAML comments and formatting. It flagged the loss of YAML formatting as a potential blocker.
*   **My (Gemini) Prior:** I see the system as a **purpose-built tool for a specific workflow**, not a general-purpose Markdown editor. The owner is trading the fragility of manual YAML editing for the speed and structure of a web UI. In this context, losing YAML comments and flow-style formatting inside the *machine-owned* weekly file is an acceptable and expected trade-off. The ADR invariant is about preserving the owner's *semantic intent* and *prose content*, not the byte-for-byte formatting of the YAML container. Therefore, I disagree with Codex's Blocker #3. It's a known consequence of using `pyyaml`, not a bug in this PR's logic, and it doesn't risk the core data the system manages. The real invariant breach is persisting machine-generated defaults, which Codex correctly identified.

### 3. CLAUDE/CODEX BLIND SPOTS

Both previous audits were excellent but missed a few key points.

1.  **Blind Spot: `PermissionError` Retry Loop is Too Slow.** The `_atomic_write` function at `shared/weekly_writer.py:112-121` implements a linear backoff (`0.15s`, `0.30s`, `0.45s`). This is a good idea, but the delay is far too long for a synchronous web request. A user clicking "Save" will face a hang of up to `0.15 + 0.30 + 0.45 = 0.9` seconds if Obsidian or Syncthing holds a lock. This will feel like a broken UI. The backoff should be much shorter, e.g., `time.sleep(0.05 * (attempt + 1))`, for a total of ~150ms. The user would rather get a fast failure and retry message than a long hang.

2.  **Blind Spot: Inconsistent `strip()` Application.** Codex correctly noted that route handlers `strip()` prose, violating the "verbatim" invariant. I'll add that this is applied inconsistently. The review sections are stripped (`bridge_weekly.py:301-307`), but the `top3`/`next3` values passed to `write_weekly` are not, leaving potential leading/trailing whitespace inside the stored `[[wikilinks]]`. This should be standardized: either nothing is stripped (true verbatim) or everything is stripped (consistent cleaning).

3.  **Codex Overstatement:** As mentioned in my "Different Prior," Codex's Blocker #3 (YAML formatting loss) is overstated for this specific application. It's not a data-loss blocker for a file whose frontmatter is primarily machine-managed according to a strict allowlist. The owner should not be adding comments to `top3` or `status`.

4.  **Codex Understatement:** Codex correctly identifies the TOCTOU window with the `mtime` token but doesn't connect it to a specific, plausible failure mode. Given Syncthing, a conflict file (`.sync-conflict-`) could be created *after* the initial `_check_token` but *before* the `_atomic_write`. The current write would then silently overwrite the user's intended file, leaving the conflict file as the only record of the concurrent edit. The user might not notice this until much later. This strengthens the case for moving away from a simple `mtime` token.

### 4. DATA-LOSS DEEPENING

Here is my independent assessment of the genuine data-loss risks, ranked highest to lowest.

1.  **Critical: `review` form blanks `隨手筆記`.** (Codex Blocker #1). This is the most severe risk. A routine, correct user action (saving the weekly review) actively destroys unrelated data (`隨手筆記`) because the field is missing from the form. This is a guaranteed data-loss bug. (`thousand_sunny/routers/bridge_weekly.py:301-307`).

2.  **High: `targets` dict is replaced, not merged.** (Claude #1, Codex Blocker #2). This is a close second. Saving one target (e.g., UFO) via one form can silently delete another, owner-set target (e.g., pomodoro) that wasn't part of that form. This violates the principle of least astonishment and erases user intent. (`thousand_sunny/routers/bridge_weekly.py:360-384`).

3.  **Medium: Machine-default `ufo: 5` is persisted as owner intent.** (Codex). This is a data-integrity issue that masquerades as data loss. When the owner saves a plan with no UFO target set, the system writes `targets: {ufo: 5}`. This pollutes the vault with machine-generated intent, making it impossible to distinguish between "the owner set a target of 5" and "the owner set no target." (`thousand_sunny/routers/bridge_weekly.py:384`, using view default from `shared/weekly_indexer.py:703-704`).

4.  **Low: Prose blanking on stale-but-matching token.** (Claude #2). The risk of typing in Obsidian after loading the web UI, having the `mtime_ns` token match by chance, and then having the web form blank the new prose is theoretically possible but extremely unlikely. The `If-Match` token mitigates this sufficiently for a single-user vault.

### 5. ARCHITECTURAL CONCERNS

1.  **`mtime` is the wrong token primitive.** I agree with Codex's push-back. `mtime` is fragile. It can be reset by `git` or backup tools, and nanosecond resolution doesn't save it from the TOCTOU race condition. A **content hash (SHA-256) of the file as read** is the correct primitive for an `If-Match` token. The check becomes: hash the file content on GET, send hash to client. On POST, re-read the file, hash it, and compare to the submitted token. If they match, proceed with the write. This is immune to `mtime` changes and significantly shrinks the race window.

2.  **Status FSM is unenforced.** (Claude #5). The routes allow any status to be set at any time (`planning` -> `reviewed`). While the owner is the only user, this lack of state machine enforcement in the routes (`bridge_weekly.py`) is a design flaw. The "Advance to Active" checkbox should only be shown on the `planning` page, and the "Mark as Reviewed" button should only be enabled for `active` weeks. The writer should remain dumb (validating vocabulary only), but the routes must be smart.

3.  **Resolver Logic is Brittle.** The current resolver (`_resolve_top3`) is a chain of `if/elif` checks against different dictionaries. This is hard to maintain and is the source of the slug-vs-project collision issue. A better architecture would be to build a single, unified lookup table during indexing: `{'key': ResolvedItem}` where `ResolvedItem` is a dataclass containing its type (`task`, `project`), canonical name, and slug. This makes resolution a single dictionary lookup and forces disambiguation logic to happen explicitly during index creation.

### 6. VERDICT

**Block before merge.**

I agree with Codex's BLOCK verdict, but for slightly different reasons. The YAML formatting issue is not a blocker, but the combination of guaranteed data loss (`隨手筆記` blanking), silent intent destruction (`targets` clobbering), and subtle CJK/NFD resolution bugs makes this unsafe to ship to a real vault.

**Top 3-5 Priority Fixes:**

1.  **Blocker:** Fix the `隨手筆記` data loss. The `weekly_review_save` route must not write a blank `notes` section. Either remove it from the `sections` dict it builds or, preferably, add a (pre-filled) `notes` textarea to the review form partial.
    *   `thousand_sunny/routers/bridge_weekly.py:301-307`

2.  **Blocker:** Implement safe, partial updates for `targets`. The writer API needs to change. Instead of `frontmatter={"targets": ...}`, it should be something like `update_frontmatter_keys={"targets.ufo": 5}`. The route handlers must be updated to read the existing `targets` dict, merge changes, and then pass the full, merged dict to the writer.
    *   `shared/weekly_writer.py:494-496` (logic needs to merge, not replace)
    *   `thousand_sunny/routers/bridge_weekly.py:360-384` (routes must read-modify-write)

3.  **Blocker:** Stop persisting machine-default targets. The form submission for `plan-save` must distinguish between "user submitted nothing" and "user submitted 0". If the input field is empty, the `targets.ufo` key should not be included in the write payload at all.
    *   `thousand_sunny/routers/bridge_weekly.py:384`

4.  **Follow-up (Strongly Recommended):** Fix the NFC/NFD file-path bug. Apply `unicodedata.normalize('NFC', ...)` to file stems read from the filesystem before using them in lookups.
    *   `shared/weekly_indexer.py:576` (and any other `p.stem` that becomes a lookup key)

5.  **Follow-up (Architectural):** Replace the `mtime_ns` token with a content hash (e.g., SHA-1 of the file content as read). This is a more robust guard against concurrency issues with Syncthing.
    *   `shared/weekly_writer.py:371-379` (token generation)
    *   `shared/weekly_writer.py:484-488` (token check)
