# ADR-033: Thumbnail Generation Pipeline (Brainstorm-driven, Dual-route)

**Date:** 2026-05-26
**Status:** Draft v1 (post grill 2026-05-26; pending multi-agent-panel review)
**Owner:** 修修
**Related:** [ADR-030](ADR-030-vault-as-substrate-read-strategy.md) (vault-substrate) · [ADR-031](ADR-031-project-workspace-migration.md) (Tier C project workspace) · [ADR-032](ADR-032-hyperframes-broll-pipeline.md) (Hyperframes B-roll)

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

The 10 numbered decisions below are commit-grade. Each follows the pattern:

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

**Rationale** — YouTube platform supports either Title A/B testing OR Thumbnail A/B testing, not both simultaneously. Independent management aligns with platform mechanics and with the Ali Abdaal design principle that "title and thumbnail are complementary, not redundant" — they vary along orthogonal axes.

**Alternatives considered**:
- (rejected) Paired `(title, thumbnail)` rows — initially proposed by Claude in grill Q4; 修修 over-ruled citing YT platform constraint.

**Implication** — The thumbnail's large-font hook text is a separate field inside each `thumbnail_idea` (see D3), not derived from `title`. Title and Thumbnail commit flows are decoupled.

### D3. Thumbnail idea structure — free-form prose with 5-line format convention

**Decision** — Each thumbnail idea is a multi-line markdown string. LLM brainstorm output and 修修's edits follow a 5-line convention:

```
大字：{3-5 字 punchy hook}
我的表情：{closed-enum emotion tag, e.g. "surprised"}
視覺：{free-form description}
數字/圖示：{free-form description, may be "無"}
背景：{free-form description, used as Unsplash query}
```

The closed-enum emotion tag (line 2) is the **only** strict field. Other lines are descriptive.

**Rationale** — Free prose feels natural for brainstorm UX; 5-line format gives just enough structure for the downstream pipeline to extract composition variables via simple regex + closed-enum lookup. No second LLM call is needed at render time to parse.

**Alternatives considered**:
- (a) Full free prose — would require a second LLM parse step at render time, adding latency + cost.
- (b) Fully structured form (5 separate input fields) — feels rigid for brainstorm; doesn't match human "stream of thought" pattern.
- **(c) Hybrid (chosen)** — free textarea with convention, regex-extract at render.

**Implication** — Brainstorm LLM prompt must instruct the model to follow the 5-line format strictly, with `我的表情:` line constrained to the closed enum (see D5). Pipeline render step uses regex to extract; if regex fails, fallback is to inline-edit the idea in the UI.

### D4. Reference library — raw image dump (mine + peers)

**Decision** — 修修 dumps reference thumbnail PNGs into vault paths:

```
Attachments/cutouts/reference/youtube/mine/*.png       (5-10 修修's past hits)
Attachments/cutouts/reference/youtube/peers/*.png      (10-20 Ali Abdaal / Jeff Su / Huberman / Attia / Bryan Johnson etc.)
Attachments/cutouts/reference/podcast/mine/*.png       (5-10)
Attachments/cutouts/reference/podcast/peers/*.png      (10-20 Stephen Bartlett / Lex / Attia podcast etc.)
```

No annotations, no tags, no metadata file. **Sonnet 4.6 with vision** extracts style patterns when given the reference batch as few-shot.

**Rationale** — 修修's taste is implicit knowledge; forcing 修修 to write "why I like this" annotations would be slow and inaccurate. Vision LLMs are strong enough to extract pattern from N similar images in one inference. mine + peers are treated as a unified "approved style" set at LLM time — the directory split exists only for 修修's filesystem navigation.

**Alternatives considered**:
- (b) Image + 修修-written commentary — too slow to bootstrap; 修修 confirmed unwilling to write commentary.
- (c) No reference library, prompt-only style description — would produce generic YT thumbnail style, not 修修-specific voice.

**Implication** — Brainstorm LLM call must attach reference images. Token cost per brainstorm ≈ 20-40K input tokens. PR4 ships per-call attachment (no embedding cache). If cost grows, PR5+ may add an embedding cache. See open question §OQ1.

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

### D8. Podcast guest funnel — simplified 3-stage (random + Laplacian + vision LLM)

**Decision** — Podcast cutout extraction follows a simplified funnel:

```
For each video in [host_video_path, guest_video_path]:
  Stage 1 — random frame sample (FFmpeg)
    Draw ~50 frames at uniformly random timestamps across the video duration.
  Stage 2 — Laplacian blur filter (OpenCV)
    Compute Laplacian variance per frame; drop frames below threshold (default σ = 100,
    tunable per video). Keep ~20 sharpest frames.
  Stage 3 — Sonnet 4.6 vision LLM evaluator
    Attach the ~20 frames + the reference library (D4) batch + brainstorm idea texts
    (D3) as context. Prompt: "From these {N} candidate frames, pick the top 5 that
    best match 修修's taste (per references) and could carry the emotion implied by
    the brainstorm ideas. Return JSON with frame_index + reason per pick."
```

Then 修修 manually picks 2-3 frames from each video's top-5 (D9). u2net runs on confirmed picks only. **The funnel is manually triggered** (a button in the Podcast tab), one-shot per episode.

**Rationale** — The handoff doc proposed an L1-L4 funnel with WhisperX time-window narrowing as Stage 1. This is unnecessary in 修修's setup because:
- Dual-camera recording (D9) already separates host and guest into different files; no per-frame speaker identification needed.
- Podcast cadence is 1 episode/week; the funnel does not need to be CPU-optimal.
- Random sample + blur filter + vision LLM is a 3-call total per video, ~$0.50-$1.00 per episode, ~2-3 minutes wall time including transfers.

Vision LLM does the heavy lifting at Stage 3 because:
- It can simultaneously check sharpness, expression, framing, eye openness, mouth shape, and 修修-taste alignment in one inference.
- Traditional CV (MediaPipe face + EAR + emotion classifier) requires hyperparameter tuning per camera angle/lighting; vision LLM degrades gracefully.

**Alternatives considered**:
- Full L1-L4 funnel from handoff — unnecessary given dual-camera + low cadence.
- WhisperX time-window first — adds dependency on transcribe pipeline output; doesn't help when whole video has only one speaker.
- Pure vision LLM (no Laplacian pre-filter) — would attach 50 frames to Sonnet (~50 × 1MB PNGs → context bloat + cost); Laplacian is a cheap pre-filter.

**Implication** — `shared/thumbnail_funnel.py` is a new module (~250 LOC) with three pure functions. Tests use video fixtures from `data/test_fixtures/podcasts/` (one short MP4 + one expected top-5 manifest). Vision LLM prompt is versioned (`prompts/funnel_v1.txt`). Funnel cost goes to `state.db api_calls.scope_json="thumbnail_funnel"` for audit. See open question §OQ3 for parameter tuning policy.

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

**Implication** — A new render worker `agents/foundry/render_workers/thumbnail_worker.py` (~200 LOC) mirrors `hyperframes_worker.py`. Two new compositions `video/compositions/thumbnail/youtube.html` + `podcast.html` (Hyperframes HTML format). ADR-032 receives a small cross-ref note ("render layer extended to thumbnail stills per ADR-033 D10").

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
| `Attachments/projects/{slug}/thumbnail.png` | 🤖 | `thousand_sunny/routers/bridge_projects.py` commit endpoint | Obsidian preview, frontmatter wikilink | binary |
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

| Module | Path | LOC est | Owner |
|---|---|---|---|
| YouTube composition HTML | `video/compositions/thumbnail/youtube.html` | ~150 | foundry agent (ADR-032 namespace) |
| Podcast composition HTML | `video/compositions/thumbnail/podcast.html` | ~100 | foundry agent |
| Composition shared tokens | `video/compositions/thumbnail/_tokens.css` | ~50 | foundry agent |
| Render worker | `agents/foundry/render_workers/thumbnail_worker.py` | ~200 | foundry agent |
| Funnel module | `shared/thumbnail_funnel.py` | ~250 | shared (no agent home) |
| Cutout library | `shared/cutout_library.py` | ~150 | shared |
| Brainstorm + render endpoints | `thousand_sunny/routers/bridge_projects.py` (extension) | +250 | Bridge |
| Tab UI update | `thousand_sunny/templates/bridge/projects/_tab_title_thumbnail.html` | +250 | Bridge |
| Tab UI partials | `thousand_sunny/templates/bridge/projects/_thumbnail_*.html` (idea card, funnel pool, etc.) | ~150 | Bridge |
| Frontmatter schema doc update | `docs/schemas/project-frontmatter-nested.md` | +40 | docs |
| Vault layout doc update | `docs/VAULT-LAYOUT.md` | +20 | docs |
| One-off cutout import script | `scripts/import_shosho_cutouts.py` | ~80 | scripts |
| Brainstorm LLM prompts | `prompts/thumbnail/brainstorm_youtube_v1.md`, `..._podcast_v1.md` | ~60 each | prompts |
| Funnel vision LLM prompt | `prompts/thumbnail/funnel_v1.md` | ~80 | prompts |
| Tests — funnel | `tests/test_thumbnail_funnel.py` | ~150 | tests |
| Tests — cutout library | `tests/test_cutout_library.py` | ~80 | tests |
| Tests — render worker | `tests/test_thumbnail_worker.py` | ~120 | tests |
| Tests — Bridge endpoints | `tests/test_bridge_projects.py` (extension) | +200 | tests |

**Estimated PR4: 6-7 day-equivalent of focused work.**

The PR can be split if needed:
- **Split A**: PR4-1 = composition HTMLs + render worker + cutout library + YouTube route end-to-end; PR4-2 = funnel + Podcast route. Cost: extra PR overhead, ~0.5 day. Benefit: each PR ships independently usable.
- **Split B**: PR4-1 = brainstorm UI + render YouTube only (no funnel, no Podcast). PR4-2 = funnel + Podcast composition. Cost: similar.

Default: **single PR4** (修修 prefers simple sequencing per grill 2026-05-26).

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

1. `pytest tests/test_thumbnail_*.py tests/test_cutout_library.py tests/test_bridge_projects.py -x` green.
2. `ruff check shared/ thousand_sunny/ agents/foundry/ scripts/` clean.
3. `ruff format --check ...` clean.
4. Browser smoke test (Playwright headless or 修修 manual):
   - **YouTube project**: brainstorm title × 3 + idea × 3 → render 3 thumbnails → commit 1 → vault attachment exists + frontmatter `thumbnail` matches.
   - **Podcast project**: funnel runs, top-5 grids render, 修修 confirms 2 host + 2 guest, u2net writes, brainstorm runs, render uses correct cutouts.
5. Obsidian-side: open `Projects/{slug}.md` → preview pane shows the chosen thumbnail (Obsidian wikilink resolution).
6. Syncthing-side: chosen thumbnail visible on 修修's phone within Syncthing latency (~30s typical).
7. Cost audit: one brainstorm + 3 renders for a YouTube project < $0.50 total; one full Podcast workflow (funnel + brainstorm + 3 renders) < $2.00 total. Logged to `state.db api_calls.scope_json`.
8. Failure mode: render failure (Hyperframes error, ffmpeg error, Unsplash 503) renders an inline error toast in the idea card without corrupting frontmatter. Test case: deliberately bad composition variable JSON.

---

## Open technical questions (non-blocking)

### §OQ1. Reference library caching strategy

Per-brainstorm attachment of 20-40 reference images is the simple path (~20-40K input tokens per call). At 修修's expected brainstorm cadence (~2-5 per week), this is ~$5/month — acceptable. If cadence grows or 修修 expands reference library to 50+ images, embedding cache is the natural escalation:

- Pre-compute Sonnet vision embedding of each reference image once
- Store embedding in `data/thumbnail_reference_embeddings/{hash}.json`
- Brainstorm call references embeddings (smaller payload) instead of raw images

PR4 ships the simple per-call attachment. Cost is monitored via `state.db api_calls` audit; revisit if monthly cost exceeds $20.

### §OQ2. Emotion match — regex vs LLM secondary call

D3 + D5 specify regex extraction of the emotion tag from `我的表情:` line, relying on the brainstorm LLM to write a valid closed-enum tag.

Edge cases:
- 修修 edits the idea and replaces "surprised" with "驚訝" → regex fails to match enum → render fallback?

PR4 strategy:
- Regex first; if extraction yields a non-enum token, the render endpoint returns a 400 with "請在『我的表情』那行使用合法 tag：{enum list}".
- Future PR5+ may add an automatic Chinese → enum mapping (LLM call or hardcoded translation table).

### §OQ3. Funnel parameter tuning

Default Laplacian threshold (σ = 100), sample count (50 frames), Stage-3 LLM top-K (5) are starting values. PR4 ships these as constants; tuning policy:

- Log per-funnel-run stats: `total_sampled / passed_blur_filter / vision_picked / 修修_confirmed`
- After 5-10 episodes, review stats and adjust constants in a small follow-up PR
- Per-video tuning UI deferred to PR-N

### §OQ4. Re-render behavior — overwrite vs version history

When 修修 edits an idea text and clicks [↻ 重渲], the new render:

- (chosen) **Overwrites** `data/thumbnails/{slug}/runs/{ts}/v{N}.{variables.json, png}` for the same idea index. `manifest.json` retains historical entries (append-only).
- (rejected) Creates a new `runs/{new_ts}/` for every re-render — would inflate `data/` size and confuse the commit flow.

If 修修 ever needs to "go back" to a previous render, the chosen + archived thumbnails in vault (`_archive/`) provide rollback at the committed level. Working-state rollback is not provided.

### §OQ5. Reference image attribution

For peers references (Ali Abdaal, Stephen Bartlett etc.), the cutout/reference/peers/ folder will hold downloaded thumbnails. Since these are only LLM input (not republished), copyright concerns are minimal — but the folder is **vault-local only**, never committed to git, never published. ADR-033 records this constraint; no further enforcement needed.

---

## Cross-references

- [ADR-030](ADR-030-vault-as-substrate-read-strategy.md) — D1 vault canonical SoT; D4 substrate routing
- [ADR-031](ADR-031-project-workspace-migration.md) — Tier C project workspace; PR1 shipped `_tab_title_thumbnail.html` shell that ADR-033 fills in
- [ADR-032](ADR-032-hyperframes-broll-pipeline.md) — Hyperframes B-roll pipeline; ADR-033 extends Hyperframes render layer to thumbnail stills (D10). ADR-032 receives a cross-ref note.
- [`docs/schemas/project-frontmatter-nested.md`](../schemas/project-frontmatter-nested.md) — γ schema extended with thumbnail fields
- [`docs/VAULT-LAYOUT.md`](../VAULT-LAYOUT.md) — §2 + §3 extended with cutout/projects/reference paths
- Handoff document (修修 + Claude conversation, attached to grill session 2026-05-26) — original brainstorm-time freeform notes; ADR-033 supersedes by formalizing 10 decisions.
