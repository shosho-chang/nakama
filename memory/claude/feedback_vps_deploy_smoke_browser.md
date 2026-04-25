---
name: VPS deploy 後的 smoke test 不能信 curl 200/403，要靠修修瀏覽器確認
description: nakama.shosho.tw 全域走 CF SBFM，curl/wget/Python requests UA 全 403 — 包括早就上線的 /bridge/memory；靠 curl response code 驗 deploy 沒意義
type: feedback
originSessionId: 788acb56-5d6f-452b-b1eb-20fdc8a14057
---
**規則：VPS 部署 thousand-sunny / nakama-gateway 等 CF-fronted 服務後，smoke test 要修修瀏覽器（帶 cookie）才算數，不要拿 curl 200/302/403 當證據。**

**Why:** 2026-04-25 PR #136 + #137 部署完後我跑 `curl https://nakama.shosho.tw/bridge/drafts` 想確認 route 活著，三個 endpoint 全回 403。一度懷疑 deploy 沒生效；同時 curl 既有的 `/bridge/memory`（部署數週）也回 403 — 確認 403 是 CF SBFM 擋 curl UA，**所有 nakama.shosho.tw 對 curl 都 403**。連 `-A` 換 browser UA 也擋（CF 還看其他 fingerprint）。詳情見 [feedback_uptimerobot_cost_benefit.md](feedback_uptimerobot_cost_benefit.md)（同條 SBFM 已知坑）。

**How to apply:**

- VPS 部署 PR 後告知修修做兩件事：(1) `git pull && systemctl restart <service>`、(2) 開瀏覽器訪問新 route 確認。我這邊**不要再 curl 浪費時間**
- 真要 self-verify deploy：`ssh nakama-vps 'systemctl status thousand-sunny | head -10'` 看 service 是否 running + 沒 crash log。route registration 從 `journalctl -u thousand-sunny -n 50` 看 uvicorn startup log（會列出 routes）
- VPS 內部 smoke 要繞 CF：`ssh nakama-vps 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/bridge/drafts'` — 直連 uvicorn port 不過 CF
- 例外：`/healthz` 類 path 已被 CF WAF skip rule 放行（per `feedback_uptimerobot_cost_benefit.md`），那條 curl 才會回真實 status；其他 route 不要 generalize
- **不要** assume curl 403 就是「CF 擋」就 OK — 也可能 service 真的死了 + CF 把 502 包成 challenge page。要 disambiguate 必須走 ssh 看 service status

**踩過**：PR #136 部署後我 curl 三 endpoint 全 403 一度想 debug，發現 `/bridge/memory` 也 403 才意識到 CF 全擋。修修瀏覽器看到正常 = source of truth。
