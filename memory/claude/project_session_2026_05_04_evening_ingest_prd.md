---
name: 收工 — 2026-05-04 晚 Stage 1 ingest PRD #351 ship + 5 slice 拆完
description: PRD #351 上線 + 5 slice (#352-356) ready-for-agent；6 grill 凍 scope（B2-B / 圖片全抓 / 字數 heuristic 砍 / C3 deferred / E1 hybrid / 失敗檔丟棄）；PRD doc 落 docs/plans/2026-05-04-stage-1-ingest-unify.md；下個 session dispatch Slice 1 #352 implementation
type: project
created: 2026-05-04
---

從 evening memo 5 grill 起手 → 凍 scope → PRD ship → 5 slice 拆完。下個 session 起手直接 dispatch Slice 1 #352 (P9 task prompt + worktree)。

## 1. Grill 結論凍結 6 題

| Q | 拍板 |
|---|---|
| **Q1** 抓+翻 sync vs async | **B2-B**：抓完進 inbox 不翻 → 修修真人判斷 → reader 內按「翻譯」鈕 trigger → 跳雙語 reader（沿用 PubMed reader short-circuit pattern） |
| **Q2** inbox 儲存 + UI | a1 + b1 **既有 ship**（`Inbox/kb/` + reader homepage = inbox view from `_get_inbox_files()`）— 真正 gap 是 `/scrape-translate` 同步要改 BackgroundTask |
| **Q3** 圖片 first-class | **A 一視同仁全抓**（學術 + 部落格不分），等真出現「廣告圖污染」case 再加 filter |
| **Q4a** 失敗判斷 | **砍字數 threshold**（heuristic 不可靠，BMJ Medicine 半身字數可能 > 1500），改靠真人；只保留 < 200 字硬擋當粗篩 |
| **Q4c** failed 5 層後 inbox PDF 入口 | **C3 deferred**，等真實使用兩週看 failed 率再決定 |
| **Q5** PubMed cron unify | **E1 hybrid dispatcher**（min reuse `fetch_fulltext`，cron 0 改動 0 風險）；E2 layer 抽 + 重組留 follow-up |
| **Q6** 失敗檔刪除 | reader header + inbox row 雙刪除按鈕；連動刪 annotation；< 200 字硬擋 |

## 2. PRD ship

- **GH issue**: [#351](https://github.com/shosho-chang/nakama/issues/351) — `enhancement` + `ready-for-human`
- **PRD doc**: [docs/plans/2026-05-04-stage-1-ingest-unify.md](../../docs/plans/2026-05-04-stage-1-ingest-unify.md) 仿 PRD #337 結構（30 條 user stories + 4 deep + 3 shallow modules + slice 拆法）

## 3. 5 slice 拆完（全 AFK + ready-for-agent）

| # | Title | Blocker |
|---|---|---|
| [#352](https://github.com/shosho-chang/nakama/issues/352) | Slice 1: URL ingest tracer bullet — async pipeline 骨架 | None |
| [#353](https://github.com/shosho-chang/nakama/issues/353) | Slice 2: Academic source detection → reuse 5 層 fetch_fulltext | #352 |
| [#354](https://github.com/shosho-chang/nakama/issues/354) | Slice 3: 翻譯按鈕 + 雙語 reader (B2-B flow) | #352 |
| [#355](https://github.com/shosho-chang/nakama/issues/355) | Slice 4: URL ingest 圖片 first-class | #352 |
| [#356](https://github.com/shosho-chang/nakama/issues/356) | Slice 5: 失敗檔丟棄 + annotation 連動刪 | #352 |

Slice 1 critical path（其他 4 並行依賴）。建議 sequential ship Slice 1 → parallel ship 2-5。

## 4. 重要 reframe（grill 過程）

- **修修結論校準 framing**：不是「unify 5 層 OA backend engine」這種技術 reframe，是「reader URL 入口升級到跟 PubMed digest 同等級 + 部落格也納入」— 加部落格進 scope 改變所有題的答法
- **Q1 反射錯誤**：問「每週 ingest 幾篇」是 perf optimization 問題，跟 scope freeze 不該 stack（→ feedback_grill_scope_not_perf_optim）
- **Q5 推論錯誤**：用 memory 推 PubMed digest = fulltext-driven → 修修 push back → grep code 才知 abstract-only（`agents/robin/pubmed_digest.py:9` docstring）+ fulltext 是 score 完才附加下載；framing 錯改成「兩 use case 互補不重複」（→ feedback_grep_existing_feature_behavior）

## 5. 下個 session 起手

直接 dispatch Slice 1 #352 implementation agent：
- P9 六要素 task prompt（CLAUDE.md「P9 六要素」段範本）
- worktree isolation ([feedback_worktree_absolute_path_leak](feedback_worktree_absolute_path_leak.md))
- 依 [feedback_phase3_single_worktree_proven](feedback_phase3_single_worktree_proven.md) nakama 規模單 worktree 序列夠用
- ship 完 merge 後 parallel dispatch Slice 2-5（4 並行）

或修修選 sandcastle parallel route 一次 dispatch 所有 slice（[reference_sandcastle](reference_sandcastle.md)）。

## Reference

- 早上七層架構：[project_session_2026_05_04_pipeline_arch](project_session_2026_05_04_pipeline_arch.md)
- 下午 PRD #337 ship：[project_session_2026_05_04_pm_annotation_ship](project_session_2026_05_04_pm_annotation_ship.md)
- 晚上 grill scope 起點：[project_session_2026_05_04_evening_ingest_grill](project_session_2026_05_04_evening_ingest_grill.md)（pre-grill 狀態，5 grill 在 §5）
- PR #94 publisher HTML fallback (5 層 第 5 層): [project_robin_pubmed_digest](project_robin_pubmed_digest.md)
- 5 層 OA APIs: [reference_oa_fulltext_apis](reference_oa_fulltext_apis.md)
- Stage 1 anchor: [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md)
