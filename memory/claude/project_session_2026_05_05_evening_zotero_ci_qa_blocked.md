---
name: 收工 2026-05-05 evening — TDD principle 拍板 + Zotero CI 全綠 + QA 卡 dual-window race
description: TDD = sandcastle by default 原則拍板（feedback_afk_must_use_sandcastle 改寫）；Zotero 3 PR 機械 ruff + coverage 修綠；QA Step 1 撞修修另一視窗 git checkout main race 中止；下次起手 verify branch + 用 worktree 跑 QA
type: project
created: 2026-05-05
---

延續下午 Zotero MVP ship 的 review session，三軸線：

## 1. TDD = sandcastle by default 拍板

修修問了三題自然推導出新原則：
- TDD step 做什麼 / 花多少 token？
- 跳過 TDD 直接 sandcastle 會踩什麼？
- 全部進 sandcastle 可不可以？

新原則：「**程式碼品質 first，context 低檔 → 能進 sandcastle 就進**」。捨棄舊版「Cycle 4 切 sandcastle」reactive 閾值。

| 階段 | 走法 | 主對話 token |
|---|---|---|
| HITL planning + interface 設計 + tracer bullet | 前景 | 50-80K |
| Commit `tdd-prep/<slice>` failing tests | 前景 | <5K |
| RED→GREEN cycles | sandcastle | ~5K |
| Refactor + commit + open PR | sandcastle | 0 |
| Review + squash merge | 前景 | ~10K |

第 N+1 slice 重用 pattern → 100% sandcastle (~30K total)。

落地：[feedback_afk_must_use_sandcastle.md](feedback_afk_must_use_sandcastle.md) 改寫 + MEMORY.md description 同步（PR #399 commit 8ceae99）。

## 2. Zotero 3 PR CI 全綠

第一輪 lint-and-test fail = 機械錯誤：
- E501 docstring line too long (`zotero_reader.py:7`)
- F541 redundant f-string prefix (`test_inbox_writer.py`)
- I001 import sort（3 test files）

修法：每 branch 獨立跑 `ruff check --fix` + `ruff format` + 手修 E501（不走 stack-wide rebase 避免 conflict）。
- PR #397: commit `801965d`
- PR #398: commit `359c081`（rebase Slice 1 撞 zotero_sync.py + test_zotero_assets.py conflict，abort 改獨立 fix）
- PR #399: commit `3171c72`

第二輪 #399 fail：critical-path coverage 94.77% < 95%（`thousand_sunny/routers/robin.py` 加 `zotero_ingest` route 後降）。修法：commit `b64e280` 加 3 個 negative test：
- auth fail → 302 /login
- missing inbox → FileNotFoundError → 404
- non-Zotero inbox → ValueError → 400

最終：三 PR lint-and-test 全 SUCCESS，等修修 squash merge。

## 3. Zotero QA Step 1 卡 dual-window race（**待下次**）

QA 進度：
- ✅ 啟 dev server uvicorn :8000
- ✅ Zotero library 在 `~/Zotero`（Windows 預設，無需設 `ZOTERO_LIBRARY_PATH`）
- ✅ 修修從 Zotero "Show File" 拿到 itemKey `IARWBZ7Y`（zotero 7 沒 Copy Item Links 選項，drag-drop / Edit menu 都不行，Show File 路徑反推）
- ✅ 拼 `zotero://select/library/items/IARWBZ7Y` 貼進 reader
- ❌ Reader 寫 placeholder OK，但 background dispatch 噴 **Firecrawl Bad Request: URL uses unsupported protocol**

Diagnose 路徑：
1. grep `parse_zotero_uri` 在 `agents/robin/url_dispatcher.py` → **0 matches**
2. `git show fcbe34e` 確認 commit 有加 zotero wiring（`+98 LOC` import + config + dispatch + `_dispatch_zotero`）
3. `git diff fcbe34e^..fcbe34e` diff 證實 Slice 1 加了 short-circuit
4. 但當前 file 沒這些 — `git branch --show-current` 顯示 **main**，不是 `feat/zotero-slice-3-two-file-fanout`

**根因**：修修另一視窗動 EPUB 期間 `git checkout main` 切走 working tree。我啟 server 前沒重 verify branch，啟到 main 沒 zotero wiring 版本，dispatcher 走 general firecrawl path → firecrawl 看到 `zotero://` 不符 `^https?://` regex 直接 reject。

## 下次 session 起手清單

1. 先 `gh pr view 397/398/399 --json state,mergedAt` 確認三 PR 是否已 squash merged
2. **若已 merged**：直接 main 上 checkout + 重啟 server，重做 QA Step 1
3. **若還 OPEN**：用 `git worktree add ../nakama-zotero-qa feat/zotero-slice-3-two-file-fanout` + 獨立 port 8001
4. 重做：Step 1 HTML happy path → Step 2 PDF fallback → Step 3 two-file fan-out
5. **啟 server 前必跑 `git branch --show-current` 確認 branch**

## 強化教訓 — dual-window git checkout race

[feedback_dual_window_worktree.md](feedback_dual_window_worktree.md) 已寫過「同機雙視窗開發必用 worktree」，[feedback_shared_tree_devserver_collision.md](feedback_shared_tree_devserver_collision.md) 寫過 dev server mtime collision，但這次踩到的是 **`git checkout` 切 branch 直接傳染** — server import 路徑不變但 source code 換掉，啟 server 完全無感是另一個 branch 的版本。

下次強規則：
- **EPUB ↔ Zotero 兩條軸線必 worktree 隔離**，不靠 branch switching
- **啟 server 前 confirm branch**（自動化：runbook 加 `git branch` 第一行）
- **長 session 中途 verify**：每次重啟 server / 跑 test 前重 confirm branch（成本低，避免 silent drift）

## 重要 artifact

- 三 PR 全綠：[#397](https://github.com/shosho-chang/nakama/pull/397) / [#398](https://github.com/shosho-chang/nakama/pull/398) / [#399](https://github.com/shosho-chang/nakama/pull/399)
- 新原則 memory：[feedback_afk_must_use_sandcastle.md](feedback_afk_must_use_sandcastle.md)
- 下午 ship 記憶：[project_session_2026_05_05_pm_zotero_ship_token_burn.md](project_session_2026_05_05_pm_zotero_ship_token_burn.md)
- ADR-018 / ADR-019 / Robin CONTEXT.md 含 Zotero 詞彙

## 主對話 token 用量

最高 230K / 1M (23%) — 比下午 ship session 的 507K 健康很多。原因：
- Review 對話沒有 cycle 重複進 context
- Zotero 機械 fix 直接 ruff --fix（沒 cycle TDD）
- 沒有大量 file Read（只 spot read 認 bug）

Ship session：507K cycles 16 + spot diagnose
This session：230K review + small CI fix + diagnose
驗證新原則合理性 — 「review + 小修 + diagnose」前景跑 OK，不會爆。
