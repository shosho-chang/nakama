---
name: ig-cards
description: Produce or revise an evidence-backed Podcast Episode IG Carousel from a cleaned transcript, including independent editorial panel review, 1080×1350 Design System rendering, and the Bridge per-card human review gate. Use when the user asks for Podcast Carousel、IG 輪播、社群圖卡、從 Podcast 逐字稿做 IG 貼文，or wants to inspect/revise an episode's existing ig-carousel package. Do not use for book or health-information carousel templates, which are separate future forks.
---

# Podcast IG Carousel

Anchor this skill at Content Pipeline Stage 5 (製作). Treat the carousel as an independent
episode asset, not as packaging metadata or a transcript summary.

## Run the workflow

1. Resolve one episode directory. Require `transcript_prose.md`, `transcript.srt`, and
   `packaging/cutouts/*.png`. Allow `social_brief.md` but do not require it.
2. Read content only from the cleaned transcript. Never copy example text from the Design
   System template; every visible claim must retain transcript evidence IDs and timestamps.
3. Run `scripts/run_podcast_carousel.py` with episode metadata and the Design System template.
   Load secrets from `NAKAMA_ENV_FILE` or the repo `.env`; never print them.
4. Let the script run three independent lenses: IG Audience, Episode Editorial, and
   Brand/Evidence. Accept only findings that pass deterministic page/evidence verification.
   Revise and re-review; do not render when verified blockers remain or the panel cannot
   converge within the configured rounds.
5. Verify all PNGs are exactly 1080×1350 and inspect representative pages visually.
6. Open `/bridge/ig-cards/<episode-folder>` in the authenticated Thousand Sunny app. The human
   gate closes only when every page in the same revision is approved.

## Fixed content contract

- Sequence: `cover → hook → ordered point/re_hook sequence → quote → CTA`.
- Use as many points as the episode needs, up to 20 total pages. A re-hook is a separate page
  and must introduce the next point group.
- Use quote A on odd episodes and quote B on even episodes. B contains a host question and a
  guest answer; fall back to A only with a recorded evidence reason.
- Lightly rewrite long questions and quotes only when meaning remains unchanged. Never stitch
  non-contiguous guest answers.
- Use one emphasis substring per page. Character counts are guidance, not hard limits. Let the
  renderer shrink text per region; flag text below the readable floor as `needs_review`.
- Keep CTA platforms fixed to Apple Podcasts, Spotify, and YouTube.

## Artifact contract

Write to `<episode>/ig-carousel/`, next to `packaging/`:

- `editorial/rNNN/copy_spec.v1.json` and `panel_result.v1.json`
- `revisions/rNNN/pages/NN.png`, render state, Copy Spec, and review manifest
- content-addressed `templates/<sha256>/` snapshot
- `current.json`, `review_feedback.v1.json`, and `run_summary.json`

Do not publish to Instagram. Book and health-information post designs are out of scope.
