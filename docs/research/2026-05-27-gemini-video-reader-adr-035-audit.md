Excellent. I will audit ADR-035 with the requested lenses. My perspective is that of an external reviewer who has not seen the prior discussions, focusing on multilingual realities, multimodality, and factual grounding.

Here is my structured 6-section report.

---

### 1. Code/Spec grounding

The ADR is generally well-grounded, but some areas require more specific definition before implementation can begin.

**Concrete and Actionable:**

*   **D5 (Schema Bump):** The `EvidenceAnchor` schema change is explicit and unambiguous. The `locator` format (`t=123.4-145.7`) is a robust, well-reasoned choice that can be implemented directly.
*   **D7 (Promotion Phases & Schema):** The three-phase promotion plan is clear, and the example `SourcePage` markdown schema is specific enough to write a template for. This provides a clear implementation path.
*   **D6 (UI Layout):** The top-bottom layout diagram and list of shared vs. new components provide a strong starting point for a frontend developer.

**Hand-wavy and Requiring Clarification:**

*   **D2/D4 (Fallback Flow):** The fallback from a failed `yt-dlp` fetch to a local Whisper job is conceptually clear but procedurally vague. The ADR states the UI will show an "entry point" to upload a local file. This glosses over significant complexity: how does the user acquire the video file if `yt-dlp` itself failed? How is the uploaded file associated with the original `source_id`? What state does the `watchlist` entry sit in while this is happening? This seam is more complex than presented.
*   **D6 (Cue Interaction Logic):** The spec for "cue-boundary snap (click cue / shift-click range)" is an idea, not a spec. An implementer will need to decide how to map a click event on a rendered text block back to the underlying WebVTT cue data structure, which is non-trivial. The `av-reader.js` controller's responsibilities are high-level and need to be broken down.
*   **D3 (Cast List UI Flow):** The manual cast list is central to the design. The ADR states a "cast form" appears after URL paste (D4) and a "chip selector" appears in the annotation modal (D3). The exact UI/UX of these components, especially how a user adds, removes, or corrects a speaker in the cast list post-ingestion, is undefined.

### 2. Drift / inconsistency

I identified one major and one minor point where decisions create tension or are underspecified.

1.  **Phase 1 vs. Phase 2 Dependencies (Major):** There is a significant inconsistency between the phased ASR plan in **D2** and the risk mitigation strategy in the "Consequences" section. D2 strictly defines Phase 1 as using only YouTube auto-captions and Phase 2 as introducing on-demand Local Whisper. However, the "Negative / 風險" section uses the on-demand Whisper path ("UI re-transcribe with Whisper button") as the primary escape hatch for poor Chinese auto-caption quality. This implies that the Phase 2 infrastructure is a *de facto requirement* to make Phase 1 viable for the user's mixed-language reality. The strict phasing is therefore misleading; the system must be designed from PR1 to accommodate the on-demand ASR flow, even if the button is initially hidden.
2.  **Shared Infrastructure Definition (Minor):** The ADR claims it will "share... promotion pipeline wiring, entity infrastructure" with the Bridge chassis (**D1**). While this is the goal, the actual integration points are not defined. ADR-034 defined the *what* (Entity Promotion), but this ADR doesn't specify *how* a `SourcePageReviewItem` originating from a video will plumb into that system. It creates a new `SourcePage` schema in **D7**, but the upstream process of how this new source kind is handled by the existing promotion review UI is left to PR3, which may hide unforeseen integration challenges.

### 3. Numerical / factual claims

Here is a verification of the factual and numerical claims made or implied in the ADR.

*   **Claim:** Whisper is 0.1× realtime on an RTX 4070. (Mentioned in "Trigger justification")
    *   **Verification:** **Plausible, but model-dependent and requires optimization.** 0.1x realtime means processing a 60-minute file in 6 minutes. This is achievable with optimized implementations like `faster-whisper` or `whisper.cpp` using GPU acceleration (cuBLAS) on a `base` or `medium` model. It is not achievable with the base PyTorch implementation of the `large` model. The claim is reasonable as a performance target but lacks the necessary context.
*   **Claim:** A 1.5× playback speed is the default for the user. (D6)
    *   **Verification:** **Verifiable as a User Preference.** This is a statement of the user's personal workflow, not a technical claim about the platform. It is a valid input for a default setting.
*   **Claim:** YouTube auto-caption is "free and predictable." (Implied by its choice for Phase 1 in D2)
    *   **Verification:** **Partially Correct.** "Free" is accurate. "Predictable" is not. The availability, timing, and quality of auto-captions are highly variable. They are often not available immediately after upload, and for non-English languages like Mandarin Chinese, the lack of punctuation and speaker labels, and higher word error rate, make the raw output unpredictable for a reading-like experience. The ADR acknowledges the quality variance but understates the predictability problem.
*   **Claim:** `yt-dlp` for captions is in a "grey-area" regarding YouTube's ToS. (Consequences section)
    *   **Verification:** **Accurate.** YouTube's ToS (Section: "Permissions and Restrictions") forbids downloading any part of the content without prior written permission from YouTube or the respective rights holders. While captions are text, they are part of the "Content." `yt-dlp` operates by reverse-engineering the client-side API, which is a classic ToS violation. This risk is correctly identified.
*   **Claim:** YouTube embed `iframe` with the IFrame Player API is the ToS-compliant method. ("Open questions")
    *   **Verification:** **Accurate.** The IFrame Player API is the officially sanctioned way to embed and control a YouTube player on a third-party site. In contrast, directly accessing and playing the stream URL (e.g., via `<video>` source) is explicitly against the ToS. The ADR correctly identifies the compliant path.

### 4. Assumption push-back

Here are 5 load-bearing assumptions in the ADR that I would challenge.

1.  **Assumption:** Poor quality Chinese auto-captions are an acceptable starting point for a "reading" experience.
    *   **Push-back:** This assumption is questionable. The user's request is to "read" the video like an article. YouTube's Chinese auto-captions often lack punctuation, sentence breaks, and have a higher error rate. This creates a "wall of text" that is fundamentally difficult to read and comprehend, undermining the core user goal. Starting with a frustrating experience for 50% of the user's content diet is a risky strategy.
    *   **Load-bearing:** High. This directly impacts the usability of the core feature for a primary use case (Chinese language content). If the experience is poor, the user may abandon the feature before Phase 2 improvements arrive.

2.  **Assumption:** Manual speaker labeling is strictly dominant over automated diarization for this N=1 user.
    *   **Push-back:** The ADR's rejection of `pyannote` is very strong. While manual tagging is indeed the ground truth, it introduces non-trivial cognitive friction *at the moment of annotation*. For a long podcast with two speakers, the user must constantly remember who is speaking to correctly tap the chip. An automated system, even if imperfect (e.g., correctly separating "speaker_1" and "speaker_2" but not naming them), could pre-fill this information, reducing the user's task to a one-time assignment of "speaker_1 = Host" and "speaker_2 = Guest." The assumption that adding friction to *every single annotation* is better than a one-time correction workflow is worth challenging.
    *   **Load-bearing:** Medium. The proposed manual system works, but it might be more fatiguing than anticipated, discouraging annotation.

3.  **Assumption:** The visual track of the video contains no valuable information for this knowledge-work task.
    *   **Push-back:** The ADR is entirely text-anchored. It treats a video as just a timed audio track. For the user's domain (Health/Wellness), content often includes visuals like slides with diagrams, on-screen text with key takeaways, or demonstrations of exercises. Ignoring the visual track leaves a significant amount of potential knowledge on the table. A user might want to screenshot a slide and attach it to an annotation, a workflow the current design does not consider.
    *   **Load-bearing:** Medium to High. It represents a major blind spot that could limit the feature's depth and utility, forcing the user into a secondary workflow (manual screenshots) outside the system.

4.  **Assumption:** A vertical top-bottom layout is the only viable path for future PWA compatibility.
    *   **Push-back:** This is a false dichotomy. Responsive design exists to solve this exact problem. A well-designed UI can use a horizontal two-pane layout on desktop/landscape and gracefully reflow to a vertical stacked layout on mobile/portrait. Rejecting the superior desktop experience to preemptively solve the mobile problem with a single, compromised layout is a premature optimization.
    *   **Load-bearing:** Medium. It locks the project into a potentially suboptimal desktop UX, which is likely the primary environment for this kind of focused knowledge work.

5.  **Assumption:** Cue-level selection is a sufficient proxy for sentence-level or idea-level selection.
    *   **Push-back:** YouTube's auto-generated cues are timed for readability on-screen, not for semantic completeness. A single thought or sentence is often fragmented across multiple cues. While shift-clicking to select a range is a good workaround, the core interaction is still bound to arbitrary time chunks rather than the user's mental model of "I want to save that sentence." This could feel clunky.
    *   **Load-bearing:** Low to Medium. The proposed UI is workable, but it may feel less intuitive than a more text-native selection model, adding minor but persistent friction.

### 5. Alternatives not considered (or rejected too fast)

The ADR's "Considered Options" section is thorough, but some avenues were missed or dismissed with weak reasoning.

*   **Alternative Missed: Hybrid ASR Strategy for Phase 1.** Instead of a binary choice (YT captions OR local Whisper), a hybrid approach could provide a better v1 experience. **Proposal:** For English videos, use the high-quality YouTube captions. For Chinese videos, automatically trigger a local Whisper job upon ingestion. This respects the reality of the quality difference and delivers a usable product for both languages from day one, without waiting for the user to manually trigger a re-transcribe.
*   **Alternative Missed: Leveraging the Visual Track (Multimodality).** The system could incorporate a simple "Capture Frame" button next to the annotation modal. This would grab the current frame from the `<video>` element, save it as an image, and associate it with the annotation. This directly addresses the multimodality blind spot without requiring complex computer vision, adding significant value for content with slides or diagrams.
*   **Alternative Rejected Too Fast: Automated Diarization as a *Suggestion*.** The rejection of `pyannote` is framed as an all-or-nothing choice between perfect manual labels and flawed automated ones. The reasoning misses the middle ground: use diarization to *pre-populate* the speaker chips as `Speaker 1`, `Speaker 2`, etc. The user's task then becomes a simple, one-time mapping (`Speaker 1 -> "Host Name"`) rather than a constant, per-annotation selection task. This turns the automation from a flawed source of truth into a powerful suggestion engine.
*   **Alternative Rejected Too Fast: Responsive Layout.** As mentioned in the assumption push-back, the rejection of a horizontal layout based on PWA compatibility is weak. A modern CSS approach using media queries could easily support both a side-by-side view on desktop and a stacked view on mobile from a single codebase, offering the best of both worlds.

### 6. Verdict

**Not ready to ship.**

The ADR is strong in its vision, component breakdown, and phased approach. However, it is undermined by a critical assumption about the usability of its core data source (Chinese auto-captions) and a significant blind spot regarding multimodality. The proposed solution risks delivering a frustrating Phase 1 experience for half of the user's content.

To be unblocked for an **Accepted** status, the following three things are required:

1.  **Revise the ASR Strategy (D2):** The ADR must be updated to address the poor quality of Chinese auto-captions in Phase 1. The recommended solution is a **hybrid ASR model**: ingest with YouTube captions for English content, but automatically trigger a background Whisper job for detected Chinese content. This makes the feature viable for the user's full workflow from day one.
2.  **Incorporate a Multimodal Path:** Acknowledge that video is more than audio. The ADR must be amended to include a plan for capturing visual information. A simple "Capture Frame" button associated with annotations is a concrete, high-value, low-complexity first step. This should be added to the UI spec in **D6** and the implementation plan.
3.  **Re-evaluate Diarization as a Suggestion Engine:** The "Rejected: Auto diarization" section should be revised. The decision should be reframed to reject it as a *definitive source*, but to consider it on the roadmap as a *suggestion layer* to reduce annotation friction. This changes the "strictly dominates" claim to a more nuanced and realistic position.
