## 1 — CODE GROUNDING

The referenced ADR exists at `docs/decisions/ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md`. It is marked `Status: Accepted` at line 4, says it supersedes the compose pipeline portion of ADR-005a at line 6, and says it amends ADR-024 RCP ownership at line 7.

The main code references mostly exist, but several implementation claims are future-state or wrong-path:

- `agents/brook/compose.py` exists and really does implement `compose_and_enqueue` at lines 445-585. It builds `DraftV1`, wraps it in `PublishWpPostV1`, and enqueues with `source_agent="brook"` at lines 533-563. The ADR’s characterization of Entry B as topic-to-full-draft-to-approval-queue is code-grounded.
- `/brook/chat` exists. `thousand_sunny/routers/brook.py` defines `GET /brook/chat` at line 22, `POST /start` at line 33, `POST /message` at line 65, and export at line 112. `thousand_sunny/app.py` includes `brook.router` at line 121 and even redirects `/` to `/brook/chat` at line 117.
- `agents/brook/synthesize/` exists with eight Python files. Its public API produces an evidence pool and outline draft (`agents/brook/synthesize/__init__.py:67-147`).
- `agents/brook/repurpose_engine.py` exists and is line-agnostic, but its core `Stage1Result` is still intentionally open: `data: dict` at lines 115-123. ADR-027’s proposed typed `Line1bStage1Result` is not implemented.
- `agents/brook/line1_extractor.py` exists, but `agents/brook/line1b_extractor.py` does not. Current Line 1 schema has `hooks`, `identity_sketch`, `origin`, `turning_point`, `rebirth`, `present_action`, `ending_direction`, `quotes`, `title_candidates`, `meta_description`, and `episode_type` at lines 38-54. It does not have ADR-027’s `narrative_segments / quotes / titles / book_context / cross_refs / brief`.
- `prompts/brook/synthesize_outline.md` exists, but has no `trending_angles`. The current prompt only requests sections with `section`, `heading`, and `evidence_refs`.
- `shared/schemas/brook_synthesize.py` exists, but `OutlineSection` has only `section`, `heading`, and `evidence_refs` at lines 52-63. There is no `trending_match`. `BrookSynthesizeStore` has no `unmatched_trending_angles` at lines 87-99.
- `agents/brook/scaffold/` does not exist.
- `shared/repurpose/closed_pool.py` does not exist.
- `agents/brook/compliance_scan.py` and `agents/brook/tag_filter.py` do not exist. Current imports are `shared.compliance` and `shared.tag_filter` in `agents/brook/compose.py:25` and `agents/brook/compose.py:40`.

One more grounding issue: ADR-027 says `shared/schemas/publishing.py` is “agent-agnostic” in the keep list at line 162. That is false today. `DraftV1.agent` is `Literal["brook"]` in `shared/schemas/publishing.py:190`.

## 2 — DRIFT DETECTION

ADR-027 is directionally compatible with the Line 2 red line in `CONTENT-PIPELINE.md:36`, but it leaves serious doc and contract drift.

ADR-001 is not merely “related.” It says Brook is `Composer` at `docs/decisions/ADR-001-agent-role-assignments.md:30` and explains Brook composes in service of the owner’s expression at line 36. ADR-027 changes Brook’s identity to “Scaffold + Repurpose Only” at `CONTENT-PIPELINE.md:132`. That is an amendment to ADR-001, not just a related reference. ADR-027 should explicitly amend ADR-001.

ADR-005a is handled better. It is already marked `Partially Superseded by ADR-027` at `docs/decisions/ADR-005a-brook-gutenberg-pipeline.md:4`, and its amendment preserves builder/schema assets while retiring compose at line 10. The push-back: keeping `DraftV1` as “agent-agnostic” is inconsistent with `agent: Literal["brook"]`.

ADR-012 says `Zoro = outward` and `Brook = inward` at `docs/decisions/ADR-012-zoro-brook-boundary.md:14-19`. Claude uses that to justify moving RCP to Brook. That is too fast. ADR-012 line 44 frames Robin as source-to-wiki and Brook as wiki-to-article. RCP is not just “article-side”; it aggregates annotations, source maps, concept links, questions, and outline skeletons from reading/source artifacts. That is closer to Robin’s source/annotation boundary than Claude admits.

ADR-014 is not contradicted by typed `Line1bStage1Result`, but ADR-027 should say it is a line-specific extractor schema layered on top of the existing open engine. ADR-014 explicitly rejects a shared Stage 1 schema and endorses open `data: dict` at `docs/decisions/ADR-014-repurpose-engine-plugin-interface.md:72-79` and `127-129`.

ADR-021 is weaker support than ADR-027 implies. It is `Status: Proposed` at `docs/decisions/ADR-021-annotation-substance-store-and-brook-synthesize.md:3`, not accepted. The implementation exists, but ADR-027’s trending-angle extension is not in code.

ADR-024 is the biggest drift point. ADR-024 originally says “Robin may produce a Reading Context Package” at line 60, and the implementation still does: `shared/schemas/reading_context_package.py:4` says the schema is emitted by `agents.robin.reading_context_package.ReadingContextPackageBuilder`; the builder module says the same at `agents/robin/reading_context_package.py:10`. The tests import `agents.robin.reading_context_package` at `tests/agents/test_reading_context_package.py:34`.

The ADR-027 amendment has already been inserted into ADR-024 at line 87, saying RCP producer ownership is now Brook. But the code, tests, schema docstring, and `CONTENT-PIPELINE.md:48` still point at Robin/RCP, while `CONTENT-PIPELINE.md:132` says Brook owns RCP. That is live spec drift inside the same branch.

## 3 — NUMERICAL / FACTUAL CLAIMS

The PR #78 test count is historically documented in `memory/claude/project_brook_compose_merged.md:13` as `53` tests: `11 tag_filter + 16 style_profile_loader + 9 compliance_scan + 17 compose_pipeline`. In the current repo, that exact count is stale.

Current test function counts by file:

- `tests/shared/test_tag_filter.py`: 11 test functions. This matches.
- `tests/agents/brook/test_style_profile_loader.py`: 23 test functions, not 16.
- `tests/shared/test_compliance.py`: 35 test methods/functions, not 9.
- `tests/agents/brook/test_compose_pipeline.py`: 17 test functions. This matches.

So: the PR-era memory may have been true at merge time, but ADR-027 should not cite it as a current factual count without saying “PR-era.” The current repo has expanded or relocated those tests, and `compliance_scan.py` no longer exists.

The “13+ modules in `agents/brook/`” claim is true only loosely. Current direct Python files under `agents/brook/` are 12 including `__init__.py` and `__main__.py`, or 10 functional top-level files. Recursively, including `synthesize/` and `script_video/`, there are 28 Python files, or 22 non-dunder files. The better statement is: “Brook has 10 functional top-level Python modules plus two subpackages with 12 additional functional modules.”

The queue query is valid. `shared/state.py` defines `approval_queue` at line 140, `source_agent` at line 147, and `status` at line 158. `shared/approval_queue.py` inserts both fields at lines 159-178. `tests/agents/brook/test_compose_pipeline.py:115-116` asserts a Brook row has `source_agent == "brook"` and `status == "pending"`.

## 4 — ASSUMPTION PUSH-BACK

The B vs 2b distinction is not principled enough. Killing Entry B because it generates full prose while keeping Repurpose 2b because “transcript is atomic” is motivated reasoning unless “atomic transcript” is narrowly defined. A finished podcast transcript can be atomic for Line 1. An interview transcript plus research pack turned into a blog in the owner’s voice is not automatically atomic; it is raw material plus synthesis. If the guest says one sentence and Brook writes 500 words around it using the author’s book, that is compose-with-extra-steps.

The current renderer confirms the risk. `agents/brook/blog_renderer.py:7-10` says it builds an LLM prompt with Stage 1 JSON and style profile and calls Sonnet to “write 8-segment blog body.” That is full prose generation. Closed-pool sources reduce factual drift; they do not solve the voice-authorship red line.

Closed-pool enforcement is also overclaimed. Layer 1 physical isolation only limits retrieved context; it does not remove the model’s parametric memory. Layer 2 prompt instruction is not a security boundary. Layer 3 citation checking is enforceable only if the output schema is claim-atomic: every sentence or clause maps to a source span. Paragraph-level citation checks will pass while synthesis glue, framing, and owner-voice claims leak in between citations. ADR-027 currently has no `closed_pool.py`, no citation schema, and no validator.

Killing Entry A because “Claude.ai already exists” is weak. Claude.ai does not know the local KB, Project page state, source slugs, style profiles, compliance vocab, approval queue, or RCP state. The local chat route already does KB search before starting a conversation (`thousand_sunny/routers/brook.py:46-56`). Fully deleting it may downgrade the workflow. The right move is to kill drafting/export behavior, not necessarily the context-aware interaction surface.

RCP ownership consolidation is also not automatically simpler. Robin already owns annotations and Source Promotion; the implemented RCP builder is deterministic, LLM-free, and Robin-side. Moving RCP to Brook means Brook must understand Robin’s annotations, digest/notes layout, source maps, concept links, and source IDs. That adds coupling. Keep RCP production in Robin/shared; let Brook consume RCP for scaffold presentation.

## 5 — ALTERNATIVES NOT CONSIDERED

First: deprecate Entry B behind a hard feature flag instead of immediate deletion. Disable new `compose_and_enqueue` calls, drain `approval_queue WHERE source_agent='brook' AND status='pending'`, preserve tests temporarily, and delete after repurpose-to-Usopp handoff is implemented. Tradeoff: slower cleanup, but safer rollback and less accidental breakage around `DraftV1` and approval queue contracts.

Second: keep Entry A as a context bridge, not a chat composer. Replace `/brook/chat` with a Project/RCP/KB context exporter: gather style profile, compliance warnings, source links, and scaffold bullets, then copy/open a Claude.ai prompt. No local LLM drafting, no `brook_conversations`, no export draft. Tradeoff: still maintains a UI surface, but it preserves the useful local context Claude.ai lacks.

Third: keep RCP in Robin/shared and define a shared `evidence_package` schema. Robin produces annotation/source evidence packages. Brook consumes them for scaffold and synthesize. Repurpose 2b consumes them for closed-pool validation. Tradeoff: one extra shared schema, but clearer ownership and fewer cross-agent internals.

Fourth: narrow 2b. Require a `human_atomic_basis` field: transcript spans plus owner-authored notes. Every generated paragraph must map to either transcript span or owner note plus source citations. Without that, output must be labeled research-summary/episode-notes, not owner-voice blog prose.

## 6 — FINAL VERDICT

Approve with modifications. Do not approve as-is.

Required changes:

1. Rewrite ADR-027 Section 6, “Repurpose 2b.” State that transcript + research pack is not automatically atomic. Full owner-voice prose is allowed only with paragraph-level human atomic anchors and sentence-level source spans; otherwise it is research summary, not Line 2/owner voice content.

2. Reverse or narrow Section 3, “RCP producer = Brook.” Keep RCP production in Robin/shared unless you also move `agents/robin/reading_context_package.py`, schema docstrings, tests, and `CONTENT-PIPELINE.md:48`. My recommendation: Robin/shared produces RCP; Brook consumes it.

3. Fix factual/path drift in the implementation tables. Replace missing paths (`agents/brook/compliance_scan.py`, `agents/brook/tag_filter.py`, `line1b_extractor.py`, `shared/repurpose/closed_pool.py`) with “to be created” or current real paths. Do not call `shared/schemas/publishing.py` agent-agnostic until `DraftV1.agent` is changed.

4. Amend ADR-001 explicitly. Brook’s role is changing from `Composer` to `Scaffold + Repurpose`; treating ADR-001 as merely “Related” is underspecified.

5. Replace “kill Entry A” with “remove drafting/export and retain context bridge.” The local KB/context surface is valuable; the dangerous part is ghostwriting, not context assembly.
