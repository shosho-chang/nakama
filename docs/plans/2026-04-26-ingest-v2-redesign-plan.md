# Ingest Pipeline v2 — Redesign Plan

> **凍結日**：2026-04-26
> **觸發**：textbook-ingest skill Phase 1 MVP 在 ch1 單章測試後，發現整個 ingest 生態系（含 Robin、kb-ingest、textbook-ingest）有觀念級設計缺陷
> **對齊**：Karpathy gist personal cross-source wiki 哲學（<https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>）
> **Owner**：修修 + Claude Opus 4.7

---

## 1. 凍結的 4 個設計原則

修修在 2026-04-26 session 確認以下原則，所有 ingest pipeline 動工都必須對齊：

| # | 原則 | 內涵 |
|---|---|---|
| **P1** | **Karpathy aggregator** | concept page 是 cross-source evidence aggregator，不是事實 oracle、也不是 ingest changelog；新 source 進來要把資訊真正 merge 進 concept body 主體（Definition / Core Principles / Practical Applications），有衝突另闢 `## 文獻分歧 / Discussion` 結構化記錄 |
| **P2** | **LLM-readable deep extract** | ingest 用最強 model（Opus 4.7）+ 不省 token，因為一次 ingest 影響後續多次 retrieval；不為人類閱讀友善而摺扣，給 agent 消化用，retrieval-time 由下游 agent 做風格與抽象層級調整 |
| **P3** | **圖表 first-class** | 圖片必須 export + Vision LLM domain-aware describe + table preserve as markdown table + math 公式 LaTeX 化；不可 drop 任何 visual / structured 內容 |
| **P4** | **Conflict detection mandatory** | aliases-based dedup（同義異名）+ 強制讀既有 page body 做 cross-source diff + `## 文獻分歧 / Discussion` aggregation；新 source 對同一 concept 提出與既有相異觀點時必須結構化記錄、不可靜默 append |

**Token / Wall-time reality check**（用 Opus 4.7 thorough 模式）：

- 單章 input（含 Vision pass）~30-50k token
- 單章 output（chapter source thorough）~30-80k token
- 全書 11 章 ~500k-1M token / 60-90 分鐘
- Max 200 monthly quota：一本中型教科書 ≈ 一週 quota 高 burn day（合理投資 — 一次 ingest 影響後續多次 retrieval）

---

## 2. 為什麼要 v2（Phase 1 MVP 揭露的觀念級破洞）

textbook-ingest skill Phase 1 MVP 把 *Biochemistry for Sport and Exercise Metabolism* (MacLaren & Morton, Wiley 2024) Ch.1 ingest 進 vault 後，發現：

1. **chapter source 雖然結構化但訊息密度遠低於原書** — 圖、表、公式全 drop（EPUB 的 `<img>` `<table>` `<math>` 都被 BeautifulSoup `get_text()` 攤平）
2. **既有 6 個 concept page update 都只是 body 末尾 append `## 更新（date）` block** — 內容是 LLM 寫的 imperative todo（「應新增 X、應補充 Y」），沒有真正 merge 進 concept body 主體
3. **`磷酸肌酸系統.md` 對 PCr 主導時間的敘述是 10-15 秒、ch1 教科書原文是 1-10 秒** — 兩個說法很可能 measure 不同 endpoint，但目前 pipeline 沒有偵測 / 沒有寫進 Discussion section、agent retrieval 拿到 page 會自信地說 10-15 秒
4. **`ATP再合成.md` 與 `肌酸代謝.md` frontmatter 已壞**（mid-page `---` + raw text），是 `_update_wiki_page` 對 unicode 長 filename 的 yaml.dump 處理錯誤造成的歷史 bug
5. **textbook-ingest skill 規範 `mentioned_in:` schema、Robin production code 寫 `source_refs:` schema** — 兩套 schema 並存且互不相容

這五個現象的共通根因：**Karpathy aggregator 哲學從未在 production code 實作過**。修這個破洞需要重寫 update path 為「LLM diff-merge into main body」+ 強制讀既有 body 做 conflict detection + Discussion section 結構化、共用底層 module（`shared/kb_writer.py`）統一寫入路徑。

---

## 3. Audit findings 摘要

完整 audit（含每個 finding 的檔案/行號、實作 cost、優先序）由 Sub-agent A（Opus）於 2026-04-26 session 產出，總計 **12 findings**：

| # | 問題 | 對應原則 | 優先 | Cost |
|---|---|---|---|---|
| A-1 | concept page 雙 schema + Robin update 是 todo append | P1 + P4 | Critical | ~280 行 |
| A-2 | `extract_concepts` prompt 只給 slug list 不給既有 body | P4 | Critical | ~180 行 |
| A-3 | EPUB images / tables / math 全 drop | P3 | Critical | ~240 行 + Vision call |
| A-4 | chapter-summary prompt「每節 300-500 字」違反 P2 | P2 | High | ~60 行 prompt |
| A-5 | `shared/config.py:35` env override 順序 bug（silent corrupt vault）| infra | High | ~6 行 + test |
| A-6 | `obsidian_writer.write_page` 不做 frontmatter merge | P1 + P4 | High | ~160 行 |
| A-7 | 沒有 `## 文獻分歧 / Discussion` section + `update_conflict` action | P4 | High | ~70 行 |
| A-8 | textbook-ingest 與 kb-ingest schema 雙軌、寫入路徑各做各的 | schema | High | ~380 行 |
| A-9 | `parse_book.py` PDF 路徑用 raw `get_text` 不用 `pymupdf4llm` | P3 | Medium | ~30 行 |
| A-10 | `IngestPipeline._truncate_at_boundary(content, 30000)` 一律截斷 | P2 | Medium | ~60 行 |
| A-11 | `ATP再合成.md` / `肌酸代謝.md` 既有 broken frontmatter | data | High | ~120 行 migration |
| A-12 | chapter source frontmatter `created`/`updated`/`ingested_at` 重複 | schema | Low | ~30 行 |

**最 critical insight（觀念級破洞）**：

- Robin `agents/robin/ingest.py:472-510` `_update_wiki_page` 把更新寫成 `## 更新（{date}）` body append，內容是 `「應新增 X、應補充 Y」`的 imperative todo，**從未** merge 進 concept body 主體。看 `肌酸代謝.md` 末尾 10 條 `## 更新（2026-04-13）` 全是 todo 句、page 主體永遠停留在第一次 ingest 版本。
- `_get_concept_plan` 第 307-314 行只把 `KB/Wiki/Concepts/` 與 `KB/Wiki/Entities/` 的 stem 列表注入 prompt，**從未** 讓 LLM 看到既有 page body — 同義異名 false negative + 內容衝突無法偵測 + update 訊號丟失三連發。

---

## 4. ADR-010-v2 大綱（草稿目錄）

待修修確認後寫成 `docs/decisions/ADR-010-textbook-ingest-v2.md`：

### §1. Context — 為什麼要 v2

- v1 解決「整本書怎麼進 KB」的 workflow gap
- v1 留下 3 個未解問題：concept page 不是 aggregator / 圖表全丟 / 無 conflict detection
- v2 把這三個問題收進 schema + workflow

### §2. Principle Restatement

凍結 P1-P4 並用它們驅動 v2 決策（見本文件 §1）。

### §3. Decision

#### §3.1 Concept page schema v2（取代 v1 D-2）

- Frontmatter 強制欄位：`schema_version: 2` / `aliases: [list]` / `mentioned_in: [wikilink list]` / `source_refs: [path list]`（過渡相容）/ `domain` / `confidence` / `tags` / `discussion_topics: [list]`
- Body schema：Definition / Core Principles / Sub-concepts / **Field-level Controversies**（領域共識爭議）/ **文獻分歧 / Discussion**（KB 內部 cross-source 分歧）/ Practical Applications / Related Concepts / Sources
- Update logic 凍結：禁止 `## 更新（date）` body append；改為 LLM diff-merge 進主體；conflict 寫進 `## 文獻分歧`
- 既有 broken page migration script spec

#### §3.2 Chapter Source page schema v2

- 保留 v1（type=book_chapter / section_anchors / page_range / book_id）
- 新增 `figures: [{ref, path, caption, llm_description, tied_to_section}]` frontmatter list
- Body 結構：每節 deep extract（不限字數）+ verbatim quote 1-2 句 + `### Section concept map`（mermaid / nested bullet）

#### §3.3 Ingest pipeline v2 step

每章 ingest 改為 5 step：

1. **Read** chapter（含圖片從 EPUB / PDF 抽出到 `Attachments/Books/{book_id}/ch{n}/`）
2. **Vision describe** 每張圖片（domain-aware prompt，從 book_subtype 推斷 medical / sports / nutrition / neurology）→ inline 替換占位符
3. **Deep extract** chapter source page（chapter-summary v2 prompt，不限字數）
4. **Concept extract with conflict detection**：aliases dedup → 對每候選 concept **讀既有 page body** 注入 prompt → LLM 輸出 4 種 action：`create` / `update_merge` / `update_conflict` / `noop`
5. **Wiki page write via kb_writer**：`create` 走 schema v2 template / `update_merge` 走 LLM diff-merge into main body / `update_conflict` 寫進 `## 文獻分歧`

#### §3.4 圖片 / Table / 公式處理規格（取代 v1 D-3）

- EPUB：`<img>` → `Attachments/Books/{book_id}/ch{n}/fig-N.{ext}` + 占位符 / `<table>` → markdown table walker / `<math>` → LaTeX inline `$$...$$`
- PDF：走 `pymupdf4llm.to_markdown(with_tables=True)`
- Vision describe prompt：domain-aware（從 book_subtype 推斷）

#### §3.5 共用 kb_writer module（取代 v1 D-5）

- `shared/kb_writer.py` 為兩個 skill（textbook-ingest、kb-ingest）+ Robin agent 共用底層
- 暴露：`upsert_concept_page(slug, action, frontmatter_patch, body_patch)` / `update_mentioned_in(page, source_link)` / `aggregate_conflict(page, conflict_summary)` / `read_concept_for_diff(slug)`
- 統一 `schema_version: 2` enforcement

### §4. Migration（v1 → v2）

- broken page migration script（A-11）
- 既有 Robin schema 升 v2 backfill：`source_refs:` → 加 `mentioned_in:` 平行欄位、`## 更新` body 區塊用 LLM 一次性 merge 進主體（手動 review 後 commit）
- schema_version=1 page 在第一次 update 時自動 lazy migrate

### §5. Risks & Mitigations

- 圖片 ingest 的 vault 容量膨脹 → `Attachments/Books/` 容量監控 + 過大圖片下采樣
- Vision describe 失敗 / 不認識專業圖 → fallback `[FIGURE: alt-text only]` placeholder
- `update_merge` LLM diff 把既有內容洗掉 → 加 backup 機制（每次 update 先寫 `.bak` 24h）+ round-trip test
- 兩套 skill 整合過渡期 → schema version detect + 舊 schema page lazy migrate

### §6. Acceptance Criteria（v2 MVP）

- [ ] 既有 broken concept page 全修好
- [ ] `config.py` env 順序 bug 修
- [ ] 重 ingest 一本中型教科書，圖片全部 export 且 LLM described
- [ ] 至少一個 concept page（如「肌酸代謝」）展示 `## 文獻分歧` section 內含跨 source diff
- [ ] kb_writer module 通過 round-trip test：write → read → update → read，frontmatter / body 不損失
- [ ] textbook-ingest skill 與 kb-ingest skill 寫出來的 concept page schema 100% 一致

### §7. Out of Scope（明確不在 v2 內）

- multi-provider ingest（v1 §B2 Phase 2 維持）
- web UI 上傳（v1 §B1 Phase 2 維持）
- 中文教科書 OCR（敦促 v3 處理）

---

## 5. 三步 Sequencing

按 cost vs impact 排，三個獨立 deliverable：

### Step 1 — Hygiene 修補（即刻，low-risk，1 個 PR）

- A-5 `config.py` env 順序 bug + `get_db_path` 同改（6 行 + 30 行 test）
- A-11 broken concept page migration script + 一次性掃 vault 修好（80 行 + 40 行 test）
- 開新 branch `fix/config-and-broken-pages`

### Step 2 — ADR-010-v2 草稿（設計凍結，30-60 分鐘）

- 把本文件 §4 大綱寫成完整 ADR
- review 後 accept、merge to main

### Step 3 — Implementation（落地 ADR-010-v2，1-2 週）

- `shared/kb_writer.py` 共用底層（取代 Robin `_update_wiki_page` + textbook-ingest 寫入路徑）
- `extract_concepts.md` 重寫 + 強制讀既有 body diff
- `chapter-summary.md` 拿掉字數上限 + structured deep extract
- `parse_book.py` 圖片 export + table 結構化 + Vision describe step
- 重 ingest ch1 + 跑 retrieval acceptance test
- 批 ingest 剩 10 章

---

## 6. ch1 已 ingest 出口（v1 schema，待 v2 backfill）

- `KB/Wiki/Sources/Books/biochemistry-sport-exercise-2024/ch1.md`（chapter source，165 行）
- `KB/Wiki/Entities/Books/biochemistry-sport-exercise-2024.md`（book entity stub，status: partial，chapters_ingested: 1）
- 4 新 concept：`能量連續體` / `糖解作用` / `有氧能量系統` / `無氧能量系統`
- 6 既有 concept body append `## 更新（2026-04-25）` + source wikilink：`磷酸肌酸系統` / `ATP再合成` / `肌酸激酶系統` / `磷酸肌酸能量穿梭` / `肌酸代謝` / `運動營養學`
- KB/log.md append 12 條 entry

剩 10 章（書內 ch2-ch11）**hold 等 v2 redesign 後再批 ingest**（避免重做）。

---

## 7. Handoff — 下次開新對話從哪繼續

新 session 的 Claude 從這個檔案 + 以下檔案 onboard 後就有完整 context：

1. **本文件**（`docs/plans/2026-04-26-ingest-v2-redesign-plan.md`）— 設計哲學 + audit + sequencing + acceptance criteria
2. **`docs/research/2026-04-26-workflow-inventory.md`** — 整個 nakama 的 agent / skill / use case catalog（給跨 task 上下文）
3. **記憶**：
   - `memory/claude/project_textbook_ingest_v2_design.md` — v2 設計凍結 + 當前狀態
   - `memory/claude/project_robin_aggregator_gap.md` — Robin update 不是 aggregator + 已知 broken pages
   - `memory/claude/feedback_kb_concept_aggregator_principle.md` — concept page = aggregator 的設計哲學（cross-session 都受用）
4. **既有 v1 設計依據**：`docs/decisions/ADR-010-textbook-ingest.md`
5. **Critical 檔案**（如要 implementation）：
   - `agents/robin/ingest.py:472-510`（`_update_wiki_page`，要重寫）
   - `agents/robin/ingest.py:298-339`（`_get_concept_plan`，要加讀既有 body）
   - `agents/robin/prompts/extract_concepts.md`（要加 conflict detection）
   - `.claude/skills/textbook-ingest/scripts/parse_book.py:302-319`（`_epub_html_to_text`，要加 image / table / math 處理）
   - `.claude/skills/textbook-ingest/prompts/chapter-summary.md`（拿掉字數上限）
   - `shared/config.py:30-51`（env 順序 bug）
   - `shared/obsidian_writer.py:16-50`（要加 `update_page` helper）

---

## 8. Open questions（待修修 confirm）

1. **Sequencing**：Step 1 → Step 2 → Step 3 序貫？還是 Step 1 + Step 2 並行（hygiene fix 跟 ADR 草稿沒依賴）？
2. **既有 6 concept page 的 ch1 update**：要不要重做（用 v2 schema 重新 merge into main body）還是維持 v1 schema 直到全本 v2 backfill？
3. **ADR-010-v2 是新增 ADR-011 還是替換 ADR-010**？建議**新增 ADR-011-textbook-ingest-v2** + ADR-010 標記 superseded（保留歷史脈絡）。
4. **Vision LLM 用 Opus 4.7 還是 Sonnet 4.6**？Opus 品質高但 token 貴 5 倍；Sonnet 對教科書 figure 描述應夠用。

---

## 9. References

- v1 ADR：`docs/decisions/ADR-010-textbook-ingest.md`
- Karpathy gist：<https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>
- Phase 1 MVP test branch：`test/routers-franky-robin-sse`（與此 redesign 不相干，後續另起 branch）
- Audit conversation：2026-04-26 session（含 sub-agent A 完整 12 findings + ADR-010-v2 §1-§7 大綱）
