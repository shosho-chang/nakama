---
name: ruff 預設 isort 啟用 order-by-type（CONSTANTS 先於 PascalCase）
description: ruff 的 isort 預設按 naming convention 分類排序——ALL_CAPS 常數在前、PascalCase 類在後、lower 函式再後——不是純 ASCII 字母序
type: feedback
tags: [ruff, isort, import-order, lint]
originSessionId: d6248f62-150b-43c6-ac05-98394223e172
---
ruff 的 isort（`[tool.ruff.lint]` 含 `I` rule）預設 `order-by-type = true`，同一 import 群組內會依「偵測到的類型」先分區再字母序：

1. ALL_CAPS 常數（`DEFAULT_X`、`MAX_Y`）
2. PascalCase 類（`AlertV1`、`HealthProbeV1`）
3. camelCase / lowercase 函式（`get_pricing`、`calc_cost`）

**Why:** ASCII 字母序會把 `AlertV1` 排在 `DEFAULT_X` 之前（A < D），但 ruff 的預設會反過來。Repo 內證據：`tests/test_lifeos_writer.py` 就是 `CONTENT_TYPES, DEFAULT_TASKS, ProjectExistsError`（常數先，類後）。

**How to apply:**

- 寫多 member 的 `from X import (A, B, C)` 時，手動排序用這個心智模型：大寫常數一區 → 類一區 → 函式一區，每區內 ASCII 序。
- Debug CI I001 錯誤時先看是不是跟這規則撞車，不要硬猜成 ASCII 序。
- 如果想關掉（不建議），`pyproject.toml` 加 `[tool.ruff.lint.isort]` 設 `order-by-type = false`。
- 案例：Franky Slice 1（2026-04-23 PR #74）第一次 CI fail 就是這個，把 `AlertV1` 擺到 `DEFAULT_FAIL_THRESHOLD` 前面被打槍。
