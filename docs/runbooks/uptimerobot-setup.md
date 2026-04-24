# UptimeRobot 外部 Uptime Probe 設定

> ⚠️ **DEPRECATED（2026-04-24）** — 本 runbook 不再是 external uptime probe 的首選方案。
>
> 2026-04-24 Franky 上線依此 runbook 設定，**三層坑合計 25+ 分鐘未轉綠**後擱置：
> 1. **HEAD vs GET** — HTTP(s) monitor 預設送 HEAD，`/healthz` 只接 GET → 405。切 HTTP Method 是 **paid-only**。Workaround 是改用 `Keyword` monitor（強制 GET）。
> 2. **Keyword 欄位對特殊字元不友善** — `"status":"ok"` 雙引號會被搞壞；**改用純 ASCII unique 字串**（e.g. `nakama-gateway`）。
> 3. **Cloudflare Bot Fight Mode 擋 datacenter IP** — UptimeRobot Ashburn 節點被 CF 當 bot → challenge page → keyword miss → 回報 DOWN。**要做 CF WAF Skip rule**，且要等 propagate。
>
> **新的首選方案：GitHub Actions cron + Slack webhook**（`.github/workflows/external-probe.yml`，~15 分鐘寫完；runner IP 不在 CF datacenter bot list；Slack mobile push 繞過 VPS 夠用；零 CF 配置）。
>
> 背景：[memory/claude/feedback_uptimerobot_cost_benefit.md](../../memory/claude/feedback_uptimerobot_cost_benefit.md)
>
> **本 runbook 保留的場景：** 僅當你需要 SMS fallback（VPS + CF Tunnel + GitHub runner 全掛時發手機簡訊）才走 UptimeRobot。個人創作者實際觸發機率 1-2 次/年。啟用前務必先做下方「三坑預防檢查」。

---

## 三坑預防檢查（啟用前必看）

走本 runbook 前，下列三件事必須先做完，否則 monitor 一定不會轉綠：

1. **Monitor type 選 `Keyword`（不選 HTTP(s)）** — Free plan 的 HTTP(s) 固定送 HEAD，`/healthz` 只接 GET。Keyword type 強制 GET 因為要讀 body。
2. **Keyword 欄位用純 ASCII unique 字串** — 不要用 `"status":"ok"`（雙引號會被解析器搞壞）。建議用 service 名如 `nakama-gateway` 或 `healthz`。
3. **CF WAF Custom Rule 預先放行 UptimeRobot UA** — 規則見本文件尾的「踩坑備忘」區段。做完後等 ~5 分鐘 propagate 再啟用 monitor。

---

**Scope:** ADR-007 §2 的 blocker 緩解 — 讓 VPS 掛時也能收到告警（Slack / Franky DM 都依賴 VPS，繞不出去）。
**Owner:** 修修手動完成（需要個人手機號 + email）。
**執行條件:** Franky Slice 1 已部署、`GET https://nakama.shosho.tw/healthz` 在公網可訪問。
**時間預估:** 約 20 分鐘（含三坑預防檢查）。

---

## 為什麼需要外部 probe

Franky 本身跑在 VPS 內。VPS 掛 = Franky 一起死 = Slack DM 不會送 = 修修不會知道。

外部 probe 解法：UptimeRobot 在他們自己的基礎設施上每 5 分鐘戳 `https://nakama.shosho.tw/healthz`，失敗時從 **email + SMS** 兩個**繞過 VPS 的通道**通知修修。

`reliability.md` §4 把 VPS 列為首要 SPOF；本 runbook 是 Phase 1 的對應緩解措施。

---

## 前置檢查

執行前確認以下都已完成（非修修手動的部分）：

- [ ] Franky Slice 1 已 merge 到 main
- [ ] VPS 已 deploy 新版 `thousand_sunny`（包含 `GET /healthz`）
- [ ] 本機驗證：`curl -s https://nakama.shosho.tw/healthz | jq .status` 回 `"ok"`
- [ ] 本機驗證：Response time < 200ms（`curl -w "%{time_total}\n" -s -o /dev/null https://nakama.shosho.tw/healthz`）
- [ ] 本機驗證：response 不含 secrets（看 body 沒有 token / password 字樣）

---

## 步驟

### 1. 建立帳號（一次性）

1. 前往 https://uptimerobot.com/ 註冊（免費方案 50 monitors / 5-min interval）
2. 綁修修主要 email（建議放 alias，例如 `uptimerobot@shosho.tw`，日後轉寄方便改）
3. 綁台灣手機號（台灣格式 `+886 9xx xxx xxx`）

### 2. 新增 Alert Contacts

在 **My Settings → Alert Contacts** 建立兩個 contact：

| Type | 通道 | Alert Timing |
|---|---|---|
| Email | 修修主 email | Notify every time status changes |
| SMS | 修修手機號 | **Only when monitor goes DOWN**（免費額度 20 則/月，要省） |

> 為什麼 SMS 只發 DOWN：UptimeRobot 免費 SMS 每月只有 20 則；如果 UP/DOWN 都發，一次 flapping 就能打爆配額。DOWN-only 保證最關鍵的「全掛了」訊號一定送得到。

### 3. 新增三個 Monitor

在 **Dashboard → + New Monitor** 建立：

| Monitor Name | Type | URL | Interval | Timeout | Keyword |
|---|---|---|---|---|---|
| Nakama Gateway healthz | **Keyword** | `https://nakama.shosho.tw/healthz` | 5 minutes | 30s | `nakama-gateway`（純 ASCII，無引號） |
| WP — shosho.tw | Keyword | `https://shosho.tw/` | 5 minutes | 30s | `shosho`（或任一首頁必現字串） |
| WP — fleet.shosho.tw | Keyword | `https://fleet.shosho.tw/` | 5 minutes | 30s | `fleet`（同上） |

> **Type 一律選 Keyword，不選 HTTP(s)** — HTTP(s) 預設送 HEAD 會被 `/healthz` 回 405；切 GET 是 paid-only。Keyword type 強制 GET。
>
> **Keyword 值用純 ASCII unique 字串** — 不要用 `"status":"ok"`，雙引號會被解析器搞壞。建議用 service 名。
>
> **前置任務：** `/healthz` response body 必須含有該 keyword。Nakama Gateway 目前回傳 `{"service":"nakama-gateway","status":"ok",...}`，所以 `nakama-gateway` 成立（contract 來源：[shared/schemas/franky.py:95](../../shared/schemas/franky.py#L95) `service: Literal["nakama-gateway"]`）。若未來 `/healthz` schema 變動，這裡的 keyword 要同步。

對每一個 monitor：

- **Alert Contacts:** 勾選 Email + SMS（SMS 會自動只在 DOWN 觸發）
- **Alert When Down For:** 設 `10 minutes`（避免單次網路抖動把 SMS 額度用完）
- **Advanced Settings → HTTP Headers:** 留空（`/healthz` 不需 auth）

### 4. Maintenance Window（避免 VPS 週期維護觸發告警）

在 **My Settings → Maintenance Windows** 建立：

| Name | Type | Interval | Start | Duration |
|---|---|---|---|---|
| VPS weekly OS patch | Weekly | 每週二 | 03:00 Asia/Taipei（對應 UTC 19:00 前一天） | 30 min |

> 時區陷阱：UptimeRobot 後台設定用 UTC。台北 03:00 = UTC 19:00 前一天。

### 5. 匯出 Status Page（選用，未來 Chopper 社群信任訊號用）

在 **Dashboard → Status Pages → + Add Status Page**：

- **Type:** Private（只給修修看；公開版未來 Chopper 階段再考慮）
- **Monitors:** 勾上面三個
- **Custom Domain:** 留空（Phase 1 用 `*.uptimerobot.com` subdomain 即可）

複製 Private URL 存到 `memory/claude/project_phase1_infra_checkpoint.md` 或個人密碼管理器。

---

## 驗收測試（修修設定完後做）

### A. Happy path

1. 等 5 分鐘讓每個 monitor 跑一次
2. 在 Dashboard 看三個 monitor 全顯示 **Up**
3. 收到一封「All monitors are up」初始化 email

### B. 模擬 DOWN — SMS 送達測試

> **警告:** 此測試會發 1 則 SMS，占月度免費額度。

1. VPS 上暫停 `thousand-sunny` service：`sudo systemctl stop thousand-sunny`
2. 等 10 分鐘（配合 "Alert When Down For: 10 minutes"）
3. **預期:** 收到 email + SMS 通知「Nakama Gateway healthz is DOWN」
4. **關鍵驗證:** 訊息是從 UptimeRobot 寄的，**不是** Slack（證明繞過 VPS 通道成功）
5. VPS 恢復 service：`sudo systemctl start thousand-sunny`
6. 等 5 分鐘，收到 email「Nakama Gateway healthz is UP」（無 SMS，因為 UP 不發）

### C. 告警矛盾情境（手動觀察）

- **外部 probe 說 UP 但內部 Franky 說 DOWN**（e.g. Cloudflare 到 origin 某段路徑炸）
  → 處理原則：UptimeRobot 為外部權威，Franky DM 為內部補充資訊。先信外部。
- **外部 probe 說 DOWN 但內部 Franky 說 UP**（e.g. UptimeRobot 到 VPS 的某段 route 炸）
  → 先手動 curl 確認是誰對，再決定修哪邊。

---

## 完工後要做的事

- [ ] 回填進 `.env.example`：添加 `NAKAMA_HEALTHZ_URL` 說明（Franky loopback probe 的 override 點）
- [ ] 更新 `project_phase1_infra_checkpoint.md`：勾掉 UptimeRobot 設定
- [ ] 加 private status page URL 到 `reference_vps_paths.md` 或其他 reference 記憶
- [ ] Slice 3 的 weekly digest template 要加「本週外部 probe uptime %」欄位（由 UptimeRobot 匯出 API 抓）

---

## 踩坑備忘（補充）

- **Cloudflare Bot Fight Mode 擋掉 UptimeRobot datacenter IP**：2026-04-24 Franky 上線實測 → monitor 持續 DOWN 因為 CF 把 UptimeRobot Ashburn 節點當 bot 回 challenge page，body 無 keyword → 失敗。**解法：在 CF Dashboard → Security → WAF → Custom Rules 加 Skip rule：**
  ```
  Expression:
  (http.host eq "nakama.shosho.tw" and starts_with(http.request.uri.path, "/healthz") and http.user_agent contains "UptimeRobot")

  Action: Skip → 勾選 All Managed Rules + Super Bot Fight Mode + User Agent Blocking + Rate Limiting
  ```
  做完後等 ~5 分鐘 propagate 再啟用 monitor。其他網域（`shosho.tw` / `fleet.shosho.tw`）若也走 CF，要同樣加規則 — 把 `http.host eq` 的 host 替換即可。
- **Keyword matching 誤判**：若 `/healthz` response 改版後 `"status":"ok"` 字串位置變動，keyword check 可能炸。`HealthzResponseV1` 的 schema 有 `status` 欄位，移除或改名 = 破契約，需要同步更新本 monitor 的 keyword。
- **SMS 額度用完**：免費 20 則/月用完後，只剩 email。升級 Pro 方案（$7/mo 起）或等月初 reset。
