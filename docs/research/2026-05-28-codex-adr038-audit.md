**Section 1 — CODE GROUNDING**

Foundry Phase 1 file grounding mostly checks out. The cited LOC counts are exact if counted as physical lines: `agents/foundry/pipeline.py` 206, `srt_flattener.py` 106, `chinese_normalizer.py` 135, `beat_aligner.py` 131, `planner.py` 114, `render_dispatcher.py` 60, `fcpxml_emitter.py` 230, `edit_log.py` 66. `agents/foundry/render_workers/` already exists and contains `hyperframes_worker.py`, `reader_playwright_worker.py`, `web_playwright_worker.py`, plus an extra `thumbnail_worker.py` not mentioned in ADR-032/038. Adding `agents/foundry/render_workers/resolve_driver.py` and `agents/foundry/render_workers/resolve/*.lua` will not collide path-wise, but it will make `render_workers/` even less clean: thumbnail and Resolve driver are not really peer `render_target` workers. I would put Resolve under `agents/foundry/resolve_driver/` or `agents/foundry/drivers/resolve/`.

The Bridge UI caveat is true. `thousand_sunny/routers/foundry.py:302-336` implements `POST /foundry/{episode_id}/beat/{beat_id}/replan`, but lines 318-321 explicitly say Phase 1 replan only marks for re-plan and the actual LLM re-plan must happen later. The code sets `render_status = "pending"` and `text_approved = False` at lines 322-323, appends `user_notes`, saves the same beat, and writes an edit log. `visual/replan` does the same pattern at `thousand_sunny/routers/foundry.py:387-415`. So D3 is fixing a real shipped gap, not inventing one.

D2 needs more code change than ADR-038 implies. `fcpxml_emitter.py` is not globbing, but it is hard-coded to beat-id filenames: `out_dir / f"b_roll_{beat['beat_id']}.mp4"` at `agents/foundry/fcpxml_emitter.py:108`, and the clip name remains `b_roll_{beat_id}` at line 209. `hyperframes_worker.py` also writes `out/b_roll_<beat_id>.mp4` at `agents/foundry/render_workers/hyperframes_worker.py:72`. D2’s “reads via storyboard lookup” is correct architecturally, but not “ready”; it needs schema, worker, dispatcher, emitter, README, and tests updated together.

ADR-032’s 3-path dispatcher contract is still only schema-reserved. `render_dispatcher.py:33-38` routes `hyperframes`, `reader-playwright`, and `web-playwright`, but both non-Hyperframes workers raise `NotImplementedError` at `reader_playwright_worker.py:12-15` and `web_playwright_worker.py:12-13`. ADR-038 defers Reader/Web Playwright again to Phase 2.5. That is drift. ADR-032 §1 and §Phase 1.5 made these the next sibling backlog items; ADR-038 jumps to Resolve automation without proving that Resolve import is the larger real bottleneck than unsupported book/web quote B-roll.

**Section 2 — DRIFT DETECTION**

ADR-038 is honest that ADR-032’s real-episode acceptance is still open: §Context line 16 says the “修修真實 10-15min episode” criterion has not been satisfied, and §Acceptance line 268 repeats it. The problem is the phrase “Phase 2 work and dogfood can proceed in parallel.” That is too permissive. It lets Phase 2 churn start before the one data point that would reveal whether D1 is actually valuable. PR-A/B/C are low-risk utilities. PR-D/E/F should not merge to production before the ADR-032 real-episode acceptance closes.

ADR-001 is already amended correctly. `docs/decisions/ADR-001-agent-role-assignments.md:31` has Foundry as an independent Video Production Line, so §0 is grounded.

CLAUDE.md vault rules do not block ADR-038. `CLAUDE.md:100-119` says any new agent vault write must follow `docs/VAULT-LAYOUT.md`. Current Foundry writes to `data/script_video/<episode-id>/` and `agents/foundry/edit_log/`, not the vault. ADR-038 D1 says Resolve output lands at `out/episode_resolve_rendered.mp4`, still under the episode directory. No vault rule change is required unless future Resolve export targets a vault attachment folder.

Sandcastle eligibility is overstated. The Nakama Sandcastle Dockerfile installs Node 22, Python 3, `gh`, `typescript`, `pytest`, `ruff`, `hypothesis`, and `pytest-cov`, but no Lua runtime (`docs/runbooks/sandcastle-templates/Dockerfile:1-60`). Therefore PR-D can be Sandcastle-tested only at the Python wrapper/mock boundary. It cannot parse or execute Lua scripts unless the template adds `lua5.1` or `luajit`. “Lua-mock” is fine for subprocess contract tests; it is not evidence the Lua scripts are syntactically valid or Resolve-compatible.

**Section 3 — NUMERICAL / FACTUAL CLAIMS**

The `course-video-manager` grounding is weaker than ADR-038 presents. The public GitHub root confirms it is a nontrivial TypeScript repo with `app`, `components`, `resources/resolve`, `.sandcastle`, `evals`, and 1,645 commits, not obviously a “25KB TS/React app.” The root file list visible from GitHub also does not show a `LICENSE` file, and the README section shown there describes publishing workflow automation, not a narrow Resolve-only tool. ([github.com](https://github.com/mattpocock/course-video-manager))

I could not independently fetch Matt’s raw source files from this sandbox, and the referenced `docs/research/2026-05-28-course-video-manager-borrowings.md` does not exist locally. That absence matters. ADR-038’s exact claims like `video-processing-service.ts:408-501`, `export-hash.ts` 79 LOC, and `clip-and-append.lua` 169 LOC should not be accepted without checking in the research artifact. As written, the ADR asks reviewers to trust a missing source report.

The local LOC estimates for new work are optimistic but not absurd for D2/D7/D5. `export_hash.py ~80` and `storyboard_diff.py ~100` are plausible. `silence_detection.py 50+30` is plausible for parser plus CLI, but not for planner integration and fixtures. The big undercount is D1 and D3. `resolve_driver.py ~250` must handle path discovery, Windows path normalization, env var serialization, subprocess lifecycle, stdout/stderr parsing, typed timeline specs, dry-run mode, errors, and tests. `beat_editor.py ~200` plus `replan_agent.py ~150` is also light once split/merge/realignment/schema invariants and Bridge endpoint behavior are included.

The 16-character SHA claim is mathematically right: 16 hex chars = 64 bits = `2^64` space. Collision risk for 5,000 rendered B-rolls/year is about `5000*4999/(2*2^64) = 6.8e-13` per year. Collision is not the real D2 risk. Incomplete hash inputs are.

The token-cost claim is wrong. ADR-038 says `12k input × 25 beats` and “~$0.20 per re-plan.” That is 300k input tokens. At $3/MTok input pricing, input alone is about $0.90, before output tokens. Anthropic’s pricing table lists Sonnet 4/4.5 at $3/MTok input and $15/MTok output; even if Sonnet 4.6 is price-compatible, the arithmetic does not produce $0.20. 

**Section 4 — ASSUMPTION PUSH-BACK**

Claude is assuming D1 solves the dominant friction. I do not buy that yet. ADR-038 §D1 says manual FCPXML import is the friction because 修修 already uses DaVinci. But Phase 1 has not passed a real episode. The bigger friction may be poor layout fit, unsupported Reader/Web Playwright B-roll, no inline review, or the placeholder re-plan flow. D1 ROI should be a hard gate: run one real episode through FCPXML first, time the import/rework pain, then decide whether Resolve automation deserves first-class Phase 2 status.

D1’s “battle-tested in Matt’s workflow” does not transfer cleanly. Matt’s repo is a different product and workflow; ADR-038 itself says Matt uses WSL2/Windows path translation, while Nakama targets native Windows. Resolve scripting is session-stateful. ADR-038 §Risks line 365 admits unstated Resolve session assumptions are high-probability. That is not a normal implementation risk; it is a reason to spike before committing the architecture.

D3’s general idea is good: structured tool calls are better than free-form diff parsing. But the six-op surface is not complete for Foundry. 修修 will want `set_layout`, `set_transition`, `patch_broll_params`, `set_timing`, `replace_anchor_quotes(start_quote,end_quote)`, `duplicate_broll_from(other_beat)`, and probably `restore_previous_render`. `shift_anchor(direction, char_count)` is especially weak in Mandarin; character counts are not how an editor thinks, and they are brittle across normalized punctuation.

The “tool-call edit pattern is reliable because Matt used it for sub-document edits” is a loose analogy. Matt’s article/course editing domain is English document mutation. Foundry is Mandarin transcript timing plus media render specs. Tool calls help, but the hard part is not applying JSON edits; it is preserving exact-copy anchors, SRT timing, beat continuity, and cache invalidation.

**Section 5 — ALTERNATIVES NOT CONSIDERED**

Make Python-native Resolve API the primary D1 spike, not Phase 2.5. The bundled Resolve scripting docs support both Lua and Python, and the Python path uses `DaVinciResolveScript` / `scriptapp("Resolve")` against a running Resolve instance.  That maps better to this Python codebase: typed specs, normal unit tests, no env-var IPC, no Lua copyright issue, no second language in `agents/foundry/`. If Python setup fails on 修修’s Windows Resolve install, then fall back to `fuscript.exe` with a documented failure reason.

The smaller Phase 2 should be D2 + D3 only. PR-A fixes wasted render time. PR-G fixes the shipped re-plan placeholder. Together they improve the current FCPXML workflow without assuming Resolve automation is the bottleneck. Ship that after or during the real-episode dogfood, then decide D1 from evidence.

Designing from scratch is not “weeks” more expensive. D2 is a hash function. D7 is a diff function. D5 is a `silencedetect` parser. D3 needs a domain-specific edit API anyway. Borrowing the idea is useful; porting code is not where the value is. For D1, clean-room scripting from Resolve docs is the safer path.

Postponing D1 until Phase 1 acceptance costs about one podcast cycle, maybe one week. That delay is cheap compared with building a Resolve driver nobody uses. ADR-038’s §Risks already labels “修修 doesn’t dogfood D1 → ROI zero” as Medium probability. Treat that as a sequencing constraint, not a row in a risk table.

**Section 6 — FINAL VERDICT**

Verdict: approve with modifications; do not accept ADR-038 v1 as-is.

Required changes:

1. Rewrite §D1 as a gated Resolve spike: Python-native `DaVinciResolveScript` first, Lua/fuscript fallback only after documented failure. D1 cannot merge to production before one real ADR-032 episode is completed.
2. Rewrite §OQ1: no verbatim Lua port from an unlicensed repo. File the GitHub issue if desired, but implement clean-room from Resolve scripting docs upfront.
3. Fix §D2 hash inputs. Keep 16-char SHA if you want; it is enough. But hash the actual render contract: `EXPORT_VERSION`, selected layout YAML content digest, component/render-target, params, transitions if render-affecting, render duration if worker-affecting, and a component/worker version or source digest. `fcpxml_emitter.py` must read `status.cached_hash` or an explicit `render_path`, not infer beat-id filenames.
4. Expand §D3’s edit ops before implementation. Add layout, transition, param patch, quote-based anchor replacement, timing, duplicate/restore operations. Remove deterministic-same-model acceptance unless the chosen model actually supports stable seed semantics.
5. Re-slice. PR-D is not 3 days. Make D1 a 1-day local Windows spike, then 5-7 days implementation if the spike passes. PR-G is closer to 5-7 days than 3.5. PR-D is not Sandcastle-eligible for Lua execution unless the Docker image installs Lua.

Owner questions, explicitly: D1 is not robust enough as specified; use Python-native first. D2’s 16-char prefix is enough, but the proposed inputs are incomplete. D3’s six tools are not complete for real Foundry editing. PR-D’s 3-day estimate is optimistic and its dependency graph misses D2 filename migration and real Resolve validation. §OQ1’s issue-then-rewrite plan is not sound; rewrite from scratch now.
