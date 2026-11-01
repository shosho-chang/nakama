# 2026-09-26 — 加 `nakama-sanji/0.1` Cloudflare WAF skip rule（修修手動）

**Owner**: 修修（CF dashboard 需要 owner 權限；repo 的 `CLOUDFLARE_API_TOKEN` 沒有讀寫 WAF 規則的權限）
**Trigger**: Sanji 主迴圈自 2026-09-06 起每一輪 `GET /events → 403`（Cloudflare challenge 頁）
**預估時間**: 5 分鐘

---

## 為什麼

Sanji（`agents/sanji/wp_client.py`）從 VPS（202.182.107.202，Vultr）打
`https://fleet.shosho.tw/wp-json/nakama-gam/v1/*`，帶的 User-Agent 是 `nakama-sanji/0.1`。
這個 UA **從來沒登記進 skip rule**（2026-08 上線時漏了 [cf-waf-skip-rules.md](cf-waf-skip-rules.md) 的 SOP），
前 12 天沒被 CF 判成機器人純屬運氣；2026-09-05 深夜到 09-06 05:00 之間開始被
Super Bot Fight Mode 判定為 bot，從此每一輪都拿到 challenge 頁。

證據（2026-09-26 從 CF GraphQL `firewallEventsAdaptiveGroups` 查 VPS IP 過去 24h）：

| 次數 | 動作 | 來源 | UA | 路徑 |
|---|---|---|---|---|
| 1419 | managed_challenge | SBFM「manage definite bots」 | `nakama-sanji/0.1` | `/wp-json/nakama-gam/v1/events` |
| 286 | skip | Custom「Skip SBFM for WordPressClient agent」 | `nakama-wordpress-client/1.0` | `/wp-json/wp/v2/users/me` |

同一台 VPS、同一個路徑前綴——有登記的 UA 過、沒登記的被擋。從修修桌機（住宅 IP）打同一個 URL 是正常的 401（到得了 WP）。

---

## 步驟

### 1. 開 Cloudflare dashboard

→ https://dash.cloudflare.com/ → 選 **shosho.tw** zone（fleet.shosho.tw 是同 zone 的子網域）

### 2. 進 Custom Rules

左側 **Security → WAF → Custom rules**（不是 Managed rules）→ **Create rule**

### 3. 填規則

| 欄位 | 值 |
|---|---|
| **Rule name** | `Skip SBFM for Sanji agent` |
| **Expression**（點「Edit expression」直接貼） | `(http.user_agent eq "nakama-sanji/0.1" and http.host eq "fleet.shosho.tw" and starts_with(http.request.uri.path, "/wp-json/nakama-gam/") and ip.src eq 202.182.107.202)` |
| **Choose action** | `Skip` |
| **要跳過的東西** | ☑ **All Super Bot Fight Mode Rules**（只勾這個就夠；畫面文字若不同，以「Skip → Super Bot Fight Mode」為準） |
| **Place at** | First（跟其他 `Skip SBFM for ...` 規則放一起） |

四個條件全部 AND：UA、host、API 路徑、VPS IP——只開給 Sanji 打自己的 API，濫用面跟
`Skip SBFM for WordPressClient agent` 一樣窄。

### 4. Deploy

按 **Deploy**。約 30 秒生效。

---

## 驗收（做完 ssh 跑這條，回我結果）

```bash
ssh nakama-vps "cd /home/nakama && python3 -m agents.sanji health"
```

| 結果 | 意義 |
|---|---|
| 印出一段 JSON ✅ | rule 生效 |
| `403` + `Just a moment` / `Cloudflare challenge` ❌ | 還沒生效——檢查 UA 是否拼成 `nakama-sanji/0.1`、有沒有勾 SBFM、等 1 分鐘再試 |

生效後**不用重啟任何服務**：Sanji 主迴圈每 60 秒重試一次，下一輪就會通。
cursor 停在 event #416，plugin 端積壓的事件（09-26 早上是 417 筆：222 presence_day、
144 reaction_added、51 comment_received）會在幾輪內自動補處理——被讚／被留言的 XP
一次補進帳，沒有遺失（presence_day 目前不在計分名單，只推進 cursor）。確認方式：

```bash
ssh nakama-vps "journalctl -u nakama-sanji --since '10 min ago' --no-pager | grep -E 'cycle:|恢復' | tail -5"
```

看到 `[loop] cycle: N events, M deterministic grants` 就是恢復了。
