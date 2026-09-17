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

**2026-09-17 補充 — 先確認 ruff 版本對得上 CI，否則上面整條 checklist 是假的**：
`pyproject.toml` pin `ruff>=0.15,<0.16`（註解寫明「0.16.0 改 format 風格，未 pin 會讓
全部 PR format check 紅」），但 `E:\nakama\.venv-v2` 裡是 **0.16.2**。用它跑
`ruff format --check .` 會同時犯兩種錯：本機說乾淨的檔案 CI 照樣紅（PR #1279 就是這樣
燒掉一輪 CI），本機說「73 個檔案要重排」則全是假陽性——照上面那條「順手把上游漂移一起
format 修掉」去做，會把 73 個無關檔案捲進 PR。

**How to apply:** 驗格式前先 `ruff --version` 對一次 `pyproject.toml` 的 pin。對不上就裝
一份對版本的來跑，不要動 `.venv-v2`（別的東西靠它）：

```bash
python -m pip install --quiet --target /tmp/ruff15 "ruff>=0.15,<0.16"
/tmp/ruff15/bin/ruff.exe format --check .
```

`ruff check`（lint）兩個版本目前沒有分歧，出問題的只有 `format`。
