# ADR-048 Panel Audit — Gemini 2.5 Pro（2026-06-24）

> Multi-agent panel step 3，逐字保留（讀 ADR-048 v1 + ADR-047 + Codex audit）。判決：approve with significant modifications。

Here is the audit from the perspective of the third reviewer, Gemini.

***

## ADR-048 Audit — Multi-Agent Panel Step 3 (Gemini)

To: 修修
From: Gemini (Third Reviewer)
Date: 2026-06-25
Subject: Audit of ADR-048 (Robin Memory Architecture) and Codex Review

This audit follows Claude's proposal and Codex's rigorous code-grounded review. My goal is to add a third perspective, focusing on cognitive architecture, the semantics of memory extraction in a multilingual context (Traditional Chinese), and potential second-order effects that a purely technical or English-default lens might miss. I will acknowledge where I concur with Codex and elaborate where my reasoning differs.

### Section 1 — MEMORY-ARCHITECTURE & PERSONALIZATION LENS

The proposed strategy in D-C, "Robin learns the user from annotations + accept/skip signals," is conceptually appealing but semantically fragile. Codex correctly identifies the signal as "noisy," but I want to reframe the core challenge: this is not a signal processing problem, but an **intent inference problem** under conditions of extreme ambiguity.

A user's annotation (`註解`) is not a direct statement of belief. It is a low-cost interaction with a text, whose intent can range from agreement ("這句說得對"), disagreement ("這觀點有問題"), to simple utility ("引用這段數據"). The system, lacking this context, is forced to assume the simplest intent: "user finds this interesting/true." This is a dangerous default. The `accept/skip` signal is slightly stronger but, as Codex notes, still ambiguous. A "skip" could mean "I already know this," which is the *opposite* of "this is not a topic of interest."

From a memory systems research perspective (e.g., work on grounded cognition, memory consolidation), durable memories are formed through reinforcement, contextual richness, and explicit elaboration. ADR-048's proposal shortcuts this process, attempting to create a semantic memory (`user_memories`) from a single, low-context behavioral trace (`candidate_events`). This is akin to building a psychological profile based on what newspaper articles someone circles, without knowing *why* they circled them.

**Multilingual (zh-TW) Angle:**
The English-default lens of my predecessors misses a crucial ambiguity in the term `註解` (zhùjiě). In English, "annotation" often implies a brief, marginal comment. In Chinese, `註解` can encompass everything from a simple highlight, to a personal reflection (`筆記`, bǐjì), to a critical counter-argument. The current proposal treats all of these as a monolithic signal of "interest."

Furthermore, Chinese syntax allows for dense, topic-comment structures where negation or modality can be subtle. An LLM extractor, especially a smaller one like Haiku, could easily misinterpret a highlighted sentence like 「這種做法**未必**是最佳選擇」 ("This approach **is not necessarily** the best choice") as an endorsement of "the best choice" if it misses the nuance of `未必`.

Therefore, the extraction strategy in D-C is not just noisy; it is foundationally unsound without a mechanism to capture user intent. It risks populating Robin's memory with false beliefs about you, which will then corrupt its ranking and summarization functions.

### Section 2 — DIFFERENT PRIOR

My training prior differs from Claude's optimism and Codex's architectural purism in the following ways:

1.  **Behavioral Patterns Precede Semantic Beliefs:** Before an agent should claim "修修 believes X," it should first learn "修修 frequently accepts candidates about topic Y" or "修修 often annotates sentences containing statistical claims." My prior suggests modeling user *behavior* first, as a separate, observable layer. `user_memories` (semantic beliefs) should only be derived from high-confidence, recurring behavioral patterns, not from single actions. ADR-048 conflates the observation with the conclusion.

2.  **The Primacy of Negative Space:** What a user *doesn't* do is often as informative as what they do. A robust memory system should track not just `accept` signals but also patterns in `skip` signals. If you consistently skip candidates from a certain source or about a certain sub-topic, that is a powerful, durable preference. The current `user_memories` schema is built around positive assertions and lacks a first-class representation for negative preferences or boundaries.

3.  **Constitutional AI and Self-Correction at the Source:** ADR-047 introduced a nightly reflection pass, which is a form of self-correction. My prior emphasizes applying this principle earlier, at the point of extraction. Instead of just extracting a fact, the extractor should generate a *hypothesis* with its reasoning: "Based on the user accepting a card derived from this annotation, I hypothesize they are interested in mitochondrial efficiency. Confidence: Low. Source: `candidate_events:1234`." This makes the memory auditable and allows the reflection pass to work with richer, grounded data rather than disembodied claims.

4.  **Tool Use as the Strongest Signal:** The most unambiguous signal of user preference is not a passive annotation but active tool use. When you ask Nami to research a topic or ask Robin to create a fleeting note (as proposed in D-D), that is a direct, high-intent command. My prior would weight memory derived from these explicit commands an order of magnitude higher than memory inferred from ambiguous signals like `accept/skip`.

### Section 3 — CLAUDE/CODEX BLIND SPOTS

Claude and Codex share a bias towards viewing memory as a monolithic, semantic store (`user_memories`). They both missed the opportunity to stratify "memory" into distinct, interconnected layers with different levels of confidence and purpose.

1.  **The Missing `candidate_events` Layer:** Both the ADR and the Codex audit jump from `accept/skip` actions directly to `user_memories`. Codex rightly suggests storing these actions first, but frames it as "product telemetry." I see it as a fundamental, non-negotiable architectural layer. There should be a `candidate_events` table (`timestamp`, `candidate_id`, `action_type`, `user_note_on_action`). This table is the **ground truth** of user interaction. `user_memories` are then a *fallible, synthesized view* on top of this ground truth. This distinction is critical for debugging, explainability, and preventing the "garbage in, garbage out" problem. Neither document makes this architectural separation explicit.

2.  **The "Why" of an Action:** Both documents overlook the most valuable signal: asking the user *why* they took an action. A simple, optional "Reason for skip?" dropdown (e.g., "Not relevant," "Already know," "Low quality") or a text field on accept would provide the missing intent context. This transforms a noisy signal into a high-quality, labeled dataset for fine-tuning Robin's ranking model. This is a common pattern in human-in-the-loop systems that both models overlooked.

3.  **ADR-048 D-F's Circular Dependency:** Codex correctly points out that the episodic layer prerequisite for D-F is not fully landed. However, both missed a more subtle logical flaw. D-F proposes a shared detector to find "repeat tasks." But what defines a "task"? For the system to recognize that "修修請 Robin 收 X 主題 5 次" is a repeating task, it needs a schema to define task boundaries, inputs, and success criteria. This schema does not exist. The ADR hand-waves this by referencing an "episodic layer," but that layer, as described in ADR-047, is for observational memories ("today I observed 修修..."), not structured task logs. The blind spot is assuming that a log of events is the same as a log of *tasks*.

### Section 4 — PERSONALIZATION RISK & SIGNAL QUALITY

I will amplify Codex's concerns with concrete mitigations rooted in my different prior.

**D-C (Annotation/accept-skip → user-memory):**
*   **Risk:** As discussed, this creates a high-speed feedback loop of misinterpretation. Robin infers a false preference, ranks candidates based on it, you accept one of the better-looking ones (confirmation bias), which reinforces the false preference. This is a classic filter bubble, but it's worse: it's a *hallucinated preference bubble*.
*   **Mitigation 1 (Behavioral Layer First):** Do not write to `user_memories` from D-C in Phase 2. Instead, write to a new `user_behavior_patterns` table. The reflection agent can analyze `candidate_events` nightly to find patterns like "User accepts >70% of candidates from `arxiv.org`" or "User skips 90% of candidates mentioning 'corporate earnings'." These are grounded, defensible observations. Only after a pattern is stable for weeks should a higher-level agent propose a semantic `user_memories` entry.
*   **Mitigation 2 (Explicit Intent Capture):** Add an optional, one-click reason for `skip` actions in the review UI. This provides the crucial negative signal and disambiguates intent.

**D-F (Shared skill-suggester over episodic task logs):**
*   **Risk:** False "repeat task" detection. Without a structured definition of a "task," the system might bundle unrelated actions. For example, "research topic X" and "find a specific paper by author Y on topic X" are different tasks. A naive detector based on keywords would incorrectly merge them, leading to useless skill suggestions.
*   **Risk (Privacy):** While this is a single-user system, a raw, unstructured log of every command given to every agent creates a highly sensitive and difficult-to-manage data blob. A structured log is paradoxically more private because it forces a clear definition of what is being stored, making it easier to audit and control.
*   **Mitigation 1 (Structured Task Schema):** Before D-F, there must be an ADR defining a `task_events` schema. It should include `task_id`, `agent_name`, `user_intent_verb` (e.g., `summarize`, `fetch`, `create_note`), `normalized_entities`, `input_hash`, and `output_artifacts`. This provides the structure needed for a reliable repeat-task detector.
*   **Mitigation 2 (Human-in-the-Loop):** The skill suggester should not just say "I see a repeat task." It should present the evidence: "I've seen 5 tasks in the last 7 days that look similar. [Show Task 1, 2, 3, 4, 5]. Do these represent the same workflow?" This allows you to confirm or deny the pattern before a skill is created.

### Section 5 — ARCHITECTURAL CONCERNS

I largely agree with Codex's assessment but will add a few points.

*   **D-E (Memory as platform default) Over-coupling:** Codex warns against making memory writes the default. I'll go further: this decision is premature and conflates two types of memory. **Episodic event logging** (structured `task_events`) *should* be a platform default. It's cheap, structured, and provides the raw data for all future learning. **Semantic memory extraction** (`user_memories`) should *never* be an automatic default; it is a costly, high-risk process that requires a specific policy for each agent, as Codex noted. ADR-048 bundles these two, which is an architectural error.

*   **D-D (Slack bot) Optimizing the Wrong Thing:** I agree with Codex that the bot is scope creep. My reasoning is different: the bot optimizes for *ingestion convenience* before the system has proven it can handle the *processing and synthesis* of that information reliably. The highest value is in fixing the core loop (D-B: inbox) and improving the intelligence of the review (D-C: better signals). Adding a new firehose of low-context fleeting notes via Slack will only exacerbate the signal-to-noise problem. First, prove Robin can learn from high-quality signals (curated annotations), then open it up to lower-quality ones (chat).

*   **Technical Debt:** The biggest technical debt ADR-048 locks in is the idea of a single, flat `user_memories` table for all types of memory. By mixing inferred preferences, stated facts, and behavioral patterns, it creates a semantic soup that will become impossible to reason over. The debt is the eventual, painful migration to a stratified memory system (e.g., `user_stated_facts`, `user_inferred_preferences`, `user_behavioral_patterns`).

*   **Phase Ordering:** The proposed phasing is flawed. D-F (skill suggestion) is presented as Phase 4, but it depends on a structured task log that isn't even defined. D-E (platform default) is Phase 5, but it's a foundational decision that affects all other agents. The ordering should be driven by the data flow:
    1.  Fix data loss (Phase 0/1: Bug fix + Inbox).
    2.  Capture clean ground-truth data (New Phase: `candidate_events` and `task_events` schemas).
    3.  Build reliable, low-level models on that data (Revised D-C: Behavioral pattern detection).
    4.  *Then*, and only then, build higher-level features like semantic memory, bots, and skill suggestions.

### Section 6 — FINAL VERDICT

**Approve with significant modifications.** The core problem statement is valid, but the proposed solutions are architecturally naive and jump to high-level features without building the necessary data foundations.

My top required changes, which build upon and refine Codex's recommendations:

1.  **Re-scope Phase 1 (D-B) to include `candidate_events`:** Do not just build the inbox. Simultaneously, create a `candidate_events` table to log every `accept`, `skip`, and `defer` action with its timestamp and `candidate_id`. This is the ground-truth dataset that all future learning depends on. This is a stronger version of Codex's recommendation #1.

2.  **Replace Phase 2 (D-C) with Behavioral Pattern Extraction:** Defer writing to `user_memories` from Robin. The new Phase 2 deliverable is an agent/process that analyzes `candidate_events` to generate and store observable *behavioral patterns* (e.g., "high affinity for source X," "low affinity for topic Y"). These patterns, not raw actions, become the input for personalizing the P-1 ranking. This directly addresses the core flaw in D-C that both I and Codex identified.

3.  **Introduce a "Task Schema" Prerequisite for D-F:** Before any work on the skill-suggester, a separate, preceding ADR must define a structured `task_events` schema. All agents must be retrofitted to emit events to this schema. This makes Codex's point #5 a formal prerequisite and ensures D-F is built on a solid foundation.

4.  **Split D-E into Two Decisions:** Re-label D-E as "Platform Data Logging." Decide that structured `task_events` and `candidate_events` logging *is* a platform default. Create a *new, separate* ADR for "Default Semantic Memory Extraction," to be considered only after the behavioral pattern approach (my point #2) has been evaluated. This refines Codex's recommendation #4.

5.  **Defer D-D (Slack Bot) Indefinitely:** The bot is a new product surface, not a memory feature. It should be its own ADR, proposed only when the core knowledge processing loop is demonstrably robust and intelligent. This reinforces Codex's recommendation #3.

This revised plan prioritizes fixing data loss, establishing high-quality ground-truth data streams, and building intelligence from the bottom up, mitigating the significant personalization and architectural risks in the current proposal.
