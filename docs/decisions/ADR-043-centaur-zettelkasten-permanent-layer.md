# ADR-043 Centaur Zettelkasten — 人寫的永久筆記層 + 三條 stage 1–3 收斂

- 狀態：Proposed
- 日期：2026-06-06
- 決策者：修修
- 關聯：
  - **擴展 ADR-024**（Source Promotion）— 在 Global Concept 之上加一層「人親手寫」的永久筆記；**deprecate「Global KB Concept」一詞**，其「長期權威概念」語意改由人寫的 Permanent Note 承接，原 `KB/Wiki/Concepts/` 內容降級為 **Candidate Concept**
  - **修訂 ADR-011**（concept page schema v2）— `ConceptPageV2` 需加 `status` 欄位（現為 `extra="forbid"` 無此欄）；ingest 輸出標 candidate / `#ai-draft`
  - **修訂 ADR-028**（VAULT-LAYOUT）— 新增 `KB/Permanent/`、`KB/Wiki/MOCs/`、`KB/Fleeting/`，並把 `KB/Permanent/` 標為 KB/ 底下唯一「人類擁有、agent 唯讀判斷型」的例外
  - **延續 ADR-042**（KB 輕量化）— 連結探勘走 LLM-over-小語料；向量留到規模煞車，且屆時只對 `KB/Permanent/` 建索引（非 raw vault）
  - **依賴 N519**（`feat/n519-llm-promotion-extractor`，未 merge，main 仍 dry-run）— B 書脊椎接真 LLM claim 抽取器的前置
  - 詞彙落在 `agents/robin/CONTEXT.md` §Centaur Zettelkasten

> **Panel audit trail（v1 → v2）**：本 ADR 經 3-way panel（Claude 起草 → Codex/GPT-5 + Gemini 2.5 Pro 審）。逐字 audit 存 `docs/research/2026-06-06-codex-centaur-zettelkasten-audit.md`、`docs/research/2026-06-06-gemini-centaur-zettelkasten-audit.md`。兩者皆 Approve-with-modifications。v2 採納：① 事實校正（Concepts 目錄已空、schema 無 status 欄）② consumer 相容性（retrieval-first）③ 3 個月測試=硬 gate ④ 切片改 Obsidian-first（修修裁決，逆轉 v1 的 Web-UI-first）⑤ 連結探勘加 token preflight + 向量留煞車（修修裁決）⑥ 候選生命週期 ⑦ CJK 錨點健檢 ⑧ 跨語 alias ⑨ 輕量 tripwire（author 欄 + 測試，非 hash）。

## 脈絡

方法論「AI 增強的卡片盒 / The Compounding Vault」(`/centaur_zettel`) 主張：知識複利的關鍵不是 AI 寫得多，而是守住一條紅線——**改寫與連結（產生理解的摩擦）留給人，記帳與檢索（純損耗摩擦）交給 AI**。套到 nakama 三條 ingest pipeline（B 書 / C 文章 / E YouTube），經 code 核對後：

- **基石缺口**：方法論要的「人親手寫的永久筆記層」三條都沒有（`KB/Permanent/` 不存在）。
- **C 是錯層不是缺層**：`KB/Wiki/Concepts/{slug}.md` 由 LLM 寫成品、用 Opus 4.7 diff-merge 自動併入（`kb_writer.upsert_concept_page`）。
- **核准式 HITL ≠ 作者式 HITL**：現有人類介入是「核准 AI 產出」；方法論要的是「你親手寫」。

**當下真實狀態（panel 校正，這改變了優先序）**：ADR-042 清理後 live vault 的 `KB/Wiki/Concepts/` **目前是 0 個 md 檔**（KB/Wiki 約 95 檔 / 826KB）。所以**眼前問題不是「遷移既有概念 backlog」，而是「證明修修讀完後會真的去寫永久卡」**——這是這類系統最常見的安靜死法（資料夾在、UI 在、候選累積，人寫的那層永遠空著）。

值得記下：Robin `CONTEXT.md` **已**內建這條紅線，只是只管 Stage 4（`Writing Assist Surface`「scaffolds, not ghostwrite」）。本 ADR 把同一條紅線**下延到 Stage 3 永久卡層**。方法論文件由 Claude chat 在未掌握 codebase 下寫成 → **採其 high-level method，實作以 nakama 為準**（文件若干 code claim 已過時，見決策依據）。

## 決策

1. **新增人寫的永久筆記層 `KB/Permanent/`。** 修修一手用自己的話寫的原子概念卡（一卡一概念、依概念命名）。Stage 3 最終產物、卡片盒核心。

2. **語意鎖 + 輕量 tripwire（非 hash、非 runtime 硬擋）。** frontmatter 拆判斷型（`status`/正文/連結關係 = 人）vs 記帳型（`source_refs`/`created`/`modified`/`aliases`/`mentioned_in`/`tags` = AI）。實現：(a) ingest/promotion 的 target resolver 永不解析到 `KB/Permanent/`；(b) agent 唯一寫入口 `update_permanent_bookkeeping()` 只改記帳型 key、沒有寫正文/status 的 API；(c) 每筆寫入帶 `author: human | agent_*` frontmatter；(d) **輕量 tripwire 取代「純慣例=hope」**：測試斷言 promotion target 永不回 `KB/Permanent/`、測試永久卡正文寫入只來自 human 路徑、dev/CI grep 稽核對 `KB/Permanent/` 的寫入呼叫。無 hash、無 policy engine——夠輕，但可稽核。

3. **`KB/Wiki/Concepts/` 降級為 Candidate Concept，且必須影響檢索（非只 frontmatter）。** 停止 auto-write 成品 + 停 Opus 自動 merge canonical；ingest 輸出標 `status: candidate` + `#ai-draft`。因目錄已空，工作重點是**未來 route-C 輸出落成候選**，不是遷移 backlog。需 schema 工作：`ConceptPageV2` 加 `status` 欄、`promotion_renderer` 改 emit。**候選生命週期**：永久卡從候選寫成後，候選改 `status: superseded` + `promoted_to: [[永久卡]]`（留 provenance、從主搜尋濾掉）。

4. **Consumer 相容性 / retrieval-first（早做，否則永久卡隱形）。** `kb_indexer` 加索引 `KB/Permanent/`；`kb_search`/Brook synthesize/Reading Context Package **預設優先永久卡**、候選標示為低信任/opt-in 參考。否則 Brook 會把 AI 草稿當人類知識引用——違背 Centaur 本意。這層 awareness 要**先於或同時於** authoring pilot 落地。

5. **三條收斂在 Stage 3 脊椎（流程），但候選呈現依證據形狀適配。** 共用：AI 摘要 → AI 候選 →【紅線】→ 人寫永久卡 → AI MOC + lint + 探勘。三條只在證據來源不同（B: N519 LLM claim / C: LLM 摘要 / E: 畫線叢集）。**但候選工作台原生呈現各自證據形狀**（影片=時間戳+講者、書=章節位置、文章=段落），不硬塞單一 rigid schema——收斂流程，不收斂呈現。

6. **連結/pattern 探勘 = LLM-over-小語料 + token preflight；向量留煞車。** 連結候選、跨卡矛盾偵測（lint）餵相關卡片集給 LLM。**corpus selector 只餵 `KB/Permanent/` 正文 + 精簡 metadata**，且**每次 all-corpus LLM call 前做 token-budget preflight**（既有 `kb_search` 已知 LLM rerank 約 50 chunks 撞牆）。不重建向量（延續 ADR-042）。**撞方法論規模煞車（200–500 文件）才升級，且屆時只對 `KB/Permanent/` 建小向量索引**（非 raw vault）——因 LLM-over-corpus 在塞得進 context 時即能做概念類比；撞牆後的 candidate-selection 才需語意向量。MOC 由 AI 維護於 `KB/Wiki/MOCs/`；**跨語 alias（卡片盒↔Zettelkasten）在搜尋/graph/探勘原生解析**，避免中英兩張平行圖。

7. **採用受 gate 控管：3 個月誠實測試 = 硬 gate；Slice 1 走 Obsidian-first pilot，Web UI 延後。** 不在習慣證明前 front-load 昂貴 Web UI（呼應方法論自身的試水溫）。**Gate pass/fail 準則**：手寫永久卡數、被 Brook/輸出複用數、被 source annotation 連結數、修修是否自願回訪該層。**未過 gate：不擴 B/E 收斂、不建完整 Bridge authoring UI**；過了才投。

## 決策依據（已驗證 file:line / 事實；panel 校正後）

- **基石缺口**：`grep` 證實 `KB/Permanent/`、`KB/Wiki/MOCs/`、`KB/Fleeting/` 不存在。
- **C 錯層**：`agents/robin/ingest.py:467-517` `_execute_plan` → `kb_writer.upsert_concept_page`；`shared/kb_writer.py` `update_merge` 呼叫 Opus 4.7 `_DIFF_MERGE_PROMPT`（Codex 已核 file:line 屬實）。
- **Concepts 目錄已空**（Codex live-vault 核對）：`KB/Wiki/Concepts/` 0 md；KB/Wiki 約 95 md / 826KB。ADR-042 記的 3324→481 是清理當下歷史值。→ Slice 0「降級 backlog」改為「未來輸出落候選」。
- **schema 缺口**（Codex）：`shared/schemas/kb.py` `ConceptPageV2` 無 `status` 欄且 `extra="forbid"`；`shared/promotion_renderer.py:218` emit `type: concept`。降級是真 schema+renderer 工作。
- **B 卡 dry-run**：`thousand_sunny/promotion_wiring.py:174` `llm` 模式 `raise RuntimeError`；real extractor 在 `feat/n519-llm-promotion-extractor`（未 merge）。方法論「PR #833」不存在，實為 N519。
- **E 概念層 deferred**：`shared/video_source_map_builder.py:228` 只產 `SourcePageReviewItem`、零 concept；**但 service 層仍會產 video `EntityReviewItem`（speaker chips，`promotion_review_service.py:349`）**——措辭應為「影片有 source-page + entity chips，無 concept 候選路」。
- **consumer 現況**（Codex）：`kb_indexer.py:56` 索引 Sources/Concepts/Entities/Annotations，**不含 Permanent**；Brook `_search.py:73` 不分 candidate/permanent；`reading_context_package.py:24` 直接走 concept 頁。→ 不改 consumer，永久卡隱形、候選仍 canonical。
- **無 /lint**：三條 + Brook 皆無跨來源矛盾偵測。
- **CJK 錨點脆弱**（Gemini）：Obsidian `^loc`/`^p` 區塊錨對無詞界的中文不穩，改句即可能靜默斷鏈。
- **既有資產可直接接**：`KB/Annotations/`（`annotation_store.py`，ADR-017）、`KB/Raw/`、`KB/Wiki/Sources/`、證據錨點（書 claim、影片 `t=start-end`、文章 source_refs、annotation 區塊錨點）。
- **方法論「絕對唯讀」與修修補充衝突** → 以判斷/記帳分型為準（見 `CONTEXT.md` flagged ambiguities）。

## 新增 / 降級 / 保留

### 新增
- 資料夾：`KB/Permanent/`（人寫）、`KB/Wiki/MOCs/`（AI）、`KB/Fleeting/inbox.md`
- code：`update_permanent_bookkeeping()`（窄記帳寫入口）、`ConceptPageV2.status` 欄 + renderer 更新、`kb_indexer` 索引 Permanent、`kb_search`/Brook/RCP candidate-aware 降權、連結探勘 LLM pass + token preflight、lint 跨卡矛盾 + CJK 錨點健檢、author-provenance 欄 + tripwire 測試
- 之後（過 gate 才做）：完整 Bridge authoring Web UI、E 畫線叢集→候選、B 接 N519、fleeting triage、reflux
- doc：VAULT-LAYOUT + LifeOS CLAUDE.md 登記三資料夾 + `KB/Permanent/` 人類擁有例外

### 降級
- `KB/Wiki/Concepts/`：未來 route-C 輸出標 candidate + `#ai-draft`；停成品 auto-write、停 Opus auto-merge canonical；寫成永久卡後候選轉 superseded + `promoted_to`

### 保留
- `KB/Annotations/`、`KB/Raw/`、`KB/Wiki/Sources/`、`KB/Wiki/Entities/`、證據錨點、promotion review/manifest、acceptance gate、atomic write + 備份
- ADR-042 的 FTS5/BM25 搜尋（探勘走 LLM-over-corpus，不動搜尋層；向量不重加）

## 切片計畫（v2：retrieval-first + Obsidian-first，gate 控管）

- **Slice 0**（最便宜，先做）：建空骨架三資料夾 + 範本；`ConceptPageV2` 加 `status` 欄 + renderer；**consumer awareness**（`kb_indexer` 索引 Permanent、`kb_search`/Brook/RCP candidate-aware 降權）；author 欄 + tripwire 測試；更新 VAULT-LAYOUT + LifeOS CLAUDE.md。零使用者行為改變。
- **Slice 1**（Obsidian-first pilot，挑 C 餵候選）：route-C ingest 改吐候選（非成品）；一個顯示「候選 + 來源 + 你的 annotation」的 side-pane / CLI context 視圖；修修**在 Obsidian 手寫永久卡**；連結探勘 LLM pass（含 token preflight）即時回饋新連結。**核心是驗證「人寫卡」習慣**。
- **【Gate】3 個月誠實測試** — 依準則判定。未過 → 停在這、重新評估（可能退回純參考索引）。
- **Slice 2+（過 gate 才做）**：完整 Bridge authoring Web UI → E 影片畫線叢集→候選 → B 接 N519 + 修致謝/版權頁過濾 → 維護複利（MOC 維護、lint 跨卡矛盾→選題 + CJK 錨點健檢、fleeting triage、reflux）。

## 風險與緩解

- **最大風險是紀律不是工程**（方法論「三個月誠實測試」）：故 Slice 1 用 Obsidian-first pilot 最小工程驗習慣，gate 未過不投大 UI；若只查不寫，誠實退回純 Karpathy 參考索引。
- **永久卡隱形 / 候選被當權威**：retrieval-first（Slice 0）先讓 consumer 認得 Permanent + 降權候選，否則 Brook 引 AI 草稿。
- **連結探勘規模 / 品質**：token preflight + corpus selector 只餵永久卡；撞 200–500 文件煞車才上 permanent-only 向量（概念類比在塞得進 context 時 LLM 已能做，撞牆後才需語意 selection）。
- **CJK 錨點 rot**：定期錨點健檢 lint，斷鏈報修修。
- **過度工程化**：個人系統，鎖只做 author 欄 + 測試，不 hash、不 runtime 硬擋。
- **N519 未 merge**：B 脊椎在 N519 前停在候選前；切片排序把 B 放最後，不擋 C/E。
