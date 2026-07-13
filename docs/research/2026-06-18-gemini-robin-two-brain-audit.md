# Gemini 2.5 Pro audit — ADR-045 Robin 雙腦架構 + KB skill 家族

> Verbatim output from gemini-2.5-pro via multi-agent-panel skill, 2026-06-18.
> Lens: different reasoning chain + CJK/bilingual + cross-document consistency.

---

Excellent. As the third auditor, my role is to provide a distinct perspective, focusing on cross-document consistency, bilingual implications, and blind spots missed by the initial draft and the first audit. I will also audit Codex's audit.

---

### 1. CROSS-DOCUMENT CONSISTENCY

ADR-045 maintains alignment on core red lines but introduces subtle drift and omissions when cross-referenced with its foundational documents.

-   **vs. ADR-043 (Decision 8):** ADR-045's skill family creates a functional schism with ADR-043's `/draft-map` concept. ADR-043 decision 8 positions `/draft-map` as an *offensive synthesis tool* that operates *over the human-written `KB/Permanent/` layer* to find arguments and gaps. ADR-045 frames `kb-query`, `insight`, and `gap-scout` as operating on "Robin's brain" (`KB/Wiki/`). This silently splits synthesis into two domains (AI's brain vs. Human's brain) without a clear strategy for how they interact. The ADR fails to answer: when should one use `/draft-map` versus `kb-query`? This is a significant architectural ambiguity.
-   **vs. Centaur v0.2 §7 (Red Line 5):** ADR-045 D-C-1 correctly cites Red Line 5 (no self-citation between Concepts/Outputs). However, the definitions for `insight` ("挖修修沒想到的連結" / "dig up links you didn't think of") and `gap-scout` are high-risk for violating this rule in practice. An "insight" derived from chaining two AI-generated Concepts is precisely what Red Line 5 forbids. The ADR lacks an enforcement mechanism or output contract (e.g., requiring every insight to be a tuple of `(Source1_Ref, Source2_Ref, Proposed_Link_Type)`) to make this red line testable.
-   **vs. CONTENT-PIPELINE.md (Stage 4 Line 2):** The ADR acknowledges the "no ghostwriting" rule but the proposed `daily-review-assist` skill ("開卡建議卡名" / "suggest card name") pushes the boundary. A well-formed title is a "complete sentence" and a core part of the atomic content. This drifts from providing scaffolds (outlines, evidence) to generating a key piece of the final authored content, violating the *spirit* if not the letter of the Stage 4 Line 2 rule.

### 2. CJK / BILINGUAL

The ADR inherits known CJK-related technical debt without acknowledging it and is silent on bilingual alias management across the two "brains."

-   **Anchor Rot Risk:** ADR-043's panel audit explicitly flagged that Obsidian's block anchors (`^p-N`, `^loc`) are fragile for CJK languages which lack consistent word boundaries, leading to silent link rot if a sentence is edited. ADR-045's Decision D-A (ingest full text, use annotations as emphasis signals) directly relies on these anchors for its `source_refs`. By doing so, it inherits the entire link rot risk identified in ADR-043 but **fails to mention this risk or propose a mitigation** (e.g., tasking the `kb-lint` skill with anchor validation, as hinted in ADR-043). This is a critical omission.
-   **Bilingual Alias Handling:** ADR-043 decision 6 mandates that bilingual aliases (e.g., 卡片盒 ↔ Zettelkasten) be natively resolved. ADR-045's two-brain architecture does not specify how these aliases are managed. Are they a shared resource? Does Robin's brain maintain a separate alias list from the Permanent layer? This ambiguity could lead to two parallel, desynchronized knowledge graphs.

### 3. REASONING SOUNDNESS

The core "two brains" metaphor is not architecturally load-bearing and introduces a logical contradiction.

-   **Load-Bearing or Ceremony?** The architecture could be described more simply and accurately using ADR-043's existing terms: a "Candidate Layer" (`KB/Wiki/`) and an "Authoritative Layer" (`KB/Permanent/`). The "two brains" framing adds narrative ceremony but introduces no new technical invariant, write-guard, or data flow that wasn't already established. The asymmetry ("Robin looks up to 修修, not vice-versa") is just a restatement of the candidate→authoritative promotion path and retrieval-first policy from ADR-043.
-   **Internal Contradiction:** The ADR creates a contradiction. It defines Robin's brain as both a "完整、可自洽...的理解層" (a complete, self-consistent layer of understanding) and simultaneously as "candidate-tier / low-trust" (`status: candidate`, retrieval降權). A system cannot be both a complete, coherent model of a domain *and* untrustworthy by default. This framing is confusing. "Candidate Workspace" or "Evidence Ledger" are more precise terms that avoid this contradiction.

### 4. AUDIT-THE-AUDIT

Codex's audit was technically sharp and correctly identified major factual errors. However, it missed the CJK link-rot inheritance and was too quick to dismiss the phased rollout logic.

-   **Where Codex Was Right:** Codex correctly invalidated the claims about concept extraction running on full text (it's on the summary), the stale facts about `textbook-ingest`, and the overstatement of Zoro's capabilities. Its pushback on "two brains" as ceremony is spot-on. Its recommendation to fix `kb-query`'s scope is crucial.
-   **Where Codex Was Wrong:** In its point D-D pushback, Codex claims waiting for `kb-query` is "not reasonable" because search already works. This misses the point. The purpose of `kb-query` is not just search, but *synthesis over Robin's brain*. Without any content in `KB/Wiki/Concepts/`, there is nothing to synthesize. Therefore, ADR-045's decision to delay `kb-query` until after `ingest` has populated the Wiki is sound and aligns with the `feedback_iterative_playbook_refinement` principle. Codex focused on the retrieval mechanism, not the synthesis substrate.
-   **Where Codex Missed Something:** Codex's audit, while code-grounded, completely missed the CJK-specific link rot risk inherited from ADR-043. It also didn't connect the dots between ADR-045's new skill family and the pre-existing `/draft-map` offensive synthesis tool from ADR-043, thereby missing the architectural schism.

### 5. BLIND SPOTS

Both the original ADR and Codex's audit missed two critical dimensions: governance/maintenance and the human-in-the-loop failure modes.

-   **Taste-Loop Overfitting:** The `taste-profile` skill is designed to make Robin's outputs "越來越貼近修修的喜好與品味" (closer and closer to 修修's preferences and taste). This introduces a significant, unaddressed risk of creating a filter bubble or an overfitted echo chamber. The architecture needs a mechanism for "taste-breaking" or injecting novelty to prevent Robin from only finding things he already knows 修修 will like, defeating the goal of discovering "意想不到的洞見" (unexpected insights).
-   **Human Discipline & Governance:** ADR-043 correctly identified that the biggest risk is human discipline ("人寫的那層永遠空著" / "the human-written layer remains forever empty"). ADR-045 defines 8 skills that will generate suggestions, candidates, and review items, massively increasing the cognitive load on the human operator. It lacks any governance plan for this influx: What is the SLA for reviewing insights? What happens to stale recommendations? Without this, the system risks becoming a "nag-factory" that generates more noise than signal, leading to the exact human abandonment scenario ADR-043 warned against.

### 6. VERDICT

**Approve-with-modifications.**

The ADR correctly identifies the need to formalize Robin's role and skill boundaries. However, its core metaphor is flawed, it inherits technical debt without acknowledgement, and it ignores critical governance and overfitting risks. The following changes are required to make the architecture sound.

1.  **In `docs/decisions/ADR-045...md`, Section "脈絡" and "D-B":** Reframe the "雙腦不對稱" (asymmetric two brains) concept. Replace it with ADR-043's established language: "Candidate Layer" (`KB/Wiki/`) and "Authoritative Layer" (`KB/Permanent/`). This removes the logical contradiction and ceremonial language.
2.  **In `docs/decisions/ADR-045...md`, Section "D-C-1":** Add a subsection clarifying the relationship between the new skills and ADR-043's `/draft-map`. Specify that `kb-query`/`insight` operate on the Candidate Layer for evidence discovery, while `/draft-map` is the primary tool for synthesizing arguments from the Authoritative `KB/Permanent/` layer.
3.  **In `docs/decisions/ADR-045...md`, Section "風險與緩解":** Add a new risk: "CJK Anchor Rot". The mitigation is to explicitly assign the `kb-lint` skill the responsibility of periodically validating `source_refs` block anchors (`^p-N`/`^loc`) and reporting broken links, fulfilling the need identified in the ADR-043 audit.
4.  **In `docs/decisions/ADR-045...md`, Section "D-C":** Add a new row to the skill table for `taste-profile` under a new "Governance" column. The entry should state: "Includes a 'novelty injection' mechanism to periodically surface outlier documents/connections, mitigating taste-loop overfitting. The mechanism will be defined in the SKILL.md."
5.  **In `docs/decisions/ADR-045...md`, Section "D-C-5":** Accept Codex's correction. Update the text to accurately reflect that `.claude/skills/textbook-ingest` and `.agents/skills/textbook-ingest` do not exist in the repository, and the remnants are in docs, prompts, and scripts.
6.  **In `docs/decisions/ADR-045...md`, Section "D-B":** Add a sentence clarifying alias management: "Bilingual aliases (e.g., 卡片盒 ↔ Zettelkasten) are a shared resource, indexed and resolved consistently across both the Candidate and Authoritative layers to maintain a unified knowledge graph."