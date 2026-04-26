---
name: KB concept page = cross-source aggregator，不是 oracle 也不是 todo dump
description: concept page schema 設計哲學 — Karpathy gist 對齊：aggregator + Discussion section for conflicts；禁止 body 末尾 `## 更新` todo-style append
type: feedback
originSessionId: 27c0b340-d612-4f47-aba4-b4f3727267fd
---
**規則**：所有 KB concept page 必須是「cross-source evidence aggregator」，不是事實 oracle、也不是 ingest 變更日誌。新 source 進來要把內容真正 merge 進 concept body 主體（Definition / Core Principles / Practical Applications），有衝突另闢 `## 文獻分歧 / Discussion` section 結構化記錄。

**Why:** Karpathy gist 的 cross-source personal wiki 哲學 — wiki 是 evidence-aggregator + open-question tracker；KB 不是 RAG dump、不是 oracle，是「不同 source 對同一概念講了什麼 / 哪些一致 / 哪些分歧」的可審視 artifact。過去 Robin pipeline 的「body 末尾 `## 更新（date）` block + LLM imperative todo 註記」嚴重違背這個哲學 — 結果 concept body 永遠停留在第一次 ingest 版本、後續 source 全變補丁。

**How to apply:**

- **寫 ingest update path 時**：必須讀既有 page body → LLM diff-merge 進主體段落 → 衝突寫進 `## 文獻分歧`
- **新建 concept page frontmatter schema** 必含：
  - `aliases:` — 同義詞清單，dedup 用（解決同義異名 false negative）
  - `mentioned_in:` — aggregator backlink wikilink list
  - `discussion_topics:` — conflict 警示燈（agent retrieval 看到此欄位知道要讀 Discussion section）
- **禁止**：
  - body 末尾 append `## 更新（date）` 純 changelog block
  - 同義異名 slug 各建一頁（如「糖解作用」與「糖酵解」）
  - LLM 寫 imperative todo（「應新增 X、應補充 Y」）當作 update（這只是 nag、不是 merge）
- **衝突分兩類**（Discussion 不要混）：
  - **Field-level Controversies**（領域共識爭議）— 該領域知名 issue，eg. 「肌酸補充劑量爭議」
  - **文獻分歧 / Discussion**（KB 內部分歧）— KB 內 source A vs source B 對同 concept 講不同話，要列 source wikilink + 數字差異 + 可能原因 + 共識點/不確定區
- **`schema_version: 2`** 標記新 schema page；舊 page 在第一次 update 時 lazy migrate

## 適用範圍

- Robin agent ingest（`_update_wiki_page`）
- textbook-ingest skill
- kb-ingest skill
- 未來任何寫 concept page 的 agent / skill — 都走共用 `shared/kb_writer.py`
