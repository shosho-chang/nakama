# ADR-024 Cross-Lingual Concept Alignment — Gemini 2.5 Pro Audit

**Date:** 2026-05-08
**Reviewer:** Gemini 2.5 Pro (independent third-party push-back lens)
**Subject:** ADR-024 monolingual-zh source ingest, cross-lingual Concept alignment
**Verdict:** Reject + refine alternative architecture. Codex's rejection is correct, but for incomplete reasons. This audit focuses on the unaddressed lifecycle and multilingual-native architectural flaws.

---

### 1. What Codex missed (architectural / lifecycle / behavioral lens beyond code grounding)

Codex's audit is excellent at code-grounding, correctly identifying missing files (`shared/alias_map.py`), incorrect file paths (`concept_dispatch.py`), and contradictions (`is_zh_native`). However, its focus is primarily on implementation correctness *within the proposed architecture*. It misses the more fundamental, long-term problems with the architecture itself:

*   **Linguistic Debt:** In **Decision 1. Concept page 命名**, the choice to make filename language the canonical identifier creates profound linguistic debt. The ADR dismisses the alternative of language-neutral filenames based on a weak "vault 簡潔" preference. Codex questions this justification but doesn't connect it to the long-term pain of renames, the structural inability to add a third language (e.g., Japanese), or the conflation of identity with a human-readable label. This is the ADR's single most damaging decision, and Codex's push-back is not strong enough.

*   **Behavioral Recovery Loops:** Codex correctly identifies the risk of the `is_zh_native: bool` flag but frames it as a "type-level contradiction." The deeper issue is behavioral: what happens when the LLM gets it wrong? ADR-024 offers no recovery path for 修修. When "肌酸" is incorrectly mapped to "Creatinine" in the **Decision 2. Ingest concept extraction** step, how does the user even *detect* this silent error, let alone correct it? The system lacks a user-facing review or disambiguation step, a critical flaw in any cross-lingual system.

*   **Calcification of Aliases:** The "lazy build" approach in **必做工程任務 #5** and **不做的事** means aliases accrete in frontmatter over years. Codex points out that old English pages will be left behind but misses the second-order effect: this frontmatter becomes an unmanageable, de-normalized blob of text. There is no strategy for alias review, disambiguation, or pruning. This frontmatter-as-database approach is a classic path to KB rot.

*   **Cross-Domain Ignorance:** ADR-024 reinvents a wheel that has been engineered for decades by projects like Wikipedia, WordNet, and SNOMED CT. All of these systems converged on a language-agnostic identifier (a page ID, a synset ID, a concept code) with language-specific labels attached. The ADR's filename-as-identity model ignores this entire body of prior art, a massive architectural red flag that Codex's code-level audit does not raise.

### 2. MULTILINGUAL CONSIDERATIONS — cross-lingual retrieval, term mapping, disambiguation

The ADR's approach is brittle and naive about the complexities of cross-lingual mapping, especially for a user writing in 繁體中文.

*   **Wrong Term Mapping & Recovery:** The failure mode is severe. During ingest, if a Chinese term like 「肌酸」 (jī suān, Creatine) is near-missed by the LLM and mapped to the existing grounding pool entry "Creatinine", a biochemically distinct substance, this error becomes canonical. The annotation gets merged into the wrong concept page. Brook then synthesizes incorrect scientific claims. The ADR provides **zero recovery loop**. There is no UI for 修修 to review the LLM's proposed `canonical_en` mapping before it's committed. The fix requires manual file editing (`Concepts/Creatinine.md` frontmatter) and possibly re-ingesting the source, which is an unacceptable user burden.

*   **`is_zh_native` Calibration:** The LLM has no reliable calibration for this. 「自由基」 (zìyóujī) is a standard translation for "free radical." If the ingest LLM is hesitant (e.g., the source text is philosophical, not biochemical), it might incorrectly flag `is_zh_native=true`. This creates a new page `Concepts/自由基.md` that directly competes with the existing `Concepts/Free Radicals.md`. The KB is now forked. This isn't an "escape hatch"; it's a mechanism for uncontrolled namespace pollution.

*   **Alias Normalization:** The "NFKC + casefold + plural" strategy cited from the "5/8 P0 batch" is mismatched. `casefold` is irrelevant for Chinese characters. Applying English pluralization/stemming rules to an alias map that includes Chinese terms is nonsensical and risks creating false equivalences or failing to normalize valid English variants when mixed with Chinese aliases. The normalization strategy must be per-language.

*   **Wikilink Display Portability:** The `[[Concepts/Mitophagy|粒線體自噬]]` syntax is an Obsidian-specific rendering feature. While many Markdown processors can be extended to support it, it is not standard. If Brook's output is ever consumed by a different pipeline (e.g., a static site generator, another LLM), these links will either break or render improperly, showing the canonical English name. This couples the knowledge representation to a specific editor's rendering logic.

*   **Chinese Language Variants:** The ADR completely ignores the 繁體 (zh-Hant) vs. 簡體 (zh-Hans) distinction. 修修 is a 繁體中文 user. If a scraped article contains 简体 characters ("粒线体自噬"), will the system recognize it as an alias for the same concept? The "NFKC" normalization mentioned does not handle this character-set conversion. This is a fundamental oversight for a system claiming to handle Chinese source ingest.

### 3. CROSS-DOMAIN ANALOGUES — has this exact problem been solved better?

Yes, this problem has been solved many times, and ADR-024 ignores all established patterns.

*   **Wikipedia Interlanguage Links:** Wikipedia uses language-specific pages (e.g., `en.wikipedia.org/wiki/Mitophagy`, `zh.wikipedia.org/wiki/粒線體自噬`) linked by a central, language-agnostic entity ID in Wikidata (e.g., `Q6881954`). This correctly separates identity from language-specific labels. ADR-024's model of making one language's label (English) the identifier is architecturally inferior and less scalable.

*   **MediaWiki Redirects:** MediaWiki uses redirects for true synonyms *within the same language* (e.g., "mtDNA" redirects to "Mitochondrial DNA"). It does not use redirects for interlanguage links. Aliases in ADR-024 are being used to serve the function of interlanguage links, which is the wrong tool.

*   **ICD-10 / SNOMED CT:** These medical terminologies are the gold standard. They use stable, language-agnostic codes (e.g., SNOMED CT `721685002` for "Mitophagy"). Attached to this code are descriptions in multiple languages, with flags for "preferred term," "synonym," etc. This is the model Codex gestures toward and is the robust, long-term solution.

*   **WordNet / BabelNet Synsets:** These also use a language-agnostic identifier for a "concept" (a synset). Different words (lemmas) in different languages are then linked to this synset. This again reinforces the separation of identity and label.

*   **Obsidian Wikilink Alias:** The ADR over-relies on the pipe `|` alias for its cross-lingual UX. This is a *display-time rendering trick*. The underlying link still points to `Concepts/Mitophagy.md`. If that file is renamed, all links break unless Obsidian's tooling is used to update them. It does not solve the core identity problem.

*   **Roam/Logseq Block-based Identity:** These systems assign a unique ID to every block, making identity independent of page title or content. While a full migration is out of scope, the principle is relevant: stable, non-human-readable IDs are superior for long-term systems.

### 4. 5-YEAR LIFECYCLE / CALCIFICATION CONCERNS

The ADR's decisions will create a brittle, high-maintenance system over time.

*   **Alias Calcification:** After ingesting 100+ Chinese sources, the frontmatter of ~200 Concept pages will be filled with `aliases: ["term1", "term2", "term3", ...]` arrays. This becomes a de-normalized, untyped, and unmanaged database hidden inside Markdown files. Finding which concepts are aliased to "线粒体" will require a full text search of the vault, not a structured query. This data structure will rot.

*   **Rename Cascade Cost:** When "Mitophagy" must be renamed to "Selective Autophagy," the file `Concepts/Mitophagy.md` must be renamed. Every single file that wikilinks to it (`[[Concepts/Mitophagy]]`) must be updated. While Obsidian has tools for this, it's a high-friction operation that breaks external references and git history. With a language-agnostic ID, this would be a simple frontmatter label change with zero downstream breakage.

*   **Dual Sources of Truth:** The ADR is ambiguous about the relationship between `_alias_map` and frontmatter aliases. **Decision 1** states the `_alias_map` will be expanded by merging aliases from ingest. **不做的事** states alias is "Concept page frontmatter authoritative." This implies a one-way sync from frontmatter to an in-memory `_alias_map`. If they can drift, the system's behavior becomes unpredictable. If they must be in sync, it creates redundant data storage.

*   **Embedding Model Replacement:** The dependency on ADR-022 is noted, but the lifecycle is not. When BGE-M3 is replaced by BGE-M4 or another model in 2-3 years, the entire dense index must be rebuilt. While this is expected, the ADR doesn't consider if the *sparse* part of the system (the alias map) should be designed to compensate for the known volatility of embedding models. The current design tightly couples retrieval success to the quality of one specific model (BGE-M3).

*   **Premature Lock-in:** The `monolingual-zh` mode defined in `CONTEXT-MAP.md` is a premature optimization. The system should define a `monolingual` mode with a `lang` parameter. What happens when the first Japanese or Korean book arrives? The entire `mode` enum and all associated logic will need to be refactored, because the ADR hardcoded "zh".

### 5. BEHAVIORAL / USER-PSYCHOLOGY ANGLES

The proposed workflow creates silent failure modes and confusing user experiences.

*   **Invisible, High-Stakes Errors:** As noted, when the ingest LLM maps a Chinese term to the wrong English canonical, the error is committed silently. There is no Bridge UI or feedback loop where 修修 can confirm "Yes, 「肌酸」 maps to 'Creatine'". This violates the principle of keeping the user in control during high-stakes, irreversible actions like knowledge base integration.

*   **"Lazy Build" User Frustration:** The "lazy build" strategy for backfilling aliases sounds efficient but creates a frustrating UX. 修修 ingests a Chinese book on longevity. She then uses Brook to synthesize an article. She expects Brook to find the existing English concept `Sirtuins`, but it won't, because no Chinese source has yet mentioned sirtuins and caused the alias to be added. From her perspective, the system is unpredictably broken. A one-time, reviewed backfill, as Codex suggests, is the minimum viable approach.

*   **Grounding Pool Bloat:** The ADR acknowledges the token budget concern but dismisses it. At 500+ concepts, the grounding pool of names and aliases could easily exceed 20k-30k tokens. This will not only increase costs but also slow down every ingest and potentially degrade LLM performance as it navigates a larger, noisier context. This design does not scale gracefully.

*   **Alias Edit Conflicts:** The `aliases` array in frontmatter is a footgun. 修修 can edit it directly in Obsidian. The `textbook-ingest` agent can also edit it programmatically. What is the conflict resolution model? Last write wins? Does the agent append, or overwrite? This is undefined and will lead to data loss or confusion.

### 6. VERDICT

**Reject + refine alternative architecture.**

I concur with Codex's "Reject + alternative architecture" verdict, but I place stronger emphasis on fixing the core identity model before any other changes. The ADR's foundational sin is conflating a concept's *identity* with its *English-language label*. This is a multi-year architectural mistake.

I **uplift and sharpen** these items from Codex's alternative:
*   **A. URI/slug-based namespace:** This is the critical change. I would be more prescriptive: Implement a `canonical_id` (e.g., `cpt_20260508_ax4g`) in the frontmatter of every Concept. The filename `Mitophagy.md` becomes a human-friendly slug that *can* change. All wikilinks should eventually resolve to the `canonical_id`. This solves the rename cascade problem permanently.
*   **Structured, Language-Tagged Labels:** The frontmatter must evolve from `aliases: []` to a structured object:
    ```yaml
    canonical_id: cpt_12345
    labels:
      en:
        preferred: "Mitophagy"
        aliases: ["Mitochondrial autophagy"]
      zh-Hant:
        preferred: "粒線體自噬"
        aliases: ["粒線體吞噬作用"]
    ```
    This is non-negotiable for a multilingual system. It correctly models the data, handles language variants, and scales beyond two languages.

I **refine** these items from Codex's list of minimum required changes:
*   (2) `is_zh_native: bool` should not be replaced by `resolution_status: needs_human_canonicalization`. Instead, the entire ingest output should be a *proposal* that lands in a review queue in the Bridge UI. The output should be: `{proposed_mapping: {source_term: '粒線體自噬', target_concept_id: 'cpt_12345'}, status: 'pending_review'}` or `{proposed_new_concept: {labels: {zh-Hant: '氣血'}}, status: 'pending_creation'}`. The LLM's job is to propose, the human's job is to decide. **No automated page writes.**
*   (4) Remove `shared/alias_map.py` entirely. The single source of truth is the structured `labels` block in each Concept's frontmatter. The resolver should be a function that loads all concepts into an in-memory structure on startup, not a separate, potentially drifting file.
*   (5) The mandatory backfill command must be a **human-in-the-loop process**. A script should generate candidate `zh-Hant` labels for existing English concepts and present them to 修修 for review in a simple UI, not commit them directly.

This ADR attempts a tactical shortcut for a strategic problem. It should be rejected and rewritten to adopt a stable, language-agnostic identity model from the outset.