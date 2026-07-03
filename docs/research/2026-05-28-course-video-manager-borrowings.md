# `course-video-manager` Borrowings Report for Nakama Foundry

**Date:** 2026-05-28
**Source repo:** [`mattpocock/course-video-manager`](https://github.com/mattpocock/course-video-manager)
**Status:** Research artifact (referenced by ADR-038)
**Method:** Agent-based source inspection of cloned repo at `E:\tmp-cvm` (no longer present locally)

> **Note (v2):** ADR-038 v1 cited this file but did not commit it — panel (Codex P11) flagged. v2 ships this report alongside the ADR.

---

## 1. What the repo actually is

Matt Pocock's personal end-to-end **course authoring + publishing app** for AI Hero. React Router 7 single-tenant web app (Postgres + Drizzle + Effect-ts) running locally to: **record** lessons via OBS with live silence-detected clipping; **edit** the resulting clip list (a clip = a timestamped span of a source `.mp4`); **export** finished `.mp4`s either through ffmpeg directly or by driving **DaVinci Resolve via Lua + `fuscript.exe`**; **author** YouTube titles / descriptions / thumbnails / newsletters / articles via an in-app LLM agent; and **publish** by syncing a versioned snapshot to Dropbox and posting to Buffer via a Dropbox→Zapier→Buffer webhook chain.

**Not Remotion-based; no browser-rendered video.**

---

## 2. Architecture map

```
RECORD ──> EDIT ──> EXPORT ──> AUTHOR ──> PUBLISH
  │         │         │          │          │
  │         │         │          │          ├── publish-to-dropbox.ts (snapshot file tree)
  │         │         │          │          ├── youtube-upload-service.ts
  │         │         │          │          └── Dropbox→Zapier→Buffer
  │         │         │          │
  │         │         │          ├── text-writing-agent.ts (Vercel AI SDK ToolLoopAgent)
  │         │         │          ├── document-writing-agent.ts (article/newsletter editor)
  │         │         │          ├── prompts/generate-* (29 prompt files)
  │         │         │          └── thumbnail-editor/canvas-compositor.ts (1280×720 canvas)
  │         │         │
  │         │         ├── ffmpeg-commands.ts (silencedetect, h264_nvenc, loudnorm)
  │         │         ├── batch-export.server.ts (SSE-driven batch)
  │         │         ├── export-hash.ts (content-addressed cache: SHA256 → .mp4)
  │         │         ├── video-processing-service.ts → fuscript.exe → resources/resolve/*.lua
  │         │         └── video-concatenation-service.ts
  │         │
  │         ├── clip-state-reducer.ts (huge state machine)
  │         ├── lib/transcript-builder.ts (interleave clips + chapters → indexed transcript)
  │         ├── changelog-service.ts + changelog-diff.ts (LCS-based per-version diff)
  │         ├── video-warnings.ts (lint: missing opening chapter etc.)
  │         └── prompts/generate-chapters.ts (LLM auto-chaptering)
  │
  ├── OBS via obs-websocket-js
  ├── stream-deck-forwarder/ (HTTP→WS bridge for hotkey actions)
  └── silence-detection.ts (parses ffmpeg silencedetect stderr into clip boundaries)

DB: app/db/schema.ts (Drizzle, Postgres)
   courses → courseVersions → sections → lessons → videos → clips + chapters
```

---

## 3. Dependency stack

- **Runtime**: React Router 7.11, React 19.2, Node 20, `tsx`
- **Effect-ts ecosystem**: `effect@3.17`, `@effect/platform`, `@effect/cluster` — service layer with semaphores (`GPU_PERMITS=6`, `CPU_PERMITS=12`)
- **LLM**: Vercel AI SDK 6 (`ai`), `@ai-sdk/anthropic` 3, `openai@6.25`. New `ToolLoopAgent` abstraction.
- **DB**: Drizzle 0.44 + `postgres@3.4` + `better-sqlite3@11.6` + `@electric-sql/pglite` (testing)
- **Video**: ffmpeg shelled out (NVENC GPU), `obs-websocket-js@5`, **DaVinci Resolve via fuscript.exe + Lua**, Cloudinary
- **UI**: Radix UI, Tailwind 4, Monaco, TLDraw 5, Shiki, dnd-kit
- **Eval**: `evalite@1.0-beta` (sqlite-backed)
- **Test**: Vitest 3, `@effect/vitest`
- **No Remotion, no Playwright, no Whisper/ASR** (DaVinci's built-in `CreateSubtitlesFromAudio` instead)

---

## 4. Borrowable patterns — ranked

### HIGH (directly fills Foundry gap)

**H1. DaVinci Resolve integration via Lua + `fuscript.exe`** — `app/services/video-processing-service.ts:408-501` + `resources/resolve/{clip-and-append,export-timelines,create-timeline,add-subtitles,zoom-clip,add-gaussian-blur}.lua` (12-169 lines each). Matt's headline move: doesn't stop at FCPXML, programmatically drives Resolve itself.

> **v2 lesson learned**: Matt's choice of Lua-via-subprocess was right for his **TS app calling into Resolve**. For Nakama (Python app), the bundled `DaVinciResolveScript` Python module is strictly better. **Borrow the IDEA (drive Resolve, don't stop at FCPXML), reject the IMPLEMENTATION CHOICE (Lua-via-subprocess).** Panel-reviewed in ADR-038 v2.

**H2. Content-addressed export hash + global invalidation** — `app/services/export-hash.ts` (79 lines, self-contained). SHA256 of `{EXPORT_VERSION, sorted_clip_minimal_fields}` → 32-char hash → `{courseId}-{hash}.mp4`. Pure function + Effect that checks disk. Bumping `EXPORT_VERSION=1` constant kill-switches all caches.

> **v2 amendment**: Panel pointed out Matt's minimal-field hash is incomplete for Foundry's case. Hash must include layout YAML content digest + composition HTML digest to prevent silent stale renders. ADR-038 D2 expanded inputs.

**H3. FFmpeg `silencedetect` → clip boundaries pure parser** — `app/services/silence-detection.ts` (143 lines). `getClipsOfSpeakingFromFFmpeg(rawStderr, {startPadding, endPadding, fps})` is pure function. Trivial Python port. → ADR-038 D5.

**H4. Transcript building with interleaved chapters + `[N]` clip indices** — `app/lib/transcript-builder.ts:127` `buildTranscript(clips, chapters)` produces `{indexedClips, transcript: "...[3]...## Chapter\n\n[5]...", wordCount, sections}`. The trick: clips get inline `[N]` indices the LLM can reference; sections become `## headers`. → ADR-038 D6 (adapted to `[beat:N]` per panel to avoid CJK ambiguity).

**H5. LCS-based per-version content diff** — `changelog-diff.ts` full Myers-style LCS in 94 lines, output as `+/-/keep` lines with configurable context. Used by `changelog-service.ts` for per-CourseVersion change reports. → ADR-038 D7 (reframed as edit_log enricher per panel).

**H6. `ToolLoopAgent` + edit-tool pattern for LLM-assisted document editing** — `app/features/article-writer/document-editing-engine.ts` defines pure functions `applyEdits(doc, [{type:"replace", old_text, new_text}, {type:"insert_after", anchor, new_text}, {type:"rewrite", new_text}])`. Agent calls these as tools; engine pure and testable. → ADR-038 D3 (redesigned around quote-based semantic anchors for Mandarin; expanded to 11 ops).

### MEDIUM

**M1. Effect-ts GPU/CPU semaphores** — `ffmpeg-commands.ts:8-9` `GPU_PERMITS=6 / CPU_PERMITS=12`, wired via `gpuSemaphore.withPermits(1)(...)` around NVENC encodes. **Borrow the 6/12 ratio**; we use Python `asyncio.Semaphore`. → Phase 1.5 GPU semaphore backlog (not Phase 2 ADR-038 scope).

**M2. SSE-driven batch operations with progress streaming** — `api.courseVersions.$versionId.batch-export-sse.ts`. → Phase 2.5 (replace Bridge polling).

**M3. Stream Deck Forwarder** — 4-file mini-app (~150 LOC total) HTTP-on-5174 → WS-on-5172 bridge so a Stream Deck button can trigger editor actions. Awareness only.

**M4. Drizzle `previousVersionXId` lineage + Postgres CHECK biconditionals** — `app/db/schema.ts:80-130`. Useful pattern if Foundry adds multi-episode history with re-plan branches. Not portable as-is (Nakama is YAML+FS not Postgres) but informs design.

**M5. Thumbnail-editor canvas compositor + horizontal-position slider** — `thumbnail-editor/canvas-compositor.ts` 100-line client-side HTML5 canvas compositor for layered thumbnails. **Codex owns the thumbnail track**, flag for that window only.

**M6. `generate-chapters.ts` prompt + clip-id-anchored output** — `app/prompts/generate-chapters.ts:1-22`. LLM returns `[{beforeClipId, title}]` where `beforeClipId` is one of supplied stable IDs. Pattern: **make the LLM emit structured pointers to your data, not free text**. Foundry planner already uses exact-copy quote anchors which is the equivalent — confirmed alignment.

**M7. `evalite` for prompt regression** — `evalite.config.ts` + `evals/skill-building-text.eval.ts`. SQLite-backed eval runner with `evalite.each([{name:"Haiku 4.5"},{name:"Sonnet 4.5"}])`. → Phase 2.5 follow-up (not Phase 2 critical path).

### LOW (note only)

**L1. CLAUDE.md / CONTEXT.md domain glossary discipline** — 250-line `CONTEXT.md` of ubiquitous-language definitions with `_Avoid_:` aliases per term. Worth aspiring to for `agents/foundry/CONTEXT.md` someday.

**L2. Pitch / Pitch Status / Deliverables Calendar** — editorial-calendar lifecycle. Chopper/Bridge territory, not Foundry.

**L3. TLDraw-based diagram playground** — coding-tutorial-specific UX.

**L4. AI Hero + Kit newsletter + YouTube device-code auth** — vendor-specific.

---

## 5. NOT applicable (Matt's domain ≠ Nakama)

- **OBS-driven live recording + browser speech-detector** — Nakama starts from finished SRT
- **Optimistic clip / Recording Session state machine** — same reason
- **Course / Section / Lesson / CourseVersion immutable-snapshot schema** — Nakama is episode-based
- **Dropbox → Zapier → Buffer pipeline** — Nakama publishes to WP
- **AI Hero / Kit newsletter integration** — vendor-specific
- **TLDraw diagram playground** — coding-tutorial-specific
- **Pitches + Deliverables Calendar** — editorial layer
- **YouTube device-code OAuth** — Nakama Robin already has YouTube auth (ADR-035)

---

## 6. Surprises / things worth flagging

1. **No Remotion**. Matt explicitly chose ffmpeg + Resolve over a browser renderer. Foundry's choice of Hyperframes (HTML→mp4 via headless Chrome) is the **opposite** trade-off. Matt's setup gets NVENC speed + ProRes/4K at the cost of programmable HTML scenes.

2. **Resolve Lua is more capable than expected** — `clip-and-append.lua` (169 lines) does multi-track timeline assembly from env-var spec; `add-subtitles.lua` triggers Resolve's built-in ASR + SRT export; `zoom-clip.lua` does Ken Burns. **Serious alternative to FCPXML emission** for users who keep Resolve open.

3. **`EXPORT_VERSION` constant as global cache-buster** is a deceptively simple pattern. Foundry should add `EXPORT_VERSION` + `FCPXML_SCHEMA_VERSION` constants in `shared/foundry_versions.py` for the same reason.

4. **`Ghost Lesson` / `Materialize` concept** — DB rows that don't yet exist on disk, materialized lazily with cascade through ghost section + ghost course. Useful pattern if Foundry ever wants to "plan an episode" before SRT exists.

5. **`document-editing-engine.ts` is purely client-side and pure-functional** — LLM streams tool calls (`replace / insert_after / rewrite`), engine applies deterministically in-browser, same engine has unit tests. Cleaner separation than Foundry's current `edit_log` round-trips. Strong recommend lift for **single-beat re-plan LLM helper** (ADR-038 D3).

6. **No Whisper / FunASR in repo** — relies on Resolve's built-in caption generation. For Nakama's Mandarin pipeline this doesn't replace FunASR, but Resolve ASR worth spike-testing (ADR-038 D4).

7. **Sandcastle integrated as sibling tool** (`@ai-hero/sandcastle` dev dep, `pnpm sandcastle` script). Matt eats his own dog food. Nakama already defaults to Sandcastle.

8. **WSL2 → Windows UNC path translation hard-coded** in `video-processing-service.ts:417-421`. A clean port for Nakama parametrizes this — but moot for v2 since we're going Python-native and skipping the subprocess layer entirely.

---

## Bottom line

Highest leverage for Foundry is the **idea space**: drive Resolve directly (not stop at FCPXML), content-addressed render cache with global invalidation, tool-call edit pattern for sub-document LLM edits, silencedetect parser, LCS diff. 

**Lowest leverage (and v1 mistake)**: the **implementation choices** Matt made for his specific TS codebase — Lua via `fuscript.exe`, env-var IPC, unlicensed code lift. These don't port; Python-native is strictly better for our Python codebase.

ADR-038 v2 reflects this separation: borrow ideas + Blackmagic API recipes (always portable), reject Matt's implementation choices (Python-native instead).
