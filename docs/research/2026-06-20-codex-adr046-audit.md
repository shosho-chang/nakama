1. CODE GROUNDING

- `books.py:286` is not the `data/books/{id}/` storage fact; it is the upload route. Actual root/write path is `shared/book_storage.py:40-41` and `shared/book_storage.py:102-116`, called from `books.py:389`.
- Video annotation route line is stale/misleading: decorator is `robin.py:2025`; `robin.py:2119` is only `store.save(ann_set)`.
- Article `/robin/save-annotations` is correctly at `robin.py:806`, but it only saves and returns; no literature render trigger exists there (`robin.py:820-824`).
- Video save/delete also do not trigger literature render: save returns at `robin.py:2119-2131`, delete saves at `robin.py:2185-2189`.
- `_parse_webvtt` claim is mostly right: it strips timing tags, dedupes, and sentence-coalesces (`robin.py:652-722`), but it is still a reader-local parser, not an ingest adapter.
- Book annotation + render claim is right: POST is `books.py:578`, background literature render is `books.py:620`, calling `write_literature_note(..., source_kind="book")` at `books.py:566-573`.
- Permanent endpoint is line-stale: actual `POST /kb/api/permanent` is `kb_review.py:284`, not `:278`.
- “daily_review source-agnostic” is overclaimed. It scans `KB/Annotations/*.md` only (`daily_review.py:219-227`), filters per-item `created_at` (`daily_review.py:289-292`), and fabricates `KB/Literature/{slug}` paths (`daily_review.py:301`). It does not read Literature Note `mined_concepts`.

2. DRIFT DETECTION

- Wiki-Concept demotion is not implemented enough to safely open Slice 4. ADR-043 requires `status: candidate` + `#ai-draft` and retrieval impact (`ADR-043:36`, `ADR-043:70-71`). Current `ConceptPageV2` has no `status` field and `extra="forbid"` (`shared/schemas/kb.py:104-117`); `promotion_renderer` emits `type: concept` but no `status`/`#ai-draft` (`shared/promotion_renderer.py:226-241`).
- Old route-C ingest still writes/merges concept pages through `kb_writer.upsert_concept_page` (`agents/robin/ingest.py:543-590`), including LLM diff-merge in `kb_writer.py:785-812`. That contradicts “redline unchanged” unless ADR-046 explicitly excludes or rewrites this path.
- ADR-035 phase order is glossed over. Video concept promotion is Phase 3 after SourcePage and Entity (`ADR-035:151-159`). Current service intentionally skips video concept flow (`promotion_review_service.py:373-381`) and only builds video SourcePage + speaker entities (`promotion_review_service.py:392-406`).
- The draft says Slice 4 yields “three sources -> Wiki Concept/Entity”; false for video today. `build_video_source_map` emits one `SourcePageReviewItem` (`video_source_map_builder.py:228-239`), not concepts.
- Consumer demotion is only partial. Search boosts Permanent (`kb_hybrid_search.py:285-305`), but `search_kb` has no `scope={permanent,candidate,all}` parameter (`agents/robin/kb_search.py:142-174`), and `/kb/research` just calls it raw (`robin.py:1531-1538`).

3. SEQUENCING

- Slice 1 is fine and should ship first, but acceptance must include article/video delete re-render or stale Literature Notes will persist (`ADR-046:72`; video delete currently only updates annotations at `robin.py:2185-2189`).
- Slice 2 is underspecified: `IngestPipeline.ingest` reads a file and treats `.md` frontmatter specially (`ingest.py:135-145`). A cleaned video transcript needs a real generated raw file, title/source metadata, source_id, and source_kind mapping, not just “call `_parse_webvtt`.”
- Slice 3 hides real work. Candidate mapping is keyed only by anchor (`daily_review.py:419-422`), while the prompt includes both slug and anchor (`daily_review.py:319-323`) but asks the LLM to return anchors only (`daily_review.py:346-357`). `t=123` collisions across videos are guaranteed eventually.
- Slice 3 also has a backlog problem: existing video/book annotations will not appear unless their per-item `created_at` equals yesterday (`daily_review.py:289-292`). Need explicit backfill/review-since support.
- Slice 4 cannot ship without a target-path fix: `create_global_concept` items have `canonical_match=None` (`concept_promotion_engine.py:708-720`), while target resolution returns `None` for ConceptReviewItem without a matched path (`promotion_targets.py:74-77`), causing gate failure (`promotion_acceptance_gate.py:120-129`).
- Slice 5 “whole book cloud ingest” is not a slice of current architecture. Current book path is EPUB spine/chapter source-map extraction (`source_map_builder.py:312-327`) with one LLM claim extraction per chapter (`source_map_extractor.py:96-134`). A 1M-context whole-book ingest is a new design.

4. ASSUMPTION PUSH-BACK

- 修修’s circular-gate argument is right only for low-cost evidence accumulation: if B/E cannot produce reviewable candidates, adoption metrics are biased.
- ADR-043’s gate still has a real point: avoid building an expensive suggestion machine before proving human permanent-card authoring happens. ADR-043 explicitly calls the failure mode “candidate pile grows, human layer stays empty” (`ADR-043:26`) and gates B/E expansion (`ADR-043:44`).
- Removing the time gate but keeping “observation + cooldown” is coherent only if it is reframed as investment throttling: ungate collection/render/daily-card candidates; keep Wiki enrichment, true matchers, and whole-book/video concept generation behind measured usage.
- As written, “cooldown” is just a softer gate with no thresholds. Define numbers: candidate volume/week, permanent cards/week, % candidates acted on, stale-candidate age, LLM cost ceiling.

5. ALTERNATIVES

- Better sequencing: Slice A: fix facts + add article/video literature triggers + backfill. No LLM.
- Slice B: make daily_review source-qualified: return `{slug, anchor}` pairs, include `source_kind`, support review-since/backfill, and stop claiming `mined_concepts` support until implemented.
- Slice C: enforce Wiki demotion before more Wiki writes: `ConceptPageV2.status`, renderer `status: candidate`, `#ai-draft`, search scope, RCP candidate labeling.
- Slice D: enable B/C promotion `llm` only after ConceptReviewItem create-target and real matcher are fixed.
- Slice E: keep video Wiki concepts separate and later, after ADR-035 SourcePage + Entity are stable in use.
- Drop “whole-book cloud ingest” from this ADR; make it a spike/ADR with cost, copyright, context-window, and fallback criteria.

6. VERDICT

Rework.

Single most important change: split the decision into “ungate evidence accumulation and permanent-card candidates now” versus “keep Wiki concept enrichment gated by capability checks.” Concretely: do not approve Slice 4/5 until candidate demotion, source-qualified daily review, video phase ordering, and create-global target resolution are fixed.
