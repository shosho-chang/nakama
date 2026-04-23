# UptimeRobot 外部 Uptime Probe 設定

**Scope:** ADR-007 §2 的 blocker 緩解 — 讓 VPS 掛時也能收到告警（Slack / Franky DM 都依賴 VPS，繞不出去）。
**Owner:** 修修手動完成（需要個人手機號 + email）。
**執行條件:** Franky Slice 1 已部署、`GET https://nakama.shosho.tw/healthz` 在公網可訪問。
**時間預估:** 約 20 分鐘。

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

| Monitor Name | Type | URL | Interval | Timeout | Keyword（可選） |
|---|---|---|---|---|---|
| Nakama Gateway healthz | HTTP(s) | `https://nakama.shosho.tw/healthz` | 5 minutes | 30s | `"status":"ok"` |
| WP — shosho.tw | HTTP(s) | `https://shosho.tw/` | 5 minutes | 30s | — |
| WP — fleet.shosho.tw | HTTP(s) | `https://fleet.shosho.tw/` | 5 minutes | 30s | — |

對每一個 monitor：

- **Alert Contacts:** 勾選 Email + SMS（SMS 會自動只在 DOWN 觸發）
- **Alert When Down For:** 設 `10 minutes`（避免單次網路抖動把 SMS 額度用完）
- **Advanced Settings → HTTP Method:** GET
- **Advanced Settings → HTTP Headers:** 留空（/healthz 不需 auth）

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

- **Cloudflare WAF 擋掉 UptimeRobot**：若 monitor 持續 timeout，去 CF Dashboard → Security → WAF → Tools 查 `UptimeRobot` bot UA 是否被 Bot Fight Mode 攔截。Phase 1 用 Skip rule 放行其 UA。
- **Keyword matching 誤判**：若 `/healthz` response 改版後 `"status":"ok"` 字串位置變動，keyword check 可能炸。`HealthzResponseV1` 的 schema 有 `status` 欄位，移除或改名 = 破契約，需要同步更新本 monitor 的 keyword。
- **SMS 額度用完**：免費 20 則/月用完後，只剩 email。升級 Pro 方案（$7/mo 起）或等月初 reset。
