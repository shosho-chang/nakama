# ADR-038: Foundry Phase 2 — Resolve Python Driver + Borrowings from `course-video-manager`

**Date:** 2026-05-28
**Status:** Accepted v2 (post 3-way panel review 2026-05-28; 修修 sign-off 2026-05-28)
**Owner:** 修修
**Related:** [ADR-032](ADR-032-hyperframes-broll-pipeline.md) (Phase 1 base, partially superseded) · [ADR-014](ADR-014-repurpose-engine-plugin-interface.md) · [ADR-027](ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md)

> **v1 → v2 change log** — v1 went through a 3-way panel (Codex GPT-5 + Gemini 2.5 Pro, 2026-05-28). Both reviewers converged on substantive direction; Gemini rejected v1, Codex approved with mods. Panel verbatim audits at:
> - [`docs/research/2026-05-28-codex-adr038-audit.md`](../research/2026-05-28-codex-adr038-audit.md)
> - [`docs/research/2026-05-28-gemini-adr038-audit.md`](../research/2026-05-28-gemini-adr038-audit.md)
> - [`docs/research/2026-05-28-adr038-panel-integration-matrix.md`](../research/2026-05-28-adr038-panel-integration-matrix.md)
> - [`docs/research/2026-05-28-course-video-manager-borrowings.md`](../research/2026-05-28-course-video-manager-borrowings.md) — *missing in v1; required by panel*
>
> **Top v2 deltas (15 changes adopted, 1 deferred):**
>
> 1. **D1 inverted** — `DaVinciResolveScript` Python module is now PRIMARY; `fuscript.exe` removed entirely. Panel: Python-native is strictly superior (latency, error handling, data marshalling, session state, explicit project targeting). v1's Lua-first ordering was "letting Matt's borrowed solution dictate architecture instead of choosing best-fit for Python codebase".
> 2. **§OQ1 rewritten** — No verbatim Lua port from unlicensed repo. D1 implementation is clean-room Python from Blackmagic's official Resolve scripting docs. License question dissolves; better engineering outcome anyway.
> 3. **D2 hash inputs extended** — minimal beat fields NOT enough. Hash inputs now include digest of `layouts/*.yaml` content + composition HTML content. Silent stale-render-on-layout-edit catastrophe averted.
> 4. **D3 redesigned around quote-based semantic anchors** — `shift_anchor(direction, char_count)` is "guaranteed to fail" in Mandarin per Gemini (LLMs can't count CJK chars reliably; full/half-width punctuation; NFC/NFD). Replaced with `replace_quote(beat_id, old_quote, new_quote)`. Tool surface expanded from 6 → 10 ops to cover missing edits.
> 5. **Phase 1 acceptance gate enforced** — v1 "Phase 2 work and dogfood can proceed in parallel" was premature commitment. PR-D/E/F now hard-gated on ADR-032 Phase 1 acceptance closure (修修 real 10-15min episode end-to-end). PR-A/B/C utilities can proceed.
> 6. **PR-D estimate raised** 3d → 5-7d. Panel found 3d optimistic for Python-native driver + Windows path discovery + subprocess lifecycle + tests.
> 7. **Token cost math corrected** — v1 "$0.20 per re-plan" was off; 300k input × $3/MTok ≈ $0.90 input alone. Reframed comparison to per-successful-outcome cost.
> 8. **Driver abstraction** — `ResolveDriver` is a `Protocol` + concrete `PythonResolveDriver` + `MockResolveDriver` for tests.
> 9. **Project-targeted operations** — Resolve driver MUST explicitly target project by name via `projectManager.LoadProject(name)`, not "whichever project is active" (catastrophic data corruption risk per Gemini).
> 10. **D6 syntax** changed `[N]` → `[beat:N]` to avoid CJK comma ambiguity (`[12]` vs `[1,2]`).
> 11. **BCP 47 language tags** in frontmatter — `zh-Hant-TW`, `zh-Hans-CN`, `ja-JP`, `en` rather than v1 `zh-TW / en / bilingual` enum.
> 12. **D4 Resolve ASR Mandarin** spike required before committing English-only design — Resolve 18.5+ supports zh; 修修-side 15min spike test before D4 design freezes.
> 13. **D7 reframed** as edit_log enricher only, not "multi-episode history view" enabler (LCS alone insufficient for history UI).
> 14. **Test strategy rewritten** — Resolve has no true headless CI mode. PR tests: unit + mock driver in CI; integration tests on 修修's machine, manual or scheduled. Headless fixture claim removed.
> 15. **Sandcastle eligibility clarified** — no Lua runtime needed (D1 is Python now); PR-D fully sandcastle-eligible.
> 16. **NOT adopted (deferred to Phase 2.5)**: two-tiered D3 agent (Gemini-unique) — interesting but adds scope; ship single-tier first, dogfood, then revisit.
>
> Also: **Phase 1.5 dual-path backlog** (Reader-Playwright + Web-Playwright workers) explicitly acknowledged as still-owed. Not blocking Phase 2 but called out so it doesn't silently drift.

---

## Context

### Phase 1 status (ADR-032)

Phase 1 shipped 2026-05-26 across PRs #717 / #720 / #723 / #719 / #724 / #726. Pipeline `python -m agents.foundry --episode <id> {plan|render|emit|run}` is live; DaVinci import smoke ✅ on 2026-05-26; Hyperframes visual determinism SSIM 0.99977; LINE Seed TW @font-face shipped; Bridge UI Tier 2 at `/foundry/<ep-id>` functional.

**Phase 1 acceptance gap that remains open**: the functional criterion "修修真實 10-15min episode end-to-end with 3-5 BigStat beats → .fcpxml + .mp4" has not been satisfied. v2 makes this a **hard gate** for PR-D/E/F merge (the Resolve work). PR-A/B/C utilities can proceed without blocking on it.

### Why a Phase 2 ADR now

1. **Codex now owns the thumbnail track** (ADR-033 → 036 → 037), so Foundry can re-focus on video production proper.
2. **`course-video-manager` reverse-engineering** (2026-05-28; see `docs/research/2026-05-28-course-video-manager-borrowings.md`) surfaced 6 concrete patterns. After panel review, the BORROWING is the *idea* and the *Blackmagic API recipes*, not the *code* — clean-room implementation from official Resolve docs.

### Sibling-ADR landscape

- **ADR-032** — Phase 1 base. ADR-038 supersedes Phase 2 backlog list. Phase 1 invariants in ADR-032 §不變項 carry over unchanged.
- **ADR-014** — RepurposeEngine plugin interface. No change.
- **ADR-027** — Brook narrow. ADR-038 does NOT re-host Foundry under Brook (see §0).

### Out-of-domain context

修修 cadence: YouTube irregular, Podcast ~1/week. DaVinci Resolve Studio installed. Mandarin-first (FunASR Paraformer-zh upstream); bilingual content occasionally.

### Phase 1.5 backlog acknowledged (not Phase 2 blocker)

ADR-032 §Phase 1.5 listed Reader-Playwright + Web-Playwright workers as the next sibling backlog. Still TODO (`agents/foundry/render_workers/{reader,web}_playwright_worker.py` raise `NotImplementedError`). ADR-038 does NOT close them; Phase 2 is orthogonal. If real-episode dogfood reveals Reader/Web b-roll friction > Resolve manual import friction, Phase 2 priorities can shift before PR-D.

---

## §0. Agent boundary (not changing)

Foundry remains an independent agent under `agents/foundry/`. Settled in ADR-032 §0.1. 2026-05-28 re-examination: rename touches only ADR-001 table — no code/CLI/route/test changes. Cost > value. **No agent-boundary change.**

---

## Decision

7 numbered decisions below are commit-grade. Pattern: **Decision** → **Borrowing source** → **Rationale** → **Implication**.

### D1. Resolve Python Driver via `DaVinciResolveScript` Module (v2 inverted)

**Decision** — Foundry's Resolve driver uses the official `DaVinciResolveScript` Python module bundled with DaVinci Resolve Studio installs. Python wrapper at `agents/foundry/drivers/resolve.py`. **No Lua scripts; no `fuscript.exe`; no env-var IPC.** Driver behind a `ResolveDriver` Protocol with concrete `PythonResolveDriver` impl + `MockResolveDriver` for tests.

**Borrowing source** — **Blackmagic Design official Resolve Scripting docs** (`<Resolve install>/Developer/Scripting/README.txt` + bundled Python examples). `course-video-manager`'s Lua scripts serve as **inspiration only** — high-level recipes (create timeline, append clips, render queue, export subtitles) are in Blackmagic's docs; we implement clean-room in Python.

**Rationale (v2 inverted)** —

Panel converged: Python-native is strictly superior to `fuscript.exe`-subprocess across every axis:

1. **Latency / session reuse** — Persistent connection vs process-spawn-per-command. Critical for assembling a 25-beat timeline.
2. **Error handling** — Python exceptions with rich state; not "rc=1 + stderr-blob parse".
3. **Data marshalling** — Native Python objects (timelines, clips, projects) vs string delimiter serialization (`:::`, `___`) prone to quoting bugs with Unicode + spaces.
4. **Session state / project targeting** — `projectManager.LoadProject(name)` explicit; fuscript blindly hits whichever project is active = catastrophic data-corruption risk if 修修 has wrong project open.
5. **"Resolve is closed" handling** — Python module can launch Resolve and connect; fuscript fails cryptically.
6. **License** — Implementing from Blackmagic's documented API is unambiguously fine. Verbatim-porting unlicensed third-party Lua is not.
7. **Sandcastle / CI** — Python `Protocol` + `MockResolveDriver` testable; Lua-mock claim was weak.
8. **Forward compatibility** — Resolve 19/20 introduced API churn; clean Python against current docs > patching legacy Lua copied from a different project.

**Implication** —

- New module `agents/foundry/drivers/resolve.py` (~400 LOC; raised from v1 250):
  - `class ResolveDriver(Protocol)` — `connect() / load_project(name) / create_timeline(name) / append_clips(spec) / add_to_render_queue(preset) / start_render(blocking) / export_subtitles(track_id, path) / disconnect()`
  - `class PythonResolveDriver` — concrete impl using `DaVinciResolveScript`
  - `class MockResolveDriver` — records calls, asserts spec contracts
- Module discovery: `DaVinciResolveScript` lives at OS-specific paths (Windows: `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules\`). Driver does `sys.path.insert(0, ...)` then `import DaVinciResolveScript`. `RESOLVE_SCRIPT_API` env var can override.
- `pipeline.py` gains `drive-resolve`:
  ```
  python -m agents.foundry --episode <id> drive-resolve [--project <name>] [--render-now] [--preset <name>]
  ```
  `--project` defaults to `<episode_id>`; required if 修修 has multiple projects open.
- `run` subcommand grows `--final-via {fcpxml,resolve-driver}` (default `fcpxml` for backward compat).
- **Windows-only** at first. Linux/Mac out of Phase 2 scope.
- **NO Lua scripts in `agents/foundry/`**.

**Alternatives considered**:
- (rejected) `fuscript.exe` + Lua scripts (v1 design) — see panel rationale above
- (rejected) ffmpeg-only `--direct-mp4` skipping Resolve — loses grade value
- (rejected) FCPXML-only Phase 2 — leaves manual-import friction; doesn't unlock Resolve render queue / ASR
- (deferred Phase 2.5) Resolve Cloud rendering — Studio dongle considerations

### D2. Content-Addressed Export Hash + `EXPORT_VERSION` (v2 hash inputs extended)

**Decision** — Render outputs content-addressed: `out/b_roll_<sha256[:16]>.mp4`. Hash inputs:

1. `EXPORT_VERSION` constant
2. Sorted minimal beat fields (`broll_decision, layout, broll.component, broll.params, broll.render_target`)
3. **Content digest of referenced layout YAML file** (`layouts/full_broll.yaml` SHA256[:8])
4. **Content digest of composition HTML used by the worker** (`video/compositions/bigstat/index.html` SHA256[:8])
5. **Content digest of guardrails.yaml** when worker consults it

Re-plan checks hash before re-rendering. `EXPORT_VERSION` bump invalidates all caches.

**Borrowing source** — `course-video-manager` `app/services/export-hash.ts` (concept; ~79 LOC). Clean-room Python.

**Rationale** — Phase 1 re-plan triggers full re-render (~4min GPU wasted per re-plan, 25 beats × 10s). Content-addressed hashes make re-plan O(changed beats).

**Why expanded inputs** — Panel (both): minimal beat fields miss dimensions affecting visual output. Editing `layouts/full_broll.yaml` (font size, color) or composition HTML keeps hash same → silent stale render. Catastrophic.

**Implication**:

- New module `shared/foundry_versions.py`:
  ```python
  EXPORT_VERSION = 1  # bump invalidates all rendered b-roll mp4s
  FCPXML_SCHEMA_VERSION = 1  # bump invalidates emitted FCPXML
  ```
- New module `agents/foundry/export_hash.py` (~120 LOC; raised from v1 80):
  - `compute_beat_hash(beat: dict, ctx: HashContext) -> str`
  - `HashContext` resolves layout/composition/guardrails paths and reads digests
- `render_dispatcher.run_queue` checks `(out_dir / f"b_roll_{hash}.mp4").exists()` before dispatching
- `storyboard.yaml`: `status.cached_hash: Optional[str]`
- `pipeline.py render --no-cache` flag
- **PR-A scope expanded (Codex finding)**: `fcpxml_emitter.py:108,209` + `hyperframes_worker.py:72` all hardcode `b_roll_{beat['beat_id']}.mp4`; PR-A changes all 3 files + tests + README together
- Migration: legacy `b_roll_<beat_id>.mp4` remain readable; new renders hash-named. `EXPORT_VERSION` bump forces migration.

### D3. LLM Tool-Call Edit Pattern — Semantic Quote Anchors, 10+ Ops (v2 redesigned)

**Decision** — Single-beat re-plan via tool-call agent. Tools operate on **semantic quote anchors**, not character offsets. Surface (11 ops):

1. `replace_quote(beat_id, old_quote, new_quote)` — replace exact substring; must match `start_quote` or `end_quote`
2. `set_broll(beat_id, component, params)` — change component + params
3. `set_layout(beat_id, layout)` — switch layout
4. `set_transition(beat_id, in_transition, out_transition)`
5. `patch_broll_params(beat_id, partial_params)` — merge into existing
6. `set_timing(beat_id, start_seconds, duration_seconds)` — manual override
7. `mark_aroll(beat_id)` — `broll_decision: cutaway → aroll_only`
8. `split_beat(beat_id, at_quote)` — split at quote position
9. `merge_beats(beat_id_a, beat_id_b)`
10. `duplicate_broll_from(target_beat_id, source_beat_id)`
11. `restore_previous_render(beat_id, run_id)` — rollback (only meaningful with D2 hash history)

**Borrowing source** — `course-video-manager` `app/features/article-writer/document-editing-engine.ts` (concept). Pure-functional engine; LLM emits tool calls.

**Rationale (v2 redesigned)** —

**Why quote anchors not char offsets** — Panel (both): char counts "guaranteed to fail" in Mandarin. LLMs cannot count CJK chars reliably; full/half-width punctuation ambiguous; NFC vs NFD. Quote anchors mirror beat_aligner's str.find pattern — proven robust.

**Why 10+ ops not 6** — Panel (Codex): v1's 6 missed `set_layout`, `set_transition`, `patch_broll_params`, `set_timing`, `duplicate_broll_from`, `restore_previous_render`. Real edits need these.

**Cost reframing** — v1 "$0.02 vs $0.20" math was wrong; full re-plan ~$0.90 input alone (300k tokens × $3/MTok). Right metric is **cost per successful outcome**, not per call. $0.02 op-call failing 3× costs more than $0.90 succeeding once. Tool calls still preferred (bounded scope + deterministic engine ⇒ higher per-call success rate); savings real but not 10× v1 implied.

**Implication**:

- New module `agents/foundry/beat_editor.py` (~280 LOC; raised from v1 200):
  - `BeatEdit` Pydantic discriminated union of 11 ops
  - `apply_edits(storyboard, edits) -> storyboard` pure function
  - Re-runs `beat_aligner.align_beat` on edited beats only
  - Preserves `status.*_approved` flags; `render_status` resets via D2 hash mismatch
- New module `agents/foundry/replan_agent.py` (~200 LOC; raised from v1 150):
  - Anthropic SDK + 11 tool defs matching `BeatEdit`
  - Tool-loop max 5 iterations; token budget cap
- `pipeline.py replan-beat <beat_id> --note "<text>"`
- Bridge UI Re-plan-with-note replaces current placeholder (`thousand_sunny/routers/foundry.py:302-336` per Codex P10 confirmation — currently only clears `render_status`)
- `edit_log/` gains `edit_ops: list[BeatEdit]` field

**Alternatives considered**:
- (rejected) char-offset ops (v1 design)
- (rejected) free-form LLM diff parsing
- (deferred Phase 2.5) **Two-tiered agent** (Gemini-unique) — style chooser + ops translator. Interesting; ship single-tier first; dogfood whether intent captured.

### D4. Resolve Built-In ASR — Spike First, Then Decide (v2 amended)

**Decision** — Before committing English-only design from v1, run 15-min spike test on 修修's existing Mandarin audio: feed `raw_recording.mp4` to Resolve `CreateSubtitlesFromAudio` set to zh. Compare to FunASR/`/transcribe` baseline. Decide on actual data.

Three outcomes:
1. **Resolve zh comparable to FunASR** → D4 multi-language; `/transcribe` becomes optional second opinion
2. **Resolve zh materially worse** → D4 stays English-only (v1 design)
3. **Mixed (good clean / bad noisy)** → D4 condition-aware

**Borrowing source** — `course-video-manager` `resources/resolve/add-subtitles.lua` (concept). Clean-room Python via `Track.CreateSubtitlesFromAudio`.

**Rationale (v2 amended)** — Panel (Gemini): "English-only" was outdated; Resolve 18.5+ supports zh. Dismissing without data is sloppy. 15-min spike is cheap.

**Implication**:

- Pre-PR-F task: 修修 + Claude run spike
- Spike output: `docs/research/2026-05-{tbd}-resolve-asr-mandarin-spike.md`
- PR-F design adjusts on spike result
- Frontmatter `lang` uses **BCP 47** (`zh-Hant-TW`, `zh-Hans-CN`, `ja-JP`, `en`) — not v1's `zh-TW / en / bilingual` enum

### D5. `silencedetect` Parser for Beat Hints (unchanged)

**Decision** — Add `agents/foundry/silence_detection.py` — pure Python parser for `ffmpeg -af silencedetect` stderr → `(speaking_start, speaking_end)` spans. `python -m agents.foundry --episode <id> hint-beats` produces `storyboard_hints.yaml` (planner consumes as prior).

**Borrowing source** — `course-video-manager` `app/services/silence-detection.ts` (~143 LOC concept). Clean-room Python.

**Rationale, Implication, Alternatives** — Same as v1; no panel push-back. Opt-in via `--hint-beats` flag.

### D6. `[beat:N]` Clip-Index Anchors (v2 syntax change)

**Decision** — In re-plan flow (D3), beats render to LLM with `[beat:N]` index prefixes (changed from v1 `[N]`). Tool calls accept `beat_id="[beat:7]"` or canonical UUID.

**Borrowing source** — `course-video-manager` `app/lib/transcript-builder.ts:127` (concept).

**Rationale (v2 amended)** — Panel (Gemini): bare `[N]` ambiguous in CJK contexts where `，` is enumerator. `[12]` vs `[1, 2]` confusion would silently apply edits to wrong beats. `[beat:N]` unambiguous.

**Implication** — `beat_editor.py` accepts both UUID and `[beat:N]`. `replan_agent.py` prompt uses `[beat:N]`. No on-disk schema change.

### D7. LCS Storyboard Diff — Edit Log Enricher Only (v2 reframed)

**Decision** — Add `agents/foundry/storyboard_diff.py` — Myers-style LCS, `+/-/keep` line list per beat. Used by:
- `edit_log/` entries (record actual delta per re-plan, not just note text)
- `python -m agents.foundry --episode <id> diff <a.yaml> <b.yaml>` CLI

**v2: NO claim about enabling "multi-episode history UI"** — per Gemini, LCS alone insufficient; needs who/when/note/version context. D7 is useful primitive for richer edit_log but doesn't move needle on history view.

**Borrowing source** — `course-video-manager` `app/lib/changelog-diff.ts` (~94 LOC concept). Clean-room.

**Rationale, Implication, Alternatives** — Same as v1.

---

## §Borrowings Not Adopted in Phase 2

GPU/CPU semaphore (Phase 1.5), `evalite` (Phase 2.5), SSE polling replacement (Tier 3 UI), thumbnail compositor (Codex-owned), Stream Deck Forwarder (niche), Drizzle schema (YAML+FS instead), OBS live recording / Course-Lesson schema / Dropbox publish chain (domain/vendor mismatch).

---

## Phase 2 PR slicing (v2 revised)

| PR | Scope | Estimate | Phase 1 gate? | Depends on |
|---|---|---|---|---|
| **PR-A** | D2 export hash + `EXPORT_VERSION` + cache-skip + **fcpxml_emitter + hyperframes_worker filename rename** + `storyboard.yaml.cached_hash` + tests | **2d** (v1: 1.5d) | No | — |
| **PR-B** | D7 LCS storyboard diff + edit_log enrichment | 1d | No | — |
| **PR-C** | D5 `silencedetect` parser + `--hint-beats` flag | 1d | No | — |
| **PR-D** | D1 Python Resolve driver (Protocol + PythonResolveDriver + MockResolveDriver + `drive-resolve` subcommand, no render-queue) | **5-7d** (v1: 3d) | **YES** | — |
| **PR-E** | D1 render queue + `--render-now` + path discovery + project-targeted ops + 修修-machine integration test | **2d** (v1: 1.5d) | **YES** | PR-D |
| **PR-F** | D4 Resolve ASR — 1d spike + 1-2d implementation if spike OK + BCP 47 lang field | 2-3d | **YES** | PR-D, spike result |
| **PR-G** | D3 + D6 beat_editor (11 ops, semantic anchors) + replan_agent + `replan-beat` subcommand + Bridge endpoint | **4d** (v1: 3.5d) | No | PR-A (hash), PR-B (edit_log) |

**Total**: ~16-19d (v1: 12.5d). Expansion reflects panel-required scope corrections.

**Sandcastle eligibility**: PR-A/B/C/D/G fully eligible (pure Python + MockResolveDriver). PR-E/F require 修修's local machine with Resolve Studio.

**Phase 1 acceptance gate** — PR-D/E/F do NOT merge until ADR-032 Phase 1 acceptance closes (one real 10-15min episode end-to-end through FCPXML path). PR-A/B/C/G are pure utilities; no gate. If real-episode reveals Reader/Web Playwright friction > Resolve manual import friction, PR-D/E/F priorities can shift before merge.

---

## Acceptance Criteria

### Phase 1 closure gate (carried over from ADR-032)

- [ ] 修修真實 10-15min episode end-to-end produces `.fcpxml` + individual `.mp4`s without intervention. **PR-D/E/F blocked on this.**

### Functional (Phase 2)

- [ ] `python -m agents.foundry --episode <id> drive-resolve --project <name>` loads named project, creates timeline, appends V1 raw_recording + V2 b-roll clips at storyboard timing. (D1)
- [ ] Driver throws explicit error if `<project>` not found; no "active project" fallback. (D1)
- [ ] `MockResolveDriver` test suite green — records call sequence, asserts spec invariants. (D1)
- [ ] `drive-resolve --render-now` queues render, produces `out/episode_resolve_rendered.mp4` within 5min for 12min episode. (D1)
- [ ] `replan-beat 7 --note "make it punchier"` modifies only beat 7; edit_log contains `edit_ops` with quote-based anchors. (D3)
- [ ] Second `render` invocation on unchanged storyboard logs `skipped (cache hit)` for all cutaway beats. (D2)
- [ ] Editing `layouts/full_broll.yaml` invalidates cache for beats using that layout. (D2)
- [ ] Bumping `EXPORT_VERSION` invalidates all caches. (D2)

### Determinism / Visual

- [ ] Resolve driver: same `storyboard.yaml` driven twice produces structurally identical timeline (clip count, track count, in/out by frame).
- [ ] D3 replan-beat deterministic given same beat + same note + same model.

### Compatibility

- [ ] Phase 1 fixture passes `run` with default `--final-via=fcpxml`
- [ ] DaVinci import smoke test unaffected
- [ ] Hyperframes SSIM ≥ 0.99 regression still passes

### Code quality

- [ ] `pytest tests/foundry/ tests/test_export_hash.py tests/test_storyboard_diff.py tests/test_beat_editor.py tests/test_resolve_driver_mock.py tests/test_silence_detection.py -x` green
- [ ] `ruff check + ruff format --check` clean

### Panel-required (v2 new)

- [ ] No Lua scripts in repo (clean-room only)
- [x] §OQ1 attribution resolved (skipped per 修修, 2026-05-28)
- [x] Borrowings research artifact committed at `docs/research/2026-05-28-course-video-manager-borrowings.md`
- [ ] D4 spike result committed before PR-F design freeze

---

## Out of scope

- **Phase 2.5**: SSE Bridge UI, Tier 3 inline `<video>`, evalite, examples retrieval, multi-episode listing UI, GPU semaphore, **two-tiered D3 agent** (deferred), `--direct-mp4` ffmpeg-only path, Reader/Web Playwright workers
- **Phase 3**: DataChart / Map / Caption components, BGM/SFX audio, SRT burn-in, Linux/Mac Resolve, bilingual ASR auto-arbitration, Hyperframes Studio iframe

---

## Open Questions

### §OQ1. Attribution to `course-video-manager` (v2 reframed; resolved)

**No code lifted from Matt's repo.** Implementation is clean-room from Blackmagic docs. License question per se is moot.

**Courtesy GitHub issue to Matt: skipped** (修修 decision, 2026-05-28). No notification will be sent; the inspiration is cited in this ADR and in `docs/research/2026-05-28-course-video-manager-borrowings.md` as the project-internal record.

### §OQ2. `DaVinciResolveScript` discovery on Windows

1. `RESOLVE_SCRIPT_API` env override
2. Default Windows: `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules\`
3. Helpful error message with discovery instructions

### §OQ3. Resolve API stability across versions

Pin tested version in `agents/foundry/drivers/resolve.py` header. Version-probe at `connect()`; warn (not fail) if running against untested major version. Resolve 19.x is 修修's current.

### §OQ4. Tool-call agent model choice

D3 `replan_agent.py` uses `shared/llm_router.py` (Sonnet 4.6 default). Token budget cap; max 5 tool-loop iterations.

### §OQ5. Resolve driver vs FCPXML — default?

Phase 2 ships `--final-via=fcpxml` default. After 4-8 weeks of dogfood, evaluate switch. Follow-up ADR-038-amend, not pre-committed.

### §OQ6. D4 Mandarin ASR spike outcome

To be filled by spike result before PR-F design freezes. See D4.

---

## Consequences

### Immediate impact

1. **New runtime dep**: DaVinci Resolve Studio (修修 has) + `DaVinciResolveScript` module (bundled).
2. **New Python deps**: none.
3. **NO Lua files.** Pure Python.
4. **New CLI**: `drive-resolve`, `replan-beat`, `add-captions`, `diff`, `hint-beats`.
5. **New Bridge endpoint**: `POST /foundry/<ep-id>/replan-beat/<beat_id>`.
6. **`fcpxml_emitter.py` + `hyperframes_worker.py` filename change** (Codex P10): hardcoded `b_roll_{beat_id}.mp4` → hash-named. EXPORT_VERSION bump forces migration.
7. **No change to**: Phase 1 invariants, ADR-001 table, ADR-027 Brook scope, ADR-014 contract.

### Impact on existing ADRs

| ADR | Impact |
|---|---|
| **ADR-032** | Phase 2 backlog superseded. Phase 1 invariants + acceptance unchanged. **Phase 1.5 dual-path backlog explicitly still owed.** |
| **ADR-014/027/001/035/033/036/037** | None |

### Risks (v2 updated)

| Risk | Probability | Mitigation |
|---|---|---|
| Resolve API breaks in v20+ | Med | Pin tested version; warn on untested |
| `DaVinciResolveScript` module not at default path | Low | `RESOLVE_SCRIPT_API` env override |
| Resolve session-state assumptions (wrong project active) | Low (was High in v1) | Explicit `LoadProject(name)` — eliminated v1's risk |
| Export hash inputs incomplete → stale renders | Low (was Med in v1) | Layout YAML + composition HTML digests included |
| D3 quote anchors fail on minor formatting drift | Med | str.find primary + AnchorNotFoundError hard fail (mirrors beat_aligner) |
| `EXPORT_VERSION` bump forgotten | Med | CI check: `shared/foundry_versions.py` changed when worker/emitter did |
| D3 replan_agent unbounded tool loop | Low | Max 5 iter + token budget cap |
| 修修 doesn't dogfood D1 → ROI zero | Med (gated now) | **PR-D/E/F gated on Phase 1 acceptance**; reprioritization gate |
| Resolve = proprietary GUI dep lock-in | Med | `ResolveDriver` Protocol + `MockResolveDriver` keeps Foundry testable without Resolve |
| Resolve has no true headless CI | High | Accepted; CI = unit + mock; integration on 修修 machine manual/scheduled |
| D4 Mandarin spike unfavorable | Med | D4 falls back to v1 English-only |

### What stays unchanged

- `agents/foundry/` location + CLI (additive only)
- `data/script_video/<ep>/` layout (additive)
- `storyboard.yaml` core schema (additive `status.cached_hash` only)
- Phase 1 invariants
- Vault write rules (Foundry doesn't write vault)

---

## References

- [`docs/research/2026-05-28-course-video-manager-borrowings.md`](../research/2026-05-28-course-video-manager-borrowings.md) — **REQUIRED v2 deliverable**
- [`docs/research/2026-05-28-codex-adr038-audit.md`](../research/2026-05-28-codex-adr038-audit.md)
- [`docs/research/2026-05-28-gemini-adr038-audit.md`](../research/2026-05-28-gemini-adr038-audit.md)
- [`docs/research/2026-05-28-adr038-panel-integration-matrix.md`](../research/2026-05-28-adr038-panel-integration-matrix.md)
- [ADR-032](ADR-032-hyperframes-broll-pipeline.md), [ADR-014](ADR-014-repurpose-engine-plugin-interface.md), [ADR-027](ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md)
- [`memory/claude/project_foundry_phase1_complete.md`](../../memory/claude/project_foundry_phase1_complete.md)
- [`mattpocock/course-video-manager`](https://github.com/mattpocock/course-video-manager) — **inspiration source, no code lifted**
- DaVinci Resolve Developer Documentation — `<install>/Developer/Scripting/README.txt` (canonical API ref)

---

## Panel Integration (v2 new section)

3-way panel ran 2026-05-28. Claude draft v1 → Codex GPT-5 audit → Gemini 2.5 Pro audit → integration matrix → v2 rewrite.

**Verdict summary:**
- Claude v1: Approve (self-vote)
- Codex: Approve **with modifications**
- Gemini: **Reject** (rewrite D1, OQ1; fix D2, D3; add Phase 1 gate)

Both reviewers converged substantively. 21-row integration matrix at [`docs/research/2026-05-28-adr038-panel-integration-matrix.md`](../research/2026-05-28-adr038-panel-integration-matrix.md).

**Statistics**: 15 adopted (with code-grounded scope corrections) / 1 deferred (Phase 2.5 two-tiered agent) / 0 reject / 0 contradictions requiring user adjudication.

**Biggest learning** — v1's structural mistake was letting `course-video-manager` dictate architecture instead of choosing best-fit tools for a Python codebase. Once D1 flipped to Python-native, license question dissolved, sandcastle eligibility improved, PR-D scope clarified. Process lesson: **when borrowing from another codebase, separate the *idea* (always portable) from the *implementation choices* (only sometimes portable).**
