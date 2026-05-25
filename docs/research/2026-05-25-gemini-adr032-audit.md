[2026-05-25 23:01:31,433] nakama.gemini_client WARNING — thinking_budget=4096 超過 max_tokens(8192) // 4，自動縮為 2048 避免餓死 output
Here is the audit of ADR-032 from the perspective of a Gemini model.

***

### **Independent Audit of ADR-032: Gemini Perspective**

**To:** Shosho, `nakama` Project Owner
**From:** Gemini Auditor, Multi-Agent Panel (Reviewer 3)
**Date:** 2026-05-26
**Subject:** Second Opinion on ADR-032 Hyperframes-based Script-to-B-roll Pipeline

This document provides a third-party audit of ADR-032, following the initial draft by Claude and the first-pass audit by Codex (GPT-5). As requested, my purpose is not to rubber-stamp prior findings but to provide push-back from a different analytical perspective, leveraging my distinct training in multilingual contexts (specifically Mandarin Chinese), video production workflows, and architectural risk assessment.

I concur with Codex's primary findings regarding code grounding (the implementation is not in the main tree), ADR drift (the "Composer" language for Brook is outdated post-ADR-027), and overly optimistic PR estimates. I will not repeat those points in detail. Instead, this audit focuses on net-new insights and disagreements.

### Section 1 — MANDARIN/MULTILINGUAL CONSIDERATIONS

The pipeline's core function is processing Mandarin Chinese content. My analysis indicates that both the ADR and prior audits have significantly underestimated the complexities of this, which will manifest as bugs in the `srt_flattener` and `beat_aligner` components.

1.  **Anchor Extraction is Fundamentally Harder in Chinese.** Codex correctly identified that `rapidfuzz` is brittle if the LLM paraphrases. The problem is deeper. Chinese lacks explicit word boundaries (spaces), making substring matching highly sensitive to minor variations. The proposed `start_quote` and `end_quote` system will fail on common linguistic patterns:
    *   **Punctuation Ambiguity:** The LLM might include or omit a trailing comma or period. In Chinese, this is often a full-width `，` or `。`. The `beat_aligner` must normalize or strip both full-width and half-width punctuation from both the source text and the LLM's anchors before comparison. This is not specified.
    *   **Common Particles:** The LLM might add or drop grammatical particles like `的`, `了`, `著` without changing the semantic meaning. For example, "他說的話" vs. "他說話". A simple substring match will fail, and `rapidfuzz` might give a score below the 0.85 threshold.
    *   **The "Exact Copy" Mandate is Necessary but Insufficient.** Codex’s recommendation to force the LLM to copy anchors exactly is correct. However, the `srt_flattener` must first produce a "canonical" text representation for the LLM to copy from—one with normalized punctuation and spacing. This canonical representation must then be used by the `beat_aligner` for matching. This pre-processing step is missing from the ADR's scope and PR-2's estimate.

2.  **Number Normalization is a Critical Pre-processing Step.** The ADR and Codex touch on "一萬一千" vs "11,000". The reality for a Taiwanese context is more complex. The `srt_flattener` must handle at least four common formats before the text ever reaches the LLM:
    *   Chinese Characters: `一萬一千`
    *   Arabic Numerals: `11000`
    *   Arabic with Commas: `11,000`
    *   Mixed/Financial: `一·一萬` (common in spoken language and informal text)
    Without a robust normalization layer (e.g., using the `cn2an` library or a custom equivalent) that converts all numeric forms to a single canonical format (e.g., `11000`), the `beat_aligner` will be unreliable, and the LLM will be prone to hallucinating different formats.

3.  **Sentence Boundary Detection Across SRT Cues.** The ADR states the Planner sees "攤平 prose (不知道 SRT 結構)". This is correct, but the process of "flattening" is non-trivial. A single Mandarin sentence often spans multiple SRT cues due to natural speech pauses. The `srt_flattener` cannot simply join lines with a space. It must intelligently join text from consecutive cues, respecting sentence-ending punctuation (`。`, `？`, `！`) to form coherent paragraphs for the LLM. A naive implementation will create sentence fragments, confusing the planner.

4.  **Font Availability in the Hyperframes Runtime.** The design system specifies `LINE Seed TW`. This is a specific, non-standard font. The headless Chrome instance used by Hyperframes' render worker will *not* have this font installed by default. The ADR fails to address this. The likely outcome is a fallback to a generic system font (e.g., Noto Sans CJK TC, Heiti TC), which will have different character widths, weights, and kerning, breaking the visual design. The fix requires either installing the font in the containerized render environment or using `@font-face` within the Hyperframes composition and ensuring it's correctly captured. This is a Phase 1 risk, not a Phase 2 backlog item as the ADR implies.

5.  **Chinese Quotation Marks `「」` vs. `refs.yaml`.** The ADR assumes the LLM will correctly identify quotes and that these can be looked up in `refs.yaml`. The planner's prompt must be explicitly instructed to use Taiwanese standard brackets `「` and `」`. If it defaults to Western `"` or mainland Chinese `“`, the `refs.yaml` lookup logic will fail. This requires specific prompt engineering and validation.

### Section 2 — DIFFERENT PRIOR

My training data and architectural priors lead me to different conclusions about the core technologies chosen.

1.  **Hyperframes is Not a Simple Renderer; It's a Web Development Stack.** Claude and Codex treat Hyperframes as a function call that turns props into a video. My prior sees it as a sandboxed web browser environment. This implies a different class of problems: CSS box model quirks, GSAP animation timeline bugs, font loading races, and browser version compatibility issues. Debugging a failed Hyperframes render is not like debugging a Python script; it requires inspecting a DOM, a CSS layout, and a JavaScript execution context. The ADR's estimate for PR-4 (2 days for dispatcher + worker) dangerously underestimates the effort required to create a robust, debuggable, and deterministic rendering workflow for even a single component like `BigStat`.

2.  **Playwright for Video is a High-Variance Hack.** The ADR celebrates the `web_highlight_record.py` path for its visual fidelity. While clever, using a browser automation tool for frame-perfect video capture is inherently fragile. My prior flags this for high variance in performance and quality. Issues like screen tearing, inconsistent frame rates due to system load, and timing drift between the highlight animation and the capture are probable. Unlike Hyperframes' `renderSeek` which is designed for determinism, Playwright's screencast is best-effort. This path should be labeled "experimental" and its output subject to much stricter visual QC.

3.  **The "Talking Head Sacred" Principle is Misinterpreted.** The ADR states the talking head is "永不 re-render". This is technically inaccurate in a professional workflow. As soon as Shosho applies *any* color correction, LUT, or stabilization in DaVinci Resolve, the clip is marked for re-rendering on final export. The principle's *true* value is **preserving the original source quality for the grade**, not avoiding a re-encode entirely. The FCPXML approach correctly achieves this by referencing the pristine source file. The ADR's justification is slightly misleading and should be corrected to reflect the actual benefit: non-destructive editing and maximum grading latitude.

### Section 3 — CLAUDE/CODEX BLIND SPOTS

Both previous models, likely due to shared training biases, overlooked several practical workflow and dependency issues.

1.  **The "Mistake-Cleanup-as-Prerequisite" Assumption is Brittle.** Both models accepted that the input `raw_recording.mp4` is "已 mistake-cleaned". This assumes a perfect, separate, upstream process. What happens when a mistake is missed? The entire timing of the "clean" SRT will be misaligned with the video file. A single missed "NG" take of 5 seconds will throw off every subsequent B-roll placement. A robust pipeline would not assume perfection. It would, at minimum, require a total duration check between the SRT's final timestamp and the MP4's duration, flagging significant mismatches. The current design is brittle by tying itself completely to an unverified upstream process.

2.  **The Robin Reader EPUB Ingestion Path is Undefined.** Both models glossed over *how* Playwright navigates to the correct book and page. The ADR mentions `book_slug_robin: "atomic-habits"`. This implies Robin Reader has a URL scheme like `http://localhost:XXXX/reader/atomic-habits?page=87`. Is this true? Does Robin Reader even run a local web server? Or does it require a file path? The `reader-playwright` path is entirely dependent on this unspecified interface. This is not a "Phase 1.5" detail; it's a fundamental feasibility question for one of the three core render paths.

3.  **The SRT Line Break Problem for LLM Context.** I mentioned this in Section 1, but it's worth highlighting as a shared blind spot. Both models failed to question how the `srt_flattener` would handle the semantic discontinuity of SRT line breaks. An LLM given text that is naively joined (`"line1 line2 line3"`) will lose the prosodic and semantic grouping intended by the speaker. This will lead to poorly chosen beat boundaries.

4.  **Hyperframes Version Pinning and Breaking Changes.** The ADR hardcodes `Hyperframes v0.6.42`. Both models accepted this as stable. In the fast-moving world of open-source creative tools, a minor version bump (e.g., to 0.7.0) could introduce breaking changes to the `render` CLI command, its props schema, or the underlying Chrome version, leading to silent visual regressions. The project needs a strategy for this: either strict version pinning via package management and container images, or a dedicated suite of visual regression tests. The ADR ignores this dependency risk.

### Section 4 — VIDEO PRODUCTION DOMAIN DEPTH

My analysis of the video-specific claims reveals further risks.

1.  **FCPXML 1.10 is a Risky Choice for DaVinci Resolve.** While technically a standard, DaVinci Resolve's support for FCPXML versions has been notoriously inconsistent. Version 1.9 is often cited as more stable for compatibility. While 1.10 adds features, it has also introduced import errors in past Resolve versions. The choice of 1.10 should be explicitly validated with the *exact* version of DaVinci Resolve Shosho uses. A better strategy would be to make the FCPXML version a configurable output, defaulting to a known-good version like 1.9 or 1.11 if it proves more stable. The ADR presents 1.10 as a given, which is a gamble.

2.  **Render Concurrency Math is Flawed.** The ADR claims `concurrency=2` is acceptable because Hyperframes uses ~50% CPU/GPU. This ignores the other render paths. A Playwright screencast is also GPU-accelerated. The pipeline could dispatch one Hyperframes job and one Playwright job simultaneously. This would create contention for GPU encoding resources (NVENC/VCE) and system memory, leading to slowed or failed renders. A simple process-based concurrency limit of 2 is insufficient. A more robust solution would use a resource-aware semaphore (e.g., one "GPU slot") that both Hyperframes and Playwright workers must acquire before rendering.

3.  **Hyperframes Determinism is Not Guaranteed.** The ADR assumes `renderSeek` is deterministic. It is *mostly* deterministic, but this depends on the stability of the entire stack: the Chrome version, installed fonts (see §1), and the GSAP animation code itself. A minor Chrome update pushed via `apt-get upgrade` in a Docker container could alter rendering behavior. True determinism requires snapshot testing, where a hash of the output video is compared against a known-good version. The current plan has no such validation, risking silent visual drift.

### Section 5 — ARCHITECTURAL CONCERNS

Zooming out, the proposed architecture introduces significant complexity and maintenance burdens for a one-person workflow.

1.  **The Dual-Path Architecture Creates a Debugging Schism.** The ADR justifies the 3-path dispatcher on the grounds of "visual contract". Architecturally, this creates two fundamentally different rendering and debugging stacks: a deterministic, component-based stack (Hyperframes) and a brittle, automation-based stack (Playwright). When a B-roll clip is black, buggy, or misaligned, the debugging process is completely different depending on the `render_target`. This doubles the cognitive load for maintenance and troubleshooting. A simpler architecture might have forced all content into the Hyperframes model, even if it meant sacrificing some visual fidelity on quotes for the sake of a unified, more robust pipeline.

2.  **The 2-Layer Approval is Overly Ceremonious for a Solo User.** The text-approve/visual-approve workflow is common in team settings. For a single user (Shosho), this introduces 2N decision points for N beats. The "auto-render-on-approve" feature sounds efficient but could be frustrating if Shosho wants to approve all text drafts first and then trigger a single batch render overnight. The UI should support both modes: per-beat auto-render and a "Render All Approved" batch action. The current design is too rigid.

3.  **The Three-Store Learning Corpus Will Atrophy.** The system proposes `examples/`, `edit_log/`, and `STYLE.md` as three separate sources for learning. In practice, a solo operator is unlikely to diligently maintain all three. The `edit_log` will capture raw feedback, but the manual step of curating that feedback into `examples/` and `STYLE.md` will likely be skipped. The architecture should favor a single, auto-managed source of truth. For instance, the system could automatically flag high-quality `edit_log` entries as potential examples, presenting them in the UI for one-click promotion to the `examples/` corpus.

4.  **This Pipeline's Complexity Exceeds Brook's Mandate.** Codex correctly noted the ADR-027 scope reduction. I will go further: this pipeline, with its multiple render workers, real-time UI updates, and complex dependency management, is not a "repurposing" task. It is a full-fledged production system. Forcing it into the `agents/brook/` namespace risks bloating Brook into a monolith. This system deserves its own agent, perhaps named `foundry` or `render-agent`, with a clearly defined API that Brook can call. This would maintain architectural separation of concerns.

### Section 6 — FINAL VERDICT

**Approve with significant modifications.**

The core direction of ADR-032—moving to an SRT-first, storyboard-based, Hyperframes-powered pipeline—is a major improvement over ADR-015. However, the current proposal is naive about the complexities of its chosen language (Mandarin), its core technologies (Hyperframes, Playwright), and its proposed architecture. It confuses spike success with production readiness.

My top 5 required modifications, which build upon and extend Codex's findings, are:

1.  **(PR-2 Scope) Implement a Mandarin-Aware Text Normalization Layer.** Before any LLM planning or beat alignment, create a `normalize_chinese_text` function in the `srt_flattener`. It must handle: (a) full-width/half-width punctuation, (b) multiple number formats (一萬一千, 11000, etc.), and (c) intelligent joining of SRT cues to respect sentence boundaries. This is a prerequisite for reliable anchor matching.

2.  **(PR-4 Scope) De-risk the Hyperframes and FCPXML Paths.** Add two blocking acceptance criteria to PR-4: (a) Create a visual snapshot test for the `BigStat` component to ensure render determinism. (b) Verify FCPXML 1.10 import into Shosho's specific DaVinci Resolve version with a test project containing transforms; be prepared to fall back to 1.9 or 1.11. This addresses the core output risks.

3.  **(ADR Change) Re-scope Phase 1 to a Single Render Path.** I agree with Codex's recommendation to disable the Playwright paths. I further recommend the ADR be amended to state that the Playwright approach is an *experimental alternative* to be validated, not a first-class citizen. This simplifies the Phase 1 architecture and reduces maintenance surface area. The `web_highlight_record.py` script must be promoted from its spike location and have its dependencies (e.g., the Robin Reader interface) fully specified before it can be considered for inclusion.

4.  **(PR-5 Scope) Simplify the UI and Raise the Estimate.** I concur with Codex that the 3-day estimate for the Tier 3 UI is wrong (5-7 days is more realistic). I further recommend simplifying the UX for a solo operator: in addition to the per-beat actions, add global "Approve All Text" and "Render All Approved" buttons. This better supports a batch-oriented workflow and reduces click fatigue.

5.  **(ADR Change) Architecturally Isolate the Pipeline.** Amend the ADR to propose this pipeline as a new, dedicated agent (e.g., `agents/foundry/`) instead of embedding it within Brook. This respects the ADR-027 decision in spirit, not just in letter, and promotes a healthier, more modular system architecture for the long term. Brook's role would be to invoke this new agent, not to contain it.
