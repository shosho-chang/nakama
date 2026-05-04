---
name: 在 grill 推論既有 feature 行為前必 grep code
description: 別憑 memory / 命名 / 設計 doc 推論既有 feature 實際做什麼；修修 push back「PubMed digest 只下 abstract」grep code 才確認 — 主流程 abstract，full text 是附加；推論前必驗
type: feedback
created: 2026-05-04
---

既有 feature 的「實際做什麼」常跟 memory / 命名 / 設計 doc 描述有 gap：

- 命名「PubMed digest」+ memory 寫「OA 全文自動下載」→ 我推「digest = fulltext-driven」
- 修修 push back「目前 Robin PubMed digest 只有下載 abstract」→ grep `pubmed_digest.py` 才看到：
  - line 9 docstring：「（那條是檔案導向、全文處理；這條是 **abstract-only**）」
  - line 271：abstract 從 RSS 抓
  - line 311：curate 階段只用 800 字 abstract
  - line 326-327, 354-355：curate + score 兩個 LLM call **都只看 abstract**
  - line 134：`_fetch_fulltext_for_all` 是 score 完才附加，**不是主流程**

**Why:** 2026-05-04 Stage 1 ingest grill Q5 我用 memory 推 PubMed digest 跟新 feature「重複」，frame 出「unify 5 層 fallback engine 共用」問題；修修一句 push back 揭穿 framing 錯（兩 use case 互補不重複，只引擎部分 reuse）；浪費一輪 grill iteration

**How to apply:**

- grill 內 frame 既有 feature 跟新 feature「**重複 / 衝突 / 共用**」之前必 grep code 入口檔的 docstring + 主流程 + 關鍵 LLM call 的 input，看「**實際吃什麼餵什麼**」
- 不能憑命名 / memory description / 設計 doc 推論
- 同精神延伸：[feedback_design_rationale_trace](feedback_design_rationale_trace.md)（寫 rationale 前 trace pipeline）+ [feedback_reuse_module_inspect_inner_text](feedback_reuse_module_inspect_inner_text.md)（reuse 前 grep prompt / docstring / hardcoded literal）
- 通則：行為斷言要 ground 在 code，不憑直覺
