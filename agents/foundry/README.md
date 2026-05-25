# `agents/foundry/` — script-to-B-roll pipeline

ADR-032 production agent. Consumes clean SRT + talking-head video, produces a DaVinci Resolve-importable FCPXML timeline + individual B-roll mp4 clips.

```
data/script_video/<episode-id>/                ← INPUT + OUTPUT root
├── episode.yaml                               ← metadata: title, target_duration, refs map
├── raw_recording.mp4                          ← talking head (already mistake-cleaned)
├── transcript.srt                             ← from /transcribe skill
├── refs.yaml          (optional)              ← quote disambiguation map
├── storyboard.yaml    (generated)             ← planner output, UI edits in place
└── out/                                       ← render artifacts
    ├── b_roll_<beat_id>.mp4                  ← individual B-roll clips
    ├── episode.fcpxml                         ← ★ DaVinci import target
    └── episode.srt    (optional)              ← caption track if Phase 2 enabled
```

## CLI

```bash
python -m agents.foundry --episode <id> plan      # SRT → storyboard.yaml
python -m agents.foundry --episode <id> render    # storyboard.yaml → b_roll_*.mp4
python -m agents.foundry --episode <id> emit      # → episode.fcpxml
python -m agents.foundry --episode <id> run       # plan + render + emit
```

PR-1 ships entry shell only; subcommand bodies raise NotImplementedError pointing at the implementing PR (#713 / #714 / #715).

## Bridge UI

`/foundry/<episode-id>` — Phase 1 Tier 2:
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
  start_quote: "研究追蹤了 11,000"           # exact substring of normalized transcript
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
srt_flattener.py + chinese_normalizer.py
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

## Phase 1 PR map

| PR | Issue | 範圍 |
|---|---|---|
| PR-1 | #712 | This scaffold (✓) |
| PR-2 | #713 | srt_flattener + chinese_normalizer (cn2an) + beat_aligner |
| PR-3 | #714 | planner LLM call + storyboard schema |
| PR-4 | #715 | render_dispatcher + hyperframes_worker + LINE Seed TW @font-face + FCPXML emitter + DaVinci import fixture |
| PR-5 | #716 | Thousand Sunny Bridge UI Tier 2 + endpoints + edit_log writer |

ADR-032 §Phase 1 PR slicing for v2 estimates.

## DaVinci import smoke test (PR-4 acceptance)

After PR-4 ships, import `tests/foundry/fixtures/davinci_import/minimal.fcpxml` into DaVinci Resolve. Expected:

- V1 + V2 tracks render correctly stacked
- No schema validation errors
- B-roll mp4 at correct timestamp
- If 1.10 fails → re-emit with `--fcpxml-version 1.11` or `1.9` (auto-fallback)

Document outcome in PR-4 review comment.

## Pipeline invariants (from ADR-032)

- **Talking head sacred for grade** — V1 raw_recording.mp4 referenced; broll pipeline never re-encodes. DaVinci does encode on final export (LUT / grade), but the source clip retains full grading latitude.
- **SRT is timing source of truth** — no estimates; `/transcribe` upstream is the canonical input.
- **Mistake-cleanup out of scope** — upstream tool's responsibility. foundry assumes SRT is already clean.
- **`docs/design-system.md` is the only brand source** — planner loads at runtime, never duplicates tokens locally.

See [ADR-032](../../docs/decisions/ADR-032-hyperframes-broll-pipeline.md) for the full architectural rationale + 3-way panel audit.
