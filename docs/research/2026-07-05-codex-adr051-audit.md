# Codex (GPT-5) audit — ADR-051 Director skill

- Date: 2026-07-05
- Dispatch: codex exec, model_reasoning_effort=medium, --sandbox read-only, repo worktree
- Note: 審計時 ADR-051 尚未 merge 進該 worktree branch，Codex 依 prompt 內摘要 + 現行 code 交叉查證（其 Section 1 有註明）
- Verbatim below.

---

**1. CODE GROUNDING**

ADR-051’s broad current-state claims are mostly right, but there are important caveats.

The “bigstat-only” claim is correct for actual renderable B-roll. `agents/brook/script_video/schemas/storyboard.py` only allows `render_target: "hyperframes" | "reader-playwright" | "web-playwright"`, and `agents/brook/script_video/guardrails.yaml` only allows `hyperframes` with `bigstat`. `render_dispatcher.py` routes three targets, but only `hyperframes_worker.py` renders. `reader_playwright_worker.py` and `web_playwright_worker.py` are explicit `NotImplementedError` stubs.

The “empty example corpus” claim is correct: `agents/brook/script_video/examples/_index.yaml` has `examples: []`, and `planner.py` gates example loading on `len(entries) >= 5`.

Bridge review exists and is more than a stub. `thousand_sunny/routers/brook_video.py` implements `/brook/video/{episode_id}`, text approve, edit, replan, visual approve/edit/replan, batch approve/render/finalize, status polling, and edit-log promotion. The HTML template exists at `thousand_sunny/templates/brook_video/storyboard.html`. However, do not call it cleanly migrated: `promote_to_example()` still writes to `agents/foundry/examples`, not `agents/brook/script_video/examples`. That is real ADR-050 drift.

Layouts are correctly blocked for side/PiP in implementation. `fcpxml_emitter.py` says Phase 1 only `full_aroll` / `full_broll`; transform layouts require a DaVinci fixture. The layout YAML files exist, but the emitter does not implement transforms.

Bigger grounding issue: ADR-051 and the July 5 plan/context files named in the prompt are not present in this worktree at the requested paths. I found ADR-032/038/050, but not `docs/decisions/ADR-051-director-skill-storyboard.md`, not `docs/plans/2026-07-05-director-skill-plan.md`, and not `agents/brook/script_video/CONTEXT.md`. That means any “ADR-051 says…” audit here is necessarily against the supplied prompt plus current code, not the actual ADR file. If the owner is about to sign off, the ADR needs to be committed into this worktree first.

Also: `export_hash.py` still defaults to `agents/foundry/layouts` and `agents/foundry/guardrails.yaml`, while the live package under review is `agents/brook/script_video`. That can break cache hashing or silently hash the wrong guardrails after ADR-050.

**2. DRIFT DETECTION**

ADR-051’s `render_target: "asset"` plan conflicts with current contracts. `BRollSpec.render_target` is a Pydantic `Literal` with `extra="forbid"` on every model. Any storyboard emitted through `planner.plan_episode()` with `render_target: asset` hard-fails validation. The prompt contract in `prompts/broll_planner.md` also says “Emitting any other value causes a validation hard-fail.”

It also conflicts with dispatcher assumptions. `render_dispatcher.dispatch_beat()` always computes a hash, derives `out/b_roll_<hash>.mp4`, and dispatches to a worker branch. Unknown target raises `ValueError`. There is no asset bypass, no copy/link stage, and no status field for external asset provenance.

It conflicts with `fcpxml_emitter.py` too. Emit only knows “cutaway beat with `render_status: done` maps to `out/b_roll_<cached_hash>.mp4`.” It does not read `params.path`, `asset_uri`, source attribution, license metadata, in/out subclips, or asset duration intent. If ADR-051 treats asset B-roll as already-downloaded media, schema and emitter need a first-class asset reference, not just `render_target: asset`.

ADR-038 §D2 content-addressed cache can support asset media, but only if hash inputs include the asset identity and transformation intent: absolute/episode-relative path, file digest or stable asset id, trim range, scaling/crop policy, and any generated overlay metadata. Otherwise changing a downloaded file under the same path can reuse stale FCPXML/render status.

Guardrails drift exists already. `guardrails.yaml` claims it is “enforced by validator before storyboard.yaml write,” but I did not find a validator enforcing `max_cutaways_per_minute`, allowed components, or no-consecutive-same-component. `planner.py` only validates the Pydantic schema. ADR-051 must not rely on guardrails being machine-enforced unless it adds enforcement.

**3. NUMERICAL / FACTUAL CLAIMS**

The rhythm numbers are not ready to encode as rules. A 15-second frame-sampling analysis of two videos can be used as a hypothesis, not a policy. “Book-type ~2.4 scene-changes/min vs health-type ~1/min; TH share 37% vs 65%” is too small, too coarse, and too content-dependent. It should become a skill note like “default to lower visual density for health/reflection episodes; override after watching the episode,” not a hard threshold.

The current guardrail cap of `max_cutaways_per_minute: 4` is an upper bound, not a target. ADR-051’s “too loose for health-type, about right for book-type” is directionally consistent with those two samples: 1/min is far below 4/min; 2.4/min is below but closer. But “about right” is overstated. Four cutaways/min is 40 per 10 minutes, while the planner prompt says “~15-25 cutaway beats per 10-minute episode,” i.e. 1.5-2.5/min. The code and prompt already disagree numerically.

Do not encode “health = 1/min” as a skill rule. Encode a review heuristic: for reflection/health episodes, start with mostly talking head, add cutaways only for concrete claims, mechanisms, contrast, or needed pacing relief. For book/explainer episodes, allow denser visual support, but still keep the existing 15-25/10min budget until more episodes are measured.

**4. ASSUMPTION PUSH-BACK**

D1 needs push-back. “Creative multi-tool loops can’t be programmed” is false as stated. The right distinction is not “creative = skill, deterministic = program.” It is “selection and judgment can be agentic; artifact normalization and asset resolution must be programmable.” Claude is choosing the architecture where Claude stays in the hot path. That is useful for the next episode, but dangerous as the system of record.

A skill run is non-reproducible unless ADR-051 defines captured inputs and outputs: skill version, model, prompt/playbook version, browser/download decisions, asset URLs, search queries, rejected candidates, license notes, and final storyboard diff. “Improving each episode” as unversioned prose is process memory, not software. It will drift, and two weeks later nobody can bisect why episode N looked worse.

D1 should be reframed: the Director skill may produce or revise `storyboard.yaml`, but the repo needs deterministic passes for validation, asset resolution, cache identity, and FCPXML emission. The skill should not directly establish new schema conventions ad hoc.

D5/D6 Envato handoff to a separate Codex computer-use session is operationally fragile. It depends on login state, UI layout, download folder behavior, naming discipline, and manual transfer back into episode assets. It also weakens provenance. Better v1: the Director outputs an `asset_requests.yaml` batch with search terms, desired duration, mood, negative constraints, and license/source fields. A human or separate tool fulfills it into `assets/` with a manifest. The pipeline consumes the manifest deterministically.

D7 is the weakest technical claim. “PyMuPDF locates the quoted sentence bbox” assumes exact text match. The script is Traditional Chinese; papers are English; the spoken line is likely a paraphrase or translation. PyMuPDF can locate exact English strings in a PDF page. It does not solve cross-lingual paraphrase grounding. ADR-051 must not promise automatic sentence bbox from script quote. The correct design is: Director records paper DOI/PDF, page number, English source sentence(s), and Chinese paraphrase. A tool may help search text, but human/agent confirmation is required before highlight bbox. For v1, make paper highlight a manual page/rect or page/sentence selection task, not an automatic guarantee.

**5. ALTERNATIVES NOT CONSIDERED**

Alternative 1: skill as thin orchestrator over a stronger `plan` program. Keep Director as the creative operator, but have it call deterministic CLIs: `plan`, `validate-storyboard`, `resolve-assets`, `render`, `emit`. Tradeoff: slightly more engineering now; much better reproducibility and easier rollback. This is the best fit.

Alternative 2: storyboard-first, asset-resolution second. Let the Director create semantic beats with `visual_type`, `intent`, and `asset_request_id`, but do not bind Envato/YouTube/PDF files in the first pass. Then a deterministic resolver turns requests into local assets and a manifest. Tradeoff: one extra artifact; much cleaner schema and licensing/provenance.

Alternative 3: defer asset-heavy types for v1. Ship only BigStat plus manually placed DaVinci asset clips for the first real episode. Use the Director skill to annotate suggested B-roll in notes, not automate download/render. Tradeoff: less automation this week; avoids schema lock-in around stock/KOL/paper workflows before the real pain is known.

**6. FINAL VERDICT**

Approve with modifications, not as-is.

Top required changes:

1. **D1:** Reframe skill-over-program as “skill orchestrates, deterministic tools own contracts.” Add required run logs and versioned skill prompts. Do not let an interactive skill be the only system of record.

2. **D2/schema:** Do not add bare `render_target: "asset"` until `storyboard.py`, `render_dispatcher.py`, `export_hash.py`, `fcpxml_emitter.py`, Bridge UI, and validation all define asset semantics together.

3. **D5/D6:** Replace “separate Codex computer-use Envato downloads” with an `asset_requests.yaml` / `asset_manifest.yaml` handoff. Browser download can be an implementation detail, not the architecture.

4. **D7:** Remove the claim that PyMuPDF can locate translated/paraphrased scientific quotes automatically. Require explicit English source sentence/page/rect confirmation.

5. **Repo hygiene:** Commit ADR-051, the July 5 plan, and `CONTEXT.md` before sign-off; fix lingering `agents/foundry` paths in `export_hash.py` and Bridge example promotion.
