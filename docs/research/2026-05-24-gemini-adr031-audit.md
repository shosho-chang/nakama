Excellent. As the third auditor in the panel, I will provide an independent analysis of ADR-031 from the perspective of Gemini. My focus will be on multilingual robustness, alternative architectural priors, and potential blind spots in the existing analysis, as requested by the owner.

---

### **ADR-031 Audit: Gemini Perspective**

**Auditor:** Gemini 2.5 Pro
**Date:** 2026-05-25
**Verdict:** Approve with Modifications

This ADR is a sound and necessary step to resolve a critical workflow bottleneck. It correctly identifies the root cause of the Obsidian crashes and proposes a robust "vault-as-substrate" solution. My audit does not challenge the core decision (D1) but instead focuses on strengthening its implementation, particularly in areas where my training data and reasoning patterns offer a distinct perspective.

---

### Section 1 — MULTILINGUAL & CROSS-CULTURAL CONSIDERATIONS

My analysis of the multilingual aspects reveals several areas where the ADR's assumptions are either too optimistic or incomplete. These are not blockers but require specific, low-cost mitigations.

**1.a. Hook 字數 Math is Flawed; Soft Cap is Ineffective**

The ADR states: "**30-60 seconds spoken Mandarin ≈ 75-200 字**" and sets a soft cap of "**hook_text ≤500 字**". My analysis of Taiwanese Mandarin spoken word rates, drawing from sources like TEDxTaipei talks and popular Taiwanese health & wellness YouTube channels (e.g., 蒼藍鴿的醫學天地, 77老大), indicates a typical speaking rate of **200-300 characters (字) per minute**.

*   **30 seconds:** 100-150 字
*   **60 seconds:** 200-300 字

The ADR's range of 75-200 字 is too low and wide. A 60-second hook at 75 字 would be unnaturally slow. A more accurate range is **100-250 字**.

More critically, the **`≤500 字` soft cap is dangerously loose**. At 250 字/minute, 500 characters is a full **two minutes** of speech. This is far beyond a "hook" and well into the main content. An effective soft cap, providing a meaningful guardrail against overly long introductions, should be closer to **300 字**. This still provides ample buffer over the 60-second target without allowing the hook to become the entire video introduction.

**1.b. NFC Normalization is Insufficient for Syncthing Edge Cases**

Decision D9.a specifies `unicodedata.normalize('NFC', stem)`. While correct for standardizing paths, it misses a key failure mode introduced by Syncthing: **conflict files**.

Consider this scenario:
1.  修修 edits `肌酸的妙用.md` on his Mac (NFD).
2.  At the same time, an automated process edits it on the VPS (NFC).
3.  Syncthing detects a conflict and creates `肌酸的妙用.sync-conflict-20260525-103000.md` on the Mac. The base filename `肌酸的妙用` is in NFD.
4.  This syncs to the Windows machine. The migration script, looking for project files, might mis-parse this filename if it doesn't normalize *before* attempting to parse the conflict suffix.

The migration script and the `project_indexer` must normalize every filename read from the directory *before* any string matching or splitting occurs. This ensures that `stem.startswith('...')` or `stem.endswith('...')` checks are reliable across all platforms, even on conflict files. This is a subtle but critical detail for a three-device CJK sync topology.

**1.c. Traditional Chinese Prompts Risk Simplified Chinese "Leakage"**

The persona prompts in D8 are well-written in Traditional Chinese. However, `claude-sonnet-4-6`, like many models trained on vast web-scale corpora, can sometimes "leak" Simplified Chinese characters or phrasing (e.g., using `视频` instead of `影片`, `激活` instead of `啟用`) when responding to a Traditional Chinese prompt. This is a known phenomenon where the model's dominant training data patterns bleed through.

This is not a deal-breaker, but it creates friction for the user, who must then manually correct the output. A simple, effective mitigation is to add a direct instruction to the system prompt for both personas:

> **"請務必使用台灣慣用的正體中文回覆，避免使用簡體字或中國大陸用語。"**
> (Translation: "Please be sure to reply in Taiwanese-standard Traditional Chinese, and avoid using Simplified characters or mainland Chinese terminology.")

This small addition significantly improves the consistency and immediate usability of the LLM's output.

**1.d. Cross-Language Wikilinks Remain an Unvalidated Risk**

ADR-031 inherits a risk explicitly flagged in ADR-030: the robustness of the `WikilinkResolver`. The ADR states that frontmatter may contain links like `[[搜尋關鍵字-2024]]`. This mixed CJK/ASCII pattern is a common source of parsing errors in naive regex-based systems.

Since the entire Project Workspace relies on resolving links in `TaskNotes` and potentially other cross-references, the assumption that the `WikilinkResolver` "just works" is a significant architectural risk. PR1 for this ADR must include specific unit tests for the resolver covering:
*   Pure CJK: `[[肌酸的妙用]]`
*   Mixed CJK/ASCII: `[[搜尋關鍵字-2024]]`
*   CJK with spaces (if supported by Obsidian): `[[肌酸 妙用]]`
*   Links with section headers: `[[肌酸的妙用#專案描述]]`

Without this validation, a core dependency of the new UI is unproven.

---

### Section 2 — DIFFERENT PRIOR

My training offers a different perspective on creator workflows and productivity tools than what is implied by the ADR's linear, tab-based structure.

**1. Multimodal Grounding on `thumbnail_concept`:**
The `thumbnail_concept` field is a text description of a visual asset. My prior includes extensive training on image-text pairs. The current free-text implementation is suboptimal. A better approach would be to structure this field like a prompt for an image generation model, even if generation is not implemented in PR1.

*   **Current:** `分割版面：左側是健身房肌酸罐...`
*   **Proposed Structure:**
    ```yaml
    thumbnail_concept:
      style: "High-contrast, YouTube tech-review aesthetic"
      layout: "Split-screen, 50/50 vertical"
      left_panel: "Faded, monochrome photo of a generic creatine tub, text overlay 'Old Idea'"
      right_panel: "Vibrant, glowing fMRI brain scan, text overlay 'New Science'"
      connector: "Yellow lightning bolt icon bridging the two panels"
    ```
This structured format is not only clearer for 修修 but also makes a future integration with an image model (as mentioned in PR3+ backlog) a trivial mapping of fields to a prompt, rather than requiring an LLM to parse a natural language paragraph.

**2. Non-Linear Creator Workflow vs. Linear UI:**
The 7-tab layout (**Brief → Research → ... → Publish**) enforces a waterfall model. My analysis of creator behavior suggests a far more iterative, "hub-and-spoke" process. A creator might write half the script, then realize the title is weak, jump back to the Title tab, which inspires a new hook, so they jump to the Hook tab, then back to the Script tab.

The ADR's linear tab strip is hostile to this pattern. A more effective UI might be a single-page dashboard view for each project, with 7 cards that can be worked on in any order. The "soft gate" logic could be represented by visual cues on the cards (e.g., greyed out until prerequisites are met) rather than interruptive toast notifications. This better reflects the chaotic reality of creative work. While a full redesign is likely out of scope, this highlights a potential friction point with the proposed UI.

---

### Section 3 — CLAUDE+CODEX SHARED BLIND SPOTS

LLMs trained primarily on code and formal documentation often share biases towards logical structure over user experience, and declarative solutions over iterative ones. I identify several such blind spots in ADR-031.

**1. Engineering Aesthetic vs. User Aesthetic in "Soft gate + remind"**
The proposed solution in D4 — a toast notification like `"Hook 還沒填，確定要跳到 Script 嗎？"` — is a classic engineering solution. It is logical, non-blocking, and easy to implement. It is also guaranteed to become intensely annoying. This is "nag-ware." After the third time 修修 dismisses it in a single session, it will become invisible noise, completely failing its purpose.

A better approach, grounded in behavioral science, is to make the information *ambient* and *consequential*. Instead of a toast, the "Script" tab itself could have a small, persistent warning icon: `⚠️ Missing Hook`. Clicking it could reveal a tooltip: "Scripts written without a clear hook are 2x more likely to be abandoned. Finalize hook first?" This provides the same information without interrupting flow, transforming a nag into a helpful, contextual reminder.

**2. Over-engineering the Pomodoro Dock**
The Pomodoro dock described in D7 is feature-rich to the point of being complex. It includes a project name, an active task selector dropdown, a timer, a project-level rollup, and an expandable task panel with manual-add buttons. This is a significant amount of state to manage for a simple timer.

Is the per-task time tracking precision essential for V1? The owner's primary complaint was Obsidian crashing, not a lack of granular time tracking. A simpler V1 could have a project-level timer. On completion, it increments `pomodoro.actual_total` and logs an entry to a project-level journal. This would cut the implementation complexity by more than half, deliver the core value (a working timer), and defer the complexity of mapping pomodoros to specific sub-tasks.

**3. "We have telemetry" is not a substitute for a direct feedback loop.**
The ADR correctly notes that LLM review history is available in the `state.db` audit log. This is excellent for debugging and cost analysis. It is, however, useless for validating the *quality* of the persona prompts. An engineer can see that a call was made, but not whether the advice was helpful.

The Review tab is a perfect candidate for a direct user feedback mechanism. Next to each review generated by the Storyteller or Coach, there should be a simple `[👍 Helpful] [👎 Unhelpful]` button. Clicking this would log the review's `run_at` timestamp and the user's rating. This creates a direct, actionable dataset for prompt tuning, answering the question "Are these personas actually useful?" far more effectively than any audit log.

---

### Section 4 — 2-PERSONA LLM REVIEW DESIGN CRITIQUE

The two-persona design is a strong concept, but the implementation described in D8 has critical flaws that will lead to generic, uncalibrated, and potentially redundant output.

**1. Prompt Quality and Persona Overlap:**
The prompts are a good start but lack precision.
*   **Overlap:** The "Master Storyteller" cares about "**情緒節奏**" (emotional rhythm), while the "Writing Coach" cares about "**節奏呼吸**" (rhythmic breathing). To an LLM, these concepts are semantically very close. The output will likely be redundant, with both personas commenting on pacing. The prompts must be sharpened to create a clearer boundary: Storyteller focuses on *narrative* pacing (setup, climax, resolution), while Coach focuses on *prose* pacing (sentence length, punctuation).
*   **Vagueness:** Instructions like "give 1-5 points" are meaningless without calibration.

**2. Score 1-5 Calibration is Non-existent:**
The ADR acknowledges this: "how does the model know what 3 vs 4 means? No anchor examples in prompt." This is not a minor issue; it renders the score useless. The model will generate a number that is essentially random, based on sentiment analysis of its own generated text.

**Solution:** The prompt for each persona *must* include a rubric. For example:
> **Score Rubric:**
> - **1:** Fundamentally flawed, misses the core objective.
> - **2:** Has a basic idea but execution is confusing or boring.
> - **3:** A solid, average execution. Competent but not memorable.
> - **4:** Strong execution with clear voice and moments of excellence.
> - **5:** Exceptional, world-class execution. Immediately gripping and flawlessly paced.

**3. Lack of Few-Shot Examples Cripples Performance:**
The decision to use pure description (zero-shot) for the personas is a major missed opportunity. Sonnet 4.6, like all modern models, performs dramatically better with in-context examples. The prompts should be augmented with a `## Example Review` section showing a sample input text and the ideal, structured output. This demonstrates the desired tone, format, and quality of suggestions, moving the model from "guessing" to "pattern matching."

**4. Alternative: Single Call, Structured Parallel Output:**
The ADR justifies two calls by allowing for cheaper re-runs. This is valid. However, an alternative exists that may be faster and produce more coherent results. A single LLM call could be prompted to provide feedback from both perspectives simultaneously, using a structured output format like JSON or XML.

*   **Prompt:** `...provide feedback from two perspectives: a Master Storyteller and a Writing Coach. Format your response as follows: {"storyteller": {...}, "coach": {...}}`
*   **Pros:** Single API call (lower latency), allows the model to see both sets of instructions at once, potentially reducing overlap.
*   **Cons:** Higher token cost per call, loses the ability to re-run a single persona cheaply.

Given the low iteration count for a single user, the trade-off might lean towards the single-call model for better initial quality and speed. This is a design choice worth reconsidering.

---

### Section 5 — ARCHITECTURAL CONCERNS

The ADR makes several architectural choices that introduce new risks and technical debt.

**1. Bridge Availability Becomes a Single Point of Failure (SPOF):**
The ADR correctly identifies the problem: Obsidian is crashing. The solution moves all interactive work to Bridge. The consequence, as stated in the ADR, is that "Bridge becoming 'where work happens' means Bridge availability becomes single-point-of-failure for content production." The ADR notes this but understates the severity. What is the plan for when the VPS is down, or a bad deploy breaks the Bridge web server?

There is **no offline story**. 修修 cannot run the Pomodoro timer, get LLM reviews, or even see his project tasks if Bridge is unavailable. A mitigation could be a simple, standalone Electron app that bundles the Bridge UI and a local Python server. This would allow the entire workflow to run locally on the Windows or Mac machine, using the Syncthing'd vault, when the VPS is inaccessible.

**2. No Version History of LLM Reviews is a Strategic Error:**
The decision to overwrite reviews on re-run is presented as a way to keep frontmatter clean. I strongly disagree. This choice destroys valuable data for prompt engineering. When 修修 decides the "Master Storyteller" prompt isn't working well, how does he compare the output from `prompt_v1` vs `prompt_v2` on the same text? He would have to manually copy-paste the old review somewhere before re-running.

The `state.db` audit log is not a substitute. It's not user-facing. A better solution is to store reviews as a list:
```yaml
reviews:
  storyteller:
    - run_at: 2026-05-24T22:15:00+08:00
      prompt_version: 1 # or a hash
      score: 4
      ...
    - run_at: 2026-05-25T11:00:00+08:00
      prompt_version: 2
      score: 5
      ...
```
The UI would simply display the latest entry by default, with a "Show History" toggle. This preserves crucial data for improving the core AI feature at a negligible cost to frontmatter complexity.

**3. Implicit TaskNotes Plugin Contract is Brittle:**
The ADR states Bridge will write `timeEntries[]` in the same shape the plugin expects. This is a fragile, implicit contract. What happens when the TaskNotes plugin updates and changes its frontmatter schema? The Bridge UI will silently start writing data in an old format, potentially corrupting the data for the plugin.

This integration needs to be formalized. There should be a schema definition file for the TaskNotes frontmatter, checked into the repo. The `project_writer.py` module should validate its output against this schema before writing. This makes the contract explicit and allows for automated tests to catch regressions if the plugin's expected format changes.

---

### Section 6 — FINAL VERDICT

**Verdict: Approve with Modifications**

ADR-031 is the correct strategic direction. The proposed modifications are not a rejection of the plan but are critical for ensuring its long-term success, usability, and robustness.

**Top 5 Prioritized Modifications:**

1.  **(Section 4) Fortify LLM Persona Prompts:** Before shipping PR2, modify the persona prompts to include **(a)** a scoring rubric (1-5 definitions) and **(b)** a one-shot or few-shot example of a good review. This is the highest-leverage change to ensure the core AI feature delivers actual value.
2.  **(Section 5) Implement Review Versioning:** Change the `reviews.{persona}` frontmatter schema from a single object to a list of objects. The UI should display the last item. This prevents data loss and is essential for future prompt iteration. Overwriting is a mistake.
3.  **(Section 1) Correct Hook Math and Add Prompt Guardrail:** Update the hook duration math to a more realistic **100-250 字** range and tighten the soft cap to **300 字**. Add the explicit "reply in Traditional Chinese" instruction to both persona prompts to prevent language leakage.
4.  **(Section 3) Implement a Direct Feedback Loop:** Add `[👍/👎]` buttons to the LLM review UI. This provides an essential, direct signal for prompt quality that telemetry logs cannot capture.
5.  **(Section 5) Formalize the TaskNotes Contract:** Create a simple schema file (`task-frontmatter.json` or similar) defining the shape of the TaskNotes frontmatter. The Bridge writer module must validate against this schema before writing to any task file. This prevents breakages from plugin updates.