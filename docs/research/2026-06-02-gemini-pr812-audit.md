As the third reviewer on PR #812, my audit incorporates the perspectives of both the Claude-authored code and the Codex-authored review, adding a Gemini lens focused on multilingual data integrity, architectural consistency, and subtle data loss vectors. I agree with Codex's verdict to block.

### 1. CJK/Multilingual & Encoding Edge Cases

Codex correctly identified the CRLF/BOM issue as a data-loss blocker. I will expand on this with specific multilingual and encoding concerns.

*   **Unicode Normalization (NFC/NFD):** The current implementation does not perform Unicode normalization. macOS filesystems often use NFD (decomposed characters, e.g., `e` + `´`), while web inputs and other systems typically use NFC (pre-composed `é`). A task slug like `déjà-vu` could be stored as `déjà-vu.md` on a Mac. The URL-decoded slug from the browser might be NFC, causing `_task_path()` to fail to find the file, leading to a `WeeklyWriteError`. While not a body-write issue, it's a latent bug in the slug-based file path logic that this feature relies on.
*   **Emoji in Frontmatter:** The owner's real-world data might include emoji in frontmatter fields, e.g., `est_pom: 🍅🍅`. PyYAML's `dump()` can handle this, but the re-serialization path identified by Codex (`shared/weekly_writer.py:147`) makes assumptions about YAML style. If the original was `est_pom: "🍅🍅"`, the quotes might be dropped, changing the file byte-for-byte, which violates the "untouched" invariant.
*   **BOM (Byte Order Mark):** Codex noted that a BOM could cause frontmatter loss. To be more specific: if a file starts with a UTF-8 BOM (`\xef\xbb\xbf`), `_FRONTMATTER_RE` (`shared/weekly_writer.py:38`) will not match from the start of the file. `_read_task()` will then incorrectly treat the entire file content, including the frontmatter, as the body. The subsequent `_write_task()` call receives an empty dictionary for frontmatter (`fm`), effectively deleting it.
*   **CRLF Handling:** The explicit CRLF normalization (`thousand_sunny/routers/bridge_weekly.py:536`) is good for the *body*. However, as Codex flagged, the `_FRONTMATTER_RE`'s failure to handle CRLF in the frontmatter block itself is the critical flaw. A file saved with CRLF line endings in Obsidian (a user-configurable setting) will have its frontmatter deleted on the first body save.

### 2. A Different Prior (Architectural Approach)

My preferred approach would have been to treat the file as a structured text document, not a YAML object with a text appendage. This avoids the entire class of YAML serialization problems.

1.  **Read the whole file once** into a bytes buffer.
2.  **Calculate the `If-Match` token** from this buffer.
3.  **Find the body separation point.** Instead of a regex limited to `\n`, I would search for the *second* occurrence of `---` followed by a newline sequence (`\n` or `\r\n`). The bytes up to and including that newline sequence constitute the "exact frontmatter prefix."
4.  **On write, concatenate** the preserved "exact frontmatter prefix" bytes with the new (normalized) body bytes.
5.  **Atomically write** the new buffer to the file.

This "byte-splice" approach completely bypasses YAML parsing/dumping and is immune to issues of comments, quoting, style, ordering, and line endings within the frontmatter, perfectly satisfying the "frontmatter NEVER mutated" invariant. The current implementation reuses `_write_task`, which is architecturally convenient but functionally incorrect for this PR's requirements.

### 3. Claude/Codex Blind Spots

*   **Codex (Understated): The Race Condition.** Codex correctly identified a race condition between the two reads for body and token (`thousand_sunny/routers/bridge_weekly.py:512-513`). However, the impact is understated. A user could:
    1.  GET the page (reads body, then token A).
    2.  An external process (e.g., Obsidian) saves the file.
    3.  The user POSTs their edit with token A.
    4.  The server's `_check_token()` compares token A against the *new* file's token B, correctly raising `WeeklyConflictError`.
    The issue is that the user was editing a stale body from the start. The conflict is detected on write, but the user's work is already based on outdated information. The reads *must* be from a single, atomic snapshot.
*   **Claude (Blind Spot): Implicit Normalization.** The code's author, Claude, seems to have missed the data loss caused by `strip("\n")` (`thousand_sunny/routers/bridge_weekly.py:512`). This is a classic blind spot where "cleaning up" data has unintended consequences. A user might intentionally have leading/trailing newlines for Markdown formatting (e.g., separating a list from a paragraph). Stripping them on read and then having `_write_task` add one back on write (`shared/weekly_writer.py:152`) is not "verbatim" and constitutes newline drift.
*   **Codex (Missed Opportunity): Error Message Specificity.** The `err=write` message (`_TASK_ERRORS` in `bridge_weekly.py:425`) is generic. It covers file locks, permissions, and missing files. A `FileNotFoundError` could be caught separately to provide a more specific message, like "任務筆記已被改名或刪除" (Task note has been renamed or deleted), which is more actionable for the user than the current generic failure message.

### 4. Data-Loss Deepening

Codex's findings on frontmatter and newline loss are correct. Here's a deeper look at the failure cascade:

1.  **Initial State:** A task file `我的任務.md` exists with CRLF line endings and a BOM, common for Windows-edited files. It contains valid frontmatter.
    ```
    ---
    key: value
    ---
    Original body.
    ```
2.  **GET Request:** The user navigates to the task page.
    *   `read_task_body()` (`shared/weekly_writer.py:181`) calls `_read_task()`.
    *   `_read_task()` (`shared/weekly_writer.py:133`) uses `_FRONTMATTER_RE`, which fails to match because of the BOM and/or CRLF.
    *   It returns `fm={}` and `body` as the *entire file content*, including the `---` delimiters.
    *   The route then calls `.strip("\n")` (`thousand_sunny/routers/bridge_weekly.py:512`), so the textarea is populated with the full file content, slightly mangled.
3.  **POST Request:** The user makes a small edit and saves.
    *   `write_task_body()` (`shared/weekly_writer.py:196`) is called.
    *   It calls `_read_task()` again, which again returns `fm={}`.
    *   It then calls `_write_task(path, fm={}, new_body)`.
    *   `_write_task()` (`shared/weekly_writer.py:147`) dumps the empty `fm` dictionary, writing just `{}\n` or nothing, followed by the new body.
4.  **Result:** The original frontmatter is **permanently deleted**. This is a critical data loss scenario directly caused by the faulty parsing logic. An empty body save would result in a file containing just `{}\n` or a single newline, destroying all previous content.

### 5. Architectural Concerns

*   **Misuse of Abstraction:** The core architectural flaw is reusing `_write_task` (`shared/weekly_writer.py:198`). This function is designed for semantic updates (e.g., adding a plan entry) where re-serializing the YAML is acceptable. The requirement for this PR is a byte-level preservation task. Forcing the semantic writer to perform a byte-level task created all the frontmatter-loss bugs. A new, dedicated function for the "byte-splice" approach was needed.
*   **Inconsistent Error Handling:** As Codex noted, a POST to a non-existent task slug redirects back with `err=write`, which is confusing (`thousand_sunny/routers/bridge_weekly.py:543`). The subsequent GET will then fail to find the task and redirect to the dashboard with `err=task`. This two-step failure bounce is poor UX. The POST should fail immediately with the correct context (e.g., redirect to dashboard with `err=task`).

### 6. VERDICT: Block

I fully agree with Codex's **Block as-is** verdict. The PR in its current state is dangerous and guarantees data loss for any user with task files containing CRLF line endings, a BOM, or even just YAML comments.

**Prioritized Fixes:**

1.  **Blocker:** Replace the implementation of `write_task_body()` (`shared/weekly_writer.py:196-198`). Do not use `_read_task` and `_write_task`. Re-implement it using the "byte-splice" method described in section 2 of this audit. It must read the file, find the end of the frontmatter block (supporting both `\n` and `\r\n`), and replace only the content that follows, preserving the frontmatter prefix byte-for-byte.
2.  **Blocker:** The GET path must be made atomic. The calls to `read_task_body` and `task_file_token` (`thousand_sunny/routers/bridge_weekly.py:512-513`) must be replaced by a single function that reads the file content *once*, calculates the token from it, and extracts the body from it. This eliminates the race condition.
3.  **Blocker:** Remove `.strip("\n")` from the body before rendering it in the template (`thousand_sunny/routers/bridge_weekly.py:512`). The body must be presented to the user verbatim to prevent newline drift on save. The normalization logic in `_write_task` that adds a trailing newline should also be part of the new byte-splice writer, but it must be documented as an intentional normalization, not a side effect.
4.  **Follow-up:** Improve error handling for a POST to a non-existent task. The `except WeeklyWriteError` block (`thousand_sunny/routers/bridge_weekly.py:541-543`) should inspect the exception (e.g., if it wraps a `FileNotFoundError`) and redirect to the main dashboard with `err=task`, mirroring the GET route's behavior for a more consistent user experience.
