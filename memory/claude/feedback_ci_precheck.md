---
name: CI 前檢查必須跑 ruff check + ruff format
description: commit 前跑 ruff check（lint）+ ruff format（格式化），不只 format，避免 CI 反覆失敗
type: feedback
---

commit Python 檔案前必須跑兩個命令：
1. `ruff check --fix .`（修 import 排序等 lint 問題）
2. `ruff format .`（格式化）

**Why:** 2026-04-14~15 連續多次 CI 失敗都是因為只跑了 `ruff format` 沒跑 `ruff check`。CI 會檢查 I001（import 排序）等 lint 規則，光靠 format 不夠。

**How to apply:** 每次 commit 前的 checklist：`ruff check --fix . && ruff format . && ruff format --check .`

**2026-04-18 補充 — 必須是「全 repo `.`」不是只檢查自己改的檔**：
PR #32 跑 `ruff format --check scripts/run_keyword_research.py` 通過，但 CI 跑 `ruff format --check .` 失敗 — 因為 PR #29 留下的 `thousand_sunny/routers/robin.py` 格式漂移被 CI 抓到。自己 PR 的檔乾淨不代表 CI 會過，因為 CI 掃整個 repo。**每次 commit 前必須 `ruff format --check .` 全 repo**。順手發現的上游漂移應該在當下 PR 一起 format 修掉（commit 訊息寫 `chore: ruff format X（修 CI）`），不用另開 PR。
