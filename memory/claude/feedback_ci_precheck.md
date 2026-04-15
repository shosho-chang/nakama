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
