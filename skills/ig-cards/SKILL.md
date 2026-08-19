---
name: ig-cards
description: Produce or correct an evidence-backed Podcast Episode social carousel, including transcript-grounded copy, three-lens review, 1080x1080 rendering, visual QA, and structured Review Gate edit jobs. Use for Podcast Carousel, IG 輪播, YouTube square posts, 社群圖卡, or revising an episode carousel package; do not use for book or health-information carousel templates.
---

# Podcast IG Carousel

Anchor this skill at Content Pipeline Stage 5 (製作). Treat the carousel as an independent episode asset, not packaging metadata or a chronological transcript summary.

## Non-negotiable boundaries

- Use the cleaned transcript as the factual source. Optional briefs may guide selection, but template text is placeholder only and must never become output copy.
- Run the workflow with the current Claude Code or Codex agent. Do not call external LLM APIs, provider-backed copy generation, or a hidden/implicit provider.
- Do not publish. Human Approve records approval only; it does not modify artifacts or trigger Stage 6.
- Keep books, health-information carousels, and other future social templates outside this skill.

## Produce a revision

1. Resolve one episode directory. Require `transcript_prose.md`, `transcript.srt`, and `packaging/cutouts/*.png`; accept `social_brief.md` as optional editorial direction.
2. Read the complete transcript and create one evidence-materialised `PodcastCarouselCopySpecV1`. Preserve evidence IDs, timestamps, speakers, and contiguous source spans for every visible claim.
3. Build one Episode Highlight Arc with this exact sequence:

   `cover → one hook → ordered points → quote → CTA`

   Do not add a Re-hook. The single Hook must establish a payoff that every ordered point answers. Select the number of points from the episode rather than a fixed count, up to 20 total pages. Before review, run a page-by-page semantic entailment check: preserve the original subject, stance, and causal direction; do not invent anxiety, motivation, or causality.
4. Blind-dispatch three independent subagents with the Copy Spec and transcript but no other review output:

   - IG Audience
   - Episode Editorial
   - Brand and Evidence

   The current end-to-end agent verifies findings against the Copy Spec and transcript, revises the one main version, and repeats the three-lens panel until it converges. Never use majority vote to erase a supported finding.
5. Run the deterministic finaliser; it renders reviewed artifacts but does not draft copy or invoke reviewers:

   ```powershell
   python scripts/run_podcast_carousel.py <episode-dir> `
     --copy-spec <copy_spec.v1.json> `
     --panel-result <panel_result.v1.json> `
     --template-dir <design-system-template-dir>
   ```

6. Verify every PNG is exactly 1080×1080. Square is the canonical cross-platform master for Instagram and YouTube posts. Inspect every page at full size; do not approve from a montage alone. When composing, rendering, correcting, or visually reviewing pages, read [`references/pilot-feedback.md`](references/pilot-feedback.md) and apply its page checklist.
7. Open `/bridge/ig-cards/<episode-folder>` in the authenticated Thousand Sunny app. Treat the Review Gate as the only human approval surface.

## Copy contract

- Use social editorial voice for cover, Hook, points, and CTA. Do not turn editorial synthesis into unattributed guest speech.
- Use one emphasis substring per page. Point emphasis must be an exact substring of the headline; keep point body plain.
- Use quote A on odd episodes and quote B on even episodes. Variant B pairs a host question with the directly connected guest answer. Fall back to A only with a recorded evidence reason.
- Lightly shorten questions and quotes only when meaning is unchanged. Never stitch non-contiguous guest answers into one quote.
- Make every point a comprehensible answer to the Hook. Distinguish similar-looking claims with different subjects—for example, `內容交給流量` and `收入交給流量` are not interchangeable.
- Keep CTA platform icons fixed to Apple Podcasts, Spotify, and YouTube. Do not add an engagement or comment line.
- Use the frozen typography hierarchy for cover, Hook, point headline, and body. Let the renderer fit each text region independently, but never collapse body copy far below its role or make the cover title smaller than a content-page title. Never clip, ellipsize, or rewrite copy during render; mark below-floor text `needs_review`.

## Review Gate semantics

- Each page has one feedback field. Non-empty text means that page requires a modification; blank means no modification for that page.
- Submitting any non-empty feedback creates one revision-bound correction job containing only the non-empty page items. It does not approve the carousel.
- Approve is valid only when every page feedback field is blank. Approve creates no correction job, modifies nothing, and publishes nothing.
- The correction job is agent-neutral. The current end-to-end agent (`codex` or `claude_code`) claims it; the three editorial lenses remain independent subagents inside that run.
- A claim owns a time-bounded lease. Another Codex or Claude Code executor must not take a job while its lease is valid; it may reclaim only after expiry. Every accepted progress update renews the current lease.
- If no compatible executor is online, leave the job `queued`. Never invent a claim, progress, or completion state.
- Creating or polling a job does not dispatch or wake Codex/Claude Code. Show the job ID and explicitly hand it back to the Codex/Claude Code task currently executing that episode; that executor must run the claim CLI itself.
- The card editor may patch only the existing display-copy allowlist for that role. It must never change `page_id`, `role`, transcript evidence, cutout identity, page order, or page count.
- Cover layout editing is limited to deterministic 1080px coordinates: guest right, bottom, height, and cover-title font size. Treat the iframe as a live view of the real render DOM, never as a draggable flattened PNG.
- A trusted editor preview requires the manifest's `render_input` receipt. Legacy manifests remain reviewable, but their editor stays disabled until the renderer creates a new revision; never edit an immutable legacy manifest in place.
- Run the preview in an opaque-origin `sandbox="allow-scripts"` iframe. Parent/editor state crosses only the scoped `postMessage` bridge; do not add `allow-same-origin` or direct DOM access. The bridge must not access storage or APIs.
- Validate emphasis as an exact substring of the role's primary display field before Apply. After every copy or layout change, rerun the renderer-exposed canonical refit function and surface fit/collision diagnostics; a visual text swap without refit is not an accurate preview.
- Applying editor changes creates a revision-bound job with `copy_edits` and/or `layout_overrides`. It does not mutate the current Copy Spec or PNG. Carry the structured values into the next Copy Spec and deterministic render.

## Correction-job CLI contract

The state tool is `scripts/podcast_carousel_correction_job.py`. If it is absent in a checkout, keep jobs queued and report the missing integration; do not edit job JSON by hand. Its public contract is:

```powershell
# Atomically claim one queued job; read claim.claim_token from stdout JSON.
python scripts/podcast_carousel_correction_job.py claim `
  <path-to-correction-job.json> `
  --executor <codex|claude_code> --executor-id <current-agent-id>

# Append monotonic, one-based progress owned by that claim.
python scripts/podcast_carousel_correction_job.py progress `
  <path-to-correction-job.json> --claim-token <token> `
  --step <step-name> --percent <0-100> --message <short-message>

# Complete only with a current result manifest, three independent review
# receipts, and the matching converged panel artifact.
python scripts/podcast_carousel_correction_job.py complete `
  <path-to-correction-job.json> --claim-token <token> `
  --result-manifest <review_manifest.v1.json> `
  --panel-result <panel_result.v1.json> `
  --reviewer-receipt ig_audience=<subagent-id>=<review.json> `
  --reviewer-receipt episode_editorial=<subagent-id>=<review.json> `
  --reviewer-receipt brand_evidence=<subagent-id>=<review.json>
```

Claim only jobs whose source revision is still current and whose manifest, Copy Spec, requested page artifacts, and every PNG still match their receipts. Respect the active lease; reclaim only an expired claim. Report progress often enough to renew the lease while performing evidence review, copy correction, panel convergence, render, and page-by-page visual QA. Progress step names are informational and never count as review evidence. Persist each independent subagent's `PanelReview` JSON with a distinct reviewer identity, then persist one `PanelResult` containing those exact three reviews. Completion derives the newer revision from the current result manifest and verifies its Copy Spec and every PNG receipt, the three reviewer receipts, and `assert_panel_renderable` against the matching converged panel. Structured copy/layout jobs additionally require an exact source-to-result diff: every requested value must be present and evidence, cutouts, identity, page order, and all unrequested fields must remain unchanged. Feedback-only jobs rely on the converged panel because free-form intent cannot be mechanically diffed.

## Artifact contract

Write to `<episode>/ig-carousel/`, next to `packaging/`:

- `editorial/rNNN/copy_spec.v1.json`, three independent review JSON files, and `panel_result.v1.json`
- `revisions/rNNN/pages/NN.png`, render state, Copy Spec, and review manifest
- content-addressed `templates/<sha256>/` snapshot
- `current.json`, `review_feedback.v1.json`, correction jobs, and `run_summary.json`

Keep template authoring in the Design System. A revision consumes an immutable template snapshot.
