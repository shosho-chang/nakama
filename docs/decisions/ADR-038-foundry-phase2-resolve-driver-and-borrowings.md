# ADR-038: Foundry Phase 2 — Resolve Lua Driver + Borrowings from `course-video-manager`

**Date:** 2026-05-28
**Status:** Draft v1 (pending multi-agent panel review)
**Owner:** 修修
**Related:** [ADR-032](ADR-032-hyperframes-broll-pipeline.md) (Phase 1 base, partially superseded) · [ADR-014](ADR-014-repurpose-engine-plugin-interface.md) · [ADR-027](ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md) (Brook narrow — Foundry stays independent agent, see §0)

---

## Context

### Phase 1 status (ADR-032)

Phase 1 shipped 2026-05-26 across PRs #717 / #720 / #723 / #719 / #724 / #726. Pipeline `python -m agents.foundry --episode <id> {plan|render|emit|run}` is live; DaVinci import smoke ✅ on 2026-05-26 (V1 black10s + V2 bigstat3s lane=1 @ 4s + A1 sync); Hyperframes visual determinism SSIM 0.99977 verified; LINE Seed TW @font-face shipped; Bridge UI Tier 2 at `/foundry/<ep-id>` is functional.

**Phase 1 acceptance gap that remains open**: the functional criterion "修修真實 10-15min episode end-to-end with 3-5 BigStat beats → .fcpxml + .mp4" has not been satisfied. Code paths are verified on fixtures; production验收 awaits one real episode. ADR-038 explicitly does **not** unblock on this — Phase 2 work and dogfood can proceed in parallel, but §Acceptance Criteria pins this as a hard gate for ADR-038 merge into production (vs draft acceptance).

### Why a Phase 2 ADR now

ADR-032 left "Phase 2 backlog" as a 6-line bullet list with no time estimate, no integration shape, no dependency analysis. Two changes since then make a real Phase 2 ADR worth writing:

1. **Codex now owns the thumbnail track** (ADR-033 → 036 → 037), so Foundry can re-focus on video production proper without thumbnail entanglement.
2. **`course-video-manager` reverse-engineering** (research delivered 2026-05-28; see §References) surfaced 6 concrete patterns that map directly to named Phase 1.5 / Phase 2 backlog items. The patterns are small, self-contained, and already battle-tested in Matt Pocock's daily course-authoring workflow. Adopting them costs days, not weeks; designing equivalents from scratch would cost weeks.

This ADR freezes the Phase 2 design around three load-bearing borrowings plus four supporting ones.

### Sibling-ADR landscape

- **ADR-032** — Phase 1 base. ADR-038 supersedes its Phase 2 backlog list (one paragraph at end of ADR-032 §Phase 1.5 / Phase 2 backlog) with a concrete plan. Phase 1 invariants in ADR-032 §不變項 carry over unchanged.
- **ADR-014** — RepurposeEngine plugin interface. ADR-038 does not change Brook's call-not-host relationship with Foundry; Brook can still invoke `python -m agents.foundry run` as a subprocess.
- **ADR-027** — Brook narrow. ADR-038 explicitly does **not** re-host Foundry under Brook (see §0).

### Out-of-domain context

修修 cadence:
- YouTube: irregular, project-driven
- Podcast: ~1 episode per week

修修 has DaVinci Resolve Studio license + Resolve open during edit sessions. `fuscript.exe` is bundled with Resolve Studio installs at `C:\Program Files\Blackmagic Design\DaVinci Resolve\` on Windows.

---

## §0. Agent boundary (not changing)

Foundry remains an independent agent under `agents/foundry/`. This was settled in ADR-032 §0.1 after Gemini panel push-back ("forcing it into agents/brook/ risks bloating Brook into a monolith"). 2026-05-28 conversation re-examined the boundary; the honest accounting is that a rename would touch only the ADR-001 agent table — no code, no CLI, no Bridge route, no test changes. Cost-of-rename > value-of-rename. **No agent-boundary change.**

---

## Decision

The 7 numbered decisions below are commit-grade for Phase 2. Each follows the pattern:

> **Decision** → **Borrowing source** → **Rationale** → **Implication**.

### D1. Resolve Lua Driver via `fuscript.exe` (HIGH borrowing, primary)

**Decision** — Add a second render/emit path alongside FCPXML: a direct Resolve driver that drops timelines into the user's open Resolve session via `fuscript.exe -q <script.lua>`. Lua scripts live at `agents/foundry/render_workers/resolve/*.lua`, invoked by a Python wrapper `agents/foundry/render_workers/resolve_driver.py`.

**Borrowing source** — `mattpocock/course-video-manager`:
- `app/services/video-processing-service.ts:408-501` (Python-side equivalent: process spawn + env-var protocol)
- `resources/resolve/clip-and-append.lua` (169 lines — multi-track timeline assembly)
- `resources/resolve/create-timeline.lua`
- `resources/resolve/export-timelines.lua` (render queue trigger)
- `resources/resolve/add-subtitles.lua` (Resolve built-in ASR — see D4)
- `resources/resolve/zoom-clip.lua` (Ken Burns)
- `resources/resolve/add-gaussian-blur.lua`

Protocol is intentionally naive: serialize args via env vars (`INPUT_VIDEOS="a:::b"`, `CLIPS_TO_APPEND="start___end___videoIdx___trackIdx:::..."`), spawn `fuscript.exe -q script.lua`, read stdout/stderr.

**Rationale** — Two value propositions:

1. **Closes the "FCPXML → manual DaVinci import" UX gap**. ADR-032 Phase 2 backlog item `--direct-mp4` was framed as "skip FCPXML when DaVinci adds no value". The honest reframe: 修修 *already* uses DaVinci for grade + final encode; the friction is the manual import step. Resolve driver lets us drop a timeline directly into 修修's open project — same DaVinci destination, zero manual step.
2. **Unlocks Resolve's mature features cheaply** — built-in ASR for caption track (alternative to FCPXML caption rendering), built-in render queue (alternative to maintaining our own ffmpeg encode params), grade preset reuse (no need to encode style in Hyperframes).

**Implication**:

- New module `agents/foundry/render_workers/resolve_driver.py` (~250 LOC):
  - `class ResolveDriver` with methods `create_timeline(name) / append_clips(spec) / add_to_render_queue() / export_srt(track_id)`
  - Internal: env-var serialization + `asyncio.create_subprocess_exec("fuscript.exe", "-q", script_path)` (not `_shell` — Windows quoting bug per ADR-033 D10).
  - Discovers `fuscript.exe` via `RESOLVE_FUSCRIPT_PATH` env var, fallback to default install paths.
- 6 Lua scripts ported verbatim from Matt's repo into `agents/foundry/render_workers/resolve/*.lua`. License: Matt's repo is unlicensed (no `LICENSE` file at clone time); we treat the Lua scripts as fair-use reference and credit in script header comments. **See §Open Questions OQ1 for license follow-up before merge.**
- `pipeline.py` gets a third subcommand `drive-resolve`:
  ```
  python -m agents.foundry --episode <id> drive-resolve [--render-now]
  ```
  Reads existing `storyboard.yaml` + rendered `out/b_roll_*.mp4`, opens Resolve session via API, creates timeline, appends clips. `--render-now` additionally queues + starts a render and blocks until export completes (writes `out/episode_resolve_rendered.mp4`).
- `run` subcommand grows optional `--final-via {fcpxml,resolve-import,resolve-driver}` (default `fcpxml` for backward compat); when set to `resolve-driver`, Foundry replaces FCPXML emit with Resolve driver invocation.
- **Windows-only** at first. Matt runs WSL2 → Windows UNC paths (`video-processing-service.ts:417-421`); our port targets native Windows directly. Linux/Mac Resolve support is out of Phase 2 scope.

**Alternatives considered**:
- (rejected) Stay FCPXML-only — leaves manual import step; doesn't unlock Resolve ASR / render queue / grade preset reuse.
- (rejected) Build our own ffmpeg-direct render path (`--direct-mp4` as originally framed) — skips grade entirely, loses DaVinci value. Resolve driver gets the benefit of "no manual import" *and* "DaVinci does the encode".
- (deferred) Headless Resolve Engine via DaVinciResolveScript Python API (not `fuscript.exe`) — supported but documented worse than Lua; revisit Phase 2.5 if Lua proves brittle.

### D2. Content-Addressed Export Hash + `EXPORT_VERSION` Cache Buster

**Decision** — Render outputs are content-addressed: `out/b_roll_<sha256[:16]>.mp4` instead of `out/b_roll_<beat_id>.mp4`. Hash inputs: `EXPORT_VERSION` constant + sorted minimal beat fields (`broll_decision`, `layout`, `broll.component`, `broll.params`, `broll.render_target`). Re-plan checks hash before re-rendering. Bumping `EXPORT_VERSION` invalidates all caches at once.

**Borrowing source** — `course-video-manager` `app/services/export-hash.ts` (79 lines, self-contained). Direct port to Python.

**Rationale** — Phase 1 storyboard re-plan currently triggers full re-render of all `broll_decision=cutaway` beats even if only beat 7 changed. With per-week podcast cadence + 25 beats per episode × ~10s/render, a single re-plan costs ~4 minutes of GPU time. Content-addressed hashes make re-plan cost = O(changed beats). `EXPORT_VERSION` (single int constant in `shared/foundry_versions.py`) is the operational kill-switch for ffmpeg / Hyperframes / encode-param changes that would otherwise leave stale renders silently cached.

**Implication**:

- New module `shared/foundry_versions.py`:
  ```python
  EXPORT_VERSION = 1  # bump to invalidate all rendered b-roll mp4s
  FCPXML_SCHEMA_VERSION = 1  # bump to invalidate all emitted FCPXML
  ```
- New module `agents/foundry/export_hash.py` (~80 LOC) — pure function `compute_beat_hash(beat: dict) -> str`. Sorted JSON of minimal fields → SHA256 → 16-char hex.
- `render_dispatcher.run_queue` checks `(out_dir / f"b_roll_{hash}.mp4").exists()` before dispatching to worker; logs `skipped (cache hit)` + writes `storyboard[beat].status.render_status = "done"` with `cached_from_hash` flag.
- `storyboard.yaml` schema gains optional `status.cached_hash` field (Pydantic: `Optional[str] = None`).
- `pipeline.py render` accepts `--no-cache` flag to bypass (useful when debugging worker changes without `EXPORT_VERSION` bump).
- FCPXML emitter reads from `out/b_roll_<hash>.mp4` via storyboard lookup (not by glob).
- Migration: existing episodes with `out/b_roll_<beat_id>.mp4` filenames remain readable; Phase 2 only writes hash-named files. No backfill script. Old files orphan after first re-render; user-cleanable.

**Alternatives considered**:
- (rejected) Mtime-based cache — fragile across machine sync, Syncthing, and DaVinci import write-backs.
- (rejected) Per-beat-id cache (no hash) — re-plan changes `broll.params` but keeps `beat_id`; would silently serve stale render.

### D3. LLM Tool-Call Edit Pattern for Single-Beat Re-Plan

**Decision** — Replace the current "full planner re-run on note" flow with a tool-call agent that calls structured edit operations on a single beat: `replace_anchor(beat_id, old_text, new_text) / set_broll(beat_id, component, params) / shift_anchor(beat_id, direction, char_count) / mark_aroll(beat_id) / split_beat(beat_id, at_char) / merge_beats(beat_id_a, beat_id_b)`. Edit engine is pure-functional and unit-tested; LLM only emits tool calls.

**Borrowing source** — `course-video-manager` `app/features/article-writer/document-editing-engine.ts` — pure-functional edit engine with `applyEdits(doc, [{type:"replace", old_text, new_text}, {type:"insert_after", anchor, new_text}, {type:"rewrite", new_text}])`. LLM streams tool calls; engine applies them deterministically; same engine unit-testable without LLM.

**Rationale** — Current `/foundry-replan` skill / Bridge UI "Re-plan with note" path re-runs the entire `planner.plan_episode` against the full SRT for every note. Two costs: (a) ~$0.20 per re-plan in tokens (Sonnet 4.6, ~12k input × 25 beats episode), (b) the re-plan can drift other beats that 修修 already approved. Tool-call pattern: single-beat scope, deterministic engine, ~$0.02 per re-plan, no drift to other beats.

This also closes the Phase 1 caveat noted in 2026-05-28 retrospective: "Bridge UI Re-plan action currently only clears `render_status`, doesn't actually re-run LLM."

**Implication**:

- New module `agents/foundry/beat_editor.py` (~200 LOC):
  - `class BeatEdit` Pydantic union of 6 edit ops above.
  - `apply_edits(storyboard: list[Beat], edits: list[BeatEdit]) -> list[Beat]` — pure function.
  - Re-runs `beat_aligner.align_beat` on edited beats only.
  - Preserves all `status.*_approved` flags except `render_status` (force re-render via hash mismatch from D2).
- New module `agents/foundry/replan_agent.py` (~150 LOC):
  - Anthropic SDK `messages.create` with `tool_choice="any"` + 6 tools matching `BeatEdit` ops.
  - Tool-loop until LLM emits `done` or hits max 5 iterations.
- `pipeline.py` gets `replan-beat` subcommand:
  ```
  python -m agents.foundry --episode <id> replan-beat <beat_id> --note "<text>"
  ```
- Bridge UI Re-plan-with-note now hits a real endpoint that calls `replan_agent.run(beat_id, note)` instead of the placeholder.
- `edit_log/` entries gain `edit_ops: list[BeatEdit]` field for downstream learning (Phase 2.5 retrieval corpus).

**Alternatives considered**:
- (rejected) Free-form LLM output + diff/patch parsing — Matt's experience shows tool calls are dramatically more reliable than diff parsing for sub-document edits.
- (rejected) Constrain to one tool op per call — adds round-trips; 5-iteration tool loop is the sweet spot per Matt's docs.

### D4. Resolve Built-In ASR for Caption Track (Optional, English Only)

**Decision** — When `--final-via=resolve-driver` is active **and** episode language is English (frontmatter `lang: en`), expose `python -m agents.foundry --episode <id> add-captions` which calls `resources/resolve/add-subtitles.lua` to trigger Resolve's `CreateSubtitlesFromAudio` and exports an `.srt` to `out/episode_resolve_captions.srt`. Mandarin episodes continue to use FunASR / `/transcribe` upstream.

**Borrowing source** — `course-video-manager` `resources/resolve/add-subtitles.lua`.

**Rationale** — `/transcribe` skill is Mandarin-optimized (FunASR Paraformer-zh). For bilingual episodes or English-only content, Resolve's built-in ASR is "free" (no extra service, no API cost) and produces reasonable English `.srt`. Two use cases: (a) bilingual cross-check on Mandarin episodes (sanity check FunASR English passages), (b) future English-content support without standing up a second ASR service.

**Implication**:

- 1 Lua script ported.
- ~50 LOC Python wrapper in `resolve_driver.py`.
- No change to `/transcribe` skill — it remains the canonical Mandarin path.
- Frontmatter `lang` field becomes load-bearing: `lang: zh-TW` (default) | `lang: en` | `lang: bilingual` (latter exports both FunASR + Resolve ASR for diffing).

**Alternatives considered**:
- (rejected) Replace `/transcribe` with Resolve ASR universally — Resolve's Mandarin accuracy is poor vs FunASR Paraformer-zh.
- (deferred) Bilingual auto-arbitration between FunASR + Resolve ASR — Phase 3.

### D5. `silencedetect` Parser for B-Roll Insertion Hint Mode (Auto-Beat Discovery)

**Decision** — Add `agents/foundry/silence_detection.py` — pure Python parser for `ffmpeg -af silencedetect` stderr → list of `(speaking_start, speaking_end)` spans. Exposed as `python -m agents.foundry --episode <id> hint-beats` which produces `storyboard_hints.yaml` (a candidate beat list the planner can use as prior).

**Borrowing source** — `course-video-manager` `app/services/silence-detection.ts` (143 lines). Trivial Python port.

**Rationale** — Current planner sees only SRT text and has to invent beat boundaries from text rhythm. Adding "speech pause" signal (silence > 0.7s) as a prior to the planner improves beat boundary accuracy at near-zero cost. Useful particularly for podcast b-roll planning where conversational pauses are real content signals.

**Implication**:

- ~50 LOC in `silence_detection.py`.
- ~30 LOC wrapper that runs `ffmpeg -i raw_recording.mp4 -af silencedetect=noise=-30dB:d=0.7 -f null -` and pipes stderr to parser.
- `planner.py` prompt gains optional `<silence_hints>` block when `storyboard_hints.yaml` exists.
- Not enabled by default; opt-in via `--hint-beats` flag on `plan` subcommand.

**Alternatives considered**:
- (rejected) WhisperX word-level VAD — heavier dependency, marginal accuracy gain for beat-boundary use case.

### D6. `[N]` Clip-Index Anchors as Planner Output Format (Secondary)

**Decision** — Planner LLM continues to emit exact-copy text anchors (`start_quote` / `end_quote`) as primary, **but** when the storyboard is rendered for the LLM's view in re-plan flow (D3), beats get stable `[N]` index prefixes that the tool calls can reference (`replace_anchor(beat_id="[7]", ...)` instead of fragile char-offset).

**Borrowing source** — `course-video-manager` `app/lib/transcript-builder.ts:127` `buildTranscript(clips, chapters)` — interleaves clips with `[N]` indices + `## Chapter` headers, and LLM agent references clips by `[N]`.

**Rationale** — Char-offset anchors (Phase 1 design) work for initial planning but are fragile under copy-edits. `[N]` indices are stable across all edits within a re-plan session.

**Implication**:

- `beat_editor.py` accepts both `beat_id` (UUID-like) and `[N]` (positional) — `[N]` resolves to current N-th beat at the moment of edit application.
- `replan_agent.py` prompt template uses `[N]` notation in the storyboard rendering.
- No change to `storyboard.yaml` on-disk schema — `[N]` is a runtime affordance.

**Alternatives considered**:
- (rejected) Replace UUIDs with positional indices on disk — breaks every existing reference + edit_log entry.

### D7. LCS-Based Storyboard Diff for Multi-Episode History

**Decision** — Add `agents/foundry/storyboard_diff.py` — Myers-style LCS over storyboard beats, emit `+/-/keep` line list. Used by:
- `edit_log/` entries to record the actual storyboard delta per re-plan (not just the note text).
- Future Bridge UI multi-episode history view (Phase 2.5).
- `python -m agents.foundry --episode <id> diff <storyboard_a.yaml> <storyboard_b.yaml>` CLI.

**Borrowing source** — `course-video-manager` `app/lib/changelog-diff.ts` (94 lines). Verbatim algorithm port.

**Rationale** — Phase 2 backlog explicitly lists "multi-episode listing + history view"; storyboard diff is the missing primitive. Trivial cost (~100 LOC), useful immediately for richer edit_log entries even before history UI ships.

**Implication**:

- ~100 LOC in `storyboard_diff.py`.
- `edit_log.py` writer extended to call `storyboard_diff(before, after)` and store result.
- No on-disk schema change for storyboard itself.

**Alternatives considered**:
- (rejected) `difflib.unified_diff` on YAML text — works at line level, breaks on key reordering, hard to attribute changes to specific beats.

---

## §Borrowings Not Adopted in Phase 2

For completeness, the `course-video-manager` borrowings that were considered but deferred:

| Pattern | Source | Why deferred |
|---|---|---|
| GPU/CPU semaphore (Effect-ts pattern, `GPU_PERMITS=6`/`CPU_PERMITS=12`) | `ffmpeg-commands.ts:8-9` | Adopt the 6/12 ratio in Phase 1.5 GPU semaphore work, but the Effect-ts wrapper is overkill for our Python `asyncio.Semaphore`. Tracked in Phase 1.5 backlog, not in this ADR. |
| `evalite` planner prompt regression suite | `evalite.config.ts` + `evals/skill-building-text.eval.ts` | Worth doing, but not Phase 2 critical path. Add when planner prompt churns more than once per quarter. Tracked as Phase 2.5 follow-up. |
| SSE-driven batch operations | `batch-export.server.ts` | Replaces Bridge polling; cosmetic. Defer until Tier 3 UI work. |
| Thumbnail canvas compositor + horizontal-position slider | `thumbnail-editor/canvas-compositor.ts` | Codex owns thumbnail track. Flag for Codex window only. |
| Stream Deck Forwarder | `stream-deck-forwarder/` | Cool, niche. 修修 has not asked for physical buttons. Note for future awareness. |
| Drizzle `previousVersionXId` lineage + Postgres CHECK biconditionals | `app/db/schema.ts:80-130` | Nakama uses YAML+filesystem, not Postgres versioning, for episode state. Pattern doesn't map. |
| OBS live recording + speech detector | `obs-connector.tsx` + `use-speech-detector.ts` | Nakama starts from finished SRT, not live recording. Domain mismatch. |
| Course / Section / Lesson schema | `app/db/schema.ts` | Nakama is episode-based, not course-based. Domain mismatch. |
| Dropbox → Zapier → Buffer publish chain | `publish-to-dropbox.ts` + `youtube-upload-service.ts` | Nakama publishes via WP (`shosho.tw`) and Robin YouTube auth (ADR-035). Vendor mismatch. |

---

## Phase 2 PR slicing

| PR | Scope | Estimate | Depends on |
|---|---|---|---|
| **PR-A** | D2 export hash + `EXPORT_VERSION` + cache-skip in `render_dispatcher` + storyboard.yaml `cached_hash` field | 1.5d | — |
| **PR-B** | D7 LCS storyboard diff + edit_log enrichment | 1d | — |
| **PR-C** | D5 `silencedetect` parser + `--hint-beats` flag | 1d | — |
| **PR-D** | D1 Resolve Lua driver core (6 Lua scripts + `resolve_driver.py` + `drive-resolve` subcommand, no render-queue) | 3d | — |
| **PR-E** | D1 render queue + `--render-now` + path discovery (`RESOLVE_FUSCRIPT_PATH`) + Windows fixture | 1.5d | PR-D |
| **PR-F** | D4 Resolve ASR caption export + frontmatter `lang` field | 1d | PR-D |
| **PR-G** | D3 + D6 beat_editor + replan_agent + `replan-beat` subcommand + Bridge endpoint wiring | 3.5d | PR-A (uses hash for cache invalidation), PR-B (edit_log enrichment) |

**Total**: ~12.5d. PR-A → PR-G can mostly parallelize; PR-G is the only one with two hard dependencies.

**Sandcastle eligibility**: PR-A, PR-B, PR-C, PR-D, PR-G are sandcastle-tagged (pure Python + tests, no GPU, no Resolve install needed for tests via Lua-mock). PR-E and PR-F require local Windows + Resolve Studio (修修's machine).

---

## Acceptance Criteria

### Functional

- [ ] Phase 1 acceptance closure: 修修真實 10-15min episode end-to-end produces `.fcpxml` and individual `.mp4`s without intervention. (Pre-existing ADR-032 gate; ADR-038 does not invent it but flags it.)
- [ ] `python -m agents.foundry --episode <id> drive-resolve` opens Resolve session, creates timeline named `<id>`, appends V1 raw_recording + V2 b-roll clips at storyboard timing, no error. (D1)
- [ ] `python -m agents.foundry --episode <id> drive-resolve --render-now` additionally queues render and produces `out/episode_resolve_rendered.mp4` within 5min for a 12min episode. (D1)
- [ ] `python -m agents.foundry --episode <id> replan-beat 7 --note "make it punchier"` produces a modified storyboard.yaml with beat 7 changed, beats 1-6 + 8-N unchanged, edit_log entry containing `edit_ops` list. (D3)
- [ ] `python -m agents.foundry --episode <id> render` second invocation on unchanged storyboard logs `skipped (cache hit)` for all cutaway beats; zero ffmpeg/Hyperframes process spawns. (D2)
- [ ] Bumping `EXPORT_VERSION` from 1 → 2 in `shared/foundry_versions.py` causes next render to invalidate all caches and re-render. (D2)

### Determinism / Visual

- [ ] Resolve driver: same `storyboard.yaml` driven twice into Resolve produces identical timeline (clip count, track count, in/out points by frame). Timeline export hash equality not required (Resolve metadata varies); structural equality required.
- [ ] D3 replan-beat is deterministic given same beat + same note + same model + same seed (where supported).

### Compatibility

- [ ] Existing Phase 1 fixture episode (`data/script_video/test-fixture-001/`) still passes `python -m agents.foundry --episode test-fixture-001 run` with default `--final-via=fcpxml`.
- [ ] No regression in DaVinci import smoke test from Phase 1 (`tests/foundry/fixtures/davinci_import/minimal.fcpxml`).
- [ ] Hyperframes worker SSIM ≥ 0.99 regression test still passes.

### Code quality

- [ ] `pytest tests/foundry/ tests/test_export_hash.py tests/test_storyboard_diff.py tests/test_beat_editor.py tests/test_resolve_driver.py tests/test_silence_detection.py -x` green.
- [ ] `ruff check agents/foundry/ shared/ scripts/` clean.
- [ ] `ruff format --check ...` clean.

---

## Out of scope (Phase 2.5 / Phase 3)

Explicitly deferred:

- **Phase 2.5**: SSE Bridge UI (replace polling), Tier 3 inline `<video>` player, evalite planner prompt regression suite, examples retrieval (>5 corpus gate), multi-episode listing UI consuming `storyboard_diff.py`, GPU semaphore (with 6/12 ratio borrowed from Matt's setup), `--direct-mp4` ffmpeg-only path (skip Resolve entirely — only build if Resolve driver proves brittle), Reader-Playwright + Web-Playwright workers (Phase 1.5 backlog item, separate ADR if needed).
- **Phase 3**: DataChart / Map / Caption components, BGM/SFX audio mixing layer, SRT burn-in caption track in FCPXML, headless Resolve Engine via Python API (vs `fuscript.exe`), Linux/Mac Resolve support, bilingual ASR auto-arbitration (D4 extension), Hyperframes Studio iframe embedding.

---

## Open Questions (non-blocking)

### §OQ1. License of `course-video-manager` Lua scripts

The repo has no `LICENSE` file at clone time. Adopting Lua scripts verbatim is technically a copyright question. Pre-merge mitigation:

- File a GitHub issue on `mattpocock/course-video-manager` asking for license clarification + permission, **before PR-D merge**.
- Fallback if no response within 1 week: rewrite Lua scripts ourselves using Resolve's public scripting API docs, treating Matt's scripts as reference. Time cost: +1-2d on PR-D.
- Document outcome in PR-D commit message.

### §OQ2. `fuscript.exe` discovery on Windows

Resolve Studio installs `fuscript.exe` at predictable paths but Resolve Free / Resolve via Mac App Store have different locations. Phase 2 ships with:

1. `RESOLVE_FUSCRIPT_PATH` env var override (highest priority)
2. Default Windows Studio path probe
3. Helpful error message with discovery instructions

No fallback to "auto-install Resolve"; require user to have it.

### §OQ3. Resolve API stability across versions

Resolve major version bumps (18 → 19 → 20) have historically broken scripting in subtle ways. Phase 2 ships pinned to Resolve 19.x (修修's current). If Matt's Lua scripts target an older version, port may need touch-up. Test surface: PR-E + PR-F Windows fixture must run on 修修's actual Resolve install before merge.

### §OQ4. Tool-call agent model choice

D3 `replan_agent.py` defaults to Claude Sonnet 4.6 via `shared/llm_router.py`. Matt's `ToolLoopAgent` uses Vercel AI SDK abstractions over Anthropic; we use Anthropic SDK directly. No abstraction layer; revisit if we ever need to swap model families mid-conversation.

### §OQ5. Resolve driver vs FCPXML — which becomes default?

ADR-038 ships FCPXML as default (`--final-via=fcpxml`) for backward compat. After 4-8 weeks of dogfood, evaluate switching default to `resolve-driver` if (a) reliability is high, (b) 修修 finds the workflow strictly better. Decision recorded in a follow-up ADR-038-amend, not pre-committed.

---

## Consequences

### Immediate impact

1. **New runtime dependency**: DaVinci Resolve Studio (修修 already has) + `fuscript.exe` accessible at known path.
2. **New Python deps**: none — pure stdlib + existing `anthropic` SDK.
3. **New Lua files**: 6 scripts under `agents/foundry/render_workers/resolve/*.lua`.
4. **New CLI surface**: `drive-resolve`, `replan-beat`, `add-captions`, `diff` subcommands.
5. **New Bridge endpoint**: `POST /foundry/<ep-id>/replan-beat/<beat_id>` consuming D3.
6. **No change to**: Phase 1 invariants from ADR-032 §不變項, ADR-001 agent table, ADR-027 Brook scope, ADR-014 RepurposeEngine contract.

### Impact on existing ADRs

| ADR | Impact |
|---|---|
| **ADR-032** | Phase 2 backlog section superseded by this ADR. Phase 1 invariants + acceptance criteria unchanged. |
| **ADR-014** | None — Brook still invokes Foundry as subprocess. |
| **ADR-027** | None — Foundry stays independent. |
| **ADR-001** | None — agent table unchanged. |
| **ADR-035** | None — Robin YouTube auth path unaffected. |
| **ADR-033/036/037** | None — thumbnail track is Codex-owned and orthogonal. |

### Risks

| Risk | Probability | Mitigation |
|---|---|---|
| Resolve API breaks in v20+ → Lua scripts need rewrite | Med | Pin tested Resolve version in README; provide error message diagnostic when API call fails |
| `fuscript.exe` path discovery fails for some installs | Med | `RESOLVE_FUSCRIPT_PATH` env override + clear error |
| Lua scripts ported from Matt have unstated assumptions about Resolve session state (active timeline, project name) | High | PR-D unit tests run against headless Resolve fixture; PR-E acceptance is on 修修's actual machine |
| Export hash collision (16-char SHA prefix) | Very low | 2^64 space; if it ever happens, bump to 24 chars |
| `EXPORT_VERSION` bump forgotten when encode params change → stale renders cached | Med | Document discipline in `agents/foundry/README.md`; add CI check that `shared/foundry_versions.py` changed when `hyperframes_worker.py` or `fcpxml_emitter.py` did |
| D3 replan_agent runs unbounded tool loop, costs blow up | Low | Hard max 5 iterations + total token budget check before each tool call |
| License question (§OQ1) blocks PR-D merge | Med | Fallback path documented; +1-2d cost if invoked |
| 修修 doesn't actually dogfood D1 (sticks with FCPXML import) → D1 ROI zero | Med | Acceptance gate requires real dogfood signoff before §OQ5 default-switch consideration |

### What stays unchanged

- `agents/foundry/` directory location and CLI surface (additive only)
- `data/script_video/<ep>/` filesystem layout (additive: `out/b_roll_<hash>.mp4` alongside legacy `b_roll_<beat_id>.mp4` for migration period)
- `storyboard.yaml` core schema (additive `status.cached_hash` field only)
- Phase 1 invariants from ADR-032
- All Vault write rules from CLAUDE.md / VAULT-LAYOUT.md (Foundry doesn't write Vault)

---

## References

- [`docs/research/2026-05-28-course-video-manager-borrowings.md`](../research/2026-05-28-course-video-manager-borrowings.md) — *to be created with this ADR PR* — full borrowing report from agent research
- [ADR-032](ADR-032-hyperframes-broll-pipeline.md) — Phase 1 base
- [ADR-014](ADR-014-repurpose-engine-plugin-interface.md) — RepurposeEngine plugin (no change)
- [ADR-027](ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md) — Brook narrow (no change)
- [`memory/claude/project_foundry_phase1_complete.md`](../../memory/claude/project_foundry_phase1_complete.md) — Phase 1 ship record
- [`mattpocock/course-video-manager`](https://github.com/mattpocock/course-video-manager) — borrowing source repo
- DaVinci Resolve Developer Documentation — Scripting API reference (bundled with Resolve install at `<install>/Developer/Scripting/README.txt`)

---

## Panel Integration

**Not yet panel-reviewed.** This is v1 draft. Multi-agent panel (Claude → Codex GPT-5 → Gemini 2.5 Pro → integration matrix) to follow before status moves from Draft to Accepted. Panel should specifically stress-test:

1. D1 — Is the Lua-via-subprocess pattern actually robust, or is there a cleaner Python-native Resolve API path we should use instead?
2. D2 — Is 16-char SHA prefix enough? Are minimal beat fields really the right hash inputs (does layout YAML content need to be in the hash)?
3. D3 — Is the 6-op tool surface complete? What edit ops are we missing that 修修 will actually want?
4. PR slicing — Is PR-D (3d) realistic for Lua port + driver + tests, or optimistic?
5. §OQ1 license question — is the GitHub-issue + rewrite-fallback path sound, or should we rewrite from scratch upfront?

Panel verbatim audits land at `docs/research/2026-05-28-{codex,gemini}-adr038-audit.md` when run.
