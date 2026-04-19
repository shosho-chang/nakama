---
name: 新功能上 VPS 前先 verify .env 有對應 key
description: Feature code 讀的 env fallback 到無用預設值會讓 prod 靜默失效；部署前要檢查 VPS .env
type: feedback
tags: [vps, deployment, env, fallback-trap]
created: 2026-04-20
---

**規則**：新功能的 backend 若有 `os.environ.get("SOME_KEY") or "some_default"` 類的 fallback，上 VPS 前要確認 `/home/nakama/.env` 實際有 `SOME_KEY=<real_value>`，而不是仰賴預設值。

**Why:** 2026-04-20 Phase 4 Bridge UI PR-B 部署後 `/bridge/memory` 顯示 0 筆，診斷發現 `_default_user_id()` 回 `"shosho"`（fallback），但 Nami 實際寫入的 rows 都是 `user_id='U05F841H127'`（Slack ID），兩者對不上。VPS `.env` 沒設 `SLACK_USER_ID_SHOSHO` 也沒設 `NAKAMA_DEFAULT_USER_ID`。本機沒炸是因為 SQLite 整個是空的，測試也用 `monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")`。Prod 資料真實存在才踩到。

**How to apply:**

1. 加新 feature 時，任何 `os.environ.get("KEY") or fallback` / `os.environ.get("KEY", default)` 都標記下來
2. 開 PR 時在描述的 Test plan 裡明列「VPS `.env` 需確認／新增哪些 key」
3. VPS deploy 後 smoke-test 不只看 HTTP 200，還要驗 response body 裡的資料跟 DB 一致
4. 真的需要 fallback 時，fallback 值要讓人**立刻發現錯了**（例如把查詢結果刻意染成空或顯眼 placeholder），而非對一個不會命中的值靜默查詢

**相關**：
- `reference_vps_paths.md` — VPS 關鍵路徑 + systemd 狀態
- `feedback_windows_abs_path_silent.md` — 類似的「本地矇混 / prod 才炸」陷阱
