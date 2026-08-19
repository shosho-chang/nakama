---
name: ig-cards
description: Produce, correct, approve, or prepare publishing of an evidence-backed Podcast Episode social carousel, including transcript-grounded copy, three-lens review, 1080x1080 rendering, Review Gate feedback jobs, and Stage 6 publish jobs. Use for Podcast Carousel, IG 輪播, YouTube square posts, 社群圖卡, revision correction, or an approved carousel publishing hand-off; do not use for book or health-information carousel templates.
---

# Podcast IG Carousel

Anchor production and correction at Content Pipeline Stage 5 (製作). Enter Stage 6 (發布) only through the explicit Publish page after the current manifest is approved. Treat the carousel as an independent episode asset, not packaging metadata or a chronological transcript summary.

## Non-negotiable boundaries

- Use the cleaned transcript as the factual source. Optional briefs may guide selection, but template text is placeholder only and must never become output copy.
- Run the workflow with the current Claude Code or Codex agent. Do not call external LLM APIs, provider-backed copy generation, or a hidden/implicit provider.
- Never treat Human Approve as Publish. Approve records approval only and returns the explicit Stage 6 page; publishing requires a separate user-created job.
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

# Complete only after a newer reviewed and visually verified revision exists.
python scripts/podcast_carousel_correction_job.py complete `
  <path-to-correction-job.json> --claim-token <token> `
  --result-revision <rNNN>
```

Claim only jobs whose source revision and manifest hash still match the reviewed package. Respect the active lease; reclaim only an expired claim. Report progress often enough to renew the lease while performing evidence review, copy correction, panel convergence, render, and page-by-page visual QA. Complete only after the result revision is newer than the source revision and all requested changes are present.

## Stage 6 Publish contract

1. Require the current `revision + manifest_sha256` to have a latest approved `CarouselFeedbackRevision`. Check active correction jobs before treating Approve as idempotent; a historical approval never overrides a newer draft or active correction. If approval is absent or the manifest drifted, stop; never carry approval forward to another revision.
2. Open `/bridge/ig-cards/<episode-folder>/publish` from the Approve response's same-origin `publish_url`.
3. Review the square asset set again, enter the already reviewed caption, and select at least one platform. Read the per-platform character and shared-caption warnings before handoff. If Instagram is selected, block captions over 2,200 characters at both client and server boundaries; do not rewrite them in the executor. Treat caption + platform set as release input, not as Stage 5 copy generation.
4. Read the displayed strategy honestly:
   - Instagram / Facebook Page use `meta_api` only when the manifest is `api_compatible` and credentials plus media transport are explicitly configured. A `manual_only` manifest always uses `agent_browser`.
   - YouTube Community always uses `agent_browser_manual` and accepts at most 10 images. Disable and reject it for larger carousels. YouTube Data API has no Community-post insert endpoint; never claim otherwise.
5. Submit once to create an episode-local, revision-bound publish job. Identical revision, manifest, caption, and platform-set submissions return the latest non-failed matching job. If that job failed, resubmit the same input to create a queued retry linked by `retry_of_job_id`. Reject a different request that overlaps any queued / claimed / in-progress platform. After a successful publish, require the explicit republish checkbox before creating a different request for an already-published platform.
6. Treat the Web page as a handoff surface, not an agent dispatcher. It does not call Anthropic API or wake a desktop agent. For a queued job, use the displayed job ID and local Codex or Claude Code claim command in the already-running agent workflow.
7. Claim only when the current Codex or Claude Code executor actually has every capability required by unfinished targets. Claim revalidates approval, `current.json`, request fingerprint, and every receipt in the immutable per-job release bundle. External publishing must use only bundle asset paths, never mutable episode revision paths.
8. Before each external platform action, run `start-target` and retain both its stable `idempotency_key` and new `attempt_id`. Immediately checkpoint with those exact bindings plus receipt/permalink or error. After an expired lease, reconcile an in-progress target with the same binding; do not restart or job-fail an uncertain target. Skip every target already marked `published`, even if a later retry proposes another strategy.
9. Perform platform actions outside the state CLI. Do not store secrets in the job. Call `complete` only after every selected platform has a checkpoint. Any failed target makes the job retryable `failed`, while published checkpoints remain carried through the whole retry lineage. Jobs without an immutable bundle are non-executable; use `retire-legacy` only after the lease expires, then create a safe replacement.

The state tool is `scripts/podcast_carousel_publish_job.py`. It never publishes by itself:

```powershell
# Discover episode-local Stage 6 work.
python scripts/podcast_carousel_publish_job.py list <episode-dir>/ig-carousel

# Claim with real executor capabilities; read claim.claim_token from stdout.
python scripts/podcast_carousel_publish_job.py claim <publish-job.json> `
  --executor <codex|claude_code> --executor-id <current-agent-id> `
  --capability <browser_session|meta_api>

# Append monotonic progress; each accepted update renews the lease.
python scripts/podcast_carousel_publish_job.py progress <publish-job.json> `
  --claim-token <token> --step <name> --percent <0-100> --message <message>

# Fence one platform attempt, then checkpoint it immediately after the side effect.
python scripts/podcast_carousel_publish_job.py start-target <publish-job.json> `
  --claim-token <token> --platform <platform>
python scripts/podcast_carousel_publish_job.py checkpoint <publish-job.json> `
  --claim-token <token> --result-json <single-platform-result.json>

# Save one per-platform receipt/permalink or error after execution.
python scripts/podcast_carousel_publish_job.py complete <publish-job.json> `
  --claim-token <token> --results-json <results.json>
```

Never call `complete` before every selected platform has a result. Use `fail` for a job-level executor failure. Respect the lease and fencing token exactly as with correction jobs.

## Artifact contract

Write to `<episode>/ig-carousel/`, next to `packaging/`:

- `editorial/rNNN/copy_spec.v1.json` and `panel_result.v1.json`
- `revisions/rNNN/pages/NN.png`, render state, Copy Spec, and review manifest
- content-addressed `templates/<sha256>/` snapshot
- `current.json`, `review_feedback.v1.json`, correction jobs, publish jobs, and `run_summary.json`

Keep template authoring in the Design System. A revision consumes an immutable template snapshot.
