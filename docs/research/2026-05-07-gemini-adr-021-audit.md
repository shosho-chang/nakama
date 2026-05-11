Here is the audit of ADR-021, applying the specified lenses.

---

### 1. What Codex missed (drift detection beyond code grounding)

Codex provided a crucial, code-grounded audit, identifying immediate implementation fallacies. However, its focus on line-by-line correctness misses the broader architectural, informational, and lifecycle risks embedded in the ADR's design philosophy.

*   **Information-Theoretic Loss in the File 1/2 Split:** Codex correctly identified that removing prose fields from File 1 would break the UI. It missed the more fundamental architectural problem: the proposed split makes File 1 (positions) and File 2 (substance) a lossy, one-way transformation. The ADR states File 2 is a "Derived view of File 1," but if File 2 were lost, you could regenerate it. If File 1 is lost, you *cannot* reconstruct the precise `cfi` anchors from the prose in File 2. This makes File 1 the more valuable canonical source, yet the entire purpose of the ADR is to make the *derived* File 2 the center of the retrieval universe. This creates a permanent architectural tension and risk of desynchronization where the indexed "truth" (File 2) no longer matches the positional "truth" (File 1).

*   **Behavioral Blind Spot in the HITL Gate:** Codex audited the `kb_search` implementation but not the user-facing consequences of its output. The ADR's "Step 3b. HITL gate" assumes a user will diligently review a K=30 evidence pool. This ignores the reality of cognitive load and decision fatigue. A human presented with 30 snippets is likely to rubber-stamp the majority, defeating the purpose of a "Human-in-the-loop" gate and turning it into a "Human-in-the-way" chore. The design optimizes for machine recall, not for sustainable human editorial judgment.

*   **Lifecycle Entropy of the Project Page:** Codex correctly flagged the immediate risk of multiple agents overwriting a Project file. The deeper, long-term issue is that this ADR turns the Project page into a mutable state machine managed by three different actors (User, Brook, Web UI) across two different interfaces (Obsidian, Web). The ADR's proposal for an "evidence section," an "outline section," and frontmatter `brook_reject` keys calcifies a pattern of agent-managed content blobs within a user's canonical document. Over five years, as Brook's skills expand, this file will accrete more agent-specific sections, becoming an unreadable and unmaintainable mix of human prose and machine state, directly violating the user's stated "Vault 簡潔性" (Vault simplicity) concern.

*   **The "Frozen Slug" Fallacy:** Codex noted the user confusion risk of "frozen slug list + live content render." The more insidious problem is that this model is brittle. A user's understanding of a source evolves. A reflection added in Step 5 might completely invalidate why a source was included in Step 3. The "live render" will surface this new reflection, but the *reason for the slug's inclusion* and the *outline generated from it* remain anchored to a past, now-obsolete understanding. The system has no mechanism to flag this semantic drift, leading the user to write an article based on a foundation that has silently shifted beneath them.

### 2. Multilingual / cross-locale concerns

The system's context is a 繁體中文 user interacting with a mixed English/Chinese knowledge base. The ADR is written with a monolingual English mindset and will fail when it meets this reality.

*   **Cross-Lingual Retrieval Failure:** The proposed search query for Brook—"Project topic (one-sentence) + top Zoro keywords concat 成單一 query string"—is fundamentally flawed for a bilingual corpus. If the user's topic is in Chinese (e.g., `關於睡眠剝奪對認知功能的最新研究`) and the keywords/corpus are primarily English (`sleep deprivation`, `cognitive performance`), the existing BM25 and single-vector embedding search in `shared/kb_hybrid_search.py` will have near-zero recall. It has no cross-lingual information retrieval (CLIR) capability. The search will be biased towards the language of the query string, failing to find relevant documents in the other language.

*   **Prose Generation and Chunking Ambiguity:** The ADR specifies File 2's structure for books as "chapter-grouped." The `agents/robin/CONTEXT.md` file clarifies that the user reads and annotates `bilingual sibling` files. Therefore, a single chapter's section in File 2 will contain a mix of English verbatim quotes (from highlights) and Chinese prose (from reflections). The current `_split_h2_chunks` logic in `shared/kb_indexer.py` is language-agnostic but the downstream LLM consumers are not. Feeding a chunk of mixed-language text to Brook for outlining can degrade performance, as the model must constantly code-switch, potentially misinterpreting nuance or creating linguistically awkward summaries.

*   **Wikilink Resolution:** The decision to use `[[Concepts/X]]` in File 2 prose assumes a simple, monolingual mapping. A robust KB will have aliases (e.g., the concept "Sleep" might have a file `KB/Wiki/Concepts/Sleep.md` with a frontmatter alias `睡眠`). The current indexer and search system must be explicitly designed to resolve these aliases during retrieval; otherwise, a search for `睡眠` will not match a document that links `[[Concepts/Sleep]]`.

*   **Concept Extraction Bias:** The ADR defers having Robin read `Syntheses` files, which is a wise short-term choice. However, if this is ever implemented, an LLM extracting concepts from the user's mixed-language reflections will be highly sensitive to the language used. A reflection written in Chinese may lead to the creation of Chinese-named concept pages, while an English one creates English pages, leading to concept duplication (e.g., `KB/Wiki/Concepts/睡眠.md` and `KB/Wiki/Concepts/Sleep.md`) that fragments the knowledge graph.

### 3. Lifecycle / 5-year concerns

The design makes several short-term trade-offs that will create significant long-term debt.

*   **File 1/2 Contract Drift:** The "derived view" model is a time bomb. In five years, the schemas for annotations will have evolved. The prose generator in `agents/robin/` will have been updated multiple times. Bugs or server timeouts during the synchronous regeneration will inevitably lead to File 1 and File 2 becoming desynchronized for some subset of the 1000+ sources accumulated. There will be no audit trail to determine which is correct, forcing expensive, manual reconciliation. The lack of a transactional guarantee on the dual-file write will ensure data drift.

*   **Index Rebuild Cost:** Adding `Syntheses` to `shared/kb_indexer.py` is trivial today with 827 paths. As the vault grows to 5,000-10,000 documents, the full, single-threaded re-index process will become untenably slow, discouraging schema changes or chunking strategy improvements. The system needs an incremental indexing mechanism (e.g., based on file modification times) to remain maintainable.

*   **Project Page as an Agent Graveyard:** As noted before, the mutation contract will lead to entropy. The Project page, a core artifact of the user's creative process, will become a dumping ground for intermediate agent state. This makes the vault harder to parse, harder to migrate to a different tool, and violates the user's core principle of simplicity. A better long-term pattern is to store agent-specific state in a sidecar file (e.g., `Projects/{slug}.brook.json`) and render it into the UI, keeping the user's markdown clean.

*   **The Stale Reject List:** The `brook_reject` list in the Project frontmatter is a form of permanent, context-free censorship. A source rejected today for a specific article might be updated or become relevant in a new light tomorrow. The user will never see it again for that project because it's on a "naughty list." Over time, these lists become stale, preventing serendipitous rediscovery and creating an invisible filter bubble around the user's writing process. A better model would be to down-rank, rather than permanently hide, rejected items on subsequent runs.

### 4. Behavioral / user-psychology angles

The proposed workflow misunderstands the cognitive states of creative work and introduces unnecessary friction.

*   **HITL Gate as a Flow-Breaker:** The ADR inserts a mandatory review step (Step 3b) between generating ideas (3a) and structuring them (3c). This forces the user out of a divergent thinking mode (gathering evidence) into a convergent, critical mode (judging evidence), only to then hand control back to the agent for another generative step. This is jarring. A more natural workflow would be for Brook to produce the evidence pool *and* a draft outline at once, allowing the user to review and reject items *in the context of the proposed structure*, which is a more meaningful and less fatiguing task.

*   **Dual-Interface Cognitive Dissonance:** The design requires the user to work with two views of the same entity: the "lean" Project page in Obsidian and the "rich" evidence panel in the Web UI. The ADR claims this aligns with the user's preference, but it creates a split-brain problem. When the Web UI mutates the `brook_reject` frontmatter, the file changes out from under the user in Obsidian. This violates the principle of a single source of truth and forces the user to mentally model which interface has authority over which part of the file, increasing cognitive load.

*   **The Illusion of Control:** The "frozen slug list + live content" model gives a false sense of stability. The user believes they have curated a definitive evidence pool, but the substance within that pool can change at any moment if they add a new annotation to a source. This can lead to confusion when an outline generated yesterday no longer seems to match the "live" evidence displayed today. The user is likely to trust the live view, not realizing the agent's decisions were based on stale data, leading to wasted effort trying to reconcile the discrepancy.

### 5. Cross-domain analogues — has this problem been solved elsewhere better?

The ADR reinvents several wheels, ignoring decades of precedent in adjacent domains.

*   **Annotation Stores (Hypothesis, Readwise):** These services master the split between position and substance. They use robust, standardized anchoring methods (e.g., W3C Web Annotation Data Model) where the annotation "body" (the substance) and "target" (the position) are distinct but linked objects within a single record. They do not maintain two separate, canonical files that can drift. The substance is indexed, but the positional data is the ultimate anchor. ADR-021's two-file system is a brittle, bespoke version of this solved problem.

*   **Outliners and Block-based Editors (Roam, Logseq, Tana):** These tools treat annotations, highlights, and reflections as first-class, addressable blocks. A reflection isn't just prose in a document; it's a block with a unique ID that can be referenced, queried, and embedded elsewhere. This avoids the ADR's problem of trying to locate substance via coarse H2 chunking. By adopting a block-based model for File 2, each reflection could be a distinct, retrievable unit, making the "which reflection hit" problem trivial.

*   **Academic Writing Tools (Zotero, Scrivener):** These tools handle the "evidence pool" concept far more gracefully. In Scrivener, a user can gather research documents into a folder, view them on one side of the screen, and write their manuscript on the other. The "pool" is just a folder of documents, not a frozen list of slugs embedded in the manuscript file itself. The user can drag new sources in or remove them at will. The ADR's approach of hard-coding the evidence list into the project file is rigid and inferior to these established writer-centric UX patterns.

*   **Build Systems & Caching (Make, Bazel):** The "File 2 is a derived view of File 1" problem is analogous to a build artifact. Modern build systems would never implement this as a synchronous, blocking call in a user-facing save operation. They would define File 2 as a build target with File 1 as its dependency. A change to File 1 would invalidate File 2, which would be regenerated by a background worker. This is precisely the asynchronous model the current book digest writer uses and which the ADR unwisely proposes abandoning.

### 6. Verdict

I concur with Codex's verdict: **do not ship as-is**. The ADR correctly identifies a critical problem—unsearchable annotations—but proposes a solution that introduces more architectural complexity, long-term maintenance costs, and behavioral friction than it resolves.

I would sharpen and extend Codex's 9 required amendments with the following 5 architectural and user-centric requirements:

1.  **Adopt a Unitary, Indexed Annotation Model.** (Supersedes Codex #2, #3). Abandon the two-file canonical split. Keep `KB/Annotations/{slug}.md` as the single source of truth. Evolve its schema to be a list of structured annotation objects (e.g., `{"type": "reflection", "body": "...", "chapter_ref": "..."}`), similar to the Web Annotation Data Model. The prose generator becomes a pure, non-canonical *view* for Obsidian readability, but the indexer should read the structured JSON from the canonical file directly. This eliminates all drift and synchronization problems.

2.  **Implement True Cross-Lingual Search.** (Sharpens Codex #6). The `kb_search` engine must be upgraded to handle the user's actual bilingual workflow. This means implementing a proper CLIR strategy, such as using separate language models for embedding queries in different languages or using a multilingual model like LaBSE, before any Brook workflow is built on top of it. Concat-string-and-hope is not an architecture.

3.  **Decouple Agent State from User Documents.** (Sharpens Codex #7). Prohibit agents from writing complex, multi-line state (like evidence pools or outlines) directly into the body of user-owned Project pages. Agent-generated content should be stored in sidecar files (`.brook/evidence.json`) or referenced by a simple, single-line link in the main document. This preserves vault simplicity and prevents long-term file entropy.

4.  **Redesign the HITL Gate for Creative Flow.** (New Amendment). Replace the proposed sequential, blocking HITL gate. Instead, Brook should perform a single "Synthesize & Outline" step that produces both the evidence list and a draft outline that uses it. The user can then review and edit both together in the Web UI, a more natural and contextual editing process that respects their cognitive flow.

5.  **Implement an Incremental, Background Indexer.** (Sharpens Codex #1, #8). The entire `kb_indexer.py` needs to be refactored to support incremental updates based on file modification times. Full re-indexing should be an explicit, manually-triggered maintenance task, not the default behavior. This is a prerequisite for scaling the KB. The synchronous regeneration on save must be moved to a background task, inheriting the proven pattern from the existing book digest writer.