# Gemini 2.5 Pro audit — ADR-051 Director skill

- Date: 2026-07-05
- Dispatch: shared.gemini_client.ask_gemini, thinking_budget=4096
- Input: ADR-051 全文 + CONTEXT.md + 實施計畫 + Codex 審計原文
- Verbatim below.

---

Excellent. As the third reviewer, my role is to synthesize the ADR and Codex's audit, adding a distinct perspective grounded in multimodal, cross-lingual, and creator-centric realities. Where Codex focused on programmatic integrity and reproducibility, I will focus on the final visual product, the creator's workflow ergonomics, and the subtle complexities of the cross-lingual context.

Here is my audit of ADR-051.

---

### **Gemini Audit of ADR-051: Director Skill**

This audit finds ADR-051 to be directionally sound in its core decision (D1) to treat the creative layer as an agentic skill. This correctly prioritizes evolving "taste" over premature optimization for a solo creator. However, the proposal over-specifies the implementation details (D8's B-roll taxonomy) and overlooks significant visual, multilingual, and workflow friction points that could undermine the final product's quality and the creator's efficiency.

Codex's audit provided an essential, rigorous check on code-level drift and programmatic contracts. I concur with its findings on schema conflicts, the need for deterministic asset resolution, and the fallacy of automatic cross-lingual PDF highlighting. My focus will be on the layers above that: the viewer's experience and the creator's cognitive load.

### **Section 1 — VIDEO/VISUAL ASSESSMENT**

The ADR and implementation plan are dangerously silent on the most important aspect of a visual medium: visual consistency. Codex did not address this. The proposed v1 B-roll taxonomy (D8) is a recipe for a visually incoherent, "Frankenstein's monster" video.

*   **Inconsistent Visual Grammar:** The seven B-roll types originate from four distinct visual sources:
    1.  **Hyperframes (Programmatic):** `bigstat`, `transition_title`, `quote_card`, `book_cover`, `doc_highlight`. These will have a clean, vector-based, and likely motion-graphics-heavy feel defined by `--sho-*` brand tokens.
    2.  **Envato (Stock):** Live-action, cinematic footage with its own color grade, lighting, and camera work.
    3.  **KOL Clips (Found Footage):** Screen-captured from YouTube, likely with different resolutions, aspect ratios, compression artifacts, and on-screen graphics from the original creator.
    4.  **Screen Recording (Creator Supplied):** UI recordings with their own visual language.

    The ADR provides no strategy for unifying these. A `bigstat` card with sharp, branded motion followed by a soft-focus, cinematic Envato clip, then a 720p screen capture of a KOL, creates jarring visual context shifts for the viewer. Before implementing seven component types, the project needs a **Visual Brand Guide** that answers: How are stock clips color-graded to match the A-roll? How are KOL clips framed or treated (e.g., placed in a device mockup, given a consistent border/filter) to signal they are "found footage"?

*   **Pacing and Legibility Pitfalls:** The plan to anchor `Big Title Transition` duration to the spoken phrase (1.5-3s) is good in principle but ignores readability. If the Chinese title is long (e.g., 「第三點：後設認知是覺察思考的思考」), can a viewer actually read and comprehend it in 1.8 seconds, especially with motion? The skill playbook needs a rule: `duration = max(speech_duration, min_readable_time)`. Furthermore, the ADR and Codex both missed the interplay between on-screen text and the channel's *permanent* Traditional Chinese subtitles. A `doc_highlight` composition, which zooms into an English PDF page already dense with text, will become an unreadable mess when subtitles are overlaid at the bottom. The system must treat the subtitle area as a permanent "no-fly zone" for critical visual information.

*   **Fullscreen vs. Overlay Design:** D3 rightly defaults to fullscreen B-roll because PiP/overlay layouts are not yet implemented. This is a bigger constraint than the ADR acknowledges. It means there is no way to show a concept visually *while keeping the creator on screen*. This makes the video feel more like a slideshow and less like a personal, presenter-led explanation. The "alpha spike" (PR D) is mission-critical; its failure would be a major blow to the channel's visual style, which often relies on the creator pointing to or interacting with overlaid graphics.

*   **Technical Mismatch (Framerate):** A subtle but crucial point. The talking-head A-roll is likely shot at 24, 30, or 60fps. Hyperframes renders are likely 30fps. Stock video can be anything. KOL clips are often 30fps but can be 60fps for gaming content. Mixing framerates without proper handling in DaVinci Resolve can lead to stuttering or motion judder. The `fcpxml_emitter` needs to be aware of source framerates to set the correct flags for the timeline, or the skill playbook must include a step to normalize/conform all assets to the project's primary framerate.

### **Section 2 — DIFFERENT PRIOR**

My training prior differs from Codex's on the core "skill-vs-program" debate, leading to a different interpretation of the risks.

Codex’s prior is that of a software engineer: non-reproducibility is a liability. It sees the "skill" as a "dangerous system of record" and pushes for deterministic tools. This is valid for a team-based software project.

My prior, grounded in observing countless solo creators, is that **creator burnout and cognitive friction are the primary project risks, far exceeding the risk of non-reproducibility.** A solo creator's "taste" is the most valuable asset, and it evolves.

*   **Skill as "Taste Capture":** The ADR's choice of a Claude skill (D1) is architecturally brilliant *for this specific context*. It's not just a way to handle creative loops; it's a mechanism for capturing and refining taste in a living document (`SKILL.md`). For a solo creator, a rigid `plan` program that gets the taste 80% right but is hard to tweak is *more* frustrating and costly than an interactive skill that gets it 95% right with some manual guidance. The ADR's loop ("run an episode -> write lessons back to the manual") is the correct way to build this system. I see the skill not as a liability, but as the core asset.

*   **Stock Licensing is an Architectural Concern:** The ADR and Codex treat "Envato search" as a simple asset-retrieval step. My prior indicates that stock asset licensing is a major architectural driver. Is the creator on a subscription plan (Envato Elements) with unlimited downloads, or buying single clips (Envato Market)?
    *   If subscription: The `assets_queue` model is acceptable, as the cost of downloading alternates is zero.
    *   If per-clip: The model is flawed. The Director skill must be far more precise, and the review UI must allow approval *before* purchase/download to avoid wasted money. The ADR implicitly assumes an all-you-can-eat subscription model, which should be made explicit.

### **Section 3 — CLAUDE/CODEX BLIND SPOTS**

Claude (in drafting the ADR) and Codex (in auditing it) share a fundamental bias: they are text-first, state-machine-oriented systems. They process the world as a series of structured inputs and outputs. This causes them to miss the lived, temporal, and human dimension of the workflow.

*   **They both missed the human cost of the "wait state."** The ADR notes the creation of a "material waiting" state. Codex flags the operational fragility of the handoff. Neither addresses the real problem: this state shatters the creative flow. The creator, "修修," acting as the Claude agent operator, gets the storyboard 90% complete, then hits a wall: `assets_queue.yaml`. They must context-switch, become a "download manager" using a separate tool (Codex), wait for downloads, then manually move files and resume the Director skill. This isn't just "fragile"; it's a recipe for frustration and lost afternoons. It turns a single creative session into a disjointed, multi-hour chore.

*   **They both missed the "visual grammar" consistency problem outlined in Section 1.** Claude's ADR proposes a grab-bag of B-roll types without a unifying aesthetic. Codex's audit checks if the *code* can handle these types but not if the *video* can. This is a classic blind spot for non-multimodal systems: they can reason about the semantic label ("stock photo") but not the aesthetic properties (color grade, depth of field, motion language).

*   **They both missed the explicit need for cross-lingual conceptual search.** Codex correctly identified that PyMuPDF cannot find a Chinese paraphrase of an English sentence (D7). But both models missed the identical problem for stock video search (D5). The script is in Traditional Chinese. The creator's *intent* is in Chinese. The Envato search query must be in English. This is not a simple translation task. A script segment about 「心態的彈性」(xīntài de tánxìng - "mental flexibility" or "resilience") requires translating a *concept* into searchable English keywords: "resilience," "bouncing back," "flexible mind," "adapting to change," "abstract neuron paths," etc. The `SKILL.md` playbook must contain a dedicated section on this cross-lingual conceptual brainstorming, likely using an LLM as a dedicated tool for query expansion.

### **Section 4 — MULTILINGUAL / CROSS-LINGUAL DEEPENING**

Codex opened the door by flagging the PyMuPDF issue. Let's go much deeper. The entire workflow is a cross-lingual tightrope walk, and the ADR is wearing flip-flops.

1.  **Search Term Generation (Envato/YouTube):** As mentioned above, this is the weakest unaddressed link. The quality of the entire video hinges on finding resonant visuals. Relying on the agent operator to spontaneously come up with good English keywords from a Chinese script is inefficient. **Recommendation:** The Director skill should have a mandatory tool-use step: `generate_search_terms(zh_hant_concept: str) -> list[str]`. This tool would use a powerful multilingual model to brainstorm a rich set of English search keywords and phrases, capturing different facets of the original Chinese concept.

2.  **Quote Card Typography:** A `quote_card` (金句卡) mixing a Chinese quote with an English name (e.g., "— Carl Jung") is a typography nightmare. Traditional Chinese fonts are monospaced and have a specific character box. Latin fonts are proportional. Simply rendering them with the same point size and line height will look amateurish. **Recommendation:** The `quote_card` Hyperframes component must be designed by a typographer to handle this pairing gracefully. It should use specific, paired fonts (e.g., Noto Sans TC for Chinese, Noto Sans for English) and have logic for relative font sizing (e.g., English attribution at 80% of the Chinese point size) and baseline alignment.

3.  **Subtitle vs. On-Screen Text Conflict:** This is a multilingual issue, not just a visual one. The viewer is listening in Mandarin, reading subtitles in Traditional Chinese, and now you're asking them to read an English paper snippet in a `doc_highlight` B-roll. This is high cognitive load. **Recommendation:** When a `doc_highlight` beat appears, the system should consider either (a) automatically fading the main subtitles for the B-roll's duration or (b) having the `doc_highlight` composition render its own (bilingual?) summary of the highlighted text in a subtitle-safe area, making the main subtitle track redundant for that moment.

### **Section 5 — ARCHITECTURAL CONCERNS**

Beyond the line-item decisions, the ADR makes two large-scale architectural bets that introduce risk.

1.  **Premature Complexity in the B-roll Taxonomy:** Defining seven B-roll types (D8) before the first E2E run is a classic case of premature optimization. This decision ripples complexity through the entire system: the schema, the skill playbook, the new Hyperframes components, the Bridge UI, and the FCPXML emitter. The project is accepting a massive implementation cost for a taxonomy that hasn't been validated by a single real-world video. This locks in concepts that may prove awkward or unnecessary. A more agile approach would be to start with the bare minimum: `bigstat`, `asset` (for manual placement), and `transition_title`. Let the need for `quote_card` or `doc_highlight` *emerge* from the first few videos, then design them based on real needs.

2.  **`assets_queue.yaml` as a Brittle, Human-Powered Message Bus:** This file is an architectural smell. It's a structured text file, passed between two different agent sessions (Claude and Codex), acted upon by a human, with its result manually placed back into a filesystem. It's a point of failure waiting to happen. What if the file is malformed? What if the download fails? What if the file is named incorrectly? While a fully-automated API-based download might be out of scope for v1, this design maximizes friction. Codex's suggestion of an `asset_requests.yaml` manifest is better, as it separates intent from fulfillment, but the core fragility of the human-in-the-middle process remains. This reinforces the "creator burnout" risk.

### **Section 6 — FINAL VERDICT**

**Approve with significant modifications.** The core insight to use a "skill" is correct, but the implementation plan is too complex and visually naive for a v1. The focus must shift from *building all the features* to *shipping a coherent first video and learning from it*.

My top required changes, prioritized:

1.  **Radically Simplify the v1 B-roll Taxonomy (D8).** For the first E2E run, reduce the seven types to three:
    *   `transition_title` (Hyperframes)
    *   `bigstat` (Hyperframes, the one existing component)
    *   `generic_asset` (`render_target: asset`, a single bucket for stock, KOL clips, and screen recordings that are manually placed).
    This dramatically reduces the scope of PRs B, C, and F, focusing effort on making the core asset pipeline work before adding variety. Add other types in subsequent releases as clear needs emerge.

2.  **Create a v0.1 Visual Brand Guide before coding new compositions (D3, D4).** Before PR C begins, create a simple markdown file defining the brand rules for video. It must specify: primary fonts for Chinese/English, brand colors, motion timing principles (e.g., "all transitions are 10-frame ease-in-out curves"), and guidelines for treating external assets (e.g., "all KOL clips must be placed within a 5% gray border"). This ensures that even a simplified v1 looks cohesive.

3.  **Explicitly Address the Cross-Lingual Concept Translation Problem (D5, E).** The `SKILL.md` playbook (PR E) must include a dedicated step and a tool/prompt template for translating a script's Chinese *concepts* into a robust list of English search terms for Envato and YouTube. This is a critical creative step and cannot be left to improvisation.

4.  **Adopt Codex's Recommendation for Asset Handoff (D5/D6, Codex #3).** Replace the fragile "separate Codex computer-use session" with a more formal `asset_requests.yaml` / `asset_manifest.yaml` workflow. This provides better separation of concerns and provenance, even if the fulfillment is still manual for v1. It's a necessary step toward future automation.

I concur with Codex's other required changes, particularly regarding schema integrity (Codex #2) and fixing repo hygiene (Codex #5). By simplifying the scope (my #1) and formalizing the visual and cross-lingual strategy (my #2 and #3), this project can avoid the twin dangers of technical debt and a poor quality final product.
