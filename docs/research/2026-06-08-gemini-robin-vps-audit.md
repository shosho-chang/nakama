This is a strong ADR and a commendably thorough code-grounded audit by Codex. My role is to surface the system-level dynamics and failure modes that a code-centric view might miss, particularly around distributed state and operational reality.

### 1. Independent Correctness Check

My analysis confirms most of Codex's findings but from a different perspective, leading to different conclusions on a few key claims in the ADR.

-   **Claim: "單寫手 → 杜絕 Robin-vs-Robin ... 衝突" (Single writer → eliminates Robin-vs-Robin conflicts).**
    -   **Verdict: True but dangerously misleading.** The ADR correctly identifies that two *Robin instances* will no longer conflict. However, it replaces a machine-vs-machine conflict with a far more subtle and unpredictable **machine-vs-human conflict**. The new topology pits the VPS Robin instance against the human user editing the same vault via mobile Obsidian. This is a much harder conflict to reason about, as human edits are asynchronous, can happen offline, and won't follow application-level locking. The claim is a red herring that distracts from the real, and now primary, data integrity risk.

-   **Claim: "衝突模型 = 單一 Robin 寫手 + 人類 Obsidian 寫不同資料夾" (Conflict model = single Robin writer + human Obsidian writing to different folders).**
    -   **Verdict: False.** This model is naive and does not hold up. While `KB/Permanent` is human-only, Robin modifies files across many directories that a human might also touch:
        1.  **`Inbox/`**: An article markdown file might be edited by the user in Obsidian for clarity *at the same time* Robin's `image_fetcher.py` decides to download images and rewrite that same file's image links. This is a direct write-write conflict on the same file.
        2.  **`KB/Annotations/{slug}.md`**: The ADR's "red line" against editing these files is a policy, not a technical control. A user on a plane with their iPad *will* open an annotation file in Obsidian to add a summary paragraph, directly conflicting with a new highlight added via the web UI once connectivity is restored. Discipline is not a substitute for a correct distributed system design.

-   **Claim: "只有 B 書是 local-only" (Only B/books are local-only).**
    -   **Verdict: Incomplete.** The critical `state.db` file, which contains metadata, progress, and mappings for books, is also local-only and not part of the Syncthing vault. Moving the application logic to the VPS without a corresponding migration and sync strategy for `state.db` is a recipe for disaster. The application will start with a blank database on the VPS, losing all existing book progress and metadata. The ADR completely omits the state of this database.

-   **Claim: "CJK / Multilingual" (Implicit).**
    -   **Verdict: Unverified and risky.** The ADR is written in Traditional Chinese, implying the user works with CJK content. The file upload path (`books.py:323` `await bilingual.read()`) relies on FastAPI's `UploadFile`. While modern frameworks handle Unicode well, I see no evidence of tests for non-ASCII filenames, EPUB internal paths, or content that could break `ebooklib` or `BeautifulSoup` parsing. This is a significant blind spot.

### 2. Audit-the-Audit (Critique of Codex)

Codex's audit is excellent and code-grounded. However, it has limitations due to its static, code-focused perspective.

-   **Incomplete Risk Analysis on Unauthenticated Endpoints:** Codex correctly identifies the missing auth on `/api/books/*`. This is a critical find. However, it doesn't fully articulate the *consequences*. An unauthenticated `POST` to `/api/books/{book_id}/annotations` could allow an attacker to write arbitrary markdown to the user's vault, potentially filling the disk or injecting malicious content. The unauthenticated `DELETE` on `/ingest-request` is a denial-of-service vector. The impact is not just "missing auth" but "unauthenticated remote file system writes and state modification."

-   **Understated `state.db` Criticality:** Codex correctly identifies `state.db` as a single point of loss. It fails to emphasize that `state.db` and `data/books/` are **not independent**. They are a single, logical data unit. Restoring one without the other leads to a corrupt state (database entries pointing to non-existent files, or files with no corresponding database metadata). The backup/restore strategy must be atomic for both.

-   **Overconfidence in `dry_run` Analysis:** Codex correctly finds that `NAKAMA_PROMOTION_MODE=dry_run` is not a true dry run because `PromotionCommitService` still writes files. This is good. However, it misses a more subtle operational risk: what does this "dry run" *look like* to the user? If the UI shows a successful-looking promotion, the user will build a false mental model of the system's capabilities, leading to confusion when `N519` actually lands and behavior changes. The current implementation is not just misleading in code, but actively confusing for the user.

-   **Missed Operational Nuance of LLM Fallback:** Codex verifies the fallback from local Qwen to a cloud LLM. It frames this as a cost/privacy issue. It misses the **performance and reliability** dimension. A local LLM call is a sub-second, high-reliability affair. A round-trip to a cloud API introduces network latency (seconds), is subject to provider outages, rate limits, and API changes. The user experience of the `ingest` process will change dramatically, from a fast local task to a slow, brittle network-dependent one.

### 3. Distributed-State & Failure-Mode Analysis

This is where the ADR is weakest. The "single writer" model is an oversimplification that ignores the reality of a human in the loop with an eventually-consistent file sync mechanism.

**Scenario 1: The Inevitable Annotation Conflict**
1.  **State:** VPS has `note-A.md` with content "Highlight 1". Syncthing has synced this to an iPad.
2.  **Offline Edit:** The user, on a flight, opens `note-A.md` in Obsidian on the iPad and adds a brilliant summary at the end: "Highlight 1\n\nThis book is about...".
3.  **Online Edit:** While the iPad is offline, the user uses their phone's web browser to access Robin on the VPS and adds a new highlight to the same book.
4.  **VPS Action:** Robin's `AnnotationStore` reads the old "Highlight 1" content, adds the new highlight, and overwrites `note-A.md` with "Highlight 1\n\nHighlight 2". This is a full file rewrite, not an append.
5.  **Sync Resolution:** The user lands and the iPad connects to Wi-Fi. Syncthing sees two divergent versions of `note-A.md`. Based on timestamps or other heuristics, it will likely either:
    a.  Declare the VPS version the winner, overwriting the iPad's version. The user's summary is **silently lost**.
    b.  Create a `note-A.sync-conflict-...md` file containing the user's summary. As ADR-030 notes, the system's indexer ignores these files, so the data is effectively **lost to the application**. The user will never know their summary is hidden in a conflict file unless they manually audit their file system.

**Scenario 2: The Interrupted Book Upload**
1.  **Action:** The user uploads a 50MB EPUB file via the web UI over a flaky cellular connection. The connection drops after 40MB have been transferred.
2.  **Server State:** FastAPI's `UploadFile` spools to memory or disk. If the request is terminated mid-stream, the handler coroutine (`books.py:283`) will likely receive a `RequestDisconnect` exception.
3.  **Failure Mode:** Does the code have a `try...finally` block to clean up the partial temporary file? If not, the disk will slowly fill with orphaned upload chunks. More critically, what if the exception happens *after* the DB record is created but *before* the file is written to `data/books/`? This leaves an orphaned DB record, and the UI will show a book that can never be opened. The ADR shows no consideration for the atomicity of this operation.

**Scenario 3: The `state.db` Schism**
1.  **Setup:** The user follows the ADR. They stop their local Robin and start the VPS one. The VPS Robin, finding no `state.db`, creates a new, empty one.
2.  **Action:** The user re-uploads their books to the VPS as instructed.
3.  **Problem:** All reading progress, `book_id`s, and metadata from the local `state.db` are gone. The new `book_id`s generated on the VPS will not match the old ones. Any existing annotation files in `KB/Annotations/` that used the old `book_id` as a slug are now orphaned and disconnected from the books they belong to. This is a catastrophic, unrecoverable data integrity failure.

### 4. Assumption Push-Back

-   **Single-Writer Topology:** This is the core flawed assumption. The system has **two** writers: Robin (automated, predictable) and the Human (manual, offline-capable, unpredictable). Designing for a single writer when two exist guarantees data loss.
-   **Dry-Run Promotion on a Public Surface:** Exposing any file-writing capability under a flag named `dry_run` is unacceptable. This invites misconfiguration and misunderstanding. An endpoint that can write to the vault should be explicitly named and gated, e.g., `NAKAMA_PROMOTION_MODE=enabled_unsafe`.
-   **Single-Password Security:** A single shared `WEB_PASSWORD` for internet-facing upload, delete, and arbitrary annotation writes is grossly insufficient. Worse, as Codex found, an *empty* password authenticates successfully. This is not just "weak," it's effectively "no security" in a default configuration. Exposing endpoints like `/execute` (which sounds like RCE-as-a-service) with this security model is reckless.
-   **Ephemeral Book Storage:** The assumption that `data/books/` can be a non-synced, non-backed-up folder is untenable the moment it becomes the *single source of truth*. The original files might exist on a local machine, but the processed, stored versions that the entire reading system depends on exist only on the VPS. This is a single point of failure.

### 5. Alternatives

The ADR's goal is sound, but the implementation is flawed. Better topologies exist.

1.  **Read-Replica on VPS:** Keep the ingest and annotation-writing Robin instance on the local desktop (with the GPU). Run a **read-only** instance of Robin on the VPS. This allows mobile reading and viewing of existing annotations. New annotations could be captured in a temporary store and synced back for the local instance to merge, or the feature could simply be disabled on the read-replica. This achieves the mobile reading goal without opening up security holes or sync conflicts.
2.  **Sync the Books:** Instead of the fragile upload model, create a dedicated `Books/` folder within the Syncthing-managed vault (or a separate Syncthing share). Place EPUBs there. This provides automatic backup and distribution. The argument that "large binaries drag down Syncthing" is a premature optimization and generally not true for a small number of files. This is what Syncthing is for.
3.  **API-Driven Sync:** Decouple the UI from the file system. The local Robin instance could be the "primary." When it creates an annotation, it writes to the vault *and* `POST`s the annotation to a secure endpoint on the VPS instance, which updates its own state. This is more complex but correctly models the distributed state.
4.  **Use a Proper Auth Proxy:** Place the entire VPS application behind a proper authentication proxy like Authelia, Cloudflare Access, or Tailscale Funnel. This provides robust, industry-standard security and offloads user management, 2FA, etc., from the application itself. The `WEB_PASSWORD` can remain as a simple second-layer defense-in-depth.

### 6. Verdict

**Reject.**

The ADR correctly identifies a valuable user goal (mobile reading and annotation) but proposes a solution with critical, unaddressed flaws in security, data integrity, and distributed state management. It dramatically increases the system's complexity and fragility while claiming the opposite.

**Top 5 Concrete Changes Required for Re-Approval:**

1.  **Fix All Security Flaws:**
    a.  Add authentication (`check_auth`) to every single endpoint under `/robin/` and `/api/books/`, especially all `POST/PUT/DELETE` methods.
    b.  Add a startup check that causes the application to **fail to start** if `WEB_PASSWORD` is empty or not set in a non-dev environment.
    c.  Place the entire application behind a proper reverse-proxy authentication system (Alternative #4).

2.  **Implement an Atomic Data Migration and Backup Strategy:**
    a.  The migration plan *must* include a step to `scp` the local `state.db` to the VPS.
    b.  A recurring, automated backup job for both the `NAKAMA_BOOKS_DIR` directory and the `state.db` file *as a single atomic unit* must be implemented and tested before this ADR is approved.

3.  **Abandon the Naive Conflict Model:**
    a.  The ADR must acknowledge the Robin-vs-Human sync conflict as the primary data integrity risk.
    b.  Implement a technical control, not just user discipline. For example, make annotation files read-only (`chmod 444`) on the file system after Robin writes them, and provide an explicit "unlock" button in the UI if a user truly needs to edit one manually.

4.  **Adopt a Safer Topology (Alternative #2):**
    a.  Move the canonical book storage into a new Syncthing-shared folder (e.g., `Vault/Books/`).
    b.  Refactor the book logic to read from this synced directory instead of relying on a one-way, out-of-band upload mechanism. This provides inherent backup and simplifies the state management.

5.  **Isolate and Re-evaluate High-Risk Endpoints:**
    a.  The `dry_run` promotion mode must be removed or renamed to reflect that it writes files.
    b.  Any truly dangerous endpoints like `/execute` or file-writing promotion commits must be disabled by default with a separate, explicit "DANGER" flag, even behind auth, until their security and necessity are rigorously reviewed in a separate ADR.
