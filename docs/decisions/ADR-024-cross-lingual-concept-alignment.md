# ADR-024: Cross-lingual Concept Alignment for monolingual-zh Source Ingest

**Status:** Superseded (2026-05-09) — see `docs/plans/2026-05-09-monolingual-zh-reader-annotation-pilot-prd.md`
**Date:** 2026-05-08
**Deciders:** shosho-chang

> **Supersede reason** (2026-05-09): Panel review (Codex GPT-5 + Gemini 2.5 Pro) on 2026-05-08 unanimously rejected this draft + recommended ID-first multilingual identity model. 修修 chose reversible pilot path: ship monolingual-zh **reader + annotation only**, defer all cross-lingual ingest decisions (Concept naming, prompt grounding pool, annotation_merger sync) until 5+ Chinese books actually consumed and real cross-lingual retrieve breakage observed. This ADR's three coupled decisions (filename canonical + zh-source ingest variant + merger aliases context) are NOT shipping. Body kept as reference for Phase 2 grill input — content NOT to be implemented as-is.
>
> Replacement spec: see Phase 1 PRD at `docs/plans/2026-05-09-monolingual-zh-reader-annotation-pilot-prd.md`.
> Panel artifacts: `docs/research/2026-05-08-codex-adr-024-audit.md` + `docs/research/2026-05-08-gemini-adr-024-audit.md` + `docs/research/2026-05-08-adr-024-panel-integration.md`.

**Related:**
- 凍結 [grill summary 2026-05-08](../plans/2026-05-08-monolingual-zh-source-grill.md) Q5 + Q6 + Q7 三題的同條 cross-lingual implementation 軸
- 擴 [ADR-017 annotation KB integration](ADR-017-annotation-kb-integration.md) 的 annotation_merger LLM-match prompt
- 擴 [ADR-021 §1 v3 schema](ADR-021-annotation-substance-store-and-brook-synthesize.md) — `KB/Annotations/{slug}.md` 結構不動，新增 cross-lingual semantics
- 倚賴 [ADR-022 multilingual embedding default](ADR-022-multilingual-embedding-default.md) — BGE-M3 全 KB index 是這個 ADR 的 dense retrieval 前提
- CONTEXT-MAP 詞彙：bilingual mode / monolingual-zh mode（2026-05-08 凍結）
- 5/8 P0 batch (#496-#502) — alias_map seed dict (NFKC + casefold + plural + seed alias dict) 是這 ADR 擴展的基底

---

## Context

修修要 ingest 純中文書（台版中譯 EPUB）+ 純中文網路文章。既有 KB 100+ Concept page **全部英文**（從 BSE / Sport Nutrition 等英文教科書 ingest 出來）— 中文 source 進 KB 是首次。

具體 surface 的 cross-lingual 問題：

1. **Concept page 命名語言** — 中文書抽出來的 concept（例「粒線體自噬」）要建什麼 page？跟既有 `Concepts/Mitophagy.md` 怎麼接？
2. **Ingest concept extraction prompt** — `textbook-ingest` skill 的 prompt 對英文 source 抽英文 concept name 已 ship、實戰穩定。中文 source 怎麼處理？
3. **Annotation → Concept page sync 跨語言 match** — 修修在 Reader 標中文 highlight 後按 sync，annotation_merger 怎麼 match 既有英文 Concept？

這三個問題實際是**同一條 cross-lingual implementation 軸**：Concept namespace 的命名規則決定了 ingest prompt 怎麼產 name、決定了 annotation merger 怎麼 match name。任何一處選錯下游全錯。

## Decision

### 1. Concept page 命名：英文 canonical + 中文 alias frontmatter

**規則**：

| Aspect | Spec |
|---|---|
| 一般 concept page filename | 英文 canonical（`Concepts/Mitophagy.md`） |
| frontmatter `aliases` array | 中文同義詞（`["粒線體自噬", "粒線體吞噬作用"]`） |
| 中文 native concept（無英文業界 term） | fallback canonical 中文（`Concepts/中醫脾胃論.md`），aliases 反向是英文翻譯（如有） |
| 既有 `_alias_map` seed dict | 擴 zh-EN entries — 從 ingest 出來的 aliases_zh 自動 merge |
| Brook synthesize / wikilink display | `[[Concepts/Mitophagy\|粒線體自噬]]` — wikilink alias display |

**3 個 user journey 驗證**：

- **中文 highlight sync**：`annotation_merger` LLM-match 拿中文 highlight + 既有 `Mitophagy.md` (frontmatter aliases 含「粒線體自噬」) → 命中 ✅
- **Obsidian 內搜尋「粒線體自噬」**：vault 內建 wikilink alias 認 frontmatter aliases，搜尋直接命中既有 page ✅
- **Brook 寫中文長壽科普 synthesize**：query 繁中 + 英文 keywords → multilingual embedding 跨語言 dense retrieve → 撈到 `Mitophagy.md` chunks → outline 渲染走 `[[Concepts/Mitophagy|粒線體自噬]]` alias display ✅

### 2. Ingest concept extraction：zh-source prompt variant + grounding pool

**規則**：

- `textbook-ingest` skill 加 zh-source prompt variant，detection 分流（`mode==monolingual-zh` 走新 prompt）
- 既有 EN-source prompt **不動**（zero 回歸風險）
- zh-source prompt **預先帶既有 KB Concept name + alias 當 grounding pool**
- 輸出 schema：

```python
class ZhConceptExtraction(BaseModel):
    canonical_en: str          # 必須英文，優先匹配 grounding pool 既有 name
    aliases_zh: list[str]      # 原文出現的中文同義詞，最多 3 個
    is_zh_native: bool         # true 時 canonical 可中文（無英文業界 term 逃生口）
```

**為什麼帶 grounding pool**：純 zh-source prompt 不加防護的失敗模式：

- **Hallucinate 假英文 canonical**：「肌酸」→ "Creatine" ✅；「氣血」→ "Qi-Blood Theory" ❌（編造）
- **既有 KB concept 沒命中**：明明 KB 有 `Mitophagy.md`，LLM 為新書抽出來叫 "Mitochondrial Autophagy" — alias map 沒接上、Concept page 重複建

Grounding pool 喂進 prompt 讓 LLM 看到既有 namespace，優先 reuse 而非自由產生。

**`is_zh_native` 逃生口**：處理「氣血 / 陰陽 / 中醫脾胃論」這類**沒英文業界標準 term** 的 concept — 不硬塞 LLM 翻譯。LLM 判定 `is_zh_native=true` 時，canonical 直接用中文（`Concepts/中醫脾胃論.md`）。

### 3. annotation_merger LLM-match：prompt 微調 + 帶 aliases 進 candidate context

**改動**（`agents/robin/annotation_merger.py`）：

- candidate Concept page 顯示時帶 frontmatter aliases 一起：

  ```
  Mitophagy (aliases: 粒線體自噬, 粒線體吞噬作用)
  ```

- system prompt 加一句：

  > Annotation 跟 Concept page name 可能不同語言。請按 semantic 判斷該 annotation 該不該注入該 Concept，不是看字面相同。

- 1-2 個 few-shot example（中文 annotation + 英文 Concept canonical + 中文 alias 的 match 範例）
- vector retrieve top-N 從 3 加大到 5（候選池更廣，補 ADR-022 multilingual embedding 仍可能 miss 邊界 case）

**不新加 zh-annotation prompt variant** — 任務性質是 judgement（在固定 candidate set 裡選哪個）不是 extraction（自由產 string），新 variant overkill。

## Considered Alternatives

### Concept page 命名（Decision 1）

| 選項 | 拒絕理由 |
|---|---|
| **A. 強制英文（LLM-translate 抽出來的中文 phrase）** | 翻譯 step 引入 hallucinate + accuracy risk；中文 native concept（氣血 / 中醫術語）強制英文翻譯產出怪詞，鎖進既有 KB 更糟 |
| **B. 直接中文（並列既有英文 page）** | vault 內 Concept page 中英並陳，違反「vault 簡潔」first-class concern；alias 失效（搜尋繁中時 `Mitophagy.md` 找不到、要記得新建的中文 page）；KB 累積後分裂、aggregator 失效 |

### Ingest prompt（Decision 2）

| 選項 | 拒絕理由 |
|---|---|
| **A. 複用既有 prompt + post-translate step** | 兩 step 各自 hallucinate；中間 state（純中文 concept）若意外 commit 進 KB 污染 |
| **C. 雙語 robust 單 prompt** | LLM「自己判斷 source 語言」自由度高 → 越 free-form 越容易飄；既有英文 prompt 可能被改動干擾，回歸風險高 |

### Annotation merger（Decision 3）

| 選項 | 拒絕理由 |
|---|---|
| **A. 不改 prompt，賭 ADR-022 + 既有英文 LLM judge cross-lingual robust** | 未驗證，可能 silent miss（中文 annotation 命中 0 candidate） |
| **C. 新加 zh-annotation prompt variant** | judgement 任務不是 extraction，固定 candidate set + alias 已經足夠，新 variant overkill；維護成本翻倍 |

## Consequences

### 必做工程任務

1. **`shared/schemas/kb.py`**（如有）：Concept page frontmatter `aliases: list[str]` 形態化 — 既有 ad-hoc convention 升 Pydantic schema
2. **`agents/robin/concept_dispatch.py`**（or wherever extraction prompt lives）：
   - 加 zh-source prompt variant
   - grounding pool inject 機制（從 `Concepts/*.md` frontmatter aliases 撈 + 餵 prompt）
   - 輸出 schema 加 `is_zh_native` field 處理
3. **`agents/robin/annotation_merger.py`**：
   - candidate display format 加 aliases
   - system prompt 微調（見 Decision 3）
   - 加 1-2 個 cross-lingual few-shot example
   - vector retrieve top-N 3 → 5
4. **`shared/alias_map.py`**（5/8 P0 batch ship）：擴 zh-EN aliases 路徑 — 從 textbook-ingest 出來的 aliases_zh 自動 merge
5. **既有 100+ 英文 Concept page**：lazy build 補 zh aliases — textbook-ingest 跑中文書時自動 merge 進 frontmatter aliases array（不批次跑）
6. **acceptance test**：
   - sample 5 條中文 highlight × 既有英文 Concept 跑 annotation_merger，驗 match precision
   - sample 1 章中文書 ingest 出來的 concept extraction，驗 grounding pool 命中率（vs 不帶 grounding 的 baseline）

### 重要副作用

- **既有英文書 ingest 行為不變**（zero 回歸 risk）— EN-source prompt 不動、annotation_merger prompt 微調對英文 input 仍保持原行為（aliases 在英文 page 裡為空 array 不影響 match）
- **5/8 P0 batch 的 alias_map seed dict 自然延伸** — 既有 NFKC + casefold + plural 機制 + seed dict 擴 zh-EN entries 是 incremental change
- **Brook synthesize wikilink 渲染要對齊**：output 為中文文章時自動走 `[[Concepts/X|alias_zh]]` display；output 為英文（如有）走 canonical
- **第一次中文書 ingest 完，vault 內可能短暫出現「沒中文 alias 的英文 Concept page」**（既有英文書 ingest 出來的 page）— lazy build 機制補
- **跨語言 retrieval 倚賴 ADR-022** — BGE-M3 全 KB index 沒 ship 的話這 ADR 假設失靈

### 不做的事

- **不批次跑 LLM 補既有 100+ 英文 Concept 的 zh aliases** — lazy build 為主、batch 是 nice-to-have、PRD-B ship 後再評估
- **不為中文 native concept 建獨立 namespace** — 跟英文 canonical concept 共用 `Concepts/` 目錄，純靠 filename 區分（`Mitophagy.md` 英文 / `中醫脾胃論.md` 中文）
- **不把 wikilink alias display 自動化進 annotation_merger 寫進 Concept page section 的內容** — Section 內容仍 verbatim 寫 annotation 原文（中文）；只是 wikilinks 引用 Concept 時走 alias display
- **不採 `_alias_map` 動態 LLM-generated cache** — alias 是 Concept page frontmatter authoritative，alias_map 只是 in-memory acceleration（PRD-A spec 階段拍）

### 待驗證假設（PRD-B 實作期間驗）

1. **`textbook-ingest` zh-source prompt 對中文書實戰準度** — 中文書抽 concept 後 canonical_en 的命中率（vs grounding pool 既有 name 重用率 vs 新建率 vs hallucinate 假名率）
2. **annotation_merger 中文 highlight match 英文 Concept 的 precision/recall** — sample 5-10 條測，acceptance threshold ≥ 0.8 precision
3. **grounding pool token budget** — 既有 100+ Concept × 3-5 alias 喂 prompt 是否會撐爆 input token（Sonnet 200k 應該還很寬，但 ingest scale 起來要驗）
4. **`is_zh_native` 逃生口的判準準度** — LLM 判定中文 native vs 應該英文 canonical 的 boundary case，需 sample 中醫類書 / 台灣本土書驗
