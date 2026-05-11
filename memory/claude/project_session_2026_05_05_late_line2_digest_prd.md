---
name: 收工 — 2026-05-05 late Line 2 digest + hybrid retrieval grill / PRD ship
description: 5-round grill 凍結 Line 2 讀書心得 digest feature + hybrid retrieval engine swap；PRD #430 + 4 slice (#431-#434) 全 ready-for-agent；vault 845 pages 8.45x zaferdace 警戒線；下次起手 dispatch #431
type: project
created: 2026-05-05
---

修修主動 push back grill 中我沒走 to-prd / to-issues 想自己 freelance plan doc — 校正回標準下游：grill → to-prd → to-issues → execute。

## 1. Grill 5 輪結論凍結

| 輪 | 議題 | 結論 |
|---|---|---|
| 1 | digest shape (A/B/C) | **A** 全書 digest 機械整理頁（Stage 4 LLM 不介入手寫憲法 — 只整理素材不代筆） |
| 2 | 上下文 (A1 vs A2) | **A1** chapter+section heading + Reader CFI deep link，**不**塞 inline 前後文（劃線 = 已讀過 = 不需重讀） |
| 3 | Vault backlink (B1+B2) | **B1+B2** 重用既有 annotation_merger LLM mapping (B1 zero-cost) + 跨 vault hybrid retrieval (B2)；加 `purpose="book_review"` framing |
| 4 | retrieval engine 換代 | 直接跳 hybrid（修修 push back：845 pages 已 8.45x 撞牆風險）；跳過 Stage 0 PoC |
| 5 | drop-in 評估 | 4 個社群 drop-in 全不適合；**自建 Blake Crosley recipe + Nakama wikilink lane** |

## 2. 三個結構性洞察

### 2.1 Karpathy retrieval ≠ embedding（澄清盲點）

我前兩輪用詞飄，憑印象說 search_kb 是 embedding-based。實際 grep `agents/robin/kb_search.py:53` 後是 **Haiku LLM ranker**：全 vault page (title + 200 char preview) 餵 Haiku 4.5 → 排 top-8 + 一句 relevance reason。

Karpathy 原版「LLM 讀 index → drill into pages」對 < 100 articles 規模工作。Nakama 比原版**更激進**（不只讀 index，是把 200+ page preview 全塞進 LLM context），但這正是 vault 大了之後的滑坡。

### 2.2 Vault scale 實測

622 Concept + 216 Sources + 7 Entities = **845 pages**（active vault `E:\Shosho LifeOS\KB\Wiki\`，**F: 是 stale**，依 reference_vault_paths_mac.md 已 documented）。zaferdace 警戒線 100 articles，Nakama **8.45x 超標**。修修估還有 3-4 本教科書 + 學術文獻 backlog 要 ingest，hybrid retrieval 是 critical path 不是 nice-to-have。

### 2.3 Wikilink lane = Nakama 獨有 leverage

社群 4 個 drop-in（lyonzin/knowledge-rag / Blake Crosley / obra/knowledge-graph / engraph）對 wikilink 的處理：
- knowledge-rag + Blake Crosley：**完全不索引 wikilinks**
- obra/knowledge-graph + engraph：first-class 但 Node.js / Rust + Windows 不支援

社群忽略是因為大部分人 vault 的 wikilinks 是手 markup 多半零散。Nakama 的 `mentioned_in:` 是 **ingest pipeline 寫的、稠密、結構性**，這條訊號接進 RRF 是 free leverage，社群工具沒人複製得來。Phase 1b（#433）做這件事。

## 3. PRD #430 + 4 slice 開出來

| # | Title | Type | Blocked by | LOC 估 |
|---|---|---|---|---|
| #430 | PRD: Line 2 讀書心得 — Book Digest + Hybrid Retrieval Engine | parent | — | — |
| **#431** | **S1: Hybrid retrieval engine Phase 1a** | AFK | None — **可立即起手** | ~600 |
| #432 | S2: Book digest feature | AFK | #431 | ~400 |
| #433 | S3: Wikilink lane Phase 1b | AFK | #431 | ~250 |
| #434 | S4: 👍/👎 ground truth signal | AFK | #432 | ~200 |
| ~~S5~~ | engine default swap | HITL（條件觸發）| 觀察 1-3 月 | — |

S5 deferred 不開（避免 stale backlog）— 等 hybrid 用一陣子 hits 品質實證後再開單獨 PRD。

並行機會：S1 ship 後 **#432 + #433 可並行 sandcastle**（程式路徑獨立、零檔案衝突）。

## 4. 技術選型凍結（Blake Crosley 2026 reference 對齊）

- BM25：SQLite FTS5（weights chunk_text 1.0 / section 0.5 / heading_context 0.3）
- Embedding：model2vec potion-base-8M（256d、~30MB、CPU only、比 sentence-transformers 50-500x 快）
- Vector store：sqlite-vec extension（vec0 虛表，零新 service，state.db 內單檔）
- Fusion：Reciprocal Rank Fusion k=60，candidate pool 30 per lane → top 10 final
- **不上 cross-encoder rerank**（Phase 2 候選，邊際收益低延遲增 100-300ms）
- Chunking：H2 boundary（textbook-ingest section_anchors 對齊）
- Engine flag rollout：option B（digest only first → 其他 caller 觀察期保 Haiku → S5 條件切 default）

## 5. 下個 session 起手

**起手 dispatch #431 via sandcastle single-worktree AFK**（依 feedback_phase3_single_worktree_proven）。

S1 工程量 ~600 LOC，sandcastle 一輪可完成。merge 後並行 dispatch #432 + #433。

**起手必先 sync**（依 feedback_sync_before_grill）：
- `git log main` 確認沒漏 squash 的 PR
- `gh issue list` 確認 #431-#434 還在
- `gh pr list` 確認 5 條 stale memory PR (#425/#419/#418/#410) 是否需 squash 或可置之不理
- 桌機開新 session 起手前 verify branch 不在 main（依 project_session_2026_05_05_evening_zotero_ci_qa_blocked）

## 6. 現場狀態（給下次起手）

- HEAD: `c0994d4` (PR #429 squash後)
- 本 session 結束前已 commit + push：本 memory file + reference_sandcastle.md + feedback_csp_books_reader_inline_blocked.md + feedback_uvicorn_reload_form_signature_stale.md（前 session 殘留）+ MEMORY.md 更新

## Reference

- PRD：https://github.com/shosho-chang/nakama/issues/430
- S1: https://github.com/shosho-chang/nakama/issues/431
- S2: https://github.com/shosho-chang/nakama/issues/432
- S3: https://github.com/shosho-chang/nakama/issues/433
- S4: https://github.com/shosho-chang/nakama/issues/434
- [Karpathy gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [zaferdace 100-article wall](https://dev.to/zaferdace/karpathys-obsidian-wiki-broke-at-100-articles-rag-fixed-it-4d4h)
- [Blake Crosley 2026 hybrid reference](https://blakecrosley.com/guides/obsidian)
- [project_session_2026_05_04_pm_annotation_ship.md](project_session_2026_05_04_pm_annotation_ship.md) — 5/4 PR #342/#343/#344 ship + ADR-017 凍結（直接前置）
- [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) Stage 2→3 Line 2 critical path
