---
name: 收工 2026-05-05 早 — 7 PR ship + Zotero pivot 戰略決定
description: 7 PR (#369-#375) merge cycle 全 ship + QA Step 2 暴露 URL scrape quality bar 不夠 → Stage 1 ingest 戰略性 pivot 到 Zotero-first；昨天 5 slice 重定位
type: project
created: 2026-05-05
---

## Ship

7 PR (#369-#374 fix + #375 memory) **全部 squash merged** 2026-05-05 早。
- Branch protection strict + serial cycle 跑完，每輪 update-branch + poll CI + merge
- Final main: `4cdce43` docs(memory) #375
- 6 fix PR 涵蓋 inbox title display / scrape button copy / sqlite migration order / url-dispatcher fallback / UTF-8 console / translate redirect

## QA partial walkthrough

- ✅ Step 1：#370 button copy + #369 title display 視覺確認
- ✅ Step 2 partial：Nature URL `s43587-024-00692-2` 觸發 ingest，Slice 2 五層 OA fallback 真的有走（Layer 1 PMC PDF 失敗 → Layer 2 Europe PMC PMID 39143318 / PMC11564093 → 解析 112,382 字元成功）
- ❎ Step 2 quality fail：修修開檔 fulltext 質感**不滿意**，當場決定 abandon URL scrape

## 戰略性 pivot 凍結 2026-05-05 早

1. URL scrape path（Stage 1 ingest Slice 1/2/4）**不再是 primary** — quality bar 無法逼近 Zotero browser snapshot
2. Zotero 從原本「訂閱期刊 only」**升級成 primary ingest path**（OA + 訂閱都走）
3. 修修既有工作流：開文章 → 沉浸式翻譯雙語對照 → Save to Zotero → Zotero annotate；**運作順暢**
4. **真正痛點 reframed**：
   - (a) Zotero ↔ Obsidian sync 缺口：Zotero 內容 + annotation 進不了 KB
   - (b) 沉浸式翻譯 inject 翻譯到 DOM → Save to Zotero 連污染版本一起存
5. 解法**不是重做 ingest**，是**蓋缺的橋**：
   - Zotero → Obsidian sync agent（既有 plan Phase B/C 升 primary）
   - 污染問題用 workflow 解（先 save 後翻 / 浮窗模式 / 只存 PDF attachment）

## 昨天 5 slice 工作重定位

| Slice | PR | 狀態 |
|---|---|---|
| 1 URL ingest skeleton | #352 | deprecate as primary，留作非 Zotero 逃生口 |
| 2 academic 5-layer OA | #353 | 同上；Europe PMC layer 仍可作為 PubMed digest 後援 |
| 3 翻譯 + 雙語 reader | #354 | **post-sync 用 keep**：Zotero sync 進來的 clean MD，Robin Reader 按翻譯產對照頁，**不污染原檔** |
| 4 image first-class | #355 | deprecate as primary（Zotero 已含 PDF + snapshot 圖） |
| 5 失敗檔丟棄 | #356 | 仍然有用（共用 inbox UX） |
| 6 fix (#369-#374) | merged | 全保留 — 修的是共用 reader / log / UI |

## UX gap noticed during QA（不是昨天 PR 的 regression）

- inbox 不會 auto-poll 狀態（處理中 → 可讀）；要手動 refresh
- 既有 Stage 1 ingest 沒做 SSE/polling
- pivot 後 priority 低（URL scrape 不再是主路徑）

## Next session

- **這個 window**：Zotero integration grill — 架構決議
  - SQLite 直連 vs Zotero Web API
  - annotation sync 方向（單向 Zotero → KB MVP，雙向後續）
  - PDF → MD 轉檔策略（pymupdf4llm 已在 repo 有用）
  - collection mapping → KB folder 結構
  - 增量 sync vs 全量；trigger（cron / 手動）
  - Better BibTeX / zotero2md / Obsidian Citations 等 prior art 評估
- **另一個 window**：epub book translation grill（修修同時開，PR #376 plan prep ship）

## 相關

- [project_zotero_integration_plan.md](project_zotero_integration_plan.md) — 升 primary 後的 plan
- [feedback_dont_recompete_on_capture_quality.md](feedback_dont_recompete_on_capture_quality.md) — 戰略 lesson
- [project_session_2026_05_04_late_stage1_ingest_ship.md](project_session_2026_05_04_late_stage1_ingest_ship.md) — 昨天 ship 的 5 slice
- [project_session_2026_05_04_overnight_smoke_followup.md](project_session_2026_05_04_overnight_smoke_followup.md) — overnight 6 fix PR 來源
- [feedback_structural_vs_functional_validation.md](feedback_structural_vs_functional_validation.md) — pipeline status=ready ≠ user-acceptable quality 的廣義 lesson
