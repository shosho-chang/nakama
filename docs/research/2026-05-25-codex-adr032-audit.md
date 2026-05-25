codex
**1. Code Grounding**

The ADR-032 implementation claims are materially ahead of the repo.

`video/compositions/bigstat/` does not exist in `E:\nakama`. The `video/` subproject still contains only `src/parser/*`, tests, `remotion.config.ts`, and package files. `video/package.json:5` says “Hyperframes video subproject,” but dependencies are only TypeScript/Vitest at `video/package.json:15-19`; there is no Hyperframes package, no composition catalog fork, and no rendered 9s 1080p MP4 in-tree. The claim “BigStat … shipped today” is false for this checkout.

`web_highlight_record.py` is also not in the main tree. `git show --stat 269da20` for PR `#710` shows only memory files: `memory/claude/MEMORY.md`, `feedback_cdp_screencast_over_recordvideo.md`, and `project_broll_dual_path_architecture.md`. That PR merged a decision note, not the Reader/Web Playwright tool. The memory itself admits the implementation reference is a spike path, not main tree: `memory/claude/feedback_cdp_screencast_over_recordvideo.md:21` points to `E:\nakama-reader-record-spike\scripts\web_highlight_record.py`. So “already shipped in PR #710” is wrong if “shipped” means reusable repo implementation.

`agents/brook/script_video/` exists, but it is ADR-015 code, not ADR-032 code. `pipeline.py:63-70` still parses `script.md` through the TypeScript DSL and then calls quote/render stubs. `_stage1_whisperx` returns `[]` at `pipeline.py:139-146`; `_stage4_render_stub` is explicitly a stub at `pipeline.py:211-213`. There is no `srt_flattener.py`, `planner.py`, `beat_aligner.py`, `render_dispatcher.py`, `render_workers/`, `layouts/`, `prompts/`, `STYLE.md`, `guardrails.yaml`, `examples/`, or `edit_log/`.

The FCPXML emitter is real but minimal: it creates `fcpxml version="1.10"` at `fcpxml_emitter.py:103`, one A-roll asset at `117-138`, and only V1 `asset-clip` elements at `168-176`. There is no B-roll V2, no `adjust-transform`, and no layout YAML consumer.

`.claude/skills/transcribe/SKILL.md` does support the claimed upstream: it describes an Auphonic + FunASR + Opus + Gemini pipeline producing clean SRT at lines `4-7`, and output discovery says SRT is `<output_dir>/<audio_stem>.srt` at `209-211`.

**2. Drift Detection**

ADR-032 mostly preserves ADR-015’s durable invariants, but it overstates compatibility. ADR-015 establishes Brook hosting at `docs/decisions/ADR-015-script-driven-video-production.md:34`, FCPXML output at `123-125`, DaVinci taking final composite at `100-102`, and per-episode `data/script_video/<episode-id>/` at `71-84`. ADR-032 keeps those.

The real drift is Brook role framing. ADR-032 cites ADR-001 “Brook still Composer,” but ADR-001 has been amended: `ADR-001:30` says Brook is narrowed by ADR-027, and ADR-027 explicitly narrows Brook from Composer to “Scaffold + Repurpose + SEO Audit” at `docs/decisions/ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md:60-66`. This pipeline can still belong in Brook because it is Stage 5 production/repurpose after human-authored SRT, but ADR-032 must stop using old “Composer” language as if ADR-027 did not happen.

ADR-014 is genuinely orthogonal. Its shape is SRT to multi-channel text artifacts, with fan-out renderers and `/bridge/repurpose/<run_id>` at `ADR-014:10-20` and `96-98`. ADR-032’s sequential video timeline pipeline should not inherit RepurposeEngine.

The design-system claim is true. `docs/design-system.md:16-24` defines `--sho-*` as the single token namespace, `34-38` names PANTONE 165 PC and LINE Seed TW, and `107-119` lists the tokens. One drift: `memory/claude/project_broll_dual_path_architecture.md:37` still says `--sho-orange`; ADR-032 correctly says load `docs/design-system.md`, not memory.

**3. Numerical / Factual Claims**

The 9.5-day estimate is too low. PR-1 at 1 day is fine if it is pure scaffolding. PR-2 at 1.5 days is plausible only for ASCII/English; Chinese SRT normalization, punctuation removal, numeric normalization, char-time interpolation, and false-negative test fixtures make it closer to 2-3 days. PR-3 at 2 days is optimistic because it needs schema, prompt, LLM failure handling, deterministic fixture tests, and “do not rewrite anchor” guardrails. PR-4 at 2 days is wrong: dispatcher + Hyperframes worker + transparent B-roll format + FCPXML V2 + DaVinci import validation is 4-6 days. PR-5 at 3 days is also wrong; see §4.

“10-minute video → 15-25 beats” is defensible for educational B-roll, but only if beats are idea units, not sentences. At 10 minutes, 15 beats means one cutaway every 40 seconds; 25 means every 24 seconds. That is a reasonable first target for a talking-head health channel.

`rapidfuzz partial-ratio >= 0.85` is not a safe single threshold for Chinese anchors when the LLM is allowed to rewrite. It can work if anchors are exact substrings copied from flattened SRT, but ADR-032’s planner contract says `start_quote` / `end_quote`, while also letting the planner creatively group prose. If the LLM paraphrases “一萬一千人” as “11,000 人,” 0.85 will create false negatives unless number normalization is excellent. The correct fix is not “lower threshold”; it is “planner must copy exact anchors from supplied text, and the validator rejects anchors not found.”

Hyperframes render timing: “~5-15s per BigStat” is plausible for an 8-second 1080p clip. Hyperframes docs show a sample 240-frame render with capture in 8.2s and encode in 8.0s, but that is an example, not a guarantee. Hyperframes rendering is frame-by-frame and seek-driven, with Chrome and FFmpeg in the loop. ([hyperframes.app](https://hyperframes.app/docs/3-guides/4-rendering))

Concurrency=2 is a reasonable starting cap only if each Hyperframes job uses low worker counts. Hyperframes docs state each render worker launches a separate Chrome process, consumes CPU/RAM, and their own default concurrent render limit is 2 on 8-core machines where each render uses 2-3 workers. ([hyperframes.mintlify.app](https://hyperframes.mintlify.app/guides/rendering)) The ADR’s “10 min video × concurrency 2 ≈ 4 min queue time” is unsupported. For 25 beats at 15s each, perfect two-wide queue is already 187.5s before process startup, file IO, failed renders, Playwright captures, and UI review. For mixed Reader/Web CDP screencast, 4 minutes is best-case, not planning truth.

**4. Assumption Push-Back**

3-path dispatcher: this is fine architecturally, but wrong for Phase 1. Keep the schema field `render_target`, implement only `hyperframes` in Phase 1, and leave `reader-playwright` / `web-playwright` behind disabled workers until `web_highlight_record.py` is promoted from spike to repo. The visual contract argument is correct: book/web quotes should preserve the real medium. The maintenance cost is not acceptable until the tool exists in main and has iframe tests.

Tier 3 UI in 3 days: wrong. Table + three actions is easy. SSE + render queue state + cancel in-flight subprocess + inline secure media serving + YAML mutation + edit-log + two-layer status is not 3 days in this codebase. Thousand Sunny has FastAPI/Jinja/HTMX patterns, but no `/script-video` route today. Budget 5-7 days or cut Phase 1 UI to Tier 2: storyboard table, approve/re-plan, polling, no inline player.

LLM anchors: wrong as written. Do not ask an LLM to emit “8-12 char quote” and then hope fuzzy matching saves it. Give the LLM numbered transcript spans and require exact copied `start_anchor` / `end_anchor`. Then run deterministic substring match first, fuzzy only as diagnostic fallback. The validator should fail the beat, not silently lower confidence.

DaVinci FCPXML transforms: high risk. Apple’s `adjust-transform` position semantics are not plain pixels; Apple documents position relative to frame height, e.g. 10 means 108 px in a 1080 frame. ([developer.apple.com](https://developer.apple.com/documentation/professional_video_applications/fcpxml_reference/story_elements/adjustment_elements/adjust-transform?changes=la_7&utm_source=openai)) ADR-032’s example `position_x: -480` reads like pixels and is likely catastrophically wrong if emitted directly. Also, current `fcpxml_emitter.py` has no transform support. Do not ship side layouts until a Resolve import fixture proves exact visual positions.

Concurrency=2: acceptable default for Hyperframes-only if worker count is pinned to 1-2 per render. Wrong as a mixed global truth for Hyperframes + CDP screencast. Use one global GPU semaphore, collect timings, then raise.

Examples tag filter in Phase 1: pure prompt waste if corpus is empty. Build the directory, but do not load `_index.yaml` or examples until at least 5 curated examples exist.

2-layer approve UX: valuable, but default it intelligently. Text approve before render prevents wasted GPU. Visual approve after render catches bad motion/layout. The tedious part is per-beat ceremony. Solve it with batch actions: “approve all text drafts,” “render approved,” “finalize all passing renders,” while preserving per-row overrides.

**5. Alternatives Not Considered**

WebVTT should be evaluated. SRT is fine as exchange format from `/transcribe`, but WebVTT maps directly to browser `<track>` and cue IDs. The pipeline can accept SRT, normalize internally to a cue model, and export WebVTT for UI playback alignment.

Pre-segment upstream in transcribe. The transcribe skill already produces clean SRT and QC. It could optionally emit sentence/idea segments or word-timestamp JSON. That moves timing truth closer to ASR and reduces planner burden.

React/Vue SPA vs Jinja/HTMX. I would still choose existing Thousand Sunny server-rendered patterns for Phase 1, but the ADR should explicitly reject SPA because inline media review and SSE state can become JS-heavy quickly.

Hyperframes Studio iframe embedding. If Studio already previews compositions, embedding it for single-beat parameter iteration may be cheaper than custom inline controls. Reject it only after checking auth, file access, and parameter injection.

Generated markdown DSL. ADR-032 rejects user-authored DSL, correctly. It does not evaluate LLM-generated DSL as an internal IR. Given the existing parser and tests, this could be a migration bridge, though storyboard YAML is cleaner long-term.

Combine “Edit fields” and “Re-plan with note.” The current two actions split mechanical and natural-language edits, but in practice Shosho will write “make the number less loud and change target to 10,000.” A single “modify this beat” surface can parse structured fields plus note, then decide whether to log taste signal.

**6. Final Verdict**

Approve with modifications. Do not approve as-is.

Top required changes:

1. In §1 dispatcher, make Phase 1 Hyperframes-only in implementation, with `reader-playwright` and `web-playwright` schema-reserved but disabled. Promote `web_highlight_record.py` into main before claiming PR #710 shipped it.

2. In §3 layout / FCPXML, replace pixel-looking transform examples with verified FCPXML units, and add a Resolve import fixture as a blocking acceptance gate. Current emitter only writes V1 A-roll.

3. In §6 beat alignment, require exact copied anchors from transcript spans. Fuzzy matching is fallback diagnostics, not primary correctness.

4. In §7 UI tier / PR-5, either raise estimate to 5-7 days or cut Phase 1 to polling + no inline player. Three days is fantasy for the stated scope.

5. In §9 learning loop, create `examples/` but do not load cold-start example retrieval. Start loading only after curated examples exist.

ADR-032’s direction is better than ADR-015, but the document currently confuses “decision memory and spike success” with “repo implementation.” Fix that language before freezing it.
tokens used
116,661
**1. Code Grounding**

The ADR-032 implementation claims are materially ahead of the repo.

`video/compositions/bigstat/` does not exist in `E:\nakama`. The `video/` subproject still contains only `src/parser/*`, tests, `remotion.config.ts`, and package files. `video/package.json:5` says “Hyperframes video subproject,” but dependencies are only TypeScript/Vitest at `video/package.json:15-19`; there is no Hyperframes package, no composition catalog fork, and no rendered 9s 1080p MP4 in-tree. The claim “BigStat … shipped today” is false for this checkout.

`web_highlight_record.py` is also not in the main tree. `git show --stat 269da20` for PR `#710` shows only memory files: `memory/claude/MEMORY.md`, `feedback_cdp_screencast_over_recordvideo.md`, and `project_broll_dual_path_architecture.md`. That PR merged a decision note, not the Reader/Web Playwright tool. The memory itself admits the implementation reference is a spike path, not main tree: `memory/claude/feedback_cdp_screencast_over_recordvideo.md:21` points to `E:\nakama-reader-record-spike\scripts\web_highlight_record.py`. So “already shipped in PR #710” is wrong if “shipped” means reusable repo implementation.

`agents/brook/script_video/` exists, but it is ADR-015 code, not ADR-032 code. `pipeline.py:63-70` still parses `script.md` through the TypeScript DSL and then calls quote/render stubs. `_stage1_whisperx` returns `[]` at `pipeline.py:139-146`; `_stage4_render_stub` is explicitly a stub at `pipeline.py:211-213`. There is no `srt_flattener.py`, `planner.py`, `beat_aligner.py`, `render_dispatcher.py`, `render_workers/`, `layouts/`, `prompts/`, `STYLE.md`, `guardrails.yaml`, `examples/`, or `edit_log/`.

The FCPXML emitter is real but minimal: it creates `fcpxml version="1.10"` at `fcpxml_emitter.py:103`, one A-roll asset at `117-138`, and only V1 `asset-clip` elements at `168-176`. There is no B-roll V2, no `adjust-transform`, and no layout YAML consumer.

`.claude/skills/transcribe/SKILL.md` does support the claimed upstream: it describes an Auphonic + FunASR + Opus + Gemini pipeline producing clean SRT at lines `4-7`, and output discovery says SRT is `<output_dir>/<audio_stem>.srt` at `209-211`.

**2. Drift Detection**

ADR-032 mostly preserves ADR-015’s durable invariants, but it overstates compatibility. ADR-015 establishes Brook hosting at `docs/decisions/ADR-015-script-driven-video-production.md:34`, FCPXML output at `123-125`, DaVinci taking final composite at `100-102`, and per-episode `data/script_video/<episode-id>/` at `71-84`. ADR-032 keeps those.

The real drift is Brook role framing. ADR-032 cites ADR-001 “Brook still Composer,” but ADR-001 has been amended: `ADR-001:30` says Brook is narrowed by ADR-027, and ADR-027 explicitly narrows Brook from Composer to “Scaffold + Repurpose + SEO Audit” at `docs/decisions/ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md:60-66`. This pipeline can still belong in Brook because it is Stage 5 production/repurpose after human-authored SRT, but ADR-032 must stop using old “Composer” language as if ADR-027 did not happen.

ADR-014 is genuinely orthogonal. Its shape is SRT to multi-channel text artifacts, with fan-out renderers and `/bridge/repurpose/<run_id>` at `ADR-014:10-20` and `96-98`. ADR-032’s sequential video timeline pipeline should not inherit RepurposeEngine.

The design-system claim is true. `docs/design-system.md:16-24` defines `--sho-*` as the single token namespace, `34-38` names PANTONE 165 PC and LINE Seed TW, and `107-119` lists the tokens. One drift: `memory/claude/project_broll_dual_path_architecture.md:37` still says `--sho-orange`; ADR-032 correctly says load `docs/design-system.md`, not memory.

**3. Numerical / Factual Claims**

The 9.5-day estimate is too low. PR-1 at 1 day is fine if it is pure scaffolding. PR-2 at 1.5 days is plausible only for ASCII/English; Chinese SRT normalization, punctuation removal, numeric normalization, char-time interpolation, and false-negative test fixtures make it closer to 2-3 days. PR-3 at 2 days is optimistic because it needs schema, prompt, LLM failure handling, deterministic fixture tests, and “do not rewrite anchor” guardrails. PR-4 at 2 days is wrong: dispatcher + Hyperframes worker + transparent B-roll format + FCPXML V2 + DaVinci import validation is 4-6 days. PR-5 at 3 days is also wrong; see §4.

“10-minute video → 15-25 beats” is defensible for educational B-roll, but only if beats are idea units, not sentences. At 10 minutes, 15 beats means one cutaway every 40 seconds; 25 means every 24 seconds. That is a reasonable first target for a talking-head health channel.

`rapidfuzz partial-ratio >= 0.85` is not a safe single threshold for Chinese anchors when the LLM is allowed to rewrite. It can work if anchors are exact substrings copied from flattened SRT, but ADR-032’s planner contract says `start_quote` / `end_quote`, while also letting the planner creatively group prose. If the LLM paraphrases “一萬一千人” as “11,000 人,” 0.85 will create false negatives unless number normalization is excellent. The correct fix is not “lower threshold”; it is “planner must copy exact anchors from supplied text, and the validator rejects anchors not found.”

Hyperframes render timing: “~5-15s per BigStat” is plausible for an 8-second 1080p clip. Hyperframes docs show a sample 240-frame render with capture in 8.2s and encode in 8.0s, but that is an example, not a guarantee. Hyperframes rendering is frame-by-frame and seek-driven, with Chrome and FFmpeg in the loop. ([hyperframes.app](https://hyperframes.app/docs/3-guides/4-rendering))

Concurrency=2 is a reasonable starting cap only if each Hyperframes job uses low worker counts. Hyperframes docs state each render worker launches a separate Chrome process, consumes CPU/RAM, and their own default concurrent render limit is 2 on 8-core machines where each render uses 2-3 workers. ([hyperframes.mintlify.app](https://hyperframes.mintlify.app/guides/rendering)) The ADR’s “10 min video × concurrency 2 ≈ 4 min queue time” is unsupported. For 25 beats at 15s each, perfect two-wide queue is already 187.5s before process startup, file IO, failed renders, Playwright captures, and UI review. For mixed Reader/Web CDP screencast, 4 minutes is best-case, not planning truth.

**4. Assumption Push-Back**

3-path dispatcher: this is fine architecturally, but wrong for Phase 1. Keep the schema field `render_target`, implement only `hyperframes` in Phase 1, and leave `reader-playwright` / `web-playwright` behind disabled workers until `web_highlight_record.py` is promoted from spike to repo. The visual contract argument is correct: book/web quotes should preserve the real medium. The maintenance cost is not acceptable until the tool exists in main and has iframe tests.

Tier 3 UI in 3 days: wrong. Table + three actions is easy. SSE + render queue state + cancel in-flight subprocess + inline secure media serving + YAML mutation + edit-log + two-layer status is not 3 days in this codebase. Thousand Sunny has FastAPI/Jinja/HTMX patterns, but no `/script-video` route today. Budget 5-7 days or cut Phase 1 UI to Tier 2: storyboard table, approve/re-plan, polling, no inline player.

LLM anchors: wrong as written. Do not ask an LLM to emit “8-12 char quote” and then hope fuzzy matching saves it. Give the LLM numbered transcript spans and require exact copied `start_anchor` / `end_anchor`. Then run deterministic substring match first, fuzzy only as diagnostic fallback. The validator should fail the beat, not silently lower confidence.

DaVinci FCPXML transforms: high risk. Apple’s `adjust-transform` position semantics are not plain pixels; Apple documents position relative to frame height, e.g. 10 means 108 px in a 1080 frame. ([developer.apple.com](https://developer.apple.com/documentation/professional_video_applications/fcpxml_reference/story_elements/adjustment_elements/adjust-transform?changes=la_7&utm_source=openai)) ADR-032’s example `position_x: -480` reads like pixels and is likely catastrophically wrong if emitted directly. Also, current `fcpxml_emitter.py` has no transform support. Do not ship side layouts until a Resolve import fixture proves exact visual positions.

Concurrency=2: acceptable default for Hyperframes-only if worker count is pinned to 1-2 per render. Wrong as a mixed global truth for Hyperframes + CDP screencast. Use one global GPU semaphore, collect timings, then raise.

Examples tag filter in Phase 1: pure prompt waste if corpus is empty. Build the directory, but do not load `_index.yaml` or examples until at least 5 curated examples exist.

2-layer approve UX: valuable, but default it intelligently. Text approve before render prevents wasted GPU. Visual approve after render catches bad motion/layout. The tedious part is per-beat ceremony. Solve it with batch actions: “approve all text drafts,” “render approved,” “finalize all passing renders,” while preserving per-row overrides.

**5. Alternatives Not Considered**

WebVTT should be evaluated. SRT is fine as exchange format from `/transcribe`, but WebVTT maps directly to browser `<track>` and cue IDs. The pipeline can accept SRT, normalize internally to a cue model, and export WebVTT for UI playback alignment.

Pre-segment upstream in transcribe. The transcribe skill already produces clean SRT and QC. It could optionally emit sentence/idea segments or word-timestamp JSON. That moves timing truth closer to ASR and reduces planner burden.

React/Vue SPA vs Jinja/HTMX. I would still choose existing Thousand Sunny server-rendered patterns for Phase 1, but the ADR should explicitly reject SPA because inline media review and SSE state can become JS-heavy quickly.

Hyperframes Studio iframe embedding. If Studio already previews compositions, embedding it for single-beat parameter iteration may be cheaper than custom inline controls. Reject it only after checking auth, file access, and parameter injection.

Generated markdown DSL. ADR-032 rejects user-authored DSL, correctly. It does not evaluate LLM-generated DSL as an internal IR. Given the existing parser and tests, this could be a migration bridge, though storyboard YAML is cleaner long-term.

Combine “Edit fields” and “Re-plan with note.” The current two actions split mechanical and natural-language edits, but in practice Shosho will write “make the number less loud and change target to 10,000.” A single “modify this beat” surface can parse structured fields plus note, then decide whether to log taste signal.

**6. Final Verdict**

Approve with modifications. Do not approve as-is.

Top required changes:

1. In §1 dispatcher, make Phase 1 Hyperframes-only in implementation, with `reader-playwright` and `web-playwright` schema-reserved but disabled. Promote `web_highlight_record.py` into main before claiming PR #710 shipped it.

2. In §3 layout / FCPXML, replace pixel-looking transform examples with verified FCPXML units, and add a Resolve import fixture as a blocking acceptance gate. Current emitter only writes V1 A-roll.

3. In §6 beat alignment, require exact copied anchors from transcript spans. Fuzzy matching is fallback diagnostics, not primary correctness.

4. In §7 UI tier / PR-5, either raise estimate to 5-7 days or cut Phase 1 to polling + no inline player. Three days is fantasy for the stated scope.

5. In §9 learning loop, create `examples/` but do not load cold-start example retrieval. Start loading only after curated examples exist.

ADR-032’s direction is better than ADR-015, but the document currently confuses “decision memory and spike success” with “repo implementation.” Fix that language before freezing it.
