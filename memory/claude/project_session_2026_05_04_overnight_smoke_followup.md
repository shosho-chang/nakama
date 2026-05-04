---
name: 收工 — 2026-05-04 夜 smoke session + 6 follow-up PR overnight
description: Stage 1 ingest manual smoke 暴露 5 條 bug + 1 UX miss → 全 dispatch sub-agent + worktree isolation 並行 → 6 PR (PR #369-#374) 全綠等驗收。修修 15+ 小時收工去睡，我接手 fix all。3/5 sub-agent hit lint failure (ruff check / format)、1/5 hit coverage gate — 我手動補。Lesson: dispatch prompt 必含 local CI 預檢。
type: project
created: 2026-05-04
---

從早上 7 點到深夜 22 點修修 15+ 小時連續 session。Stage 1 ingest 5 PR 全 merged 後跑 manual smoke 抓 BMJ + Lancet → 暴露 5 條 P0/P1 bug + 1 UX miss。修修去睡，我承接全 dispatch sub-agent 修齊 6 PR overnight ready。

## 1. Smoke 暴露的 6 個問題

| # | 問題 | 嚴重度 | 用什麼測試結構抓不到 |
|---|---|---|---|
| 1 | inbox row 顯示 `file.name` (slug 醜檔名) 而非 frontmatter title | UX | 純 UX，現有 test 沒驗 row 視覺 |
| 2 | scrape button copy「抓取+翻譯」名實不符（route 只 scrape 不 translate）| UX | copy strings 沒測試 |
| 3 | sqlite `_init_tables` 順序 bug — `CREATE INDEX ON r2_backup_checks(prefix, ...)` 在 `ALTER TABLE ADD COLUMN prefix` 之前 → server 重啟第一個 request 撞 500 | P0 | 每個 test fresh DB → CREATE TABLE 一次到位含 prefix；沒模擬「老 schema 升級」 |
| 4 | translate route 立刻 redirect `/read?file={stem}-bilingual.md` → BG task 還沒寫出 → 404 | P1 | unit test mock translate_document 秒回；real 326 段 article 才暴露時序差 |
| 5 | url_dispatcher publisher 偵測成功但 DOI extract 失敗時 silent fall-through readability → page chrome 假裝 ready | P1 | 測試 mock httpx 成功；real Lancet cloudflare/bot block 才走到那條 silent path |
| 6 | Windows cp1252 logger 噴 UnicodeEncodeError 大段 trace 干擾 log | P3 | CI 跑 Linux/macOS，cp1252 限制只在 Windows |

全是 **unit test 結構上抓不到** 的 integration / UAT bug。修修原話：「不是有寫測試嗎？」— 答：寫了，但 test strategy 結構盲區。

## 2. 6 PR overnight dispatch

按 [feedback_dispatch_everything_minimize_main_context](feedback_dispatch_everything_minimize_main_context.md) 最高指導原則 — 全 dispatch 不接手。每 sub-agent 用 `isolation: "worktree"` 拿乾淨 main checkout，獨立 branch off main、寫 fix + regression test、push、open PR、不 merge。我自己只做 #369（已有 working tree 改動，commit 自己）。

| PR | Bug # | Branch | Test that catches it |
|---|---|---|---|
| [#369](https://github.com/shosho-chang/nakama/pull/369) | 1 | `fix/inbox-title-display` | `test_get_inbox_files_extracts_title_from_frontmatter` |
| [#370](https://github.com/shosho-chang/nakama/pull/370) | 2 | `fix/scrape-button-copy` | (copy only — 沒可測 unit assertion) |
| [#371](https://github.com/shosho-chang/nakama/pull/371) | 3 | `fix/sqlite-init-table-index-order` | `test_init_tables_migrates_pre_existing_db_without_prefix_column` |
| [#372](https://github.com/shosho-chang/nakama/pull/372) | 5 | `fix/url-dispatcher-publisher-doi-warn` | 4 tests: httpx fail / chrome-only / Firecrawl success / both fail |
| [#373](https://github.com/shosho-chang/nakama/pull/373) | 6 | `fix/utf8-stream-logger` | 7 tests inc. cp1252 BytesIO 端到端 |
| [#374](https://github.com/shosho-chang/nakama/pull/374) | 4 | `fix/translate-redirect-race` | 6 tests (4 race fix + 2 helper edge paths after coverage gate fail) |

**全 6 PR `lint-and-test` SUCCESS / `mergeStateStatus: CLEAN`**。

## 3. 設計判斷（修修醒來可改 PR）

- **#374 race fix**：translate POST 後 redirect 回 `/`（inbox）而**不是** bilingual reader。新增 `translating` intermediate status (`ready → translating → translated`)。BG fail row 卡 `translating` 故意 visible（不彈回 `ready` hide failure）。多一個 click 但 0% 404
- **#372 Firecrawl fallback**：publisher meta-tag httpx fail 時新增 Firecrawl raw HTML 重抓找 `citation_doi`。每個 publisher URL fail 多一個 Firecrawl call；FIRECRAWL_API_KEY 沒設則 WARN + 退回 readability（同舊行為，不破回）
- **#370 button copy**：rename only，**沒**自動串翻譯（cost + design 留給修修決定）

## 4. Sub-agent CI gap — 3/5 hit lint / 1/5 hit coverage

Dispatch 的 5 sub-agent 全跑 pytest 在 worktree 內驗 fix 對 → 但**沒跑 ruff check / format / 沒看 critical-path coverage**：

- **#371 sqlite**：`tests/shared/test_state_init.py` ruff F401 unused `import pytest` + F841 unused `conn` var
- **#372 Lancet**：`tests/agents/robin/test_url_dispatcher.py` 沒過 `ruff format --check`
- **#373 logger**：`thousand_sunny/app.py` import block 沒排序（I001）+ `tests/test_log.py` 沒 format
- **#374 race**：`thousand_sunny/routers/robin.py` coverage 94.73% < 95% critical-path threshold（新 helper `_flip_status_to_translating` 的 OSError + count==0 邊界沒測）

我承接 4 個 lint fix（單命令 ruff fix + commit + push，dispatch overhead 大於命令）+ 1 個 coverage fix（加 2 個 helper 邊界 test）。

教訓寫進 [feedback_subagent_ci_gates_local_verify](feedback_subagent_ci_gates_local_verify.md)：dispatch prompt 必加「push 前跑 ruff check / format / coverage」acceptance。

## 5. File overlap 跟 parallel branch

`fix/sync-llm-json-contract`（修修另一個 session 在做的 sync LLM JSON contract fix）vs 我這 6 PR：

```
parallel:           agents/robin/annotation_merger.py / agents/robin/CONTEXT.md
                    docs/decisions/ADR-018-... / docs/plans/...
                    memory/claude/MEMORY.md / memory/claude/feedback_grill_before_planning.md
                    shared/anthropic_client.py
                    tests/agents/robin/test_annotation_merger.py
                    thousand_sunny/templates/robin/reader.html

我的 6 PR:           agents/franky/__main__.py / agents/robin/__main__.py
                    agents/robin/url_dispatcher.py
                    shared/log.py / shared/state.py / shared/web_scraper.py
                    tests/(...) tests/test_log.py / tests/test_translate_route.py / tests/test_robin_router.py
                    thousand_sunny/app.py / thousand_sunny/routers/robin.py
                    thousand_sunny/templates/robin/index.html
```

**file-level 無交集**。Independently mergeable。MEMORY.md 是潛在衝突點（我這 session memory 也加 entry，跟 parallel + dispatch principle PR #365 三方加行）— resolve 跟下午 PR #364/#365 那次同套路。

## 6. 早上 merge 順序建議

Strict branch protection：merge 一個其他 BEHIND，需 `gh pr update-branch` + 等 CI re-run（~3-4 min/cycle）。

建議：`#369 → #370 → #371 → #372 → #373 → #374` 從小到大、低到高 dep。或全 `gh pr merge --auto --squash` 排隊 GH auto merge。

6 PR 全 squash merge ≈ 5 update-branch cycle ≈ 15-20 min wall。

## 7. 收工狀態

- Server stopped (port 8000 free)
- All 4 agent worktrees pruned
- Local checkout on `main` clean (working tree = HEAD)
- Untracked：`.playwright-mcp/`（playwright MCP server 殘留 dir）、`memory/claude/feedback_dispatch_everything_minimize_main_context.md`（PR #365 還沒 merge 進 main 所以本地看是 untracked）

## Reference

- 早上 PRD ship + 5 grill: [project_session_2026_05_04_evening_ingest_prd](project_session_2026_05_04_evening_ingest_prd.md)
- Stage 1 5 slice ship: [project_session_2026_05_04_late_stage1_ingest_ship](project_session_2026_05_04_late_stage1_ingest_ship.md)
- QA 真主軸 sync LLM blocker: [project_session_2026_05_04_evening_qa_sync_blocker](project_session_2026_05_04_evening_qa_sync_blocker.md)
- 最高指導原則: [feedback_dispatch_everything_minimize_main_context](feedback_dispatch_everything_minimize_main_context.md)
- Sub-agent CI lesson: [feedback_subagent_ci_gates_local_verify](feedback_subagent_ci_gates_local_verify.md)
