This is a strong draft and a rigorous first-pass audit by Codex. My value is to surface the system-level and cross-cultural blind spots that both missed.

### 1. REASONING-CHAIN DIVERGENCE (What Claude & Codex both missed)

Claude's draft focuses on the user's desired outcome (ungate sources) and presents a technically optimistic path. Codex's audit correctly grounds this in code reality, revealing hidden work and architectural drift. Both, however, missed two critical system-level dynamics:

1.  **The Human Bottleneck & Review Fatigue:** Both analyses treat the human-in-the-loop (HITL) "修修" as an inexhaustible resource. The core proposal is to triple the sources feeding the candidate queue. This doesn't just add volume; it risks drowning the user in low-quality suggestions, leading to abandonment of the *entire system*. This "candidate-to-permanent-card" conversion rate is the system's most critical health metric, and flooding the input without improving the filter or review UI is a recipe for failure. The "cooldown" mechanism is reactive; a proactive design would focus on candidate quality and review efficiency *before* scaling volume.

2.  **Architectural Inconsistency vs. Clean Slate:** Codex correctly identified that old code (`ingest.py:543-590`) violates the new "redline" principle. However, it framed this as "drift." My view is more severe: this ADR proposes a clean `adapter -> spine` architecture (§3) but plans to bolt it onto a system where a legacy, non-compliant path remains active. This creates a hybrid, inconsistent state that is hard to reason about and debug. A true architectural decision would require not just adding the new path but explicitly budgeting for the deprecation and removal of the old one.

### 2. CJK / MULTILINGUAL LENS

Deferring CJK-specific issues is a critical error that undermines the entire proposal for Book (B) and Video (E) sources. This is not a "known, not solved" inconvenience; it is a fundamental threat to the core value proposition.

*   **Anchor Rot is a Showstopper, Not a "Lint" Issue:** The value of a permanent card is its reliable link back to the source evidence. In Chinese text, which lacks explicit word boundaries and often uses paragraph-based (`^p-id`) or character-offset anchors, even minor edits can break all downstream links. For books and videos, where annotations are the *only* input for candidate generation (Slice 3), this means the candidates will be generated with links that are already broken or will break immediately. This makes the "畫線叢集 → 候選" (highlight clustering to candidate) feature in Slice 3 dead on arrival. The quality will be abysmal, and the user will rightly reject the candidates, thus feeding the "adoption gate" the same failure signal the ADR aims to prevent. **This must be addressed before Slice 3, not as a Wave 2 "lint" feature.**
*   **"畫線叢集" (Highlight Clustering) is Undefined for CJK:** The draft assumes "clustering" is a trivial step. In logographic languages like Chinese, semantic proximity is not always correlated with physical proximity. A single highlight might span multiple distinct ideas. How will the system cluster highlights? By timecode proximity in videos? By page number in books? This core algorithm for Slice 3 is completely unspecified and is non-trivial for CJK.
*   **Cross-Lingual Concepts:** The system implicitly handles concepts like "卡片盒" and "Zettelkasten." The `ConceptMatcher` in Slice 4 will need a robust aliasing or knowledge graph system to avoid creating duplicate, language-specific entities. This is unmentioned but critical for a bilingual knowledge base.

### 3. CORE DECISION

Lifting the time-gate is wise, but the reasoning and replacement mechanism are flawed.

*   **修修's Argument is Sound:** The user's logic is correct. Measuring adoption of a system while only feeding it 1/3 of the intended inputs produces biased, invalid data. The gate, as implemented, was guaranteed to fail.
*   **"Observation + Cooldown" is a Weaker Gate:** Codex correctly notes this is a softer gate with no thresholds. I'll go further: it's a *lagging indicator* that replaces a (flawed) *leading indicator*. A hard gate, for all its faults, is a forcing function that demands a "go/no-go" decision on investment. The proposed dashboard merely tracks failure after it has already occurred. A better model is not a "cooldown" but an "investment throttle" tied to specific, leading metrics (e.g., "Do not start Slice 4 until the candidate-to-permanent-card conversion rate from Slice 3 is >10% for two consecutive weeks"). This retains the discipline of the original gate without its flawed premise.

### 4. SEQUENCING & HIDDEN WORK

Codex correctly identified massive hidden work in Slices 2, 3, and 5. My primary critique is that the proposed sequence (1→2→3→4→5) prioritizes feature delivery over system stability and user value.

*   **Slice 3 is the entire user-facing value proposition.** It's where "accumulation happens." However, it depends on non-existent CJK clustering logic and fragile anchoring.
*   **A safer, value-driven sequence:**
    1.  **Slice A (Technical Foundation):** Implement Codex's "Slice A/B/C" suggestions first. This includes fixing code-grounding facts, adding literature note triggers (including on delete), and fully implementing the `candidate` status for AI-generated Wiki pages *before* writing any more of them. This is non-negotiable foundational work.
    2.  **Slice B (Solve CJK Anchoring):** Address the CJK anchor rot problem. This could involve using more robust locators or implementing a reconciliation/healing mechanism. Without this, B/E sources are useless.
    3.  **Slice C (Deliver User Value - Slice 1 & 3 combined):** Now, deliver the user-facing loop. Wire up the literature note triggers (former Slice 1) and implement the *now-robust* B/E candidate generation (former Slice 3). This delivers the "accumulation" value on a stable platform.
    4.  **Slice D/E (Enrichment - Slice 4 & 5):** With the core loop proven and stable, proceed with the expensive, high-noise Wiki enrichment features.

### 5. RISK BLIND SPOTS

The draft's risk table and Codex's audit missed the following:

| Risk | Mitigation |
| :--- | :--- |
| **HITL Review Fatigue** | The system will flood the user with candidates. Implement quality-gating (e.g., confidence scores), summarization, and a streamlined review UI (`/kb/review`) *before* tripling the candidate sources. Set a budget for daily review time. |
| **Source Mutability & Link Rot** | A source article/video can be updated or deleted, silently invalidating all annotations and permanent card links. The system needs a mechanism to detect source changes (e.g., content hashing) and flag affected cards for review. `idempotent re-render` is insufficient. |
| **Architectural Debt Accrual** | The ADR proposes adding a new, compliant data path while leaving a non-compliant legacy path (`ingest.py:543-590`) active. This creates an inconsistent, brittle system. The ADR must include a chore to refactor or remove the old path. |
| **Cost Control for 1M Context** | Slice 5's "whole book ingest" is a blank check. Define strict cost controls: a per-book cost ceiling, a monthly budget, and mandatory human confirmation before running any ingest job projected to exceed a certain cost (e.g., $5). |

### 6. VERDICT

**Rework.**

The decision to lift the time-gate is correct, but the proposed implementation path is unsafe. It prioritizes velocity over stability and ignores the two factors that will actually determine success: the robustness of the core CJK data links and the cognitive load on the single human user.

**Single Most Important Change:** Re-sequence the work to **solve CJK anchor stability (Risk #2) *before* implementing candidate generation (Slice 3).** The current plan builds a feature on a foundation known to be broken, guaranteeing the generation of low-quality, unusable candidates and ensuring the user rejects the very system this ADR aims to enable.

**One Thing Codex Got Wrong:** Codex's audit was exceptionally strong on code-level detail. However, it was too shallow in its push-back on deferring the CJK issues. By treating "CJK FTS5 instability" and "anchor rot" as separable, future problems, it missed that they represent a **present and critical failure condition** for the core logic of Slice 3 ("畫線叢集 → 候選"), making the proposed sequence unviable.
