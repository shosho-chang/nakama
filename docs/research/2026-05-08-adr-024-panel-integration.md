# ADR-024 Panel Review — Integration Matrix

**Date:** 2026-05-08
**Subject:** ADR-024 Cross-lingual Concept Alignment
**Panel:** Claude (drafter, Opus 4.7 1M) → Codex (GPT-5, push-back lens) → Gemini 2.5 Pro (different reasoning chain)

---

## Verdicts

| Reviewer | Verdict |
|---|---|
| Codex | **Reject + alternative architecture** (4 reasons; minimum 5 changes if forced to modify) |
| Gemini | **Reject + refine alternative architecture** (concur with Codex; sharper emphasis on identity model) |

Both auditors converged on **ID-first multilingual labels model** as correct architecture. Universal rejection of filename-as-canonical-identity decision in ADR-024 §1.

---

## Integration matrix

| # | Topic | Claude v1 stance | Codex audit | Gemini audit | 3-way pattern | Resolution |
|---|---|---|---|---|---|---|
| 1 | Concept identity model | Filename = canonical EN; aliases in frontmatter array | Reject. ID-first slug + structured labels | Reject. **canonical_id + language-tagged labels block** (Wikipedia/SNOMED/WordNet prior art) | **Universal** | ID-first wins |
| 2 | `is_zh_native: bool` escape hatch | OK — fallback to canonical 中文 | Type-level contradiction; replace with `resolution_status: needs_human_canonicalization` | Mechanism for silent namespace pollution; replace with **proposal-queue review** (Bridge UI) | **Universal** | Replace with HITL review queue |
| 3 | Auto-write pages from ingest | Implicit (lazy build merges into existing Concept frontmatter) | Insufficient — needs review gate | **Ingest = proposal, not write.** No automated page writes. | **Universal** | Proposal artifact + 修修 curates |
| 4 | Backfill 既有 100+ 英文 Concept zh aliases | Lazy build only (隨中文書 ingest 累積) | Mandatory backfill command before production | Backfill must be **HITL UI** with candidate review | **Universal** | Backfill + HITL |
| 5 | `shared/alias_map.py` claim | Cited 5/8 P0 batch, named as engineering task | **MISSING**. Real file is `shared/kb_writer.py:716-722` simple append-dedup | n/a | **Codex factual** | File doesn't exist on main; corrected reference: `shared/concept_canonicalize.py` exists but **only on PR #441 branch `docs/kb-stub-crisis-memory`**, not merged yet |
| 6 | `concept_dispatch.py` engineering task | Named as file to modify | **Wrong path**. Live path is `agents/robin/ingest.py` + `prompts/robin/extract_concepts.md`. Schema is `ConceptPlan` in `shared/schemas/kb.py:150-168` (no canonical_en / aliases_zh / is_zh_native fields) | n/a | **Codex factual** | Fix engineering task list |
| 7 | annotation_merger "prompt 微調 + aliases context" | Minor tweak | **New wiring**. Current code at `annotation_merger.py:227-233, 321-323, 390-397` passes only `concept_slugs` strings. Need full candidate object: slug + display title + aliases + definition + source count | n/a | **Codex factual** | Reframe scope, expand candidate object |
| 8 | ADR-022 prerequisite | "Future validation note" | **Hard prerequisite**. Code direction verified; production rebuild log + dim assertion required before ADR-024 acceptance | Lifecycle concern: BGE-M3 → BGE-M4 future replacement, sparse path should not depend on any single embedding model | **2-of-3** | Gate ADR-024 on ADR-022 production rebuild verification |
| 9 | Token budget for grounding pool | "Sonnet 200k 應該還很寬" | Safe at 100 Concepts label-only (~6,800 chars / ~1,700-6,800 tokens). **EXPLODES if body excerpts copied from `agents/robin/ingest.py:_build_existing_concepts_blob`** (800 chars × N pages → 800k chars at 1000 Concepts) | Bloats past 500 Concepts; not just budget but signal degradation | **2-of-3** | Pin pool shape: labels + aliases only, NO body excerpts |
| 10 | zh-Hant vs zh-Hans split | Not addressed | Not raised | **HARD REQUIREMENT**. ADR completely ignores 繁/簡 distinction. NFKC doesn't handle character-set conversion. Scraped 简体 articles will silently fail to match 繁體 KB | **Single-source (Gemini)** | Must address — `labels.zh-Hant` + `labels.zh-Hans` (or normalization layer) |
| 11 | Cross-domain prior art | Not raised | Not raised | Wikipedia/Wikidata QID, MediaWiki redirects, ICD-10/SNOMED CT codes, WordNet/BabelNet synsets — **all converged on language-agnostic identifier**. ADR-024 ignores entire body of prior art | **Single-source (Gemini)** | Adopt — `canonical_id` (e.g. `cpt_20260508_ax4g`) |
| 12 | Wikilink alias portability | `[[Concepts/X\|alias_zh]]` works in Obsidian | Not raised | **Obsidian-specific rendering trick**. Couples knowledge representation to one editor; Brook compose downstream may break | **Single-source (Gemini)** | Flag risk; verify against `agents/brook/synthesize/_outline.py` |
| 13 | `monolingual-zh` mode lock-in | OK as enum value (CONTEXT-MAP frozen) | Not raised | **Premature lock-in**. Should be `monolingual` + `lang` parameter for future Japanese/Korean book support | **Single-source (Gemini)** | Refactor to `monolingual` + `lang` field; revise CONTEXT-MAP |
| 14 | "Vault 簡潔" justification for rejecting Chinese namespace (Option B) | Sufficient | **Insufficient**. Preference claim, not technical argument. Must address identity stability + redirect/merge tooling + Brook rendering | More damaging: **single most damaging decision** in ADR-024. Codex push-back not strong enough | **Universal but stronger from Gemini** | Strengthen technical case OR adopt ID-first which sidesteps this |
| 15 | Recovery loop for wrong LLM mapping | Not addressed | Not raised | **Critical missing**. When 「肌酸」→「Creatinine」silent error commits, no UI surface for 修修 to detect or correct. Cross-lingual systems require disambiguation step | **Single-source (Gemini)** | Required — proposal-queue Bridge UI |
| 16 | Branch dependency (PR #441 not merged) | Not raised | Not raised (couldn't see other branch) | n/a | **Claude post-audit** | ADR-024 has hard upstream dep on PR #441 closing (gated on #501 HITL by 修修) |

---

## What this means

The 8 grill questions framed ADR-024 as a refinement of existing pattern (filename canonical + aliases). Both Codex and Gemini independently arrived at the same conclusion: **the framing itself was wrong**. The grill optimised within a flawed solution space.

**Universal items (1-4)** plus zh-Hant split (10), cross-domain prior art (11), and recovery loop (15) require **structural rewrite**, not modification. The proposed alternative is essentially Wikipedia/SNOMED's model:

```yaml
canonical_id: cpt_20260508_ax4g
labels:
  en:
    preferred: "Mitophagy"
    aliases: ["Mitochondrial autophagy"]
  zh-Hant:
    preferred: "粒線體自噬"
    aliases: ["粒線體吞噬作用"]
  zh-Hans:
    preferred: "线粒体自噬"
    aliases: []
```

Plus:
- Concept page filename = stable slug (e.g. `Concepts/cpt_mitophagy.md` or `Concepts/Mitophagy.md` as legacy slug)
- Wikilinks resolve to `canonical_id`, not filename — rename cascade goes away
- Ingest output = proposal artifact in Bridge UI review queue
- Backfill 既有 100+ English Concept zh-Hant labels = HITL UI before production
- Annotation merger consumes candidate **objects**, not slug strings (slug + display + aliases + definition + source count)

---

## Open questions for 修修

### Q-A: 整體方向

| 選項 | 描述 | Cost | Risk |
|---|---|---|---|
| **A1. 接受 panel verdict，重寫 ADR-024 為 ID-first multilingual identity model** | 採用 Gemini prescriptive `canonical_id + labels` block；ingest 走 proposal-queue 不直接寫 page；annotation_merger 改 candidate object；backfill HITL UI | High — 多週重寫含 100+ Concept frontmatter migration + Bridge UI review queue + ADR-022 production verification | Low — well-trodden Wikipedia/SNOMED 路線；長期 lock-in 可控 |
| **A2. Defer ADR — 不 commit、收回 grill** | 你手上 2 本中文書先用 hack（同 epub 兩次當 bilingual+original，視覺壞但能讀），等實 ingest 5+ Chinese books 後才 ADR + ship 真正的 multilingual model | Low — 只 docs revert | Medium — 邊讀邊累積 hack debt；annotation 三型 ingest 可能未對齊 |
| **A3. 縮成 reversible pilot — read-only path** | Ship monolingual-zh **只到 reader + annotation 存到 KB/Annotations/，不 ingest 進 KB Concept namespace**；annotation_merger 跳過 cross-lingual sync；等真實使用後才決定 identity model | Medium — 讀寫 path 獨立成立、KB 不污染 | Low — 完全可逆；但 Brook synthesize 撈不到中文書 evidence |
| **A4. 局部修 ADR-024 — 接受 Codex 5 minimum changes 但拒絕 Gemini ID-first 重寫** | 修 file paths + 替換 `is_zh_native` 為 review status + 加 backfill cmd；保留 filename-as-canonical | Medium — 比 A1 輕但仍要實作 | High — Gemini 警告的 multi-year linguistic debt 不解；2 年後重寫成本翻倍 |

### Q-B: 如果走 A1（重寫），要不要先解掉 ADR-022 production gate + PR #441 merge

A1 有兩個 hard upstream dependency：
- **ADR-022 production rebuild verification** — code direction已切 BGE-M3 但 production index 沒 verify。Codex 要求 dim assertion + rebuild log entry
- **PR #441 (`docs/kb-stub-crisis-memory`) 合進 main** — 我之前誤稱的 alias_map 實為 `shared/concept_canonicalize.py`，現在只在 #441 branch，卡 #501 HITL 等你 review BSE ch3 staging

走 A1 前要不要先 unblock 這兩條？這是序列性決定。

---

## 我的建議

**A3 reversible pilot**。

理由：
- 修修 stated need 是「兩本中文書能讀」，不是「重建 KB Concept namespace」。Pilot 只到 reader + annotation 直接命中需求
- A1 是正確架構但成本高 — 你目前還沒 5+ 中文書的真實使用 data，refactor 100+ Concept 進 ID-first 是賭未來 use case
- A3 把架構決定 defer 到「我們手上實有 5 本中文書 + 看到實際 cross-lingual 撞牆 case」之後，那時再 grill 一次（含 zh-Hant/zh-Hans split + Japanese/Korean 規劃）會是 **data-driven** decision，不是現在的 speculative
- A3 不阻擋 A1：之後重寫 ADR + migrate 既有 zh annotations 進 ID-first identity 是 incremental 動作

具體 A3 scope：
- PRD-A schema：mode field 改 **`mode: "monolingual" | "bilingual"` + `lang: "zh-Hant" | "zh-Hans" | "en"`** 兩 field（採 Gemini #13 推薦，避免 monolingual-zh 早期 lock-in）
- PRD-B book: upload + reader monolingual layout + annotation 存 `KB/Annotations/{book_id}.md`（v3 schema 已支援，不動）
- PRD-C document: Web Clipper 中文檔 detection + reader monolingual layout
- **不做**：textbook-ingest zh-source variant、annotation_merger cross-lingual prompt 微調、grounding pool zh extension、frontmatter aliases zh backfill — 全部 defer 到 A1 重寫
- ADR-024 標 **superseded** + 留作 reference，新 ADR 等 5+ books 後寫

代價：你寫中文長壽科普時 Brook synthesize 暫時撈不到中文書 evidence（只撈得到中文 article 跟 既有英文 KB）。但這跟你目前的 stage 是匹配的 — 你**還沒**寫第一篇 monolingual-zh 來源的長壽文章。

---

**先給你決 Q-A**：A1 / A2 / A3（推薦） / A4？
