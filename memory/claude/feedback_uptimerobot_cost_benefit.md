---
name: UptimeRobot free 不划算，改用 GitHub Actions + Slack bot DM（但 CF WAF skip rule 省不掉）
description: 2026-04-24 實測 UptimeRobot 三坑擱置，改 GH Actions 上線；原以為 GH runner IP 可繞 CF bot list，實測也被 SBFM 擋 403，CF WAF skip rule 對 CF-fronted endpoint 是必備，選工具不能基於「避 CF 配置」
type: feedback
originSessionId: 1a1bc40d-2a4c-41f0-9c92-b3e7c6485156
---
需要 external uptime probe 戳 CF-fronted endpoint 時：

- **優先：GitHub Actions cron + Slack bot DM**（不是 UptimeRobot free）
- **必須：CF WAF skip rule**（不論選哪家工具）

**Why:** 2026-04-24 同一天踩完兩輪：

**第一輪：UptimeRobot free plan 三坑 25+ 分鐘未轉綠擱置：**

1. **HEAD vs GET** — HTTP(s) monitor 預設送 HEAD，`/healthz` 只回應 GET → 405。切 HTTP Method 是 paid-only 功能。Workaround：改用 `Keyword` monitor type（預設 GET，因為要讀 body 比對 keyword）。
2. **Keyword 欄位對特殊字元不友善** — `"status":"ok"` 雙引號會被搞壞；純 ASCII unique 字串才穩（e.g. service 名 `nakama-gateway`）。
3. **Cloudflare Bot Fight Mode 擋 datacenter IP** — UptimeRobot Ashburn 節點 IP 被 CF 當 bot 回 challenge page。

**第二輪：改 GH Actions 也被 CF 403 擋（原假設錯）：**

- 我原本以為「GH runner IP 不在 CF datacenter bot list」所以可以繞 CF 配置。實測 ubuntu-latest runner（Azure eastus）三個 URL 全回 **HTTP 403**。
- 結論：**CF SBFM 擋公有 cloud datacenter IP**，不是 UptimeRobot 獨家問題。只要 endpoint 套 CF 且 SBFM 開啟，free tier 外部 probe 服務（UptimeRobot / GH Actions / Cron-Job.org 等）普遍會中。部分商用服務（Datadog Synthetics / Pingdom 高價位）跟 CF 有 partner whitelist，但 overkill 個人場景。
- 唯一乾淨例外：probe from 真正 residential IP（如家裡 ISP），但家用網路不適合當 uptime monitor（自己掛 = false positive）。

**How to apply:**

工具選擇（CP 順序）：

1. **GitHub Actions cron + Slack bot DM**（首選）— UI 乾淨、workflow 可版本控制、secret 用 `gh secret set`、無月費上限噪音。**範本：`.github/workflows/external-probe.yml`**
2. **UptimeRobot free** — 僅當真的需要 SMS fallback（VPS + Slack + GH 三家同時死），不然 UI friction 比 GH Actions 多
3. 其他商用 probe 服務 — 月費夠高就跳過 CF 坑，但 overkill 個人場景

CF WAF skip rule（**必做**，不論工具）：

```
(http.user_agent contains "<your-probe-UA>" and http.request.uri.path in {"<probe-paths>"})
```

Action: **Skip** → All Super Bot Fight Mode rules（這條關鍵）+ All Managed Rules + User Agent Blocking + Rate Limiting

- **UA + path 兩個條件都要**：UA 從 public repo 可讀到（workflow 裡 `curl -A`），不是 auth secret；path scope 才是實質防線 — 限定 skip 只對 probe 真的會戳的 path 生效，其他 path 仍受 WAF 保護
- UA 選 specific 字串（e.g. `nakama-external-probe/1.0`），不要 `contains "bot"` 之類 generic match
- GH Actions workflow 用 `curl -A "nakama-external-probe/1.0 (github-actions)"` 設 UA
- UptimeRobot 走 `contains "UptimeRobot"`（其 UA 固定）
- **反例**：只放 UA 不限 path 會讓攻擊者偽造 UA 繞過全 zone 的 SBFM + Rate Limit，擴大攻擊面
- **真要更嚴**：改 shared-secret header（CF Free plan 已支援 `http.request.headers["x-probe-secret"][0] eq "..."` 語法），workflow 送 header 而非 UA

**Runbook 實作參考：** [docs/runbooks/external-probe-setup.md](../../docs/runbooks/external-probe-setup.md)（GH Actions + CF WAF skip rule 完整步驟）
