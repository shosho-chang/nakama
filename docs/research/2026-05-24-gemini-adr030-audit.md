This audit provides a third-party, independent review of ADR-030 from the perspective of a Gemini-family model. It focuses on areas where my reasoning chain, multilingual grounding, and architectural priors may differ from the initial draft by Claude and the first audit by Codex.

### 1 — LANGUAGE & LOCALE LENS

The project's primary language is Traditional Chinese (繁體中文), a detail with significant technical and financial implications that are understated in the ADR and missed by the Codex audit.

**Tokenization Economics are Understated**

The ADR's claim that "200k char (~70k token)" is used for cost calculation is dangerously optimistic for its primary content language. My analysis confirms the prompter's intuition: for UTF-8 encoded Traditional Chinese, the character-to-token ratio is typically between 1:1.5 and 1:2 for leading models' tokenizers. A single Chinese character often requires two or more tokens, whereas English words average 1.3 tokens.

-   **Incorrect Math:** A 200,000 character corpus of primarily Chinese text is more likely to be **100,000 to 130,000 tokens**, not 70,000.
-   **Recalculated Cost:** Using Codex's corrected pricing ($3/M input, $15/M output), the cost of a single `/digests/ask` query is not `$0.18` or `$0.24`, but closer to:
    -   120,000 input tokens @ $3/M = $0.36
    -   2,000 output tokens @ $15/M = $0.03
    -   **Total: ~$0.39 per query.**
-   This is more than double the ADR's estimate. The monthly cost projection of "$20–50/month within a year" could easily become $50–120/month. The `MAX_CONTEXT_CHARS` guardrail is therefore not just a performance boundary but the primary cost control lever, and its financial impact is currently miscalibrated in the ADR.

**Wikilink Robustness for CJK Filenames**

Decision D6 states `WikilinkResolver` will handle `[[target]]`. However, neither the ADR nor the audit addresses the specific risks of CJK filenames like `[[健康-2024]]`. While modern systems handle Unicode well, potential failure points exist in this stack:
1.  **URL Encoding:** Does the resolver correctly percent-encode `健康-2024` for the `href` attribute?
2.  **Filesystem Normalization:** macOS uses NFD (decomposed) Unicode for filenames, while Linux/Windows typically use NFC (composed). Syncthing papers over some of this, but a Python-based resolver reading from the filesystem could encounter normalization mismatches, leading to broken links that appear valid.
3.  **Case Sensitivity:** While not a CJK-specific issue, the interaction between case-sensitive filesystems (Linux on VPS) and case-insensitive ones (Windows/macOS defaults) can break wikilinks if the user is not consistent.

The ADR should acknowledge these risks and confirm the resolver implementation is robust against them.

**Cross-Lingual Retrieval is an Unstated Strength and Risk**

The user, 修修, may ask questions in English about Chinese digests, or vice-versa. The D3 "LLM-over-vault" pattern is surprisingly effective for this, as it avoids the embedding-space alignment problem inherent in cross-lingual RAG. Feeding the full, mixed-language text to a powerful multilingual model like Sonnet 4.6 or Gemini allows it to use its internal cross-lingual representations to find answers. This is a significant, unstated benefit of the chosen strategy.

However, it also introduces a dependency on the model's specific cross-lingual capabilities. A future model that is cheaper but less capable at code-switching could degrade performance. This should be noted as a long-term risk.

### 2 — DIFFERENT PRIOR

My training and architectural priors differ from the Claude/Codex consensus in several key areas, leading to a different interpretation of the ADR's long-term bets.

**Prior 1: The Vault is a Graph, Not a Filesystem**

The ADR and audit treat the vault as a collection of files in a directory tree. This is a missed opportunity. An Obsidian vault's power comes from its nature as a knowledge *graph*, where markdown files are nodes and `[[wikilinks]]` are directed edges.

-   **D3 Flattens the Graph:** The LLM-over-vault strategy concatenates files, destroying this graph structure. The LLM sees a linear stream of text, not a network of connected ideas. It cannot reason about which concepts are central (highly linked) or which notes are orphaned.
-   **A Better Bet:** The long-term bet should not just be on cheaper tokens, but on models with graph-reasoning capabilities. The architecture should preserve this graph structure as much as possible for future agents that can traverse links, analyze connectivity, and understand the topology of the user's knowledge. D3 is a pragmatic short-term solution, but it should be recognized as a temporary simplification that incurs information loss.

**Prior 2: For Personal-Scale, Cognitive Ergonomics Outweighs Scalability**

For a single-user system, the most important metric is not queries-per-second but "time to trusted insight." The architecture should feel like a direct extension of the user's own mind.

-   **The "Why" Matters:** The ADR focuses on getting *an* answer. My prior emphasizes the need for the user to understand *why* the answer is what it is. An LLM-over-vault query is a black box. A superior design would make the process transparent: "I answered your question based on these 17 digests from May 2026. I ignored 9 older digests due to the scope cap. Here is the exact prompt I used." This builds trust and gives the user agency.
-   **Sync Drift as Cognitive Dissonance:** Codex notes the risk of sync failure. I frame this differently: for a single user, a sync-drifted state is not a technical error, it's a form of cognitive dissonance. The user's "brain" is out of sync with itself. The system must therefore treat sync health not as an operational concern but as a primary UX feature, surfacing status and conflicts prominently.

**Prior 3: The Future is Multi-Modal and Agentic**

The ADR is entirely text-centric. The bet on "LLM cost crash" is too narrow. The more significant trajectory is the explosion in model *capabilities*, particularly multi-modality (images, audio, video) and agentic workflows. A personal knowledge base will inevitably include screenshots of diagrams, audio notes, and clips from lectures.

-   The `VaultBlobLoader` handles `.epub`, but the core patterns (D3, D5) are exclusively about markdown text. This creates a future architectural cliff. The ADR should acknowledge that a parallel strategy for multi-modal knowledge artefacts will be needed, and the current "markdown-in-vault" substrate may not be sufficient on its own.

### 3 — CLAUDE/CODEX BLIND SPOTS

Claude's draft and Codex's audit share a bias towards treating the system as a stable, predictable software environment. They both missed critical failure modes rooted in the messy reality of filesystems, synchronization, and long-term knowledge work.

**Blind Spot 1: Syncthing Conflict Files Are Ignored**

Both documents mention "sync drift" but fail to address Syncthing's specific conflict resolution mechanism. When a user edits a file on two offline devices, Syncthing does not merge them. It saves one as `filename.md` and the other as `filename.sync-conflict-YYYYMMDD-HHMMSS.md`.

The current implementation of `shared.digest_indexer` (`KB/Wiki/Digests/{PubMed,AI}/*.md`) would **silently ignore** these conflict files. This could lead to the system answering questions based on a stale version of a digest while the user's latest thoughts are quarantined in a file the system cannot see. This is a severe data-loss scenario from the perspective of the Q&A feature, and it was missed entirely.

**Blind Spot 2: The Archival Query Use Case Breaks D3**

Both documents accept the D3 pattern as a general solution for ad-hoc queries. However, they only consider *recency-based* queries. A critical use case for a long-term knowledge base is the *archival query*: "Summarize my thoughts on intermittent fasting from 2027."

-   D3's `MAX_DAYS=30` guardrail makes this query impossible. The pattern is fundamentally incapable of answering questions about content older than its recency window.
-   This is not a minor limitation; it's a gaping hole in the "ad-hoc query" strategy. This forces a re-evaluation of D5. The archival query use case meets condition (a) ("corpus exceeds what a 1M-context LLM can chew") over a long enough timeline, but it may not meet condition (b) ("sub-second retrieval"). The ADR provides no path for this crucial query pattern.

**Blind Spot 3: The Observability Gap is an Architectural Flaw**

Codex correctly points out the poor UX of the `已截斷` (truncated) message. I will elevate this: the lack of observability is not a UX papercut, it is a fundamental architectural flaw in a system designed for a single expert user.

The system makes an opaque, non-deterministic transformation of the user's own knowledge and presents it back. For the user to trust it, they must be able to audit it. The ADR dismisses this as an "operational" concern for dashboards (`Out of scope`), but the necessary data (which files were included/excluded, the final prompt) must be plumbed through the architecture from the start. Both Claude and Codex accepted this dismissal, which I view as a mistake.

### 4 — ARCHITECTURAL CONCERNS

**D4's "Knowledge vs. State" Boundary is Brittle**

The ADR presents a clean, binary divide. Reality is messier. Consider a digest file's frontmatter: `status: draft`, `confidence: 0.8`, `reviewed_by: 2026-06-01`. Is this knowledge or state? It's metadata *about* knowledge. It has a structured shape and may be mutated frequently during a review process, yet it lives in the vault.

The routing rule should be reframed from "human vs. machine readable" to be based on **transactional guarantees and mutation patterns**.
-   Needs atomic, multi-record updates? → `state.db`
-   Needs to be versioned alongside long-form content? → vault frontmatter
-   High-frequency, ephemeral state? → `state.db`
The current rule is too simplistic and will lead to incorrect placements.

**D5's Carve-Out Rule is a Floodgate, Not a Gatekeeper**

Codex rightly criticizes the subjectivity of the D5 conditions. I will go further: the conditions, as written, will inevitably lead to "carve-out creep." The archival query use case discussed above already puts pressure on it. Any feature needing to query beyond a 30-day window against a growing corpus will eventually meet condition (a). The "sub-second retrieval" requirement is the only thing holding the line, and it's a weak defense against a user who finds the multi-second LLM latency too slow for their workflow. The ADR needs to be more honest that D3 is a temporary strategy for *recent* items, and a more robust indexing solution (D5) will likely become the default for most non-trivial queries over time.

**The Lock-in to a Single LLM Provider is a Strategic Risk**

D3's implementation hard-codes `DEFAULT_MODEL = "claude-sonnet-4-6"`. While pragmatic for Tier A, the ADR fails to discuss provider diversity as a strategic concern. The bet on "LLM cost crashes" is implicitly a bet on *Anthropic's* pricing trajectory. A more resilient architecture would abstract the LLM provider, allowing the system to route queries to different models (e.g., a Gemini model for multilingual tasks, a smaller model for summarization) based on cost and capability. The ADR should at least acknowledge this as a future direction.

### 5 — UX & OWNER EXPERIENCE

**Answer Rendering Needs Markdown Support**

The current plan to render the LLM's answer in a `pre-wrap` block is a poor user experience. LLMs are excellent at generating structured, readable output using markdown (lists, bolding, tables). Forcing the output to be plain text cripples the model's ability to communicate clearly. The answer from `/digests/ask` should be passed through the same markdown rendering pipeline as the digest bodies themselves.

**Citation UX is Ambiguous**

The ADR is vague on how citations work. Does the LLM spontaneously invent a `[type/date]` format, or is it instructed to do so? Is this linked to the source list at the bottom? A robust implementation would require the LLM to emit structured citation markers that the backend can then transform into actual links to the source digests. Relying on the LLM to format text is brittle.

**Truncation UX Must Be Explicit**

As noted by Codex and my points above, `已截斷` is insufficient. The user must be told **what was dropped**. A better message would be: "Context includes 21 digests from 2026-05-04 to 2026-05-24. 9 older digests were excluded to meet the context limit." This provides crucial context about the answer's scope.

**Error Messages Should Be Actionable, Not Internal**

`查詢失敗：RuntimeError` exposes an implementation detail. For a single technical user, this is better than nothing, but not by much. A better error would suggest a cause or a remedy: "Query failed: The LLM API returned an error. Please try again later." or "Query failed: Could not load digest files. Please check Syncthing status."

### 6 — FINAL VERDICT

The core principles of this ADR are sound for the project's current scale, but its analysis of cost, long-term viability, and failure modes is flawed. The document contains factual errors and strategic blind spots that, if left unaddressed, will lead to budget overruns and architectural problems.

My top modifications focus on correcting the financial model, acknowledging the limitations of the proposed patterns, and hardening the system against real-world data and usage patterns.

1.  **Rewrite Context Cost & Tokenomics (Context section):** Replace the "200KB ≈ 70k tokens" and "$0.18/query" claims. Use a realistic 1:1.5+ char:token ratio for Chinese, recalculate the cost to be ~$0.39/query, and adjust the monthly budget forecast accordingly. This is a critical correction to the project's financial assumptions.

2.  **Reframe D3 as a Recency-Scoped Pattern (Decision D3):** Explicitly state that "LLM-over-vault" is a strategy for **recent ad-hoc queries** and is unsuitable for archival or historical queries. This manages expectations and clarifies the pattern's boundaries.

3.  **Strengthen D3 Guardrails with Observability (Decision D3):** Mandate that any D3 implementation must not only cap the scope but also **explicitly report the scope** to the user in the UI (e.g., "Answer based on digests from X to Y. Z older items were excluded.").

4.  **Acknowledge Archival Query Gap (Rationale / Alternatives A4):** Add a section acknowledging that archival queries are a known gap. State that they will likely require a D5-style indexed solution in the future and are not served by D3. This prevents the team from believing D3 is a more general solution than it is.

5.  **Address Syncthing Conflict Files (Rationale / Consequences):** Add a negative consequence: "Syncthing conflict files (`*.sync-conflict.*`) are currently ignored, leading to potential silent data loss for query features. A future iteration must define a strategy for detecting and handling these files."

VERDICT: approve-with-mods
