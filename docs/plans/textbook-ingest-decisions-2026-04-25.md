# Textbook Ingest — Decision Questionnaire

**用法**：每題勾選一個選項（用 `[x]` 取代 `[ ]`），需要 nuance 寫在 **Comments / overrides** 區。三題拍板後升級 ADR-010、開工。

對應提案：[textbook-ingest-design-2026-04-25.md](./textbook-ingest-design-2026-04-25.md)
背景：[project_textbook_ingest_design_gap.md](../../memory/claude/project_textbook_ingest_design_gap.md)

---

## Q1 — 章節拆分粒度：拆到章還是拆到節？

**Tradeoff**：
- **章**（建議）：1 章 = 1 source page；節用章內 metadata 表達；retrieval 走章 + chunk 兩層
- **節**：1 節 = 1 source page；source 數量 ×5–10；retrieval citation 更精確但檔案爆炸

**選一個**：

- [ ] **A — 拆到章**（我的建議）
- [ ] **B — 拆到節**
- [ ] **C — Hybrid：預設拆到章，但如果一章 > N 頁就自動拆節**（請於 Comments 寫 N）

**Comments / overrides**：

> _（修修在這裡寫補充 / 例外 / 反對理由）_

---

## Q2 — Rerank 落點：桌機還是 VPS？

**背景**：retrieve 拉 top-K=20 chunks 後，用 cross-encoder reranker（`bge-reranker-v2-m3`）打分挑 top-5 給 LLM compose。
**Tradeoff**：
- **VPS**（建議）：query latency 在 user 視角重要；ONNX 量化版 ~500MB RAM 跑得動；桌機可離線但 query 不受影響
- **桌機**：跑 full-precision torch 版稍快但每次 query 要桌機在線；vault sync 沒這條 path

**選一個**：

- [ ] **A — VPS**（我的建議；ONNX 量化版 bge-reranker-v2-m3）
- [ ] **B — 桌機**（full-precision torch；query 路徑要桌機在線）
- [ ] **C — 跳過 rerank**（直接用 retrieval top-5，省掉一個依賴）

**Comments / overrides**：

> _（修修在這裡寫補充）_

---

## Q3 — Vector store 落點：桌機還是 VPS？

**背景**：Chopper KB 的向量 + 章節 metadata 存哪。Embedding 一定在桌機產（GPU 速度），問題是落地。
**Tradeoff**：
- **VPS**（建議）：query path 就近，無需桌機在線；桌機產完 embedding `scp` 過去（一本書幾十 MB-幾百 MB）
- **桌機**：query 要桌機在線；好處是不用 scp，不過跟 Obsidian Sync 不太搭（SQLite + Obsidian sync 容易撞）
- **vault 內 sqlite + sync**：放 vault 裡讓 Obsidian Sync 帶；風險是 sync 對 SQLite WAL 不友善，可能 corruption

**選一個**：

- [ ] **A — VPS（建議）**：桌機 ingest 後 scp 推到 `/home/nakama/data/chopper_kb.sqlite`
- [ ] **B — 桌機**：query 路徑要桌機在線
- [ ] **C — vault 內 sqlite + Obsidian Sync**：自動同步但有 corruption 風險

**Comments / overrides**：

> _（修修在這裡寫補充）_

---

## 補充自由提問區

如果你想到第 4、5 題我沒列到的、或是覺得我提案哪段不對，寫在這裡：

> _（修修自由 input）_

---

## 拍板後我做什麼

1. 你 commit 這份檔案的勾選 + comment（或在 PR file diff 直接編輯）
2. 我讀勾選結果 + comment
3. 提案 doc 對應段落更新成「**Decided**」+ 內容對齊勾選
4. 升級為 `docs/decisions/ADR-010-textbook-ingest.md`
5. 開工：先做 PDF parse + TOC 抽章節 baseline，然後 chunking + embedding，最後 vector store 落地
