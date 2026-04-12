---
name: project_code_review_2026_04_12
description: 2026-04-12 首次全面 code review — 9 項修復已合併（含安全漏洞）
type: project
---

## Code Review 修復摘要（2026-04-12，commit cd744b9 + c56e499）

### Critical（已修復）
- **Path Traversal** — web.py 6 個 endpoint 全部加上 `_safe_resolve()` 路徑驗證
- **Default Secret** — 移除硬編碼預設值，auth 改用 `hmac.compare_digest`

### High（已修復）
- **kb_search.py** — `get_client("robin")` TypeError 修正（get_client 不接受參數）
- **log.py** — Logger singleton 改為正確子 logger 架構
- **events.py** — Event Bus 新增 `event_consumptions` 表，支援多 agent 獨立消費

### Medium（已修復）
- SQLite `check_same_thread=False` + WAL mode
- frontmatter 解析統一為 `utils.extract_frontmatter`
- 內容截斷改為段落/句子邊界
- vault 相對路徑改用 `get_vault_path()` 基準

### 仍待改善（Low — 未修）
- Login 無 brute-force rate limit
- `source_type` 未驗證合法值
- 測試覆蓋率仍極低（僅 5 個 test）

**Why:** 部署到 VPS 前必須修復安全漏洞；Event Bus 修復是 Nami 開發的前置條件。
**How to apply:** 新增 endpoint 時必須用 `_safe_resolve()`；新增 agent 消費事件時使用新的 `event_consumptions` 機制。
