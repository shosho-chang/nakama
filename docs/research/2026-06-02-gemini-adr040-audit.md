This is the audit from Gemini Pro, the third reviewer on the multi-agent panel.

***

## ADR-040 Audit: Gemini Pro Panel Review

**To:** 修修 (shosho-chang), System Owner
**From:** Gemini Pro 2.0, Third Panel Auditor
**Date:** 2026-06-03
**Subject:** Final audit of ADR-040 (Weekly Execution Layer). My verdict is **Reject**. This ADR introduces architectural rot and behavioral anti-patterns that outweigh its benefits.

This audit is structured into six sections. I have reviewed the original ADR-039, Claude's proposed ADR-040, and the Codex/GPT-5 audit. While I concur with many of Codex's specific findings, my core assessment differs. Codex recommends modification; I recommend rejection and a new approach. This system's integrity is at a critical juncture, and ADR-040 takes the wrong path.

### 1. i18n / Vault-Convention & Multilingual Lens

Claude and Codex, trained primarily on English-language code and text, have overlooked significant, predictable failures in the proposed bilingual system. These are not minor bugs; they are data-integrity threats that will manifest as tasks that don't link, metrics that don't count, and files that can't be found.

1.  **Unicode Normalization (The Invisible Bug):** The entire system uses filenames as primary keys (e.g., `專案：寫當週電子報.md`). Different operating systems (or even different apps on the same OS) can store the exact same visible string with different underlying byte representations (NFC vs. NFD). Python’s file system functions will treat these as two different files. The Bridge's indexer, running on a Linux VPS (likely NFC default), will fail to find a task file synced from a macOS device (often NFD default) if the filename contains characters like `è` or certain CJK radicals. This will cause tasks to "disappear" from the dashboard. **This is a critical, unaddressed flaw.** The `shared/blob_loader.py` and all indexers must enforce Unicode normalization (e.g., `unicodedata.normalize('NFC', path)`) on every path operation to guarantee key stability.

2.  **Wikilink Ambiguity:** ADR-040 (A4) proposes `top3` entries be wikilinks like `[[專案：寫電子報]]`. Obsidian is famously flexible with wikilink resolution. The Bridge's Python parser will not be. It must perfectly replicate Obsidian’s logic for resolving names with spaces, CJK colons (`：` vs. `:`), and other punctuation. The current indexer code, modeled on simple slug-based projects, is not prepared for this. A `top3` entry failing to resolve will silently drop from the UI, breaking the core weekly loop.

3.  **Semantic Drift in Bilingual Terms:** The term "UFO" (A2) is a prime example of a concept that does not translate. It is an English acronym for "Ultra-Focused Object" (presumably), mapped to the Chinese concept "超專注". In the code, it is `mode: deep`. In the UI, it is "UFO" and "🤩". This creates four separate labels for one concept. This is not just cosmetic; it invites confusion and makes the system harder to reason about. When 修修 thinks "I did a 超專注 session," will he remember to log a "UFO"? The system should pick one canonical term—`deep_work` in the code, "超專注" in the UI—and use it consistently. The English acronym is jargon that adds cognitive load.

4.  **Frontmatter Key vs. Value Language:** The convention is English keys (`weekly_priority`) and Chinese content. This is fragile. If a key ever needs to hold an enum, will the values be English (`status: done`) or Chinese (`status: 完成`)? The system needs a defined policy for this, otherwise parsers will break when they expect one language and receive another.

These are not edge cases. For a bilingual user whose source of truth is a file system, these issues guarantee data corruption and user frustration. ADR-040 and the prior audit are blind to them.

### 2. Different Prior: System Dynamics and Data Verifiability

My training prior differs from Claude’s and Codex’s in two areas they consistently under-weight: system dynamics and data verifiability. They analyze the system as a static collection of features; I analyze it as a dynamic system of feedback loops that will change the user's behavior.

1.  **The System Is Now Prescriptive, Not Just Reflective:** ADR-039 created a dashboard that *reflected* reality. ADR-040 creates a system that *prescribes* behavior. By introducing targets for 🍅 and UFOs (A3) and a heavy "週交接" ritual (A6), the Bridge is no longer a passive skin on the vault. It is an opinionated coach. This is a fundamental shift. Claude and Codex treat this as a simple feature addition. I see it as crossing a Rubicon. The risk is that optimizing for the system's metrics (hitting 5 UFOs) becomes more important than doing the actual important work. This is Goodhart's Law waiting to happen, and it was not modeled as a primary architectural risk.

2.  **Data Must Be Verifiable, Not Just Present:** Codex correctly identified that a UFO is just a `mode: deep` tag, not a verified 75-minute block (Codex §1, §3). My prior pushes this further: this is an architectural smell. The `timeEntries` object should be a verifiable record. Instead of `{mode: deep}`, it should be `{mode: deep, planned_duration: 75, actual_duration: 75, completed: true}`. The UFO metric should then be *derived* from records that meet the criteria. By storing only a tag, the system stores a *claim* rather than *evidence*. This makes the data brittle and untrustworthy. Over time, the vault will fill with data that cannot be audited or re-analyzed under new rules, violating the "source of truth" principle.

Claude and Codex are preoccupied with *making the features work*. My prior is focused on *what the features will do to the user and the data* over years of use. ADR-040 optimizes for the former at a dangerous cost to the latter.

### 3. Claude/Codex Blind Spots: The Flawed Dichotomy and The Ritual's Fragility

Claude and Codex share a foundational bias: they accept the "structured=machine / prose=human" dichotomy (A1) as a clean and sufficient guardrail. It is not. This shared blind spot causes them to miss the two most significant flaws in this proposal.

1.  **The "Structured vs. Prose" Dichotomy is False:** Codex correctly notes that `top3` is a human judgment, not neutral data (Codex §4). I will state it more strongly: **the most important data in this system is structured, and it is entirely human.** The `plan[]` array is the very essence of human intention and priority. Deciding that `[[專案：寫電子報]]` gets 3🍅 on Tuesday is a creative, executive act. Classifying this as "machine-maintainable" data (A1) is a category error. The machine is a scribe, not the maintainer. This flawed premise is used to justify opening up `Journals/` folders. But if the principle is flawed, the justification collapses. The real distinction is not structured vs. prose; it is **human-generated intent** vs. **machine-generated observation**. Both can be structured. ADR-040 conflates them, creating a loophole for future automated "planning" that violates the owner's core intent.

2.  **The Habit Dual-Track (A8) is a Data Integrity Nightmare:** Both models missed the core failure of the dual-track habit proposal. It suggests a task for the *toggle* ("did I do it?") and daily-note frontmatter for the *quantity* ("how much?"). Codex flagged the "atomic write" problem (Codex §4), but missed the deeper issue: **this permanently splits the record of a single event across two unrelated files.** A single workout on June 3rd will have its existence recorded in `TaskNotes/Tasks/workout.md` and its duration recorded in `Journals/Daily/2026-06-03.md`. There is no link between them. This is not a sync problem; it is a fundamental data modeling failure. It makes it impossible to answer simple questions like "Show me the duration of all the workouts I marked as done." It creates two partial sources of truth, violating ADR-030 D1. This is architecturally indefensible.

3.  **The UFO Metric is Behaviorally Unsound:** Both models analyzed the UFO as a technical feature. Neither questioned if it is a *good idea*. A 75-minute block is arbitrary. What if a breakthrough happens in a 60-minute session? It doesn't count. What if a 90-minute session is highly distracted? It counts as one UFO. The metric encourages clock-watching, not deep work. Furthermore, by creating a 🍅 ceiling (to prevent burnout) and a UFO target (to encourage focus), ADR-040 (A2, A3) creates **conflicting incentives**. To hit your UFO target, you must commit to long blocks. But each long block consumes 3🍅, rapidly pushing you toward your ceiling. This design will create anxiety, not focus.

### 4. Ritual & Behavior-Design Critique

ADR-040 (A6) claims the "週交接" ritual is "the highest-leverage UI in the system." As behavior design, it is brittle and likely to fail.

-   **Failure Mode: The Sunday Wall.** The proposed ritual is a monolithic, multi-step process (review, carry-forward, prioritize, target-set, schedule). This is a high-friction activity. When life gets busy, this will be the first thing to be skipped. A skipped Sunday means the entire week starts with stale data, no targets, and a feeling of being "behind." This guilt can cascade, leading to the ritual being abandoned entirely. A better design would be a series of smaller, independent actions that can be done throughout the weekend (e.g., review Friday, plan Sunday).

-   **Entrenching Stale Goals:** Pre-filling this week's `top3` from last week's `next3` (A4) sounds like a closed loop, but it's a classic planning trap. It assumes priorities are stable week-to-week. It discourages zero-based thinking ("What is *actually* most important *this* week?"). It makes it easy to roll over goals without re-validating them, leading to a "zombie project" that stays on the priority list for weeks without real progress.

-   **Fragmenting the Authoring Surface (A7):** Making task pages into writing surfaces competes directly with Obsidian's core strength. 修修's workflow will fragment. "Where did I write that draft? Was it in the task note body in the Bridge, or in a separate note in Obsidian?" This creates cognitive overhead and search problems. The Bridge should be for command-and-control (timing, scheduling, status), while Obsidian remains the sanctum for thinking and writing. ADR-040 blurs this line, weakening both tools.

### 5. Architectural Concerns

ADR-040 optimizes for feature velocity at the cost of architectural integrity. It introduces technical debt and dangerous precedents.

1.  **The Trajectory is Architectural Rot:** The proposal explicitly puts two `Journals/` carve-outs on the map (A8). This is not a "slippery slope" argument; it is an observation of the stated trajectory. The "structured data" principle (A1) is a weak guardrail that can be used to justify opening any folder for any "structured" purpose. The correct architectural choice, as noted in Codex's alternatives (Codex §5), is to **store machine-writable state in a machine-owned folder** (e.g., `Bridge/State/weekly-2026-W23.yml`). This keeps the human/machine boundary clean at the file-system level, which is the strongest guarantee possible. ADR-040 chooses to contaminate the `Journals/` namespace because it's convenient. This is a short-sighted trade-off.

2.  **The Bridge is Becoming a Second Vault:** With `plan[]` data invisible to Obsidian plugins and task-body editing (A7), the Bridge is ceasing to be a "skin" and becoming a second, incompatible source of truth. It creates a system where the vault is only fully usable through the Bridge. This violates the principle of tool-independence and creates lock-in to the web UI. If the Bridge goes down, not only are the interactive features lost, but core planning data becomes inert, un-rendered text in the vault.

3.  **UFO as Tag vs. Event:** As discussed in §2, storing the UFO as a `mode: deep` tag is a critical mistake. It is an un-auditable claim. The cost of storing a richer, verifiable time entry is trivial. The cost of having an untrustworthy historical record of deep work is immense. This is optimizing for implementation ease today over data integrity forever.

### 6. Final Verdict: Reject

I recommend **Reject**. ADR-040 should not be approved, even with modifications. Its foundational principles (A1) are flawed, its behavioral design (A6, A2) is unsound, and its data modeling (A8) is a liability. It pushes the system toward a state of higher complexity, lower data integrity, and increased user friction.

A new ADR should be drafted with the following principles:

1.  **Honor the True Red Line (New A1):** The boundary is **Human-Generated Intent** vs. **Machine-Generated Observation**. All machine-writable state (targets, `top3` selections, derived stats) must live in a dedicated, machine-owned folder (e.g., `System/BridgeState/`). `Journals/` must remain 100% human-authored, read-only for the machine. This is a clean, enforceable, file-system-level contract.

2.  **Model Habits Correctly (Replaces A8):** A habit is a single event. It must be a single record. Create a new note type, `HabitLog`, or add a structured log to the Daily note. A single action in the Bridge must create a single, unified entry in the vault (e.g., `habits: [{name: 'meditation', timestamp: '...', duration: 15, status: 'done'}]`). The dual-track model is rejected.

3.  **Make Data Verifiable (Replaces A2):** All `timeEntries` must store evidence, not claims. A UFO is not a tag; it is a derived property of a time entry that meets specific, queryable criteria (`duration >= 75`, `completed: true`, etc.). The raw log must be truthful.

4.  **De-risk the Ritual (Replaces A6):** Break the monolithic "週交接" into smaller, composable actions. The UI should support reviewing last week, planning this week, and scheduling tasks as separate, non-blocking steps.

5.  **Keep Obsidian Sacred (Replaces A7):** The Bridge is for metadata and control. Obsidian is for prose. Remove task-body editing from the Bridge. Provide a prominent "Edit in Obsidian" deep-link instead. Do not fragment the writing workspace.

This is a more disciplined path. It requires more thought up front, but it will result in a system that is more robust, trustworthy, and genuinely supportive of the owner's goals for the next decade. ADR-040, as written, is a tempting shortcut that leads to a dead end.
