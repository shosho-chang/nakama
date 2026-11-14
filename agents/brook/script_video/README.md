# `agents/brook/script_video/` — script-to-B-roll pipeline

ADR-032 production agent. Production `plan` / `run` consume the same subtitle
release the podcast line uses — ADR-063 Memo Dual-Audit Release V1 — plus
talking-head video, and produce a DaVinci Resolve-importable FCPXML timeline and
individual B-roll mp4 clips.

```
data/script_video/<episode-id>/                ← INPUT + OUTPUT root
├── episode.yaml                               ← metadata
├── raw_recording.mp4                          ← talking head (already mistake-cleaned)
├── subtitle-release/memo-dual-audit-v1/       ← 新字幕程式的 release（podcast_subtitle_release.py finalize）
│   ├── STAGE5-HANDOFF.json                    ← plan/emit 預設讀這份
│   ├── release.srt
│   ├── release-ledger.json
│   └── export-manifest.json
├── refs.yaml          (optional)              ← quote disambiguation map
├── storyboard.yaml    (generated)             ← planner output, UI edits in place
├── storyboard.subtitle-source.json (generated) ← plan 用的是哪一份 release（hash）
└── out/                                       ← render artifacts
    ├── b_roll_<sha256[:16]>.mp4              ← individual B-roll clips, content-addressed (ADR-038 §D2)
    ├── episode.fcpxml                         ← ★ DaVinci import target
    └── episode.srt    (optional)              ← caption track if Phase 2 enabled
```

## CLI

```bash
python -m agents.brook.script_video --episode <id> plan      # release.srt → storyboard
python -m agents.brook.script_video --episode <id> render    # storyboard.yaml → b_roll_*.mp4
python -m agents.brook.script_video --episode <id> emit      # → episode.fcpxml
python -m agents.brook.script_video --episode <id> run       # plan + render + emit
```

`plan` opens `subtitle-release/memo-dual-audit-v1/STAGE5-HANDOFF.json` through
the same `select_stage5_subtitle` the Resolve step uses (hash-binds release.srt,
ledger, export manifest and episode id), gives the planner only those exact SRT
bytes, and records the release identity in `storyboard.subtitle-source.json`.
`--subtitle-release-handoff <relative path>` picks another handoff inside the
episode directory. A local `transcript.srt` is never a production input. `emit`
reopens the release, rejects a storyboard planned on a different release, and
rejects anchor, timing, or cue-ID drift before writing FCPXML.

release 由 `scripts/podcast_subtitle_release.py` 產（`.claude/skills/podcast-pipeline/SKILL.md`
S3–S6），`--episode-root` 指到 `data/script_video/<episode-id>/`。影片集不做音檔前處理
（修修 2026-11-14：不跑 `run_audio_prep.py`／Auphonic）；release 仍需要一份 wav 與
`normalized-handoff.v1.json`，`scripts/podcast_subtitle_v2_evidence.py seal-normalized --audio <wav>`
可以直接封存任一份 wav（影片集這條路還沒實跑過）。

## Bridge UI

`/brook/video/<episode-id>` — Phase 1 Tier 2:
- Table view of storyboard.yaml
- Per-row actions: Approve / Edit fields / Re-plan with note
- Batch actions: Approve All Text / Render All Approved / Finalize All Passing
- Polling status updates (no SSE; no inline `<video>`)
- 2-layer approve: text_approved → render_status → visual_approved
- Edit log writes on Re-plan with note (single learning store)

PR-5 (#716).

## Schema

### `episode.yaml`

```yaml
id: N42-atomic-habits
title: 原子習慣的核心概念
target_duration_seconds: 720         # 12 min
created_at: 2026-05-25
```

### `refs.yaml` (optional)

```yaml
quotes:
  - text_anchor: "習慣是身分認同的形成"     # normalized form (PR-2)
    book: "原子習慣"
    page: 87
    book_slug_robin: "atomic-habits"     # Phase 1.5 — Robin URL scheme
```

### `storyboard.yaml`

```yaml
- beat_id: 12
  start_quote: "研究追蹤了 11,000"           # exact Verified Projection substring
  end_quote: "10 年下來發現的趨勢"
  timing:                                    # filled by beat_aligner
    start: 47.5
    duration: 8.2
  srt_line_ids: [23, 24, 25]
  broll_decision: cutaway
  layout: full_broll                         # Phase 1: full_aroll | full_broll
  broll:
    render_target: hyperframes               # Phase 1: hyperframes only
    component: bigstat
    params: { target: 11000, label: "受試者", suffix: "人" }
    transitions: { in: fade_0.3, out: cut }
  status:
    text_approved: false
    render_status: pending                   # pending | rendering | done | failed
    visual_approved: false
  user_notes: []
```

## Layer 跨越合約

```
[Python deterministic]                          [LLM creative]
srt_flattener.py (over freshly verified exact SRT bytes)
                ↓ flat_text + char↔time index
                ↓
                ←─────── planner.py ────────→
                ↓ beats with anchor (start_quote, end_quote)
                ↓
beat_aligner.py
  - str.find() primary
  - AnchorNotFoundError hard fail on miss (no fuzzy rescue)
  - rapidfuzz only under --diagnostic-fuzzy for debug listings
                ↓ timing + srt_line_ids
                ↓
render_dispatcher → render_workers/*
                ↓
fcpxml_emitter
                ↓
out/episode.fcpxml + out/b_roll_*.mp4
```

## Historical Phase 1 PR map

This table describes the pre-V2 implementation history. The production V2
handoff above supersedes its local normalization path.

| PR | Issue | 範圍 |
|---|---|---|
| PR-1 | #712 | This scaffold (✓) |
| PR-2 | #713 | legacy srt_flattener + chinese_normalizer (cn2an) + beat_aligner |
| PR-3 | #714 | planner LLM call + storyboard schema |
| PR-4 | #715 | render_dispatcher + hyperframes_worker + LINE Seed TW @font-face + FCPXML emitter + DaVinci import fixture |
| PR-5 | #716 | Thousand Sunny Bridge UI Tier 2 + endpoints + edit_log writer |

ADR-032 §Phase 1 PR slicing for v2 estimates.

## DaVinci import smoke test (PR-4 acceptance)

After PR-4 ships, import `tests/brook/script_video/fixtures/davinci_import/minimal.fcpxml` into DaVinci Resolve. Expected:

- V1 + V2 tracks render correctly stacked
- No schema validation errors
- B-roll mp4 at correct timestamp
- If 1.10 fails → re-emit with `--fcpxml-version 1.11` or `1.9` (auto-fallback)

Document outcome in PR-4 review comment.

## Pipeline invariants (from ADR-032)

- **Talking head sacred for grade** — V1 raw_recording.mp4 referenced; broll pipeline never re-encodes. DaVinci does encode on final export (LUT / grade), but the source clip retains full grading latitude.
- **The Memo Dual-Audit release is the timing source of truth** — no estimates; `release.srt` is hash-bound by `STAGE5-HANDOFF.json` (ADR-063). Podcast Subtitle V2 (ADR-056) was removed in 2026-11. A loose `transcript.srt` / legacy `/transcribe` SRT is never a production input.
- **Mistake-cleanup 是同一條 line 的選配前置 stage**（ADR-050 D3 改寫原「out of scope」invariant）— `cleanup/` 拍掌 marker 偵測產 ripple-delete FCPXML；storyboard pipeline 仍假設進場的 SRT 已乾淨。
- **`docs/design-system.md` is the only brand source** — planner loads at runtime, never duplicates tokens locally.

See [ADR-032](../../docs/decisions/ADR-032-hyperframes-broll-pipeline.md) for the full architectural rationale + 3-way panel audit.

## Content-addressed render cache (ADR-038 §D2)

Rendered b-roll mp4s live at `out/b_roll_<sha256[:16]>.mp4`. The hash is computed from:

1. `shared.video_line_versions.EXPORT_VERSION` — global cache-flush lever
2. Minimal beat fields — `broll_decision`, `layout`, `broll.{render_target, component, params}`
3. SHA-256[:8] of the referenced `agents/brook/script_video/layouts/<layout>.yaml`
4. SHA-256[:8] of the referenced `video/compositions/<component>/index.html`
5. SHA-256[:8] of `agents/brook/script_video/guardrails.yaml`

Re-running `render` on an unchanged storyboard is a no-op (every beat is a cache hit). Editing a layout YAML, a composition HTML, a beat's `broll.params`, or bumping `EXPORT_VERSION` invalidates the relevant beats.

### When to bump `EXPORT_VERSION`

Edit `shared/video_line_versions.py` and increment `EXPORT_VERSION` whenever a change to the render pipeline makes previously-cached mp4s **no longer valid output**, e.g.:

- switching hyperframes renderer version / codec settings
- changing the default font-face shipped with the pipeline
- fixing a determinism bug in a worker that produced subtly different frames

A bump invalidates **every** cached b-roll mp4 across **every** episode in one go — re-render on next `pipeline render` is unavoidable. Use sparingly; per-beat invalidation already happens automatically via inputs 2-5 above.

### Force a fresh render

```bash
python -m agents.brook.script_video --episode <id> render --no-cache
```

Bypasses the cache for one invocation without touching `EXPORT_VERSION`.

### Legacy `b_roll_<beat_id>.mp4` files

Pre-ADR-038 renders used `b_roll_<beat_id>.mp4`. Phase 2 makes no attempt to backfill — those files simply orphan in `out/` and can be deleted by hand. The first `render` after upgrading will produce hash-named mp4s alongside (or in place of, if you clean up) the legacy ones.
