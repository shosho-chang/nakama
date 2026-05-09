# ADR-024 Cross-Lingual Concept Alignment — Codex Audit

**Date:** 2026-05-08  
**Reviewer:** Codex (GPT-5, independent third-party push-back lens)  
**Subject:** ADR-024 monolingual-zh source ingest, cross-lingual Concept alignment  
**Verdict:** Reject + alternative architecture

---

### Section 1 — CODE GROUNDING
The has_original gate exists exactly where the grill summary claims. thousand_sunny/routers/books.py:118-124 takes bilingual: UploadFile = File(...) and original: UploadFile | None = File(None) with lang_pair: str = Form("en-zh"). The ingest-request endpoint at lines 236-243 says if not book.has_original: raise HTTPException(400, detail="book has no original EN file to ingest"). Monolingual-ZH ingest is structurally blocked before concept alignment begins.

shared/translator.py is hardcoded EN-to-Taiwan-Chinese, not a multilingual abstraction. Lines 30-32 declare def load_glossary() -> dict[str, str] returning {英文: 台灣中文} dict. Lines 45-57 expose def add_glossary_term(english: str, zh_tw: str) writing user_terms[english.lower()] = zh_tw. Lines 80-89 render glossary rows as - {en} -> {zh} with instruction 使用**台灣繁體中文**. Lines 116-119 open the batch prompt as 請將以下 {len(segments)} 段學術文字翻譯成台灣繁體中文. ADR-024 is correct to treat translator behavior as structurally EN-ZH-bound.

The claimed shared/alias_map.py / _alias_map seed dictionary is **MISSING**. ADR-024 at line 12 cites 5/8 P0 batch (#496-#502) — alias_map seed dict (NFKC + casefold + plural + seed alias dict) and at line 129 names shared/alias_map.py as a required engineering task. A recursive filename search for *alias* in the QA checkout found only memory/claude/feedback_init_py_alias_shadows_module.md. No shared/alias_map.py exists. The shipped alias behavior lives in shared/kb_writer.py:716-722, which appends aliases if not already present and not equal to slug. That is not NFKC normalization, not casefold, not plural handling, and not a shared resolver. ADR-024 builds on a non-existent code foundation.

annotation_merger.py does not currently pass frontmatter aliases into candidate context. The v1 path at lines 227-233 passes concept_slugs=", ".join(concept_slugs) to the prompt, where _list_concept_slugs at lines 287-292 returns only p.stem for *.md files. The v2 path at lines 321-323 and 390-397 does the same: prompt header is Existing concept slugs: with no aliases. Therefore ADR-024 ### 3. annotation_merger LLM-match：prompt 微調 + 帶 aliases 進 candidate context describes new wiring, not current code.

upgrade_to_v3 exists as claimed. shared/annotation_store.py:243-280 defines def upgrade_to_v3(ann_set: AnnotationSetAny) -> AnnotationSetV3 handling v1/v2/v3 input. The book router at thousand_sunny/routers/books.py:30-35 imports it and saves upgrade_to_v3(ann_set) at lines 369-374. ADR-021 v3 claim is verified in code.

---
### Section 2 — DRIFT DETECTION

ADR-024 does not contradict ADR-017 on annotation storage. ADR-017 at lines 24-27 says annotations are stored at KB/Annotations/{slug}.md. ADR-024 at lines 7-10 says KB/Annotations/{slug}.md 結構不動，新增 cross-lingual semantics. That part is aligned.

ADR-017 book-amendment section is stale. ADR-017 at lines 113-121 says book routes operate on v2 only. Live code imports AnnotationSetV3 and upgrade_to_v3 at books.py:30-35, accepts v3 payloads at lines 340-357, and upgrades at lines 369-374. ADR-024 must cite ADR-021 as the authoritative annotation contract and stop leaning on ADR-017 language except for storage location.

ADR-024 underplays the ADR-022 prerequisite gate. ADR-021 at lines 208-211 says 實作上 ADR-022 必須先 ship. ADR-024 at lines 140-141 says BGE-M3 全 KB index 沒 ship 的話這 ADR 假設失靈. This is not a future validation note. It is a hard prerequisite. ADR-024 must gate its own acceptance test on a verified BGE-M3 index rebuild with a runtime dimension assertion.

ADR-024 names agents/robin/concept_dispatch.py in ### 必做工程任務 as the file to modify for zh-source concept extraction. That file does not exist. The live path is agents/robin/ingest.py and prompts/robin/extract_concepts.md. _get_concept_plan at ingest.py:368-376 loads the extract_concepts prompt. The ConceptPlan schema in shared/schemas/kb.py:150-168 contains slug, action, candidate_aliases, extracted_body, and conflict — no canonical_en, aliases_zh, or is_zh_native. Fix the engineering task list to name the correct files before approval.

is_zh_native: bool collides with the grounding pool contract. ### 2. Ingest concept extraction：zh-source prompt variant + grounding pool at line 59 declares canonical_en: str  # 必須英文 and at line 61 says is_zh_native: bool  # true 時 canonical 可中文. That is a type-level contradiction disguised as an escape hatch. The grounding pool is a reuse mechanism for existing canonical names; is_zh_native is an ontology decision about namespace membership. Do not merge both into one LLM boolean that immediately writes a page.

---
### Section 3 — NUMERICAL / FACTUAL CLAIMS

KB Concept count is **INACCESSIBLE** in this checkout. No E:/nakama-qa-adr021/KB/Wiki/Concepts directory exists, so ADR-024 line 18 claim 既有 KB 100+ Concept page 全部英文 cannot be verified. The author must cite the actual vault path and include a generated count, or remove the number entirely.

The 5/8 P0 alias seed dictionary is **NOT FOUND**. ADR-024 references _alias_map at lines 39, 129, and 138. No shared/alias_map.py exists anywhere in the QA checkout. This is a false precedent claim and must be corrected before approval.

Token budget math is safe at 100 Concepts but understates the growth curve. Label-only pool: 100 names x 20 chars = 2,000 chars; 100 x 4 aliases x 12 chars = 4,800 chars; total ~6,800 chars. At mixed CJK/ASCII density that is approximately 1,700-6,800 tokens. Sonnet 200k handles that easily. The real risk is implementation drift into body-bearing prompts. The existing Robin grounding blob in agents/robin/ingest.py:60-80 includes body excerpts up to 800 chars per page. At 100 Concepts that adds ~80,000 chars; at 1,000 Concepts it reaches ~800,000 chars. ADR-024 line 154 claim Sonnet 200k 應該還很寬 is true only for a label-only grounding pool. Define the pool shape explicitly — names and aliases only, or retrieval-selected body snippets — before implementation copies the full existing-concepts blob by accident.

---
### Section 4 — ASSUMPTION PUSH-BACK

ADR-022 code support does not prove the production index has shipped. shared/kb_embedder.py:1-9 shows BGE-M3 as default with 1024-dim kb_vectors. shared/kb_embedder.py:33-35 sets _DEFAULT_BACKEND = "bge-m3" unless NAKAMA_EMBED_BACKEND=potion. That verifies source code direction only. It does not verify that data/kb_index.db was rebuilt at 1024-dim, that production uses the same env, or that retrieval is actually cross-lingual today. ADR-024 must require a runtime dimension assertion and a rebuild verification log entry before any cross-lingual merger acceptance test. Ship ADR-022 to verified production first. Then approve ADR-024.

Do not call annotation_merger changes a prompt 微調. Current code at annotation_merger.py:390-397 exposes only concept slugs to the LLM. ADR-024 ### 3. annotation_merger LLM-match：prompt 微調 + 帶 aliases 進 candidate context proposes adding aliases, few-shot examples, and expanding top-N from 3 to 5. That is insufficient for cross-lingual biomedical matching. The candidate object must include slug, display title, aliases, a short definition, and source count. Cross-lingual matching between Chinese annotation text and English biomedical terms needs deterministic candidate enrichment before LLM judgement. Expanding top-N without enriching candidates increases noise. A candidate absent from the pool cannot be rescued by top-N=5.

Do not reject a full Chinese namespace using only vault 簡潔 as justification. ADR-024 at lines 98-99 rejects Chinese-filename pages because they 違反「vault 簡潔」first-class concern. That is a preference claim, not a technical argument. The rejection must address identity stability, redirect/merge tooling, source-of-truth label rules, and Brook rendering behavior. Without those four arguments, option B is insufficiently dismissed and should be reopened.

Do not make lazy alias build the only migration strategy for old Concepts. ADR-024 at lines 143-145 says 不批次跑 LLM 補既有 100+ 英文 Concept 的 zh aliases. That abandons every existing English page that never reappears in a Chinese source. Search, Brook rendering, and annotation sync will stay English-only for those pages indefinitely. Add a backfill command that generates zh-Hant candidate aliases via LLM with human review. Lazy build on top of a backfill is acceptable; lazy build as the sole strategy is not.

is_zh_native must not be a silent boolean with immediate page-write consequences. When 自由基 is incorrectly marked native it creates a Chinese-filename page competing with an existing English Free Radicals page. When a truly Chinese-native concept is forced into canonical_en, the system invents a false English term and calcifies it. Replace the boolean with resolution_status: needs_human_canonicalization and require review before any page write.

---
### Section 5 — ALTERNATIVES NOT CONSIDERED

**A. URI/slug-based namespace.** Use a language-agnostic concept identity and stop making filename language carry ontology weight. Pages use a stable prefixed slug like Concepts/cpt_mitophagy.md while frontmatter carries canonical_id. Labels become structured data: labels.en, labels.zh-Hant, labels.zh-Hans, aliases.en, and aliases.zh-Hant. This kills the filename-language decision entirely. Rename cost drops because changing "Mitophagy" to a better English label changes a frontmatter value, not file identity. This is the correct design for a five-year multilingual KB.

**B. Bilingual Concept page body.** If the project keeps English canonical filenames, add visible bilingual label sections to the page body instead of hiding Chinese terms in frontmatter. A ## Language Labels section with English canonical, zh-Hant preferred label, zh-Hans variants, and rejected aliases gives the author an inspectable model in Obsidian and gives Brook a deterministic display source. Frontmatter-only aliases are opaque to vault readers and easy to rot silently.

**C. Defer the namespace ADR until 5+ Chinese books are ingested.** The advantage is real data: false friends between Traditional Chinese technical terms and English biomedical ones, simplified/traditional character variants, genuinely Chinese-native concepts with no English equivalent, and actual cross-lingual matching miss rates. The cost is early mess and migration work. If unblocking two specific books is urgent, ship a reversible pilot with per-book review gates and no automatic page writes. Treat it as data collection, not architecture lock-in.

---
### Section 6 — FINAL VERDICT

**Reject + alternative architecture.**

ADR-024 cannot be approved as-is for four reasons. First, shared/alias_map.py does not exist; the P0 batch precedent ADR-024 builds on is false. Second, annotation_merger.py currently exposes only slugs to the LLM — the alias wiring is new code, not a tweak, and the engineering task list names the wrong source file (concept_dispatch.py instead of ingest.py). Third, is_zh_native: bool in ### 2. Ingest concept extraction：zh-source prompt variant + grounding pool creates a type-level contradiction and writes pages without a human review gate. Fourth, ### 1. Concept page 命名：英文 canonical + 中文 alias frontmatter makes filename language a multi-year identity decision whose sole stated justification is the preference claim vault 簡潔.

The alternative architecture: adopt an ID-first multilingual label model. Keep existing English filenames as legacy slugs but add canonical_id and language-tagged label fields (label_en, label_zh_hant, aliases_en, aliases_zh_hant) to Concept frontmatter. Build a resolver that normalizes candidate labels using NFKC and casefold, checks structured labels deterministically, and sends only genuinely ambiguous cases to an LLM with enriched candidates. Make annotation merger consume candidate objects — slug, display title, aliases, definition excerpt, source count — not slug strings. Make ingest output concept_id_or_null, label_zh, label_en, language, and resolution_status; do not write pages from a bare boolean. Add an on-demand alias backfill command for old English Concepts before declaring monolingual-ZH ingest production-ready.

If the owner refuses rejection and insists on modifications, the minimum five required changes are: (1) rewrite ### 1. Concept page 命名：英文 canonical + 中文 alias frontmatter to center stable identity rather than filename language; (2) rewrite ### 2. Ingest concept extraction：zh-source prompt variant + grounding pool to replace is_zh_native: bool with resolution_status: needs_human_canonicalization; (3) rewrite ### 3. annotation_merger LLM-match：prompt 微調 + 帶 aliases 進 candidate context to require enriched candidate objects and fix the file path from concept_dispatch.py to ingest.py; (4) either implement shared/alias_map.py before approval or remove it entirely from ### 必做工程任務 and ### 重要副作用; (5) replace the lazy-only alias policy in ### 不做的事 with a mandatory backfill command requirement.