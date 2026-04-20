---
name: Nami Gmail 整合狀態
description: Nami Gmail 6 tools 完成並部署，triage 規則設定完成
type: project
created: 2026-04-20
updated: 2026-04-20
confidence: high
ttl: 90d
---

**Gmail 整合完成並 VPS 部署（2026-04-20，commits f15d3db / 761544b / 8a5d415 / e35f588）**

## 已完成

- `shared/google_gmail.py` — OAuth client（與 Calendar 帳號分離，共用 credentials.json，token 存 `data/google_gmail_token.json`）
- `scripts/google_gmail_auth.py` — 一次性 OAuth consent（需用 Gmail 帳號登入）
- 6 個 Nami tools：`list_gmail_unread` / `get_gmail_message` / `search_gmail_history` / `create_gmail_draft` / `update_gmail_draft` / `send_gmail_draft`
- Code review（PR #57）修掉三個 bug：Gmail URL `u/0/`、missing event in update_gmail_draft、per-thread service client

## Triage 規則

- Primary 未讀：`category:primary is:unread`（Promotions / Social / Updates 不看）
- 超時待回：`label:Respond/Shosho older_than:1d`，超時則 ⚠️ 優先列出
- 每次掃信固定呼叫兩次 `list_gmail_unread`

## 技術踩坑

- `googleapiclient` 的 service 不是 thread-safe，ThreadPoolExecutor 裡每個 thread 必須各自 `_get_service()`，否則 `ssl.SSLError: DECRYPTION_FAILED`

## 待辦

- 修修本機：`python scripts/google_gmail_auth.py` → scp token → 已完成（token 存在 VPS）
- 報價 Sales Kit：目前靠 `search_gmail_history("in:sent 報價")` 撐，KB/Wiki 無歷史資料

**Why:** 修修需要 Nami 幫忙管 Gmail，包括 triage、回覆草稿、報價流程。
**How to apply:** Gmail 相關功能已上線，可直接在 Slack 問 Nami。
