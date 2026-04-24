---
name: UptimeRobot 免費方案 CP 值差，改用 GitHub Actions + Slack webhook
description: 2026-04-24 實測 UptimeRobot free plan 對 CF-fronted JSON healthz 有三層坑，超過 25 分鐘未轉綠，推 GitHub Actions 替代
type: feedback
originSessionId: 1a1bc40d-2a4c-41f0-9c92-b3e7c6485156
---
需要 external uptime probe 時，優先考慮 **GitHub Actions cron + Slack webhook**，不用 UptimeRobot 免費方案。

**Why:** 2026-04-24 Franky 上線依 runbook 設定 UptimeRobot 踩了三層坑，25+ 分鐘沒轉綠後擱置：

1. **HEAD vs GET** — HTTP(s) monitor 預設送 HEAD，`/healthz` 只回應 GET → 405。切 HTTP Method 是 paid-only 功能。Workaround：改用 `Keyword` monitor type（預設 GET，因為要讀 body 比對 keyword）。
2. **Keyword 欄位對特殊字元不友善** — `"status":"ok"` 雙引號會被搞壞；純 ASCII unique 字串才穩（e.g. service 名 `nakama-gateway`）。
3. **Cloudflare Bot Fight Mode 擋 datacenter IP** — UptimeRobot Ashburn 節點 IP 被 CF 當 bot 回 challenge page，body 無 keyword → 回報 DOWN。解法要 CF WAF Custom Rule Skip，即使做了也要等 propagate。

UptimeRobot 的 unique value = 「VPS + CF Tunnel 都死時繞過 VPS 通道送 SMS」。個人創作者實際機率 1-2 次/年，miss 一次代價 = 延遲幾小時發現。三層坑合計 >= 20 分鐘初始設定 + 未來維護成本。

**How to apply:**
- **優先：GitHub Actions cron + Slack webhook** — GitHub runner IP 不在 CF datacenter bot list；Slack mobile push 繞過 VPS 夠用；零 CF 配置；~15 分鐘寫完 `.github/workflows/external-probe.yml`
- **UptimeRobot 只在「一定要 SMS fallback」時才考慮**；且預先把 CF WAF skip rule 做好、keyword 用純 ASCII、monitor type 選 Keyword，再啟用 monitor
- 如果走 UptimeRobot 的 CF WAF skip rule：
  ```
  (http.host eq "nakama.shosho.tw" and starts_with(http.request.uri.path, "/healthz") and http.user_agent contains "UptimeRobot")
  ```
  Action: Skip → All Managed Rules + Super Bot Fight Mode + User Agent Blocking + Rate Limiting
