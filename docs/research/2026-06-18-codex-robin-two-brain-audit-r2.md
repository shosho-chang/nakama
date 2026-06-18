**1. CODE GROUNDING**
- D-E is `docs/decisions/ADR-045-robin-two-brain-and-kb-skill-family.md:104-110`; source intent is `.round2-section10.md:14`.
- `shared/provenance_linter.py` still documents the N520 placeholder/deferred history at `:8-11`, `:91`, `:188`, but current code does enforce a narrow rule: `ProvenanceLinter.lint_page()` starts at `:190`, skips non-Concept/Output pages at `:218`, rejects only `derived` evidence links at `:223` and `:239`, and returns `"clean"`/`"violations"` at `:252`.
- The linter has hardcoded path classes only: terminal prefixes at `:33` / terminal wikilinks at `:63`; derived prefixes at `:36` / derived wikilinks at `:57`; unknown targets pass via `return "unknown"` at `:134`. There are no `own-output`, `provenance:self`, or `derivation` symbols.
- D-E’s derivation-set firewall is not implementable against the existing API. Writers pass only `page_path`, `body`, and `mentioned_in`: `shared/output_writer.py:93-96`, `shared/kb_writer.py:255-258`. No provenance graph, source type, or ancestor set is available.
- D-G’s RCP foundation is per-source: `ReadingContextPackageBuilder.build()` is at `agents/robin/reading_context_package.py:201`, takes singular `reading_source: ReadingSource` at `:203`, and singular path inputs at `:205-208`. `writing_assist` is also per `source_id_b64`: `thousand_sunny/routers/writing_assist.py:12-13`, `:197`, `:217-219`.
- `/draft-map` appears in ADR-043 only, notably `docs/decisions/ADR-043-centaur-zettelkasten-permanent-layer.md:16`, `:46`, `:66`, `:80`. I found no `draft-map` implementation under `agents/`, `shared/`, or `thousand_sunny/`. `gather` hits are unrelated Franky/Zoro/general functions, not a Robin KB skill.

**2. DRIFT DETECTION**
- D-E conflicts with the spirit of Centaur red line 5: `docs/plans/centaur-zettelkasten/Centaur-Zettelkasten-規格-v0.2.md:132` says Concept/Output evidence must cite Sources/Raw/Annotations, not Concept/Output. Reclassifying a derived own-output as terminal Source launders origin.
- D-E also exposes a current linter gap: `KB/Permanent/` is neither terminal nor derived, so mature Permanent cards used as own-output evidence can fall through as `unknown`, despite not being Sources/Raw/Annotations.
- D-F is consistent with ADR-001 only at the slogan level: Robin=`Knowledge Base` at `ADR-001:24`, Nami=`Secretary` at `ADR-001:25`. It adds a new routing criterion that ADR-001 does not define.
- D-F drifts against Centaur §4 unless clarified: `KB/Fleeting/...` is explicitly Nami Slack bot capture at `Centaur v0.2:88`, with daily review consuming open fleeting notes at `:106`. “Robin owns domain fleeting” must mean post-triage, not capture.
- D-G’s `/draft-map` reconciliation is only a doc claim. ADR-043 frames `/draft-map` as future, read-only, Permanent-only Slice 1 work at `:46` and `:80`; `gather` is broader: Permanent + Wiki + Sources + Entities + outline.
- D-G must stay inside ADR-024/CONTENT-PIPELINE boundaries: RCP is “not a draft” at `ADR-024:37`; Stage 4 must not ghostwrite Line 2 at `ADR-024:60` and `CONTENT-PIPELINE.md:33-36`, `:49`.

**3. FACTUAL CLAIMS**
- “`provenance_linter.py` is placeholder/not wired” is partly stale. It is wired into Concept and Output writes, but it is only a path-class linter, not a provenance firewall.
- “`provenance:self` + derivation set solves D-E” is false as stated. `provenance:self` identifies author, not dependency origin.
- “Transitive cycle is caught” is false. Existing lint sees one page’s evidence links only; it cannot detect output1→ConceptA→output2→ConceptA.
- “`/draft-map` can be reused” is ungrounded; there is no implementation to reuse.
- “Extending RCP from per-source to per-topic is cheap” is wrong. Topic→N sources needs retrieval, ranking, dedupe, tier separation, provenance grouping, and aggregation semantics.

**4. ASSUMPTION PUSH-BACK**
- D-E assumes own-output can become evidence after tagging. That is the wrong primitive; the missing primitive is dependency independence.
- D-E assumes a local derivation set is enough. It is not; the rule must be graph reachability over all outputs, Concepts, own-output Sources, and later re-ingests.
- D-F assumes “does it compound?” is decidable at capture time. Many fleeting notes are mixed: one sentence may be operational context plus reusable domain insight.
- D-G assumes “per-topic RCP” is a refactor. It is a different input model and likely a separate package type.
- D-G assumes assembly cannot become drafting. Outline skeleton + MOC + evidence board is close enough to Stage 4 drafting that explicit W-rules are required.

**5. ALTERNATIVES**
- Route O alternative: quarantine own-output as `source_type=own-output` but never terminal evidence for Concepts by default. It can support retrieval, taste profile, and style memory; promotion to evidence requires human marking as independent observation or adding external terminal evidence.
- Stronger firewall: introduce a provenance DAG with nodes for Sources, Concepts, Outputs, Permanent cards, and own-output ingests. Record `derived_from` and `cites_as_evidence`; reject any Concept/Output evidence edge to a self-origin node reachable from that page’s ancestors/descendants.
- Gather alternative: create a new `ContextAssembly` service. `/draft-map` becomes `ContextAssembly(scope="permanent", mode="read_only")`; `gather` becomes `ContextAssembly(scope="mixed", mode="scaffold")`; RCP remains per-reading-source.

**6. VERDICT**
Reject current D-E/D-F/D-G package as written.

1. In `docs/decisions/ADR-045-robin-two-brain-and-kb-skill-family.md` D-E, downgrade route O to experimental/quarantine until a graph-based provenance firewall exists.
2. In `shared/provenance_linter.py`, reject `unknown` evidence targets for Concept/Output evidence sections, or require an explicit evidence catalog proving terminal status.
3. In `shared/provenance_linter.py`, add source metadata inputs: `source_type`, `provenance`, and provenance graph/derivation closure; `provenance:self` alone must not grant terminal status.
4. In `shared/output_writer.py` and `shared/kb_writer.py`, record derivation edges on Concept/Output writes and pass provenance context into `lint_page()`.
5. In ADR-045 D-E, add the explicit forbidden transitive case: output1→ConceptA; output2 derived from ConceptA; own-output output2 must not confirm ConceptA.
6. In ADR-045 D-F and Centaur v0.2 §4, define capture-time triage fields: `capture_owner`, `triage_state`, `domain_reusable`, `operational_context`, and default mixed notes to Nami capture + Robin review.
7. In ADR-045 D-G, remove the claim that `gather` reuses `/draft-map` until `/draft-map` exists in code; define it as a future shared assembly service instead.
8. In `agents/robin/reading_context_package.py`, do not mutate `ReadingContextPackageBuilder.build(reading_source, ...)` into topic mode; add a separate `TopicContextPackageBuilder`.
9. In ADR-045 D-G, add non-overlap contracts: `kb-query` = search/results, `/draft-map` = Permanent-only read-only assembly, `gather` = mixed-tier scaffold, `insight` = judgment/defer prompts only.