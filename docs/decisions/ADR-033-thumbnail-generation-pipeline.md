# ADR-033: Thumbnail Generation Pipeline (Brainstorm-driven, Dual-route)

**Date:** 2026-05-26
**Status:** Draft v2 (post multi-agent-panel review 2026-05-26; pending 修修 sign-off)
**Owner:** 修修
**Related:** [ADR-030](ADR-030-vault-as-substrate-read-strategy.md) (vault-substrate) · [ADR-031](ADR-031-project-workspace-migration.md) (Tier C project workspace) · [ADR-032](ADR-032-hyperframes-broll-pipeline.md) (Hyperframes B-roll)

> **Path amendment（[ADR-050](ADR-050-video-production-line-brook-ownership.md)，2026-07-03）**：本 ADR 內所有 `agents/foundry/` 路徑（thumbnail_worker、render_workers、ruff scope 等）隨 video production line 歸 Brook 遷移為 `agents/brook/script_video/`。決策內容不變。

> **v1 → v2 change log** — v1 went through a 3-way panel (Codex GPT-5 + Gemini 2.5 Pro). Panel verbatim audits at:
> - [`docs/research/2026-05-26-codex-adr033-audit.md`](../research/2026-05-26-codex-adr033-audit.md)
> - [`docs/research/2026-05-26-gemini-adr033-audit.md`](../research/2026-05-26-gemini-adr033-audit.md)
>
> Integration matrix in §Panel Integration at the end. Top v2 deltas:
>
> 1. **D2 factual correction** — YouTube DOES support 3-way thumbnail A/B testing (Test & Compare feature, since late 2023). The "platform constraint" framing in v1 was incorrect; independence is now justified by orthogonal-optimization-axis argument only.
> 2. **D3 hardened** — closed-enum emotion tags promoted from inline Python constants to a single-source `prompts/thumbnail/emotions.yml` with bidirectional `en ↔ zh-Hant ↔ aliases` mapping. UI gets a live parse preview alongside each textarea.
> 3. **D4 hardened** — pre-PR4 smoke eval gate (test reference library on one known project, inspect taste match); image preprocessing pipeline (resize 512px, JPEG-recompress, max-count cap, log token count); post-commit lightweight tagging to start collecting revealed-preference dataset (prevent "taste debt").
> 4. **D6 / PR4 split** — thumbnail endpoints land in **sibling router** `thousand_sunny/routers/bridge_project_thumbnails.py`, not bloating the already-1952-line `bridge_projects.py`.
> 5. **D8 hardened** — fixed `σ = 100` Laplacian threshold removed; replaced with per-video top-N variance ranking, deterministic stratified sampling (seeded), audio-energy-burst hybrid for emotion peak capture, downscale before vision LLM call, log all funnel stats. **Expression-sample (30-sec deliberate-takes per episode) added as primary fallback path** when motion-blur u2net failure occurs.
> 6. **D10 amended** — `npx hyperframes` reuse retained for PR4 (correct trade-off vs vanilla Puppeteer for thumbnail stills), but PR4 acceptance gate requires a benchmark of CLI+ffmpeg vs Puppeteer-direct render time. Worker uses `create_subprocess_exec(argv...)` (not `_shell` with single-quoted JSON — Windows `cmd.exe` doesn't honour single quotes; hyperframes_worker.py current shell pattern is a latent bug).
> 7. **New D3a** — Director's Notes textarea per idea card, threaded into render prompt; provides iterative refinement escape hatch ("make background darker", "move face 10% left") without rewriting the 5-line idea. (Number chosen because it's an extension of D3's idea-shape decision, not a new top-level decision.)
> 8. **LLM router update** — `shared/llm_router.py` needs explicit routes for `thumbnail_brainstorm` + `thumbnail_funnel` (PR4 includes this).
> 9. **PR4 estimate revised** — 6-7 days → **8-12 days** dual-route, or **4-5 days** YouTube-only-first split (recommended). YouTube ships shippable; Podcast funnel ships best-effort with expression-sample fallback.
> 10. **Frontmatter schema drift fix** — `content_type` validation says `youtube | podcast` but doc-prose says all 4 retained. This predates ADR-033 (ADR-031 PR1 inconsistency) but D9's podcast-only fields surface it. Quick clean-up in PR4 (separate commit).

---

## Context

### Why this ADR

ADR-031 §"PR3+ Backlog" listed two thumbnail-related items (#6 image generation for `thumbnail_concept`, #7 Title&Thumbnail A/B selector) but never designed them. Both were carried forward as "later PR" placeholders.

A 2026-05-26 grill-with-docs session re-framed the problem from "image generation pipeline" to "**brainstorm-driven dual-route thumbnail composition**". The reframe materially changed what gets built and the dependency ordering. This ADR freezes the design.

### Reframe origin (grill 2026-05-26)

修修 clarified four constraints that the original ADR-031 backlog did not capture:

1. **Layouts are fixed and well-understood** — YouTube tracks Ali Abdaal / Jeff Su style; Podcast tracks Stephen Bartlett "The Diary of a CEO". The grill should not waste cycles on layout design.
2. **Variation comes from brainstorm, not pipeline cleverness** — 3 title candidates + 3 thumbnail idea text descriptions are produced in a brainstorm step (修修 + LLM dialogue). The downstream pipeline is a dumb renderer that obeys the brainstorm text.
3. **YouTube uses a pre-built host cutout library** — 修修 prepares 6-10 selfie cutouts once-off; LLM picks emotion match per idea.
4. **Podcast uses dual-camera recording + per-episode cutout extraction** — 修修 records host_angle.mp4 + guest_angle.mp4. Cutouts for **both** are extracted per-episode (not from library) because guest "在場感" requires the actual recording. The hard part is the funnel that picks the right frame from raw recording.

### Sibling-ADR landscape

- **ADR-030 D1** — Vault is canonical SoT for committed state. ADR-033 honours this: only chosen thumbnails enter vault; candidates stay in `data/`.
- **ADR-031** — Tier C Project workspace built the 7-tab shell including the Title & Thumbnail tab (`_tab_title_thumbnail.html`). ADR-033 fills in the tab body with brainstorm UI + render mechanism.
- **ADR-032** — Hyperframes is the existing render layer for B-roll segments (mp4). ADR-033 extends Hyperframes' use to thumbnail stills (PNG). A small cross-ref note is added in ADR-032.

### Out-of-domain context

修修 cadence:
- YouTube: irregular, project-driven
- Podcast: **~1 episode per week** (low volume → funnel does not need extreme optimization)

修修 quote (verbatim, grill 2026-05-26): "再強調一次，其實這樣子的編排跟流程應該是蠻簡單的，不要把程式複雜化。"

---

## Decision

The 11 numbered decisions below (D1-D10 + D3a Director's Notes, added in v2) are commit-grade. Each follows the pattern:

> **Decision** → **Rationale** → **Alternatives considered** → **Implication**.

### D1. Brainstorm-driven variation (not pipeline-driven)

**Decision** — The 3 thumbnail candidates per project come from **3 brainstorm idea texts**, each rendered as **one thumbnail**. The pipeline does not generate variation on its own (no random layout/palette/seed cycling).

**Rationale** — 修修's thumbnail taste is heavily encoded in the brainstorm step where 修修 + LLM iterate the visual concept in plain language. Asking the pipeline to invent variations after the fact creates an LLM-second-guessing-the-brief problem. Brainstorm is where the human is most engaged; the pipeline is execution.

**Alternatives considered**:
- (rejected) Pipeline auto-generates 5 layout/palette combos per single idea — would dilute brainstorm intent.
- (rejected) LLM generates idea AND variations in one shot — coupling reduces 修修's ability to edit individual ideas.

**Implication** — Brainstorm step is first-class. UI must support 3 distinct idea cards with independent edit + render controls (see D6).

### D2. Title and Thumbnail A/B are independent (not paired)

**Decision** — Title brainstorm candidates and Thumbnail brainstorm candidates are managed as **two independent lists**. There is no `[title, thumbnail]` pairing.

**Rationale** — **(v2 panel fact-check, Gemini)** YouTube actually supports 3-way Thumbnail A/B (Test & Compare feature, since late 2023). The v1 rationale "platform supports only one at a time" was wrong. v2 rationale is **orthogonal-optimization-axis**: Title and Thumbnail are CTR variables on different dimensions (text vs visual), and Ali Abdaal-style design principle is "title and thumbnail are complementary, not redundant" — they vary independently to test independent levers. Future PR may allow paired commit when 修修 wants to commit a `(title, thumbnail)` combination for joint A/B; PR4 keeps them independent.

**Alternatives considered**:
- (rejected) Paired `(title, thumbnail)` rows — initially proposed by Claude in grill Q4; 修修 over-ruled citing YT platform constraint.

**Implication** — The thumbnail's large-font hook text is a separate field inside each `thumbnail_idea` (see D3), not derived from `title`. Title and Thumbnail commit flows are decoupled.

### D3. Thumbnail idea structure — free-form prose with 5-line format convention

**Decision** — Each thumbnail idea is a multi-line markdown string. LLM brainstorm output and 修修's edits follow a 5-line convention:

```
大字：{3-5 字 punchy hook}
我的表情：{emotion tag — accepts English enum, zh-Hant display name, or alias (see emotions.yml)}
視覺：{free-form description}
數字/圖示：{free-form description, may be "無"}
背景：{free-form description, used as Unsplash query}
```

The emotion line (line 2) is matched against a single-source manifest at `prompts/thumbnail/emotions.yml` with shape:

```yaml
- key: surprised        # English enum (used internally + filesystem)
  zh_tw: 驚訝            # Default display label in brainstorm prompt + UI
  aliases: [驚喜, 大吃一驚]  # Other Chinese expressions 修修 might type
  description: 眼睛大、口型 "哇"     # For brainstorm LLM context
- key: thoughtful
  zh_tw: 思考
  aliases: [沈思]
  ...
```

UI displays the Chinese label by default; backend resolves any of `{key, zh_tw, aliases}` to the canonical `key` for filesystem lookup. **(v2 panel-revised, Codex + Gemini)** This eliminates the v1 risk that 修修 edits `驚訝` and the regex fails.

**Rationale** — Free prose feels natural for brainstorm UX; 5-line format gives just enough structure for the downstream pipeline to extract composition variables. Single-source `emotions.yml` prevents Python-constant drift across `prompts/`, `shared/cutout_library.py`, and the brainstorm prompt template.

**Alternatives considered**:
- (a) Full free prose — would require a second LLM parse step at render time, adding latency + cost.
- (b) Fully structured form (5 separate input fields) — feels rigid for brainstorm; doesn't match human "stream of thought" pattern.
- **(c) Hybrid (chosen)** — free textarea with convention, regex + alias-lookup at render.

**Implication** — Brainstorm LLM prompt instructs the model to write `我的表情:` using the `zh_tw` display name from `emotions.yml`. Pipeline render step:

1. Regex-extract each of the 5 lines.
2. For `我的表情:` line, lookup against `emotions.yml` (try `key`, then `zh_tw`, then `aliases`).
3. **UI provides a live parse preview** (HTMX-poll on textarea blur) showing what the pipeline sees: `hook=..., emotion=surprised, visual=..., decoration=..., bg=...`. If parse fails, the preview shows which line failed and suggests fixes. This is cheaper than 400-error after submit because 修修 sees the problem before clicking render.
4. If lookup still fails (typo or new word not in aliases), render endpoint returns 400 with the canonical list of accepted `zh_tw` names.

### D3a. Director's Notes (refinement layer)

**Decision** *(new in v2, per Gemini panel)* — Each idea card has a **second textarea** labeled "🪶 Director's Notes" (optional). After viewing a rendered thumbnail, 修修 can type free-form refinements ("make background darker", "use heavier font on hook", "shift my face 10% left") and click [↻ 重渲]. The notes are appended to the render prompt **on top of** the 5-line idea (not replacing it); composition variables are re-extracted with the notes as additional context.

**Rationale** — Creative iteration is iterative. Forcing all refinements through edits of the 5-line abstract description is unnatural — by the time 修修 sees a render, the changes 修修 wants are visual, not conceptual. The Notes textarea is the escape hatch for visual fine-tuning without rewriting the original brief.

**Implication** — Render endpoint accepts an optional `director_notes` field per idea. Notes persist to `data/thumbnails/{slug}/runs/{ts}/v{N}.notes.txt` for audit. Frontmatter does not store notes (transient).

### D4. Reference library — raw image dump (mine + peers)

**Decision** — 修修 dumps reference thumbnail PNGs into vault paths:

```
Attachments/cutouts/reference/youtube/mine/*.png       (5-10 修修's past hits)
Attachments/cutouts/reference/youtube/peers/*.png      (10-20 Ali Abdaal / Jeff Su / Huberman / Attia / Bryan Johnson etc.)
Attachments/cutouts/reference/podcast/mine/*.png       (5-10)
Attachments/cutouts/reference/podcast/peers/*.png      (10-20 Stephen Bartlett / Lex / Attia podcast etc.)
```

No 修修-authored annotations, no manual tags. **Sonnet 4.6 with vision** extracts style patterns when given the reference batch as few-shot.

**(v2 panel-hardened, Codex + Gemini)** — The "no annotation, vision LLM does it" stance carries two real risks:

- **Mode-collapse / cargo-culting**: vision LLM may converge on surface features ("Ali Abdaal uses yellow circles") rather than design principles ("high-contrast attention focal"). Gemini calls this "convergence on the salient mean".
- **Taste debt**: 修修's taste evolves; an unstructured reference pile grows over years with no mechanism to deprecate old styles.

v2 introduces two **lightweight** safeguards (neither requires per-image annotation):

**4.a. PR4 smoke eval gate** — Before PR4 merges, run a one-off eval: pick one known past project (修修-published thumbnail exists), run brainstorm against current reference library, inspect whether the 3 generated ideas read as plausible variants of 修修's style. If smoke fails, the fallback is a single hand-written `prompts/thumbnail/style_rubric.md` (one page, generated from the reference set with vision LLM + 修修-review pass) used **alongside** the reference images. This is built into PR4 verification §item-9 (new).

**4.b. Post-commit lightweight tagging** *(new in v2)* — When 修修 commits a chosen thumbnail, the commit endpoint additionally writes optional free-text tags to `Attachments/projects/{slug}/thumbnail_tags.txt` (one line: e.g. `high-contrast / data-viz / number-callout`). UI offers the textarea but does not enforce. Over time this builds a revealed-preference dataset with zero upfront work. PR-N (post-PR4) may surface this dataset as a "style cluster" view.

**4.c. Image preprocessing pipeline** *(new in v2)* — Before attaching reference images to the brainstorm LLM call:

- Cap image count (default 30, configurable)
- Resize to 512px longest edge (matches Sonnet vision tile size)
- Recompress JPEG q=85
- Log final token count to `state.db api_calls.scope_json`
- Randomize batch order on each call (mitigate position bias per Gemini Section 1)

Reference images on disk stay at original resolution; preprocessing produces a transient `.tmp/reference_processed/{call_id}/` batch.

**Rationale** — 修修's taste is implicit; forcing 修修 to write commentary upfront is slow and inaccurate (修修 confirmed unwilling during grill). But the v1 "zero structure forever" stance over-rotates. v2 stance: zero **upfront** annotation; gradual **revealed-preference** capture from commit actions; **smoke-test gate** before relying on the inference.

**Alternatives considered**:
- (b) Image + 修修-written commentary — too slow to bootstrap; rejected.
- (c) No reference library, prompt-only — produces generic style; rejected.

**Implication** — Brainstorm LLM call: preprocessed reference batch + 5-line idea prompt + (if smoke-eval failed) `style_rubric.md`. Cost per brainstorm now ~10-20K input tokens (resized images smaller). Commit endpoint extended with optional tag field. See revised §OQ1.

### D5. Host emotion library (B1) — closed-enum tag set of 7

**Decision** — 修修's pre-built selfie library lives at:

```
Attachments/cutouts/shosho/{emotion}/{n}.png
```

with `emotion ∈ {excited, thoughtful, surprised, explaining, serious, laughing, pointing}`.

修修 prepares 1-3 selfies per emotion in a one-off photo session, runs u2net once to remove background, and the cutouts land in their respective folders.

**Rationale** — Closed enum makes emotion matching at render time trivial (regex extracts the tag from `我的表情:` line; cutout_library returns a random PNG from that emotion folder). 7 emotions cover ~90% of health/longevity creator content (per 修修's existing publication history).

**Alternatives considered**:
- (a) Free-form tags (修修 picks any words) — would require LLM-based fuzzy matching at render time.
- (b) Larger emotion taxonomy (15-20 tags) — overkill; most won't be used.

**Implication** — The brainstorm LLM prompt enumerates the 7 valid tags and instructs the model to pick one. PR4 ships these 7 as the default closed set. Future expansion (PR5+) is allowed — adding a new emotion is a vault folder addition + brainstorm prompt update, both cheap. 修修 confirmed during grill that more emotion tags will be derived from collected reference materials in a later iteration.

### D6. UI structure — half-symmetric (Title textarea + Thumbnail 3-card)

**Decision** — In the Bridge `_tab_title_thumbnail.html`:

```
═══ Title section ═══════════════════════════════
existing title_candidates textarea (PR1)
+ [🪄 LLM 給我 3 個 A/B 候選] button

═══ Thumbnail section ═══════════════════════════
[🪄 LLM 給我 3 個 idea] button (populates idea cards)

3 cards, side-by-side (or stacked on narrow viewports):
┌── Idea N ─────────────────┐
│ textarea (5-line format)   │
│ [🎨 渲此縮圖]               │
│ [↻ 重渲]                    │
│ {rendered PNG or empty}    │
│ [✓ commit 為最終]            │
└────────────────────────────┘
```

**Rationale** — Title is pure text and benefits from textarea's fast keystroke editing for reordering / dedup. Thumbnail has a rendered visual artifact attached to each idea, justifying a card UI. Asymmetry mirrors the underlying data shape (Title = list[str]; Thumbnail = list[card-with-output]).

**Alternatives considered**:
- (a) Full symmetric 3-card UI for both — wastes vertical space for Title which has no per-item visual artifact.
- (c) Reduce Title to single textarea (no A/B at all) — contradicts D2's intent for A/B testing.

**Implication** — `_tab_title_thumbnail.html` gets meaningful additions (~250 LOC). The 5-line idea convention is enforced through placeholder text + LLM prompt design (no client-side schema validation in PR4).

### D7. Storage — hybrid (candidates in `data/`, chosen in vault)

**Decision** — Two-tier storage by lifecycle:

```
data/thumbnails/{slug}/runs/{ts}/         (repo-local, gitignored)
  ├── manifest.json                       (LLM input/output/cost per render run)
  ├── v1.variables.json                   (composition variables fed to Hyperframes)
  ├── v1.png                              (rendered candidate)
  ├── v2.variables.json
  ├── v2.png
  └── v3.{variables.json,png}

vault/
  └── Attachments/projects/{slug}/        (Syncthing-replicated, Obsidian-renderable)
      ├── thumbnail.png                   (chosen, atomic write)
      └── _archive/{old_ts}.png           (replaced thumbnails)
```

Frontmatter `thumbnail: "Attachments/projects/{slug}/thumbnail.png"` references the chosen artifact for Obsidian wikilink resolution.

**Rationale** — Candidates are working state with high churn (re-render on idea edit); committing them to vault would inflate Syncthing binary load and pollute Obsidian indexing. Chosen artifacts are committed state; entering vault matches ADR-030 D1's "vault as committed-state SoT" principle. `data/*` is already in `.gitignore`, so candidates are organically isolated from version control.

**Alternatives considered**:
- (A) All in vault — clean ADR-030 alignment but pollutes vault with N candidates × M projects.
- (C) All in repo (no vault writes) — violates ADR-030 D1; Obsidian preview broken.

**Implication** — A new commit endpoint `POST /bridge/projects/{slug}/thumbnail/commit` does the file copy + frontmatter update + archive rotation. The endpoint must be atomic — if commit fails mid-way, vault stays in known-good state (existing thumbnail.png unaffected). A new candidate-serving endpoint `GET /bridge/projects/{slug}/thumbnail/candidate/{ts}/{filename}` streams PNGs from `data/`.

### D8. Podcast guest funnel — hardened (per-video ranking + stratified + vision LLM)

**Decision** *(v2 panel-revised, Codex + Gemini)* — Podcast cutout extraction:

```
For each video in [host_video_path, guest_video_path]:

  Stage 1 — stratified frame sample (FFmpeg)
    Baseline: 1 frame per 10 seconds across video duration (deterministic, seeded).
    Audio-energy hybrid: additionally sample 3 dense frames around each detected
    energy peak (laughter, emphasis, gestural moments) — uses FFmpeg `astats`
    or `silencedetect` filter.
    Cap total at ~80 frames per video.

  Stage 2 — per-video sharpness ranking (OpenCV)
    Compute Laplacian variance (NOT 'σ' — that v1 phrasing was wrong) per frame.
    Keep top-N by variance percentile (default top 25%, i.e. ~20 sharpest).
    Log full variance distribution to state.db for tuning.

  Stage 3 — Sonnet 4.6 vision LLM evaluator
    Preprocess: downscale frames to 512px, JPEG q=85.
    Attach: preprocessed top-N + reference library batch (D4) + brainstorm idea
    texts (D3) + the closed emotion enum.
    Prompt: "Pick top 5 frames matching the emotion intent of the brainstorm idea
    AND 修修's taste (per references). Return JSON: [{frame_index, emotion_match,
    taste_match_score, reason}]."
    Known failure modes documented to 修修 (per Gemini panel):
    - LLM picks 'good portrait' (sharp, frontal) over 'good story' (mid-gesture,
      authentic emotion). 修修 review-step is the safety net.
    - Emotion labels for non-Western faces can be miscalibrated. 修修 ignores
      LLM emotion label if it disagrees with visual evidence.

  Stage 4 — 修修 manual pick (D9 quota: 2-3 host + 2-3 guest)
  Stage 5 — u2net on confirmed picks only
    Success rate on motion-blurred frames is low (Gemini panel: realistic ~60-70%
    not 100%). If u2net produces ragged cutout, 修修 can:
    (a) re-pick a different frame from top-5
    (b) use the expression-sample fallback (see below)
```

**8.a. Expression-sample fallback (primary recommended path per Gemini)** *(new in v2)* — Instead of (or alongside) mining the conversation recording, 修修 records a **30-second deliberate "expression sample" video** at the start or end of each episode:

```
"I'm going to do 7 expressions. Excited. Pause. Thoughtful. Pause. ..."
```

Each expression held for 2-3 seconds. Same lighting / framing as the conversation. The funnel can run against this short clip — 99% of frames are usable, no motion blur, no audio-energy detection needed.

修修 chooses per episode: expression-sample (recommended for reliability) vs full-recording mining (best for "in-conversation authenticity").

**Rationale** — v1's fixed `σ = 100` Laplacian threshold cannot generalize across host/guest video angles (different lighting, distance, lens). Per-video ranking is robust. Audio-energy hybrid captures emotional peaks that random sampling misses. Expression-sample side-steps motion-blur u2net failure entirely. Both Codex and Gemini converged on these revisions independently — strong signal.

**Alternatives considered**:
- Full L1-L4 funnel from handoff (WhisperX time-window as Stage 1) — unnecessary given dual-camera (D9) already separates host/guest files; cadence is low; complexity not justified.
- Pure vision LLM with no sharpness pre-filter — attaches all ~80 frames to Sonnet, payload blows up cost + token budget; sharpness filter is a cheap pre-step.
- Fixed Laplacian threshold (v1) — fails across host/guest video angle differences; replaced with per-video ranking per panel.

**Implication** — `shared/thumbnail_funnel.py` (~350 LOC) ships four pure functions:

- `stratified_sample(video, seed, periodic_interval=10, audio_burst=True)` → frame list
- `rank_by_sharpness(frames, top_pct=0.25)` → sorted top-N
- `vision_eval(frames, ideas, references, emotions_yml)` → top 5 with JSON reasons
- `run(video, *, mode="conversation"|"expression_sample")` → entry point

Tests use video fixtures from `data/test_fixtures/podcasts/` (one short MP4 + one expected top-5 manifest). Vision LLM prompt versioned at `prompts/thumbnail/funnel_v1.md`. Funnel cost + per-run stats (`total_sampled / passed_sharpness / vision_picked / 修修_confirmed`) logged to `state.db api_calls.scope_json="thumbnail_funnel"`. After 5-10 episodes the team reviews stats and tunes percentile / sample interval. Per-run parameter tuning UI deferred to PR-N. See §OQ3.

### D9. Per-episode cutout (Podcast) + reusable library (YouTube)

**Decision** — Cutout sourcing differs by route:

| Route | Host cutout source | Guest cutout source |
|---|---|---|
| YouTube | Pre-built library `Attachments/cutouts/shosho/{emotion}/` (D5) | N/A |
| Podcast | Per-episode funnel (D8), 2-3 picks per episode | Per-episode funnel (D8), 2-3 picks per episode |

Podcast per-episode cutouts land at:

```
Attachments/cutouts/podcast/{ep_slug}/host_v{n}.png
Attachments/cutouts/podcast/{ep_slug}/guest_v{n}.png
```

These accumulate over time but are not reused across episodes (each episode is a discrete event).

修修's "host" cutout in the YouTube library is **not** reused for Podcast — guest "在場感" requires the actual recording context, and pairing a stock-library host cutout with a fresh-recorded guest cutout would visually break the "two people in the same room" effect of the Diary of a CEO style.

The video file paths are declared in frontmatter:

```yaml
host_video_path: data/podcasts/{ep_slug}/host_angle.mp4
guest_video_path: data/podcasts/{ep_slug}/guest_angle.mp4
```

Paths reference filesystem locations; no upload UI (file sizes are GB-scale, unsuitable for HTTP).

**Rationale** — Distinct visual semantics per route. YouTube: 修修 talks alone, "library 修修" is acceptable. Podcast: room scene with guest, "library 修修" feels disconnected.

**Alternatives considered**:
- Single shared library for both routes — visual mismatch in Podcast.
- Both routes per-episode (no library) — wasteful for YouTube where 修修's expression set is small and repeatable.

**Implication** — `shared/cutout_library.py` exposes two interfaces:
- `pick_youtube_host(emotion_tag) → Path` (returns random PNG from library/{emotion}/)
- `pick_podcast_host(ep_slug, idea_text) → Path` (returns active confirmed cutout for episode)
- `pick_podcast_guest(ep_slug, idea_text) → Path` (same)

"Active confirmed cutout" is tracked in frontmatter (`thumbnail_active_cutouts: {host: [...], guest: [...]}`) or via a sentinel `active.json` in the episode cutout folder. PR4 ships the simplest version (frontmatter list).

### D10. Hyperframes integration — npx CLI + ffmpeg extract

**Decision** — Pipeline calls Hyperframes via subprocess to `npx hyperframes render`, producing a 1-second mp4, then `ffmpeg -vframes 1` extracts the PNG. This mirrors `agents/foundry/render_workers/hyperframes_worker.py` (ADR-032's B-roll worker).

**Rationale** — Reuses 90% of the existing worker pattern (`_build_command`, async subprocess, stderr tail, HyperframesRenderError). Wall time per still ~3.5s; 3 stills × 3.5s = ~10s per brainstorm batch — well within 修修-facing UX tolerance. Going through Hyperframes' public CLI ensures the composition's runtime hooks (GSAP timeline, frame lifecycle) execute correctly, which a vanilla Puppeteer screenshot might not.

**Alternatives considered**:
- (B) Direct Puppeteer screenshot (bypass Hyperframes CLI) — faster (~1s/still) but risks Hyperframes runtime hooks not running; would require maintaining a separate render path divergent from `hyperframes_worker.py`.
- (C) Independent Python rendering (Pillow / Playwright) — abandons Hyperframes catalog (字卡, icon blocks, shader transitions); loses brand-token consistency with B-roll.
- (D) SVG-only rendering (cairosvg) — abandons HTML composition entirely; out of scope.

**Implication** — A new render worker `agents/foundry/render_workers/thumbnail_worker.py` (~200 LOC) mirrors `hyperframes_worker.py` **structurally** but with two PR4-mandatory deviations (v2 panel, Codex):

1. **Use `asyncio.create_subprocess_exec(argv...)`, not `_shell` with single-quoted JSON** — `hyperframes_worker.py:79,86` currently uses `create_subprocess_shell` with a JSON variables string single-quoted. On Windows `cmd.exe`, single quotes are not shell quoting; this is a latent bug that thumbnail_worker.py must not inherit. (A follow-up PR can also fix hyperframes_worker.py — out of ADR-033 scope.)
2. **PR4 benchmark gate** — Before merging PR4, run a measured comparison: (A) `npx hyperframes render --duration 1 + ffmpeg extract` per still vs (B) vanilla Puppeteer `page.goto(file://) + screenshot()` on the same composition. Document timing in a one-off `tests/benchmarks/thumbnail_render_bench.md`. If (B) is materially faster AND (B) produces visually-identical output (no missed GSAP entrance animation), the choice between A/B may be revisited in PR5. v1 dismissed (B) on theoretical grounds; v2 requires empirical evidence.

Two new compositions `video/compositions/thumbnail/youtube.html` + `podcast.html` (Hyperframes HTML format). ADR-032 receives a cross-ref note ("render layer extended to thumbnail stills per ADR-033 D10").

---

## Frontmatter additions

The following γ-schema additions extend `docs/schemas/project-frontmatter-nested.md`:

| Field | Type | Required | Routes | Source | Notes |
|---|---|---|---|---|---|
| `thumbnail_ideas` | `list[str]` (multiline) | no | both | Web Title&Thumbnail tab (brainstorm) | 1-3 items typical; each is a 5-line markdown per D3. |
| `thumbnail` | `str` (vault-relative path) | no | both | Web Title&Thumbnail tab (commit) | e.g. `Attachments/projects/{slug}/thumbnail.png`. Obsidian-renderable. |
| `thumbnail_chosen_at` | ISO 8601 with TZ | no | both | Web commit endpoint | `+08:00` recommended. |
| `thumbnail_run` | `str` (run-id + variant) | no | both | Web commit endpoint | e.g. `2026-05-26T15:00:00Z/v3` for audit traceability back to `data/thumbnails/{slug}/runs/`. |
| `host_video_path` | `str` (repo-relative path) | no | podcast | 修修 manual entry | e.g. `data/podcasts/ep42/host_angle.mp4`. PR4 reads only; no validation. |
| `guest_video_path` | `str` (repo-relative path) | no | podcast | 修修 manual entry | Same shape as `host_video_path`. |
| `thumbnail_active_cutouts` | `dict` | no | podcast | Web funnel confirmation step | Shape `{host: [path, path], guest: [path, path]}`. Lists 修修-confirmed cutouts for current episode. |

**Validation**: PR4 ships read-tolerant (missing fields render as "尚未填入" placeholders). No hard schema enforcement; Obsidian editing remains free-form.

**Soft cap**: `thumbnail_ideas[i]` ≤ 500 chars (warning toast in Web UI).

`thumbnail_concept` (single multiline string, from ADR-031 γ schema) becomes deprecated. Migration:
- ADR-031 PR1 wrote `thumbnail_concept` for some projects. ADR-033 reads `thumbnail_concept` as a single-element fallback if `thumbnail_ideas` is absent (lifts to `thumbnail_ideas[0]`).
- No bulk migration script; lazy lift on next edit of the Title&Thumbnail tab.

---

## Vault layout additions

The following paths extend `docs/VAULT-LAYOUT.md` §2 + §3:

```
Attachments/
├── projects/{slug}/                      🤖 Bridge thumbnail commit
│   ├── thumbnail.png                     chosen final
│   └── _archive/{old_ts}.png             replaced versions
└── cutouts/
    ├── shosho/{emotion}/{n}.png          🤖 修修 one-off + u2net (B1, YouTube host library)
    ├── podcast/{ep_slug}/                🤖 per-episode funnel output
    │   ├── host_v{n}.png
    │   └── guest_v{n}.png
    └── reference/                        🤖 修修 manual dump (LLM few-shot)
        ├── youtube/{mine,peers}/*.png
        └── podcast/{mine,peers}/*.png
```

Producer/consumer matrix additions (VAULT-LAYOUT §3):

| Path | Tier | Producer | Consumer | Schema |
|---|---|---|---|---|
| `Attachments/projects/{slug}/thumbnail.png` | 🤖 | `thousand_sunny/routers/bridge_project_thumbnails.py` commit endpoint (ADR-033 D7 + Panel P4 sibling router) | Obsidian preview, frontmatter wikilink | binary |
| `Attachments/projects/{slug}/_archive/{ts}.png` | 🤖 | Same endpoint, rotation on re-commit | (audit only) | binary |
| `Attachments/cutouts/shosho/{emotion}/{n}.png` | 🤖 | One-off import script `scripts/import_shosho_cutouts.py` (PR4) | `shared/cutout_library.pick_youtube_host` | binary |
| `Attachments/cutouts/podcast/{ep_slug}/{host,guest}_v{n}.png` | 🤖 | `shared/thumbnail_funnel.py` confirmation step + u2net wrapper | `shared/cutout_library.pick_podcast_{host,guest}` | binary |
| `Attachments/cutouts/reference/{youtube,podcast}/{mine,peers}/` | 👤 | 修修 manual dump | Brainstorm LLM few-shot attachment | binary |

---

## Pipeline overview

### YouTube route

```
修修 in Bridge tab clicks [🪄 LLM 給我 3 個 idea]
  ↓
Bridge router POST /bridge/projects/{slug}/thumbnail/brainstorm
  - reads frontmatter (title, one_sentence, search_topic)
  - attaches reference library (D4) as vision input
  - prompts Sonnet 4.6 with vision + style: "produce 3 ideas in 5-line format (D3),
    emotion tag from closed enum (D5)"
  - writes thumbnail_ideas to frontmatter
  ↓
Tab UI HTMX-swaps 3 idea cards in
  ↓
修修 (optionally) edits any card's text
  ↓
修修 clicks [🎨 渲此縮圖] on idea N
  ↓
Bridge router POST /bridge/projects/{slug}/thumbnail/render?idea_index=N
  - regex-extracts (hook, emotion_tag, visual, decoration, bg_concept) from idea text
  - cutout_library.pick_youtube_host(emotion_tag) → cutout PNG path
  - fetches Unsplash thumbnail by bg_concept query (cached by query hash)
  - builds composition variables JSON
  - calls thumbnail_worker.render_still(composition="thumbnail/youtube", ...)
    → npx hyperframes render → 1-sec mp4 → ffmpeg → v{N}.png
  - writes data/thumbnails/{slug}/runs/{ts}/v{N}.{variables.json, png}
  - appends to manifest.json
  ↓
Tab UI HTMX-swaps rendered PNG into idea N card
  ↓
修修 clicks [✓ commit 為最終] on idea N
  ↓
Bridge router POST /bridge/projects/{slug}/thumbnail/commit?run=ts/v{N}
  - reads data/thumbnails/{slug}/runs/{ts}/v{N}.png
  - if vault thumbnail.png exists → mv to _archive/{old_ts}.png
  - atomic write to vault Attachments/projects/{slug}/thumbnail.png
  - update frontmatter: thumbnail, thumbnail_chosen_at, thumbnail_run
  - write state.db api_calls audit row
```

### Podcast route

Same as YouTube above with these substitutions:

1. **Funnel runs before brainstorm**:

```
修修 fills host_video_path + guest_video_path in frontmatter (manual)
  ↓
修修 clicks [🎬 跑漏斗從兩個影片各選 5 張候選]
  ↓
Bridge router POST /bridge/projects/{slug}/thumbnail/funnel
  - for video in [host, guest]:
      thumbnail_funnel.run(video) → top 5 frame paths in
        data/thumbnails/{slug}/funnel_runs/{ts}/{host,guest}/v{1..5}.png
  - returns top-5 manifest for UI display
  ↓
Tab UI shows two grids (host + guest) with reason captions
  ↓
修修 checks 2-3 boxes per grid (per D9 quota)
  ↓
修修 clicks [✓ 確認選定，跑 u2net 去背]
  ↓
Bridge router POST /bridge/projects/{slug}/thumbnail/funnel/confirm
  - for confirmed frame:
      run u2net (via hyperframes-media subprocess) → transparent PNG
      write to Attachments/cutouts/podcast/{ep_slug}/{host,guest}_v{n}.png
  - update frontmatter thumbnail_active_cutouts
```

2. **Brainstorm + render** then proceeds. Per-idea render uses `cutout_library.pick_podcast_host` / `pick_podcast_guest` matching the idea's emotion tag against the confirmed cutout set.

3. **Composition is `thumbnail/podcast.html`** (Diary of a CEO layout — two cutouts left/right, dark background, episode number badge, large title).

---

## PR4 implementation outline

*(v2 panel-revised: split into PR4-A YouTube-first + PR4-B Podcast funnel + sibling router pattern, per Codex Section 6 #1 + Gemini Section 6 #1)*

### Default sequencing — PR4-A then PR4-B

**PR4-A — YouTube route end-to-end (4-5 days)**:

| Module | Path | LOC est |
|---|---|---|
| YouTube composition HTML | `video/compositions/thumbnail/youtube.html` | ~150 |
| Composition shared tokens | `video/compositions/thumbnail/_tokens.css` | ~50 |
| Render worker | `agents/foundry/render_workers/thumbnail_worker.py` | ~200 |
| **Sibling Bridge router** | **`thousand_sunny/routers/bridge_project_thumbnails.py`** *(new file, panel-required)* | ~300 |
| Bridge router include hook | `thousand_sunny/app.py` (or equivalent FastAPI mount) | +5 |
| Cutout library | `shared/cutout_library.py` | ~150 |
| Tab UI update | `thousand_sunny/templates/bridge/projects/_tab_title_thumbnail.html` | +250 |
| Tab UI partials (idea card, render result) | `thousand_sunny/templates/bridge/projects/_thumbnail_*.html` | ~150 |
| Frontmatter schema doc | `docs/schemas/project-frontmatter-nested.md` | +40 |
| Vault layout doc | `docs/VAULT-LAYOUT.md` | +20 |
| One-off cutout import script | `scripts/import_shosho_cutouts.py` | ~80 |
| **Emotions YAML** | **`prompts/thumbnail/emotions.yml`** *(new, panel-required)* | ~50 |
| Brainstorm LLM prompt | `prompts/thumbnail/brainstorm_youtube_v1.md` | ~80 |
| LLM router routes | `shared/llm_router.py` (extension) | +20 |
| Smoke eval script | `scripts/eval_thumbnail_reference_taste.py` *(new, panel-required)* | ~120 |
| Benchmark | `tests/benchmarks/thumbnail_render_bench.md` *(one-off doc)* | ~30 |
| Tests — cutout library | `tests/test_cutout_library.py` | ~80 |
| Tests — render worker | `tests/test_thumbnail_worker.py` | ~120 |
| Tests — sibling router | `tests/test_bridge_project_thumbnails.py` *(new file)* | ~200 |
| Tests — emotions parser | `tests/test_emotions_yml.py` *(new, small)* | ~40 |

**PR4-B — Podcast funnel + composition (3-5 days)**:

| Module | Path | LOC est |
|---|---|---|
| Podcast composition HTML | `video/compositions/thumbnail/podcast.html` | ~100 |
| Funnel module | `shared/thumbnail_funnel.py` (stratified sample + audio-energy + sharpness rank + vision eval) | ~350 |
| Funnel router extensions | `thousand_sunny/routers/bridge_project_thumbnails.py` | +150 |
| Tab UI funnel pool partials | `thousand_sunny/templates/bridge/projects/_thumbnail_funnel_*.html` | ~200 |
| Funnel vision LLM prompt | `prompts/thumbnail/funnel_v1.md` | ~120 |
| Expression-sample workflow doc | `docs/podcast-expression-sample-howto.md` *(new, panel-required)* | ~40 |
| Tests — funnel | `tests/test_thumbnail_funnel.py` | ~200 |
| Tests — Podcast endpoints | `tests/test_bridge_project_thumbnails.py` (extension) | +150 |

**Total revised estimate**: 8-12 days dual-route; PR4-A alone 4-5 days (ships shippable YouTube workflow without waiting for Podcast).

### Cross-cutting changes (in both PR4-A and PR4-B)

- **`bridge_projects.py` NOT extended** — Codex panel finding: file is 1952 LOC already; thumbnail endpoints land in sibling router.
- **`shared/llm_router.py` extended** with `thumbnail_brainstorm` + `thumbnail_funnel` route entries → `claude-sonnet-4-6` (vision-capable).
- **`thumbnail_concept` frontmatter deprecated** with lazy fallback (`thumbnail_concept` → `thumbnail_ideas[0]` on next edit). Migration script not required — lazy lift on first Title&Thumbnail tab visit.
- **Subprocess pattern**: `asyncio.create_subprocess_exec(argv...)` everywhere new (no shell-string with single quotes; Windows quoting bug). `hyperframes_worker.py` cleanup deferred to follow-up.

---

## Out of scope (defers to later PRs)

The following are explicitly deferred and tracked in this section for future ADR descendants:

- **PR5** — AI background generation (Flux / SDXL / Imagen / Recraft / nano-banana). PR4 uses Unsplash + dark overlay; PR5 swaps in AI-generated thematic backgrounds with style anchor (LoRA or prompt template).
- **PR5+** — Multi-variant publish for A/B test (upload 2-3 thumbnails per project to YT for native A/B test rotation). PR4 commits a single chosen thumbnail.
- **PR-N** — Audio-driven funnel L1 (WhisperX time-window narrowing). Dual-camera (D9) eliminates the need; revisit only if 修修 changes recording setup.
- **PR-N** — Mobile responsive thumbnail tab.
- **PR-N** — Square-format Podcast cover (Apple Podcasts 3000×3000, Spotify 1400×1400). Same compositions, different output dimensions; ~0.5-day work.
- **PR-N** — CodeMirror editor inside idea textarea (current PR4 uses native textarea).
- **PR-N** — Embedding cache for reference library (avoid re-attaching same images per brainstorm). See §OQ1.
- **PR-N** — Per-funnel-run parameter tuning UI (Laplacian threshold, sample count). See §OQ3.

---

## Verification (PR4 completion criteria)

1. `pytest tests/test_thumbnail_*.py tests/test_cutout_library.py tests/test_bridge_project_thumbnails.py tests/test_emotions_yml.py -x` green.
2. `ruff check shared/ thousand_sunny/ agents/foundry/ scripts/` clean.
3. `ruff format --check ...` clean.
4. Browser smoke test (Playwright headless or 修修 manual):
   - **YouTube project**: brainstorm title × 3 + idea × 3 → live parse preview reflects 5-line parse + emotion alias resolution → render 3 thumbnails → commit 1 → vault attachment exists + frontmatter `thumbnail` matches + optional tag captured.
   - **Podcast project**: funnel runs (both conversation-mining mode AND expression-sample mode), top-5 grids render with reason captions, 修修 confirms 2 host + 2 guest, u2net writes (verify cutout quality manually if motion-blur), brainstorm runs, render uses correct cutouts.
5. Obsidian-side: open `Projects/{slug}.md` → preview pane shows the chosen thumbnail.
6. Syncthing-side: chosen thumbnail visible on 修修's phone within Syncthing latency (~30s typical).
7. Cost audit: one YouTube brainstorm + 3 renders < $0.50 total; one full Podcast workflow (funnel + brainstorm + 3 renders) < $2.00 total. Logged to `state.db api_calls.scope_json`.
8. Failure mode: render failure (Hyperframes error, ffmpeg error, Unsplash 503) renders an inline error toast in the idea card without corrupting frontmatter. Test case: deliberately bad composition variable JSON.
9. **(v2 panel gate)** Reference library smoke eval: run `scripts/eval_thumbnail_reference_taste.py` against one known past project; manually inspect that the 3 generated ideas are plausible 修修-style variants. If fail, generate fallback `prompts/thumbnail/style_rubric.md` before PR merges.
10. **(v2 panel gate)** Render benchmark documented: `tests/benchmarks/thumbnail_render_bench.md` records (A) `npx hyperframes + ffmpeg` vs (B) Puppeteer-direct timings on one composition. Decision recorded for PR5 to revisit.
11. **(v2 panel gate)** Emotion alias resolution test: feed 修修's likely Chinese typings (`驚訝`, `驚喜`, `surprised`, mixed case) into the parser and verify all resolve to `key=surprised`. Covered by `tests/test_emotions_yml.py`.
12. **(v2 panel gate)** Image preprocessing logged: brainstorm call writes processed image batch token count + preprocessing config to `state.db api_calls.scope_json`. Test case: deliberately oversized 4K reference → verify resize to 512px happens.

---

## Open technical questions (non-blocking)

### §OQ1. Reference library scaling

*(v2 panel-revised)* Pre-PR4 smoke eval is now D4.a (mandatory). Per-call attachment with preprocessing (cap 30 / 512px / JPEG q=85) ≈ 10-20K input tokens at 修修's brainstorm cadence (2-5/week) ≈ ~$3-5/month. Acceptable.

Escalation triggers:
- Monthly cost > $20 → consider Sonnet vision embedding cache
- Reference library > 60 images → cap at 30 most-recent or implement curation flow
- Smoke eval persistently fails → make `style_rubric.md` the primary path, references secondary

### §OQ2. Emotion alias edge cases

*(v2 panel-resolved by D3 `emotions.yml`)* Bidirectional alias map handles `驚訝 / 驚喜 / surprised / Surprised` etc. Edge case still open: 修修 types something not in aliases (e.g. `「哇」表情`). Strategy: render endpoint returns 400 with the canonical Chinese list; 修修 adjusts. Long-term: collect 修修's deviations and add to aliases (no breaking change).

### §OQ3. Funnel parameter tuning policy

*(v2 panel-revised by D8)* No fixed threshold — per-video top-N percentile (default 25%) plus stratified periodic sampling + audio-energy bursts. Tuning policy:

- Log per-funnel-run stats: `total_sampled / passed_sharpness / vision_picked / 修修_confirmed` to `state.db api_calls.scope_json`
- After 5-10 episodes, review stats; adjust percentile / sample interval / audio-energy sensitivity in a small follow-up PR
- Per-video tuning UI deferred to PR-N

### §OQ4. Render mode A vs B (npx CLI vs Puppeteer)

*(v2 panel-elevated)* PR4 gate: `tests/benchmarks/thumbnail_render_bench.md` documents timing + visual-identity comparison. If (B) Puppeteer wins materially, PR5 may swap. v1's "Hyperframes runtime hooks critical" claim was unverified rhetoric; v2 requires empirical evidence.

### §OQ5. Re-render behavior — overwrite vs version history

When 修修 edits an idea text and clicks [↻ 重渲], the new render:

- (chosen) **Overwrites** `data/thumbnails/{slug}/runs/{ts}/v{N}.{variables.json, png}` for the same idea index. `manifest.json` retains historical entries (append-only).
- (rejected) Creates a new `runs/{new_ts}/` for every re-render — would inflate `data/` size and confuse the commit flow.

If 修修 ever needs to "go back" to a previous render, the chosen + archived thumbnails in vault (`_archive/`) provide rollback at the committed level. Working-state rollback is not provided.

### §OQ6. Reference image attribution

For peers references (Ali Abdaal, Stephen Bartlett etc.), the cutout/reference/peers/ folder will hold downloaded thumbnails. Since these are only LLM input (not republished), copyright concerns are minimal — but the folder is **vault-local only**, never committed to git, never published. ADR-033 records this constraint; no further enforcement needed.

---

## Cross-references

- [ADR-030](ADR-030-vault-as-substrate-read-strategy.md) — D1 vault canonical SoT; D4 substrate routing
- [ADR-031](ADR-031-project-workspace-migration.md) — Tier C project workspace; PR1 shipped `_tab_title_thumbnail.html` shell that ADR-033 fills in
- [ADR-032](ADR-032-hyperframes-broll-pipeline.md) — Hyperframes B-roll pipeline; ADR-033 extends Hyperframes render layer to thumbnail stills (D10). ADR-032 receives a cross-ref note.
- [`docs/schemas/project-frontmatter-nested.md`](../schemas/project-frontmatter-nested.md) — γ schema extended with thumbnail fields
- [`docs/VAULT-LAYOUT.md`](../VAULT-LAYOUT.md) — §2 + §3 extended with cutout/projects/reference paths
- Handoff document (修修 + Claude conversation, attached to grill session 2026-05-26) — original brainstorm-time freeform notes; ADR-033 supersedes by formalizing 11 decisions.

---

## Panel Integration (v1 → v2)

Multi-agent panel ran 2026-05-26. Verbatim audits:
- [Codex](../research/2026-05-26-codex-adr033-audit.md) (GPT-5 via Codex CLI 0.128.0)
- [Gemini](../research/2026-05-26-gemini-adr033-audit.md) (Gemini 2.5 Pro)

### Integration matrix

| # | Topic | Claude v1 | Codex audit | Gemini audit | Pattern | Resolution |
|---|---|---|---|---|---|---|
| P1 | D4 reference library risk | "Sonnet vision extracts pattern, no annotation" | Hopeful, needs smoke eval | Convergence on salient mean + cargo-culting; taste debt | **2-of-2** | **Adopt both** — D4.a smoke eval gate + D4.b post-commit tagging |
| P2 | D8 funnel σ threshold | Fixed `σ = 100` Laplacian | Per-video top-N ranking, deterministic stratified, downscale | Audio-energy hybrid, expression-sample fallback as primary | **2-of-2 + complementary** | **Adopt all** — D8 rewritten with per-video ranking + stratified + audio-burst + expression-sample fallback |
| P3 | D3 regex + emotion enum | English enum, regex extract | 400-error too late, alias map, live parse preview | emotions.yml single source, zh-Hant aliases day 1, bidirectional | **2-of-2 + complementary** | **Adopt all** — D3 rewritten around `emotions.yml`, UI gets live parse preview |
| P4 | bridge_projects.py 1952 LOC | "+250 LOC extension" | Sibling router required | (didn't address) | **Codex unique** | **Adopt** — new `bridge_project_thumbnails.py` sibling router |
| P5 | PR4 estimate | 6-7 days | 8-12 days dual-route; 4-5 days YouTube-only | (didn't address) | **Codex unique** | **Adopt** — split PR4-A (YouTube, 4-5d) + PR4-B (Podcast, 3-5d) |
| P6 | Subprocess shell quoting | Mirror hyperframes_worker.py | Windows single-quote bug; use `create_subprocess_exec` | (didn't address) | **Codex unique** | **Adopt** — `exec(argv)` in thumbnail_worker.py; hyperframes_worker.py cleanup deferred |
| P7 | D2 YouTube A/B platform fact | "Either Title OR Thumbnail, not both" | (didn't address) | YT supports 3-way Thumb A/B since late 2023 (Test & Compare) | **Gemini unique factual fix** | **Adopt** — D2 rationale rewritten to orthogonal-axis argument |
| P8 | Creative iteration loop | (absent in v1) | (didn't address) | Director's Notes textarea per card | **Gemini unique** | **Adopt** — new D3a Director's Notes |
| P9 | LLM router routes | Default Sonnet 4.6 implicit | Explicit `thumbnail_brainstorm` route needed | (didn't address) | **Codex unique** | **Adopt** — `shared/llm_router.py` extension in PR4-A |
| P10 | Image preprocessing | "20-40K tokens, simple" | Add resize/recompress/cap rules + token logging | Attention dilution at high image counts, batch ordering bias | **2-of-2** | **Adopt all** — D4.c preprocessing pipeline; randomize batch order |
| P11 | u2net failure rate | Implicit success | (didn't address) | Motion-blur frames fail u2net, ~60-70% realistic | **Gemini unique** | **Adopt** — D8 expression-sample fallback covers this; manual re-pick option in UI |
| P12 | Single-user lock-in (creator_id) | All paths hardcode `shosho` | (didn't address) | Future-creator migration painful | **Gemini unique** | **Defer** — adding `creator_id` later is mechanical refactor; near-zero current value; YAGNI |
| P13 | Niche-specific creator conventions | Treated peers as monolithic | (didn't address) | Health/longevity (Huberman/Attia) has specific data-viz conventions | **Gemini unique** | **No ADR change** — 修修 curates the reference library per his niche; the architecture is niche-agnostic |
| P14 | Frontmatter content_type drift | Inherited from ADR-031 PR1 | Validation says `{youtube, podcast}` but doc-prose says 4 retained | (didn't address) | **Codex unique** | **Note in v2 change log** — fix in separate small commit during PR4-A |
| P15 | NPX CLI vs Puppeteer | Asserted hooks critical | "Stop claiming hooks are critical until thumbnail uses them; benchmark" | (didn't address) | **Codex unique** | **Adopt** — D10 amended; PR4 acceptance gate requires benchmark doc |
| P16 | Vision LLM "good portrait vs good story" | Implicit trust | (didn't address) | LLM picks technically clean over emotionally rich | **Gemini unique** | **Adopt as documented risk** — D8 surfaces this as known limitation; 修修 review step is safety net |
| P17 | Bilingual prompt contamination | English-emotion + Chinese-prompt mix | (didn't address) | English reference text leaking into Chinese ideas | **Gemini unique** | **Mitigated by P3** — `emotions.yml` zh-Hant display + brainstorm prompt explicitly demands Traditional Chinese output |

### Confidence summary

- **High confidence** (2-of-2 panel agreement): P1, P2, P3, P10 — adopted with full v2 rewrite of D3, D4, D8.
- **Medium-high confidence** (single panel + verifiable in code): P4, P5, P6, P9, P14, P15 — adopted; concrete code findings.
- **Medium confidence** (single panel + reasoning-based): P7, P8, P11, P16, P17 — adopted; align with 修修's stated values (simplicity + creative agency).
- **Low confidence / deferred**: P12, P13 — explicit YAGNI / 修修-curation deferrals.

### Items NOT adopted

- **P12 creator_id paths** — overengineering for current single-user reality. Adding `creator_id/` in 5 years (if ever needed) is a mechanical migration script. Cost-of-adoption-now > cost-of-migration-later.
- **P13 niche-specific reference curation in code** — the architecture is niche-agnostic; 修修 supplies the reference set. No code change.
