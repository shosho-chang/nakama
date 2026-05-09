# Codex (GPT-5) Audit — ADR-020 Textbook Ingest v3 Rewrite

**Task ID:** task-motib9le-isaj8u
**Date:** 2026-05-06
**Auditor:** GPT-5 via Codex CLI 0.128.0 (ChatGPT auth, shosho@shosho.tw)
**Wall time:** 2m 17s
**Audit framework:** 6-section second-opinion audit on Claude's analysis of textbook ingest pipeline + Option D proposal

---

**1. VERIFICATION**

Confirmed: the 87.5% stub claim is correct. I counted 622 Concept markdown files under `E:\Shosho LifeOS\KB\Wiki\Concepts`; 544 contain `status: stub`, exactly 544/622 = 87.46%. The stub wording also matches the Phase B template: `created_by: phase-b-reconciliation`, `status: stub`, and one-line body text saying it was auto-created by Phase B. Examples: `5-HT.md:7-15`, `ATP.md:10-18`, `高血壓.md:7-15`. I also spot-checked five files: four were stubs (`5-HT`, `ATP`, `高血壓`, plus the visible same-pattern Phase B files), while `ATP再合成.md` and `Advanced-Glycation-End-Products.md` show real bodies with Definition/Core Principles sections (`ATP再合成.md:30-100`, `Advanced-Glycation-End-Products.md:16-80`). The random sampler was blocked by local command policy, so the hard count is the stronger evidence.

I partly confirm Claude's 75-85% density estimate, but I would restate it: ch5 is not a catastrophic summary; it is a dense derivative source page that preserves many primary facts, figures, and tables, while dropping some edge-case prose and secondary detail. The raw walker file has 585 lines; the vault page has 1129 lines, but much of the vault expansion is frontmatter figure descriptions (`ch5.md:24-145`) and structured narrative, not verbatim content. Major numbers survive: 30-40% carbohydrate digestion (`raw ch5.md:164`, vault `ch5.md:400`), fat absorption 97% vs 50% (`raw:232`, vault `489`), <1% protein in feces (`raw:247`, vault `509`), SCFA up to 10% daily energy (`raw:310`, vault `644`), 2% vs ≥8% carbohydrate effects (`raw:375`, vault `795-796`), >500 mOsm/L (`raw:389`, vault `807`, `980`), and 60-90 g/h (`raw:491`, vault `982`). But some edge cases are lost or compressed: achlorhydria with 10,000 to 100 million microorganisms/ml appears in raw (`raw:295`) and is not present in the targeted vault search; the cellulose "possibly up to 30%" detail appears in raw (`raw:179`) but not in the vault hits. So Claude's category estimate is directionally right: structural/primary-number retention is high; secondary nuance and clinical edges are lower.

**2. ALGORITHM BUG ANALYSIS**

Yes: ADR-016's parallel path breaks the ADR-011 concept aggregation contract for textbook ingest, but the bug is in the parallel skill/prompt wiring, not in the general Robin/kb_writer implementation.

ADR-011's contract is explicit: concept extraction must output one of `create`, `update_merge`, `update_conflict`, or `noop` (`ADR-011:296-297`), conflict detection requires comparing existing body and new extract (`ADR-011:299`), and writes dispatch through `upsert_concept_page` with merge/conflict/noop behavior (`ADR-011:307-312`). The repo implementation exists: `shared/kb_writer.py` exposes `upsert_concept_page` with those actions (`shared/kb_writer.py:591-621`), and `agents/robin/ingest.py` dispatches concept actions to it (`agents/robin/ingest.py:480-526`). So I push back on any claim that the 4-action mechanism is absent from the repo. It exists.

But ADR-016's Phase A/B flow does not call it. Phase A is hard-constrained not to touch Concept files or mentioned_in (`phase-a-subagent.md:193-200`). That part is defensible for concurrency. The failure is Phase B: it extracts wikilinks, diffs by filename existence, creates stubs for NEW concepts, and appends `mentioned_in` for existing pages (`phase-b-reconciliation.md:59-80`, `80-117`). It explicitly says "Do NOT modify the body of existing Concept pages" (`phase-b-reconciliation.md:116`). That makes `update_merge` and `update_conflict` unreachable in the parallel textbook path. ADR-016 even describes Phase B as "創 stub Concept pages → update mentioned_in" (`ADR-016:38`), despite claiming it extends ADR-011 (`ADR-016:11`). That is the real design breach.

Also, the skill file is internally contradictory: the normal Step 4 still says Concept extract uses the 4-action dispatcher (`SKILL.md:342-356`), while the parallel callout routes work through Phase A/B templates (`SKILL.md:90`) whose Phase B does stub/backlink reconciliation only.

**3. OPTION D CRITIQUE**

Option D has merit as an archival fallback, but I would not bless it as the primary KB design.

Copyright risk is the first blocker. Taiwan TIPO says fair use under the Copyright Act requires case-by-case evaluation using purpose, nature, amount/substantiality, and market effect; copying an entire textbook for class use is given as an example that exceeds fair use and infringes market value. Source: TIPO fair-use FAQ and Article 65 page: https://www.tipo.gov.tw/en/tipo2/393-2382.html and https://www.tipo.gov.tw/tw/copyright/694-17672.html. A private KB is lower risk than public publication, but the user's brief says this feeds a public content pipeline. A chapter page whose body is mostly verbatim blockquote moves from "notes/extract" toward substitute copy. That is materially riskier than a structured derivative summary with short quotes.

Token cost: Claude's claim that Option D lowers token cost is only partly true. It lowers generation effort because the LLM does not rewrite body, but it increases downstream retrieval/context cost if every answer drags large verbatim blocks. The current ch5 vault page is already 1129 lines and includes compact figure/table summaries; replacing the body with full raw chapter text would likely increase recurring retrieval payload, not just one-time ingest payload.

RAG quality: verbatim is great for exact citations, definitions, and numbers, but weaker for query expansion and synthesis. The current vault page adds normalized headings, concept maps, wikilinks, and bilingual/Chinese concept anchors (`ch5.md:582-607`, `677-695`, `1029-1049`). Those are retrieval affordances. Verbatim-only body loses semantic scaffolding unless the wrapper is strong and section-level.

Maintenance on re-edition: verbatim blocks are harder to diff semantically. A structured extract can compare claim-level changes; raw quote blocks require text-diff plus human interpretation. Mentioned_in survives only if Phase B still extracts wikilinks. But verbatim textbook prose will not naturally contain the curated Chinese `[[wikilink]]` density Phase A currently creates (`phase-a-subagent.md:151`). So Option D may preserve source text while weakening the aggregation graph unless an additional concept-linking pass is retained.

**4. VISION DESCRIBE CRITIQUE**

Vision is over-specified for every figure, but not useless. The prompt asks for domain role, axes, key data points, concept linkage, terminology, and equation transcription (`vision-describe.md:39-66`, `94-111`). ADR-011 requires Vision per figure as first-class figure handling (`ADR-011:270-278`) and stores descriptions in `figures[].llm_description` (`ADR-011:197-224`). In ch5, Vision adds real value for diagrams where captions are insufficient: Figure 5.11 captures SGLT/GLUT5/GLUT2 and Na/K-ATPase details (`vault ch5.md:77-84`), and Figure 5.18 extracts plotted curve values (`vault ch5.md:113-114`). That improves text-only retrieval.

But it is excessive for decorative or low-information images. The Phase A prompt demands every figure get a real vision read (`phase-a-subagent.md:220`), and also demands structured bold markers because pilot diff allegedly showed retrieval gains (`phase-a-subagent.md:154-166`). That evidence is asserted in the prompt, not measured in an acceptance gate. Captions already exist in walker raw as figure references and many captions; the cheaper alternative is a triage pass: caption+surrounding-text by default, Vision only for charts, pathways, anatomy, tables-as-images, equations, and figures whose caption lacks labels/data. Decorative photos should be caption-only or `[decorative]`. What is lost by removing Vision entirely: label-level retrieval for pathway/anatomy/chart figures, formula transcription from raster equations, and data values embedded only in images.

**5. RECOMMENDED PATH FORWARD**

Do not adopt pure Option D. Build v3 as a two-layer ingest with explicit acceptance gates.

Phase 1 source ingest: preserve the walker raw extract as a local archival attachment or Raw/Books chapter artifact, but publish a structured chapter source page. The source page should be claim-dense, not full verbatim. Use short verbatim anchors per section only, as current prompt intends (`chapter-summary.md:59-64`, `123-125`, `256-257`). Add a "coverage manifest" per section: headings present, figures/tables present, primary numbers present, clinical edge cases present. Acceptance should grep known raw numeric strings and table/figure counts. For ch5, a gate would have caught missing achlorhydria and cellulose-fermentation edge details (`raw:179`, `raw:295`).

Phase 2 concept aggregation: after all chapter pages are written, run the real 4-action dispatcher against extracted candidate concepts. Do not create stubs from every wikilink. Wikilinks can remain unresolved until a concept passes threshold. For existing concepts, call `upsert_concept_page` with `update_merge` or `update_conflict`. For new concepts, create a real v2 skeleton with extracted body, not the Phase B one-line stub. The code already supports this (`shared/kb_writer.py:591-786`); the textbook parallel Phase B prompt needs replacement.

Phase 3 cross-source merge trigger: only promote a concept when it is either high-value in one chapter or recurring across chapters/books. Low-value wikilinks should be left unresolved or indexed in a lightweight alias map. Highest quality does not mean "622 pages"; it means fewer, richer aggregator pages.

Vision decision: use triage. Always preserve figure files and captions. Vision only when the figure contains scientific information not recoverable from caption/body. Add acceptance gates for `figures count == embeds count`, `tables count == table references`, and "caption-only/decorative/vision" classification.

Acceptance gates: no `status: stub` explosion; no Concept body shorter than a minimum for promoted concepts; Phase B must report counts of `create/update_merge/update_conflict/noop`, not just "stubbed/updated"; sample raw-vs-source coverage must include primary numbers and edge cases; and public-output mode must refuse full-chapter verbatim blocks unless rights are explicitly cleared.

**6. CODE-LEVEL FINDINGS**

1. Parallel Phase B contradicts ADR-011. ADR-011 requires `create/update_merge/update_conflict/noop` (`ADR-011:296-312`); Phase B only creates stubs and appends backlinks (`phase-b-reconciliation.md:77-117`).

2. `update_merge` and `update_conflict` are unreachable in the parallel textbook path. Phase A cannot touch Concepts (`phase-a-subagent.md:193-200`); Phase B forbids modifying existing Concept bodies (`phase-b-reconciliation.md:116`).

3. The repo implementation is not missing. `shared/kb_writer.py` implements the dispatcher (`shared/kb_writer.py:591-786`), and Robin calls it (`agents/robin/ingest.py:480-526`). The bug is pipeline bypass.

4. The skill file documents both incompatible paths: 4-action concept extraction (`SKILL.md:342-356`) and ADR-016 parallel Phase A/B (`SKILL.md:90`).

5. The shipped textbook `concept-extract.md` is stale relative to ADR-011: it outputs `create`/`update`, not the ADR-011 four actions (`concept-extract.md:62-99`, `120-147`).

6. Stub explosion is directly caused by Phase B's template: `status: stub` and one-line placeholder body are specified at `phase-b-reconciliation.md:80-99`; the vault count confirms 544 such files.

7. Vision prompt conflict: `vision-describe.md` requires flowing prose 3-8 sentences (`vision-describe.md:106-111`), while Phase A requires structured bold-marker descriptions (`phase-a-subagent.md:154-166`, `212`). The actual ch5 vault follows the Phase A structured style (`vault ch5.md:29-144`).

8. Tables are not actually spliced inline in ch5; the source page uses Obsidian embeds like `![[...tab-5-1.md]]` (`vault ch5.md:228`, `373`, `384`, `537`) despite chapter-summary forbidding transclusion and requiring inline markdown (`chapter-summary.md:174-181`). This is a concrete implementation/spec drift.

9. Density gate is absent. ADR-011 says no word limit and no truncation (`ADR-011:41`, `252-253`), but acceptance focuses on placeholders/figures/concepts (`ADR-011:668-677`) rather than raw-content coverage. The ch5 comparison shows primary numbers mostly preserved but edge cases dropped.

10. Copyright: Option D needs an explicit rights mode. TIPO's Article 65 four-factor fair-use framework and textbook-copy example make full-chapter verbatim public-pipeline use materially risky: https://www.tipo.gov.tw/en/tipo2/393-2382.html.
