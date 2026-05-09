# Gemini Panel Audit — ADR-020 Textbook Ingest v3 Rewrite

**Auditor:** Gemini 2.5 Pro (dispatched via `shared/gemini_client.ask_gemini`)
**Date:** 2026-05-06
**Dispatch script:** `docs/research/2026-05-06-gemini-adr-020-audit-dispatch.py`
**Prompt size:** 32,500 chars (~8,125 tokens)
**Output:** verbatim below

Note: Gemini self-identified as "Gemini 1.5 Pro" in output header — common LLM self-ID drift; actual model dispatched was `gemini-2.5-pro` per script.

---

This audit finds ADR-020 to be a robust and necessary course correction that correctly diagnoses the root cause of the stub crisis. It wisely incorporates Codex's structural recommendations while adhering to the owner's core principle of information fidelity. My role is to push this design further by addressing multimodal, retrieval, and multilingual blind spots that remain. My recommendations aim to harden the proposal against future edge cases and ensure the "品質 > 速度 > 成本" mandate is met not just in principle, but in practice.

### Section 1 — MULTIMODAL ASSESSMENT

ADR-020's proposal to triage Vision processing is a significant improvement over the previous "describe everything" approach, correctly identifying the cost-inefficiency noted by Codex. However, the proposed triage categories (`data-rich` / `caption-rich` / `decorative`) are too coarse for the specific domain of biochemistry and sports nutrition textbooks. My training on a vast corpus of scientific literature reveals common visual patterns that this simple classification misses.

**Multimodal Pitfalls Not Addressed:**

1. **Multi-panel Figures:** Textbooks frequently use figures labeled (a), (b), (c) to show a process, comparison, or different magnifications. Treating each panel as an independent image for triage is a critical error. The LLM must understand they form a single conceptual unit to generate a coherent description. The current triage mechanism has no provision for grouping `fig-5-1a.png` and `fig-5-1b.png`.
2. **Comparative Illustrations:** A common pattern is the side-by-side comparison (e.g., a healthy mitochondrion vs. one under oxidative stress). The core information is not in either image alone, but in the *delta* between them. A simple "data-rich" label doesn't prompt the model to perform this comparative analysis.
3. **Tables-as-Images with Complex Formatting:** Codex correctly identified "tables-as-images" as needing Vision. I will add that these often contain merged cells, footnotes indicated by symbols (*, †, §), and nested structures that standard OCR fails on. The Vision prompt must be explicitly instructed to parse these structural complexities, not just transcribe the text.
4. **Histology and Microscopy:** These images are data-rich, but their captions are often terse (e.g., "Electron micrograph of a muscle fiber"). The valuable information lies in identifying and labeling structures (sarcomeres, Z-discs, mitochondria) that are visually present but not enumerated in the caption. The triage needs to recognize this class of image as requiring descriptive annotation beyond the caption.

**Verdict: Refine.**

The triage concept is correct, but the implementation requires more specific, purpose-driven categories. I propose replacing the ADR-020 table with the following:

| Figure Type | Heuristic Identifier | Vision Action |
| :--- | :--- | :--- |
| **Quantitative** | Contains axes, legends, error bars, plot points. | **Full Vision:** Extract data series, trends, key values, and axis labels. Transcribe all equations. |
| **Structural** | Anatomical diagrams, molecular structures, histology. | **Full Vision:** Identify and label all significant components and their spatial relationships. |
| **Process** | Flowcharts, metabolic pathways, multi-panel sequences. | **Full Vision:** Describe the sequence of events, inputs/outputs, and relationships between stages. Explicitly handle multi-panel figures as a single unit. |
| **Comparative** | Side-by-side or before/after illustrations. | **Full Vision:** Focus the description on the *differences and similarities* between the compared elements. |
| **Tabular** | Image is primarily a grid of text/numbers. | **Full Vision:** Transcribe into a Markdown table, preserving structure (merged cells, footnotes). |
| **Decorative** | Stock photos, portraits of scientists. | **No Vision:** Use caption only and tag as `[decorative]`. |

This refined triage moves from a generic assessment of "richness" to a specific instruction set based on the figure's scientific purpose, directly improving the quality (品質) of the extracted information.

### Section 2 — RAG & EMBEDDING STRATEGY

ADR-020's commitment to "verbatim body" is a strong stance on information fidelity, but it correctly identifies RAG performance as an "unknown" risk. Codex rightly critiqued this, and the owner's defense ("chunking can solve") is insufficient. Leaving this as an "implementation detail" is a critical flaw. The choice of verbatim content *necessitates* a more sophisticated RAG architecture than paraphrased text would.

To de-risk this, ADR-020 must specify a concrete, state-of-the-art RAG strategy as a baseline.

**Concrete Recommendation:**

ADR-020 must mandate the following RAG architecture for source pages:

1. **Chunking Strategy: Semantic & Hierarchical, not Fixed-Size.** The proposed "sliding window 250 words" is primitive. Instead, leverage the document's inherent structure.
   - **Parent Chunks:** For each `## Section`, create one "parent" chunk. This chunk should contain the section title, the LLM-generated `Section concept map`, and the `Wikilinks introduced`. This provides a semantic summary.
   - **Child Chunks:** The verbatim paragraphs within that section become "child" chunks, each linked to their parent section. A reasonable size is ~3-5 paragraphs per child chunk, with a 1-paragraph overlap.
2. **Embedding Model: `BGE-M3`.** This model is the correct default choice for this project. Its key advantage is its native ability to handle multilingual and multi-granularity retrieval. It can effectively embed both the English verbatim text and the Traditional Chinese wikilinks/queries, which is critical (see Section 3). It also supports the hybrid retrieval approach below.
3. **Retrieval Strategy: Hybrid Search + Reranking.**
   - **Hybrid Search:** Retrieval must combine dense (vector) search using the BGE-M3 embeddings with a sparse retrieval method like BM25. This ensures that queries for specific terminology, names, or numbers (e.g., "97% fat absorption") which excel with keyword search are not missed by semantic vector search alone.
   - **Parent-Child Retrieval:** The initial search should be performed against the *parent chunks*. When a parent chunk is retrieved, the system then fetches its associated *child chunks* (the verbatim text) to provide the full context to the synthesis LLM. This "small-to-big" pattern is highly effective.
   - **Reranker:** A cross-encoder reranker (e.g., `bge-reranker-large`) is non-negotiable. After the initial hybrid retrieval fetches the top K (e.g., K=20) candidate chunks, the reranker re-evaluates them against the query with much higher precision, passing only the top N (e.g., N=5) to the final LLM. This is the single most important step to mitigate the noise from longer verbatim chunks.

This multi-layered strategy directly addresses the risks of verbatim content. It is not an "implementation detail" but a prerequisite for making the verbatim choice successful.

### Section 3 — MULTILINGUAL CONSIDERATIONS

Both the ADR and the Codex audit completely overlook a critical complexity: the system is fundamentally bilingual. It ingests English textbooks but must serve a Traditional Chinese knowledge graph, evidenced by wikilinks like `[[腸道菌群]]` (Gut Microbiota) and `[[微絨毛]]` (Microvilli). This creates significant risks for retrieval and knowledge graph integrity.

**Multilingual Pitfalls:**

1. **Cross-Lingual Retrieval Drift:** While a model like BGE-M3 is designed for cross-lingual retrieval, its accuracy is not perfect. A Chinese query for "腸道菌群" might retrieve an English paragraph about gut bacteria, but it could fail to retrieve a paragraph that uses a synonym like "intestinal flora" if the model's cross-lingual alignment for that specific term pair is weak.
2. **Term Mapping Pollution:** The current design has no formal mechanism for linking the English source term (e.g., "creatine") to the Chinese concept page `[[肌酸]]`. The LLM wrapper's `Wikilinks introduced` list is an implicit mapping, but it's uncontrolled. What happens if one chapter's wrapper links "creatine" to `[[肌酸]]` and another links it to a non-canonical `[[肌酸補充劑]]` (Creatine Supplement)? This pollutes the graph with inconsistent links.
3. **Concept Disambiguation Failure:** The English term "glycogen" is unambiguous. In Chinese, it could be `[[肝醣]]` (liver glycogen) or `[[肌醣]]` (muscle glycogen). The LLM wrapper, without explicit instruction, might just link to a generic `[[醣類]]` (carbohydrates), losing critical specificity.

**Concrete Recommendation:**

ADR-020 must be amended to include a formal bilingual term mapping discipline at the concept page level.

In the `Concept page body schema (v3)`, add a new frontmatter field:

```yaml
---
title: 腸道菌群
aliases: [Gut Microbiota, Intestinal Flora]
en_source_terms:
  - "gut microbiota"
  - "intestinal flora"
  - "gut microbiome"
type: concept
...
---
```

The `en_source_terms` field will be populated during the `create` or `update_merge` actions in Phase 2. This creates a canonical, machine-readable link between the Chinese concept page and its English source terminology. This explicit mapping provides two major benefits:

1. It can be used to augment RAG queries, performing query expansion (a search for "腸道菌群" would automatically also search for "gut microbiota" AND "intestinal flora").
2. It provides a ground-truth dataset for fine-tuning a cross-lingual reranker or validating the performance of the embedding model, turning an unknown risk into a measurable metric.

### Section 4 — CLAUDE/CODEX BLIND SPOTS

Claude (drafter) and Codex (auditor) share a common blind spot rooted in their architecture: a **system-level, process-oriented perspective that under-weights the data-level, content-centric reality of a knowledge base.** They focused heavily on the *pipeline* (how the data flows) but missed crucial details about the *data itself*.

1. **They both missed the multilingual dimension.** As detailed in Section 3, the presence of English source text and Chinese wikilinks is obvious from the provided context (`[[腸道菌群]]`). Neither model flagged the inherent cross-lingual retrieval challenges. This is a classic process-over-data blind spot; they analyzed the `ingest` process without deeply analyzing the `bilingual data` being ingested.
2. **They both accepted the "primary numbers" concept without defining it.** ADR-020 §Coverage manifest acceptance gate proposes a gate for `primary_numbers_missing`. Codex's audit (§1) even manually verifies that some numbers survive while others are lost. Yet neither asks the fundamental question: **What makes a number "primary"?** Is "250 m²" (surface area of small intestine) primary, while "10,000 to 100 million microorganisms/ml" (in achlorhydria) is secondary? This is a subjective, content-based decision. The ADR punts this to a regex-based implementation spike, which is guaranteed to fail without a semantic definition. This is a critical gap that will make the acceptance gate impossible to implement reliably.
3. **They both failed to challenge the atomicity of a "chapter".** The entire pipeline is designed around a "per-chapter" flow. In many textbooks, however, a single chapter can cover disparate topics (e.g., a chapter on "Carbohydrates" might cover both digestion and metabolic pathways like glycolysis). Conversely, a single complex concept (e.g., the Krebs Cycle) might be introduced in one chapter and revisited with clinical applications in another. The "per-chapter" processing unit can create artificial seams in the knowledge graph. A more robust design might consider a "per-topic" or "per-concept-cluster" aggregation pass *after* the initial chapter ingest.

### Section 5 — PROMOTION THRESHOLD CRITIQUE

The promotion threshold proposed in ADR-020 §Phase 3 ("≥2 sources OR single-source high-value") is a massive improvement over "every wikilink gets a page," but it introduces its own set of predictable failure modes. It is a blunt instrument for a nuanced problem.

1. **The Niche-but-Critical Concept:** A concept like "Cori cycle" might be explained in exhaustive detail in a single chapter of a biochemistry textbook and never mentioned again. It is fundamentally a single-source concept but is absolutely critical to the domain. The "high-value" escape hatch (defined as "多次提及 + 跨節 cross-ref") is insufficient, as it might only be mentioned once in its primary section.
2. **The False-Consensus Problem:** A concept like "creatine" might appear in a biochemistry textbook (source 1) and a pop-sci article on supplements (source 2). The threshold would trigger a `create` or `update_merge`. However, the textbook discusses "creatine phosphate" and its role in the phosphagen system, while the article discusses loading protocols and marketing claims. These are different facets of the same entity, and a naive merge would create a Frankenstein page. The `update_conflict` action only triggers on direct data conflicts, not on differences in scope or perspective.
3. **Quantifying "High-Value":** The definition of "high-value" is vague and qualitative ("多次提及 + 跨節 cross-ref"). This is not a machine-implementable rule and will lead to inconsistent promotions.

**Concrete Recommendation: Replace with a different mechanism.**

The binary promotion threshold should be replaced with a **Concept Maturity Model**. Instead of wikilinks being either "promoted" or "in an alias map," they exist on a spectrum.

1. **Level 1: Alias.** A wikilink target seen once. It lives in the `_alias_map.md` as proposed.
2. **Level 2: Stub.** A concept seen in a single source but deemed "high-value" by an LLM classifier (trained to look for section headings, bolded terms, definitions, etc.). It generates a page, but with `status: stub`. **Crucially, this is a new, more intelligent stub**, containing the initial extracted content and a clear prompt for what is needed for promotion (e.g., "Needs cross-reference from another source or manual review"). This reclaims the "stub" concept as a useful workflow state, not a failure state. The 200-word count minimum from Phase 2 still applies.
3. **Level 3: Active.** A concept that meets the "≥2 sources" criteria. The page is promoted to `status: active`.

This model provides a workflow. The owner can periodically review all `status: stub` pages. This solves the "niche-but-critical" problem by giving those concepts a dedicated page (a Level 2 Stub) that can be manually promoted or enriched, rather than leaving them as un-promoted aliases.

### Section 6 — FINAL VERDICT

**Verdict: Approve with modifications.**

ADR-020 is a well-reasoned and necessary pivot. It correctly identifies the pipeline bypass as the root cause of the crisis and proposes a sound architecture based on lossless ingest and synchronous aggregation. However, in its current state, it trades one set of problems (stub explosion) for another set of unaddressed risks in multimodality, retrieval, and multilingual handling. It should not be approved as-is.

My top three required modifications to move this ADR from "Draft" to "Accepted" are:

1. **Mandate a Sophisticated RAG Architecture (§Phase 1 / §Risks):** The ADR must be updated to specify the Hybrid Search + Parent-Child Retrieval + Reranker pattern detailed in my Section 2. This is not an optional "implementation detail"; it is a core requirement to make the "verbatim body" decision viable. This directly addresses the primary risk (`RAG retrieval payload bloat`) and provides a concrete path forward.
2. **Introduce a Bilingual Term Mapping Discipline (§Concept page body schema):** The ADR must be amended to include the `en_source_terms` field in the concept page frontmatter, as detailed in my Section 3. This is the only way to reliably bridge the English-source/Chinese-KB gap, de-risking cross-lingual retrieval and preventing knowledge graph pollution. This addresses a complete blind spot in the current proposal.
3. **Replace the Promotion Threshold with a Concept Maturity Model (§Phase 3):** The binary "promote/alias" threshold is too rigid. It should be replaced with the three-level `Alias -> Stub -> Active` maturity model from my Section 5. This provides a more nuanced and robust mechanism for handling the lifecycle of concepts, particularly those that are important but appear in only one source. It turns the concept of a "stub" from a sign of failure into a productive state in the knowledge management workflow.
