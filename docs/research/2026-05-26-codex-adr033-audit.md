# Codex Audit — ADR-033 Thumbnail Generation Pipeline

**Date:** 2026-05-26
**Auditor:** Codex CLI v0.128.0 (gpt-5.5)
**Artifact under review:** [`docs/decisions/ADR-033-thumbnail-generation-pipeline.md`](../decisions/ADR-033-thumbnail-generation-pipeline.md)
**Panel context:** [`C:\Users\Shosho\.claude\skills\multi-agent-panel\SKILL.md`](file:///C:/Users/Shosho/.claude/skills/multi-agent-panel/SKILL.md), Step 2 of 5
**Full transcript (with codex reasoning trace):** `.tmp/panel/codex_full_transcript.md` (8470 lines, not committed; gitignored)

This file contains Codex's refined second-pass audit only. The first pass + all CLI reasoning trace lives in the gitignored transcript.

---

**Section 1 — CODE GROUNDING**

`E:\nakama-adr-033-thumbnail\agents\foundry\render_workers\hyperframes_worker.py` exists and mostly matches ADR-033 D10's stated precedent. It has `HyperframesRenderError` at lines 21-27, `_build_command` at line 30, `asyncio.create_subprocess_shell` at lines 79 and 86, `communicate()` at line 93, and a 500-char stderr tail at lines 95-96. The proposed `thumbnail_worker.py` can mirror the broad pattern, but do not blindly copy the command-building style: `_build_command` returns one shell string and injects JSON with single quotes. On Windows `cmd.exe`, single quotes are not shell quoting. For PR4, use `create_subprocess_exec(argv...)` or a quoting helper tested on Windows. Also note the docstring says `npx --prefix video/`, but the code actually uses `cwd=video_dir` and command `npx hyperframes`.

`E:\nakama-adr-033-thumbnail\thousand_sunny\routers\bridge_projects.py` exists, but it is already 1,952 lines. It handles project CRUD, frontmatter mutation, body sections, tasks, Pomodoro, review endpoints, and five research endpoints. ADR-033's "+250 LOC" extension is physically possible but architecturally bad. Put thumbnail endpoints in a sibling router such as `thousand_sunny/routers/bridge_project_thumbnails.py`, included under `/bridge/projects/{slug}/thumbnail/*`, and keep shared logic in `shared/thumbnail_*`. That is simpler to maintain than pushing this router past 2,200 LOC.

`E:\nakama-adr-033-thumbnail\thousand_sunny\templates\bridge\projects\_tab_title_thumbnail.html` is only 96 lines and still reflects ADR-031 PR3: Zoro keyword research, `title_candidates`, and old `thumbnail_concept`. It has no 3-card idea UI, no render button, no candidate preview, no funnel grid, no commit control. ADR-033 D6 is a real replacement, not a small fill-in.

`E:\nakama-adr-033-thumbnail\shared\project_indexer.py` exposes `content_type` and `raw_frontmatter`; it is usable as the FS-direct pattern for project metadata. But it only has first-class dataclass fields for `title_candidates` and `thumbnail_concept`, not `thumbnail_ideas`, `thumbnail`, `thumbnail_run`, `host_video_path`, `guest_video_path`, or `thumbnail_active_cutouts`. PR4 must extend the indexer or consciously use `raw_frontmatter`. The current "dual-shape tolerance" is only implemented for `reviews` list-vs-dict, not for `thumbnail_concept` to `thumbnail_ideas`.

`E:\nakama-adr-033-thumbnail\docs\schemas\project-frontmatter-nested.md` has accepted ADR-033 fields, but the doc has drift: it says all four `content_type`s are retained at lines 5, 18, 88, but line 93 still validates only `youtube | podcast`, and line 106 says research was dropped. That contradiction predates ADR-033 but matters because D9's podcast-only fields depend on content type.

Hyperframes is present: `E:\nakama-adr-033-thumbnail\video\package.json` pins `"hyperframes": "0.6.42"`, and `video/compositions/bigstat/compositions/bigstat.html` exists with GSAP timelines and `window.__timelines["bigstat"]`. There is no `video/compositions/thumbnail/` yet. `video/node_modules` is absent in this worktree, and `hyperframes-media` is not declared in checked-in package manifests. The u2net claim is therefore not verifiable from this repo snapshot.

One more code-grounding gap: `shared/llm.py` has `ask`, `ask_multi`, tools, and audio; it has no explicit vision/image helper. `shared/llm_router.py` defaults `default` to `claude-sonnet-4-20250514`; only `translate` defaults to `claude-sonnet-4-6`. ADR-033 must add explicit `thumbnail_brainstorm` and `thumbnail_funnel` routing or pass `model="claude-sonnet-4-6"` directly.

**Section 2 — DRIFT DETECTION**

ADR-033 D7 does not violate ADR-030 D1. The split is lifecycle-correct: churny candidates in gitignored `data/thumbnails/{slug}/runs/{ts}/`, chosen artifact in vault at `Attachments/projects/{slug}/thumbnail.png`. That matches ADR-030 D4's split-substrate logic. The caveat is write-path discipline: ADR-030 warned Bridge vault writes are hard; ADR-031 already introduced Web mutations, so ADR-033 must inherit `shared/project_writer`-style atomic updates, conflict behavior, and failure tests.

ADR-031 PR3 backlog is superseded in spirit, but not cleanly in docs/UI. ADR-033 deprecates `thumbnail_concept`, yet the current tab still labels it "後續圖像生成在 PR3+," and the schema doc still says Title&Thumbnail lives in `title_candidates`, `thumbnail_concept` in multiple places. PR4 must remove old copy from the UI, add lazy fallback from `thumbnail_concept` to `thumbnail_ideas[0]`, and update schema section 3/4 so the deprecated field is not presented as the active model.

ADR-032's `data/script_video/<episode-id>/` precedent is only partially followed. ADR-033 invents `data/thumbnails/{slug}/runs/{ts}/`, plus `data/podcasts/{ep_slug}/host_angle.mp4`, plus vault cutouts. That is acceptable for YouTube thumbnails, but podcast assets become scattered. Do one of two things: declare thumbnails as project-scoped and keep everything under `data/thumbnails/{slug}/...`, including podcast source pointers and funnel runs; or for podcast episodes, nest thumbnail work under `data/script_video/{episode-id}/thumbnail/`. Do not leave podcast spread across three roots without an explicit reason.

VAULT-LAYOUT Pattern A markers are not needed for D7 binary writes. The chosen thumbnail and cutouts are binary attachments plus frontmatter pointers, not agent-written markdown sections. Marker convention only becomes relevant if PR4 writes manifest summaries or render notes into `Projects/{slug}.md` body. ADR-033 currently does not, so no marker is required.

**Section 3 — NUMERICAL / FACTUAL CLAIMS**

The "~3.5s/still × 3 = ~10s" claim is not grounded in the repo. ADR-032 documents BigStat at ~10s per beat and warns concurrency=1 because Hyperframes spawns Chrome/FFmpeg. There is no local benchmark proving static stills render in 3.5s, and this worktree lacks `video/node_modules`, so I could not run it. First-render `npx` cold start plus Chrome plus 1-second MP4 plus FFmpeg extract can easily exceed 3.5s on Windows. Treat 10s/batch as a target, not a fact.

The "20-40K input tokens per brainstorm" claim is plausible only if reference images are resized/capped before upload. If PR4 attaches 20-40 full PNG thumbnails directly, payload size and vision token accounting can blow past the estimate. Add a reference-loader rule: max image count, max pixel dimension, JPEG/WebP recompress, and logged token usage.

The cost math is broadly okay. Anthropic's public Sonnet 4.6 page lists pricing at $3/M input tokens and $15/M output tokens. At 20-40K input tokens, a brainstorm costs $0.06-$0.12 input plus maybe $0.02-$0.05 output, so "<$0.50 YouTube workflow" is credible if renders/Unsplash are free. Podcast "<$2.00" is also plausible, but only if funnel frames are downscaled and there are two vision calls, not unbounded retries.

"Laplacian threshold σ = 100" is a shaky phrasing and a shaky default. OpenCV blur detection usually uses Laplacian variance, not sigma, and absolute thresholds depend on resolution, compression, contrast, lens, and lighting. Across host and guest angles, one fixed value will fail. Use per-video ranking: compute variance for all sampled frames, keep top 20 or top percentile, and log the distribution. Make the hard threshold a fallback, not the primary gate.

"50 random frames → 20 sharp → 5 LLM picks" is a sensible cheap funnel, but pure random sampling is not reproducible and may miss expressions in a 60-90 minute podcast. Use deterministic stratified sampling across duration with a logged seed. This keeps simplicity while making audit and rerun behavior sane.

PR4 "6-7 day-equivalent" is optimistic. The ADR table has 18 rows, not 17, and sums to roughly 2,440 estimated LOC across Python, Jinja, Hyperframes HTML/CSS, prompts, scripts, and tests. Full dual-route PR4 with u2net, OpenCV, vision LLM, atomic vault commit, and browser QA is more like 8-12 focused days. YouTube-only first is plausibly 4-5 days.

**Section 4 — ASSUMPTION PUSH-BACK**

D1 assumes brainstorm-driven variation yields genuinely distinct thumbnails. That is not guaranteed. LLMs often collapse into three near-synonyms with the same emotion, same background, and same composition. Add an explicit diversity contract: each idea must differ across at least two axes among hook text, emotion tag, visual metaphor, background query, and composition archetype. The UI should warn when all three ideas use the same emotion/background. This is cheap and prevents "3 nearly-identical thumbnails."

D3 assumes regex plus a closed enum survives human edits. It will not. 修修 will edit Chinese text, swap colons, translate "surprised" into "驚訝," or delete a line. Inline 400 errors are not enough because they occur after intent is already formed. Add a live parse preview beside each textarea: hook, emotion, visual, number/icon, background. If parse fails, show exactly which line failed. Also accept a small Chinese alias map for the seven emotions. That is not complexity; it is protecting the simple text format.

D4 assumes Sonnet vision can infer taste from 20-40 unannotated images in one shot. This is hopeful, not evidenced. Do not build embeddings in PR4, but do require a smoke eval: feed the reference set plus one known past project and inspect whether the three ideas match 修修's taste. If it fails, the fallback should be a one-page generated style rubric stored in `data/`, not manual annotation of every image.

D8 assumes random sample + Laplacian + vision LLM is enough. The basic shape is fine, but the absolute blur threshold is wrong for two camera angles. Replace it with per-video top-N sharpness ranking and deterministic stratified sampling. Also downscale frames before the vision call; the ADR currently talks about "50 × 1MB PNGs," which is operationally sloppy.

D10 dismisses Puppeteer too quickly. Hyperframes runtime hooks are real for BigStat, where GSAP timelines matter. Static thumbnails may not need a timeline at all. Keep Hyperframes CLI for PR4 if reuse speed matters, but stop claiming hooks are critical until thumbnail compositions actually use them. Benchmark CLI+FFmpeg vs direct browser screenshot before locking PR5.

**Section 5 — ALTERNATIVES NOT CONSIDERED**

Alternative storage architecture: unify podcast thumbnail work with ADR-032's episode directory. `data/script_video/{episode-id}/thumbnail/runs/{ts}/` would keep podcast production assets self-contained and backup-friendly. The downside is YouTube projects without script-video episodes need a separate root anyway. My recommendation: keep `data/thumbnails/{slug}` for both routes, but move podcast raw/funnel references under it or explicitly cross-link to `data/script_video`.

Alternative brainstorm shape: a chat-style brainstorm could beat three parallel cards. 修修 and the LLM could iterate one strong concept, then fork it into three thumbnail candidates only at the end. That reduces mode collapse because the human steers the concept before rendering. Tradeoff: more UI and more conversation state. For PR4, keep three cards, but allow the prompt to consume a pasted brainstorm note so the cards are not zero-shot.

Alternative funnel architecture: record a 30-second "expression sample" per podcast episode: host and guest each do 3-5 deliberate expressions under the same lighting, before or after the recording. This can eliminate the LLM mining funnel for many episodes. Tradeoff: expressions may look posed, and the best "presence" frames often come from real conversation. Still, for low volume, this should be the official fallback path, and possibly the PR4 default for podcast if the mining funnel slips.

**Section 6 — FINAL VERDICT**

Approve with modifications. ADR-033 has the right product shape: brainstorm first, dumb renderer second, candidates outside vault, chosen artifact in vault. But it is too optimistic on implementation size and too trusting of untested LLM/vision behavior.

Top required changes:

1. D6/D7: do not add thumbnail routes directly into the 1,952-line `bridge_projects.py`. Create a sibling thumbnail router and keep the current router from becoming a monolith.

2. D8: replace fixed Laplacian `σ = 100` with per-video top-N/percentile ranking, deterministic stratified sampling, frame downscaling, and logged funnel stats.

3. D1/D3: add diversity enforcement and live parse preview for the 5-line idea format. Regex-only plus post-submit errors will fail during real editing.

4. D4: add a PR4 smoke eval for the reference library. Do not assume unannotated references work until one known project reproduces acceptable taste.

5. D10: either benchmark Hyperframes CLI still rendering or weaken the runtime-hooks claim. If mirroring `hyperframes_worker.py`, fix subprocess quoting instead of copying the shell-string pattern.

Ship YouTube first if schedule matters. Podcast funnel is the risky half.
