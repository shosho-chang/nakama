**1. CODE / REPO GROUNDING**

The referenced files exist: `docs/decisions/ADR-046-external-image-model-thumbnail-workflow.md:1`, ADR-033/036/037/038 under `docs/decisions/`, and the panel setup at `docs/research/2026-06-03-thumbnail-v3-panel-setup.md:4`. The ADR relationships are real, but the status hierarchy matters: ADR-036 and ADR-037 are `Accepted`, ADR-045 is only `Proposed` (`docs/decisions/ADR-045-thumbnail-arrangement-selection-eval-loop.md:3`). ADR-046 currently treats ADR-037/038 as a single prior renderer direction (`ADR-046:16-18`), but that is too smooth: ADR-037 is accepted staged production; ADR-045 is a proposed arrangement/eval extension with partial code present.

The ADR’s diagnosis broadly aligns with the repo. ADR-036 already says rendered candidates are not automatically upload-ready and only mechanical QA is enforced in code (`docs/decisions/ADR-036-title-driven-thumbnail-workflow.md:115-121`). ADR-037 says the renderer should be “deliberately boring” and compose approved layers, not invent design (`docs/decisions/ADR-037-staged-thumbnail-production-workflow.md:26-30`). The current router and modules confirm that path: `thousand_sunny/routers/bridge_project_thumbnails.py:1619-1941` implements staged deterministic render flow, and `shared/thumbnail_quality.py:58-67` explicitly frames visual QA as broken-file/readability checks, not final taste.

Several ADR-046 claims are only proposed, not implemented. Repo search shows `ThumbnailBriefV3`, `ReferencePackageV3`, `GenerationPromptV3`, `GenerationAttemptV3`, and `StyleMemoryV3` only in ADR-046, not source modules. The current router docstring lists brainstorm/render/candidate/commit endpoints (`thousand_sunny/routers/bridge_project_thumbnails.py:10-27`), and the FastAPI imports do not include an upload/import route (`thousand_sunny/routers/bridge_project_thumbnails.py:45`). The existing UI is staged render and commit, not external prompt-package/import/history: `_thumbnail_render_result.html:48-126` walks through person/layout/type/background/components/full render steps, and `_thumbnail_render_result.html:161-176` commits a deterministic candidate.

So the ADR should mark Phase 6/7/8 and the UI workflow as target design, not current capability. In particular, “After importing a generated image, Nakama stores it as a `GenerationAttemptV3` and runs critique” (`ADR-046:393-394`) reads implemented. It is not.

**2. DRIFT DETECTION**

ADR-046 does not fatally contradict the accepted direction, but it needs explicit supersession language. ADR-033 D2 says title and thumbnail A/B are independent and there is no `[title, thumbnail]` pairing (`docs/decisions/ADR-033-thumbnail-generation-pipeline.md:78-87`). ADR-036 reverses that for the later YouTube workflow: one publish title plus three thumbnail variants sharing `title_pairing` (`docs/decisions/ADR-036-title-driven-thumbnail-workflow.md:50-70`). ADR-046 follows ADR-036, but it should explicitly say ADR-036 supersedes ADR-033 D2 for YouTube V2/V3. Otherwise future agents will see two valid-looking but incompatible rules.

The bigger drift is ADR-036 D7. ADR-036 requires each idea to include one supported renderable `T-V*` tag because the renderer can only execute known templates (`docs/decisions/ADR-036-title-driven-thumbnail-workflow.md:101-113`). ADR-046 replaces that with soft fields like `reference_style` and “Ali warm explainer + Jeff clean component layout” (`ADR-046:193-207`). That is under-specified. If final art moves external, the renderable-template field can be demoted, but the concept still needs a concrete `reference_template_id` or `style_ref_id`. Otherwise the pivot loses one of the few hard-won improvements from ADR-036/038: forcing the LLM to bind a concept to an executable visual grammar.

ADR-046 correctly demotes the renderer in spirit: it says the renderer remains diagnostic/wireframe and is no longer the system of record for final art (`ADR-046:84-85`, `ADR-046:697-711`). That is the right pivot. But it should not demote all prior work to “wireframes.” Existing modules such as `shared/thumbnail_template_contracts.py:87-116`, `shared/thumbnail_arrangement.py:201-258`, and `shared/thumbnail_arrangement_eval.py:124-145` are valuable as prompt constraints, reference-template selection, deterministic text overlay, and post-import critique aids. The old renderer should become a support system around external art, not a dead branch.

Recommendation: ADR-046 should contain a “Supersedes / Amends” block. Amend ADR-036 D7 from “renderable template required for final render” to “concrete reference-template/style contract required for prompt compilation.” Supersede ADR-045’s full deterministic arrangement rollout for final art, while preserving template contracts, cheap gates, and critique rubrics as V3 support artifacts.

**3. DATA MODEL AND STORAGE RISKS**

The contracts are directionally right but not implementation-ready. They lack lifecycle, provenance, stable cross-links, and migration plan.

`ThumbnailBriefV3` (`ADR-046:461-487`) needs `project_slug`, `content_type`, `title_pool_run_id`, `selected_title_id`, `concept_id`, `brief_version`, `created_at`, `created_by`, `status`, and `supersedes_brief_id`. `idea_id: thumb-idea-001` is not enough once the user rerolls one card, edits it manually, imports several outputs, and later accepts one. It also needs a hard `reference_template_id` or `style_contract_id`; `reference_style: ["ali_warm_explainer", "jeff_clean_component"]` is too fuzzy compared with existing `ParsedIdea.reference_template_id` (`shared/thumbnail_idea.py:143-149`).

`ReferencePackageV3` (`ADR-046:488-519`) should not store only absolute paths and human labels. It needs `reference_id`, `role`, `path`, `sha256`, `mime_type`, `width`, `height`, `source_uri`, `rights/license_status`, `identity_usage`, and `approved_for_provider_upload`. The `[img 1]` convention is useful for manual tools (`ADR-046:286-291`), but it is not a stable data model. In edit mode, the previous generated output becomes `[img 1]`, so the package needs stable binding IDs plus a per-provider ordered binding map.

`GenerationPromptV3` (`ADR-046:521-538`) is underbuilt because `prompt_text` hides the compiler contract. Store structured sections plus the final compiled text: `task`, `reference_bindings`, `scene`, `composition`, `text_policy`, `negative_rules`, `provider_profile_version`, `compiler_version`, and `compiled_at`. Without this, prompt diffs become string archaeology.

`GenerationAttemptV3` (`ADR-046:540-562`) must include `brief_id`, `reference_package_id`, `visual_strategy_id`, `generation_index`, `parent_attempt_id`, `imported_at`, `imported_by`, `file_sha256`, dimensions, `original_filename`, `provider_job_id` or `manual_import`, lifecycle status, `accepted_at`, and `promoted_thumbnail_path`. The current storage precedent already distinguishes working candidates in `data/thumbnails/{slug}/runs` and chosen thumbnails in vault frontmatter (`docs/schemas/project-frontmatter-nested.md:115-122`, `docs/VAULT-LAYOUT.md:205`). V3 should extend that, not invent an isolated `external/` folder with no migration story.

`StyleMemoryV3` (`ADR-046:564-586`) is both too global and too vague. Rules need scope, evidence, and state: `scope=global/channel/project/provider`, `source_attempt_ids`, `feedback_count`, `status=pending|approved|rejected|retired`, `confidence`, `approved_by`, `approved_at`, and `last_used_at`. Do not update global memory from rejected attempts or from critiques of provider failures.

**4. PROMPT COMPILER / IMAGE MODEL RISKS**

The compiler is the least specified and highest-risk part. ADR-046 says the brief is compiled into `VisualStrategyV3` (`ADR-046:234-257`), but `VisualStrategyV3` has no data contract in the Data Contracts section. That is a blocker. If V3’s core value is prompt compilation, the compiler must be a deterministic, testable transformation: `ThumbnailBriefV3 + ReferencePackageV3 + StyleMemoryV3 + ProviderProfile -> GenerationPromptV3`. Add golden tests before UI.

Chinese text handling cannot remain open. The prompt’s “Keep all Chinese text legible and correctly written” (`ADR-046:374`) is not an engineering control. The ADR itself admits the problem remains unresolved (`ADR-046:862-886`). My recommendation is blunt: V1 should default to deterministic Traditional Chinese overlay. External models should generate the face/object/background/composition with a reserved text area or blank card; Nakama should overlay exact Chinese text using the existing typography/rendering pieces. The current deterministic system already has typography preview and measured text stages (`thousand_sunny/routers/bridge_project_thumbnails.py:1777-1788`, `_thumbnail_render_result.html:80-94`). Use that. Allow model-rendered Chinese only as an explicit experiment.

The style-memory loop is useful, but unsafe without filtering. “Avoid medical-ad look” from an accepted final is style memory. “Make this one output less weird” is attempt-local feedback. Provider quirks also need separation: “Nano Banana Pro tends to add extra labels” is provider memory, not Shosho style. ADR-046 should split project iteration memory, channel style memory, and provider workaround memory.

Provider lock-in is reduced but not eliminated. The ADR says profiles should be declarative and not hard-code one provider (`ADR-046:689-693`), but the examples privilege Prompt Edit/Nano Banana phrasing. Define provider capabilities instead: supports negative prompt, supports multi-reference order, supports image edit, text reliability, max refs, aspect ratio syntax, and whether edit prompts preserve composition. Then compile from the same intermediate strategy into provider-specific wording.

**5. PRODUCT WORKFLOW AND IMPLEMENTATION SLICING**

The manual workflow is only usable if Nakama removes friction that a note cannot. “Copy prompt, upload references, download result, import result” (`ADR-046:379-386`) is not enough. The UI must provide reference readiness, ordered upload checklist, one-click copy for full prompt and negative prompt, visible provider profile, import drag/drop, metadata defaults, thumbnail preview at feed size, and history comparison. Otherwise the user is better off with a markdown note and a folder.

The minimum viable implementation should be smaller than ADR-046’s slices. Do not start by building five new durable contracts plus style memory (`ADR-046:720-728`). Start with an adapter over the existing state: current `thumbnail_ideas` already persist in frontmatter (`docs/schemas/project-frontmatter-nested.md:115-116`), current asset manifests already live under `data/thumbnails/{slug}/asset_manifest.json` (`thousand_sunny/routers/bridge_project_thumbnails.py:1207-1218`), and current brainstorm already enforces title pairing and template metadata (`thousand_sunny/routers/bridge_project_thumbnails.py:496-501`, `prompts/thumbnail/brainstorm_youtube_v2.md:52-89`).

Recommended reorder:

1. Add a V3 prompt-package panel for existing three ideas.
2. Add `VisualStrategyV3` and `GenerationPromptV3` as pure, tested compiler outputs.
3. Add reference package readiness with hashes and ordered bindings.
4. Add manual image import as `GenerationAttemptV3`.
5. Add accept/promote path to existing vault thumbnail fields.
6. Add critique/edit prompt.
7. Defer global style memory.

Cut or defer Slice 3 Concept Generator until after prompt packages prove useful. The existing concept generator is not the bottleneck. Also defer provider APIs and style memory. The first proof should answer: “Can this workflow produce a better external prompt package than a hand-written note in under two minutes?”

UI pitfalls: avoid prompt walls inside three cards, avoid burying missing references, and do not make the history grid a generic image gallery. Each attempt needs lineage: concept, prompt version, reference package, provider/model, parent attempt, critique, and final status. This is Stage 5 YouTube production work, not Stage 4 content generation; keep it anchored there (`CONTENT-PIPELINE.md:53-64`).

**6. FINAL VERDICT**

Approve with modifications. Do not approve as-is.

Top required changes before implementation:

1. Add explicit supersession/amendment language for ADR-033 D2, ADR-036 D7, ADR-037, and ADR-045. ADR-045 should be marked deferred/superseded for final art, with template contracts and eval reused.

2. Define the missing storage and lifecycle model: stable IDs, parent attempt links, status transitions, hashes, timestamps, provenance, import/promote path, and migration from existing `thumbnail_ideas` / `thumbnail_run`.

3. Resolve Chinese text now. V1 should use deterministic overlay by default; model-rendered Chinese should be opt-in or rejected during critique.

4. Specify `VisualStrategyV3` and provider profiles as testable contracts. Add golden compiler tests before UI work.

5. Shrink the MVP to prompt package + reference readiness + manual import + accept/promote. Defer critique automation and global style memory until real attempts exist.

The pivot is directionally correct. The current deterministic renderer is not the right primary path for final upload-grade art. But the ADR currently replaces an overbuilt renderer with a potentially overbuilt prompt/history system. Tighten the contracts, preserve the useful deterministic pieces, and prove the manual prompt workflow before adding memory or provider automation.